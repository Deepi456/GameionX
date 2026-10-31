import json
import os
import sqlite3
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[1] / ".env")

from app.agents.game_builder import GameBuilderAgent, GameRuntimeConfig
from app.agents.game_designer import GameDesignerAgent
from app.agents.game_qa import GameQAAgent, QAReport
from app.agents.game_orchestrator import GameOrchestrator, WorkflowExecutionError
from app.auth import (
    create_session,
    current_user,
    delete_session,
    hash_password,
    set_session_cookie,
    validate_session_configuration,
    verify_password,
)
from app.database import connect, initialize_database, json_dumps, utc_now
from app.models import (
    ActivityResponse,
    AuthResponse,
    GameDefinitionV2,
    GenerateRequest,
    GenerateResponse,
    GameSpec,
    HealthResponse,
    LoginRequest,
    PlaytestReport,
    ProjectCreateRequest,
    ProjectResponse,
    RegisterRequest,
    SavedGameResponse,
    UpdateGameRequest,
    UserResponse,
)
from app.provider import (
    DevelopmentMockProvider,
    GeminiProviderError,
    GroqProviderError,
    create_provider,
)

app = FastAPI(title="GameionX API", version="0.1.0")
configured_origins = [
    origin.strip()
    for origin in os.getenv("GAMEIONX_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(
        {
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            *configured_origins,
        }
    ),
    allow_methods=["DELETE", "GET", "PATCH", "POST"],
    allow_headers=["Content-Type"],
    allow_credentials=True,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
)
validate_session_configuration()
initialize_database()
provider = None
designer_agent = None
builder_agent = GameBuilderAgent()
qa_agent = GameQAAgent()


def _ensure_provider():
    global provider, designer_agent
    if provider is None:
        provider = create_provider()
        designer_agent = (
            None
            if isinstance(provider, DevelopmentMockProvider)
            else GameDesignerAgent(provider)
        )
    return provider


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    active_provider = _ensure_provider()
    return HealthResponse(
        provider=active_provider.name,
        message=(
            "Using the development-only mock provider."
            if designer_agent is None
            else f"Using {active_provider.name.title()} for GameSpec generation."
        ),
    )


def _partial_generation_result(error: WorkflowExecutionError) -> dict | None:
    workflow = error.workflow_state
    outputs = workflow.agent_outputs

    def latest_output(agent: str) -> dict | None:
        result = next(
            (
                item
                for item in reversed(outputs)
                if item.agent == agent and item.status == "completed"
            ),
            None,
        )
        return result.output if result else None

    spec_output = latest_output("GameDesignerAgent")
    runtime_output = latest_output("GameBuilderAgent")
    if spec_output is None or runtime_output is None:
        return None

    spec = GameSpec.model_validate(spec_output)
    runtime = GameRuntimeConfig.model_validate(runtime_output)
    qa_output = latest_output("GameQAAgent")
    qa_report = (
        QAReport.model_validate(qa_output)
        if qa_output is not None
        else QAReport(
            status="unverified",
            scope="structural",
            summary="QA did not produce a report before the workflow stopped.",
            issues=[],
        )
    )
    playtest_output = latest_output("BrowserPlaytestRunner")
    if playtest_output is not None:
        qa_report = GameQAAgent.attach_playtest(
            qa_report,
            PlaytestReport.model_validate(playtest_output),
        )

    return {
        "provider": provider.name,
        "generation_id": workflow.generation_id,
        "prompt": workflow.original_prompt,
        "spec": spec.model_dump(mode="json"),
        "runtime_config": runtime.model_dump(mode="json"),
        "game_definition": (
            workflow.scene_configuration.model_dump(mode="json")
            if workflow.scene_configuration is not None
            else None
        ),
        "qa_report": qa_report.model_dump(mode="json"),
        "stages": [
            stage.model_dump(mode="json") for stage in workflow.stage_history
        ],
        "workflow": workflow.model_dump(mode="json"),
    }


@app.post("/api/generate", response_model=GenerateResponse)
def generate(request: GenerateRequest) -> GenerateResponse:
    active_provider = _ensure_provider()
    try:
        report = GameOrchestrator(
            active_provider,
            designer=designer_agent,
            builder=builder_agent,
            qa=qa_agent,
        ).generate(
            request.prompt,
            request.mode,
            bypass_cache=request.bypass_cache or request.regenerate,
        )
    except WorkflowExecutionError as error:
        cause = error.__cause__
        if isinstance(cause, (GeminiProviderError, GroqProviderError)):
            return JSONResponse(
                status_code=503,
                content={
                    "code": "llm_unavailable",
                    "message": "The AI is busy right now. Please try again in a minute.",
                },
                headers={"Retry-After": "60"},
            )
        status_code = 422
        headers = {"Retry-After": "30"} if status_code == 503 else None
        return JSONResponse(
            status_code=status_code,
            content={
                "detail": str(error),
                "workflow": error.workflow_state.model_dump(mode="json"),
                "partial_result": _partial_generation_result(error),
            },
            headers=headers,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (GeminiProviderError, GroqProviderError) as error:
        if error.status_code in {429, 500, 503}:
            return JSONResponse(
                status_code=503,
                content={
                    "code": "llm_unavailable",
                    "message": "The AI is busy right now. Please try again in a minute.",
                },
                headers={"Retry-After": "60"},
            )
        raise HTTPException(
            status_code=error.status_code,
            detail=str(error),
            headers={"Retry-After": "30"} if error.status_code == 503 else None,
        ) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return GenerateResponse(
        provider=report.provider,
        generation_id=report.generation_id,
        prompt=request.prompt,
        spec=report.spec,
        runtime_config=report.runtime_config,
        game_definition=report.game_definition,
        qa_report=report.qa_report,
        stages=report.stages,
        workflow=report.workflow,
    )


@app.post("/api/auth/register", response_model=AuthResponse, status_code=201)
def register(request: RegisterRequest, response: Response) -> AuthResponse:
    email = (
        request.email
        if request.email is not None
        else f"{request.username.casefold()}@legacy.gameionx.invalid"
    )
    username = request.username or email
    with connect() as connection:
        try:
            cursor = connection.execute(
                """
                INSERT INTO users(username, email, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (username, email, hash_password(request.password), utc_now()),
            )
        except sqlite3.IntegrityError as error:
            raise HTTPException(
                status_code=409,
                detail="An account with that email or username already exists.",
            ) from error
        user_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO activities(user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (user_id, "login", "Account registered.", utc_now()),
        )
    token = create_session(user_id)
    set_session_cookie(response, token)
    return AuthResponse(
        user=UserResponse(id=user_id, username=username, email=email)
    )


@app.post("/api/auth/login", response_model=AuthResponse)
def login(request: LoginRequest, response: Response) -> AuthResponse:
    with connect() as connection:
        if request.email is not None:
            row = connection.execute(
                """
                SELECT id, username, email, password_hash
                FROM users WHERE email = ? COLLATE NOCASE
                """,
                (request.email,),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT id, username, email, password_hash
                FROM users WHERE username = ? COLLATE NOCASE
                """,
                (request.username,),
            ).fetchone()
    if row is None or not verify_password(request.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    with connect() as connection:
        connection.execute(
            "INSERT INTO activities(user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (row["id"], "login", "Successful login.", utc_now()),
        )
    set_session_cookie(response, create_session(row["id"]))
    return AuthResponse(
        user=UserResponse(
            id=row["id"],
            username=row["username"],
            email=row["email"],
        )
    )


@app.post("/api/auth/logout")
def logout(
    response: Response,
    session: str | None = Cookie(default=None, alias="gameionx_session"),
) -> dict[str, str]:
    response.delete_cookie("gameionx_session")
    delete_session(session)
    return {"message": "Logged out."}


@app.get("/api/auth/me", response_model=UserResponse)
def me(user: UserResponse = Depends(current_user)) -> UserResponse:
    return user


@app.post("/api/games", response_model=SavedGameResponse, status_code=201)
def save_game(payload: GenerateResponse, user: UserResponse = Depends(current_user)) -> SavedGameResponse:
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO saved_games(
                user_id, title, prompt, spec_json, runtime_json, qa_summary, status,
                game_definition_json, qa_report_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.id,
                payload.spec.title,
                payload.prompt or payload.spec.description,
                json_dumps(payload.spec.model_dump()),
                json_dumps(payload.runtime_config),
                payload.qa_report.get("summary", "QA completed."),
                (
                    "ready"
                    if payload.workflow
                    and payload.workflow.final_status == "ready"
                    and payload.workflow.runtime_verified
                    else "warning"
                    if payload.qa_report.get("status") == "warning"
                    else "failed"
                    if payload.qa_report.get("status") == "fail"
                    else "unverified"
                ),
                json_dumps(
                    payload.game_definition.model_dump(mode="json")
                    if payload.game_definition is not None
                    else {}
                ),
                json_dumps(payload.qa_report),
                utc_now(),
                utc_now(),
            ),
        )
        game_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO activities(user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (user.id, "save", f"Saved game {game_id}.", utc_now()),
        )
        row = connection.execute(
            "SELECT * FROM saved_games WHERE id = ? AND user_id = ?",
            (game_id, user.id),
        ).fetchone()
    return _saved_game_response(row)


@app.get("/api/games", response_model=list[SavedGameResponse])
def list_games(user: UserResponse = Depends(current_user)) -> list[SavedGameResponse]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM saved_games WHERE user_id = ? ORDER BY created_at DESC",
            (user.id,),
        ).fetchall()
    return [_saved_game_response(row) for row in rows]


@app.get("/api/games/{game_id}", response_model=SavedGameResponse)
def get_game(game_id: int, user: UserResponse = Depends(current_user)) -> SavedGameResponse:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM saved_games WHERE id = ? AND user_id = ?",
            (game_id, user.id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Saved game not found.")
    return _saved_game_response(row)


@app.patch("/api/games/{game_id}", response_model=SavedGameResponse)
def update_game(
    game_id: int,
    request: UpdateGameRequest,
    user: UserResponse = Depends(current_user),
) -> SavedGameResponse:
    with connect() as connection:
        connection.execute(
            "UPDATE saved_games SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (request.title, utc_now(), game_id, user.id),
        )
        row = connection.execute(
            "SELECT * FROM saved_games WHERE id = ? AND user_id = ?",
            (game_id, user.id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Saved game not found.")
    return _saved_game_response(row)


@app.delete("/api/games/{game_id}", status_code=204)
def delete_game(game_id: int, user: UserResponse = Depends(current_user)) -> Response:
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM saved_games WHERE id = ? AND user_id = ?",
            (game_id, user.id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Saved game not found.")
        connection.execute(
            "INSERT INTO activities(user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (user.id, "delete", f"Deleted game {game_id}.", utc_now()),
        )
    return Response(status_code=204)


@app.post("/api/projects", response_model=ProjectResponse, status_code=201)
def create_project(
    payload: ProjectCreateRequest,
    user: UserResponse = Depends(current_user),
) -> ProjectResponse:
    now = utc_now()
    status = _project_status(payload.qa_report)
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO projects(
                owner_id, title, original_prompt, status, spec_json, runtime_json,
                game_definition_json, qa_report_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.id,
                payload.spec.title,
                payload.prompt,
                status,
                json_dumps(payload.spec.model_dump(mode="json")),
                json_dumps(payload.runtime_config),
                json_dumps(
                    payload.game_definition.model_dump(mode="json")
                    if payload.game_definition is not None
                    else {}
                ),
                json_dumps(payload.qa_report),
                now,
                now,
            ),
        )
        project_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO activities(user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (user.id, "save", f"Saved project {project_id}.", now),
        )
        row = connection.execute(
            "SELECT * FROM projects WHERE id = ? AND owner_id = ?",
            (project_id, user.id),
        ).fetchone()
    return _project_response(row)


@app.get("/api/projects", response_model=list[ProjectResponse])
def list_projects(user: UserResponse = Depends(current_user)) -> list[ProjectResponse]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM projects WHERE owner_id = ? ORDER BY created_at DESC",
            (user.id,),
        ).fetchall()
    return [_project_response(row) for row in rows]


@app.get("/api/projects/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: int,
    user: UserResponse = Depends(current_user),
) -> ProjectResponse:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM projects WHERE id = ? AND owner_id = ?",
            (project_id, user.id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return _project_response(row)


@app.patch("/api/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: int,
    request: UpdateGameRequest,
    user: UserResponse = Depends(current_user),
) -> ProjectResponse:
    with connect() as connection:
        connection.execute(
            """
            UPDATE projects SET title = ?, updated_at = ?
            WHERE id = ? AND owner_id = ?
            """,
            (request.title, utc_now(), project_id, user.id),
        )
        row = connection.execute(
            "SELECT * FROM projects WHERE id = ? AND owner_id = ?",
            (project_id, user.id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return _project_response(row)


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(
    project_id: int,
    user: UserResponse = Depends(current_user),
) -> Response:
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM projects WHERE id = ? AND owner_id = ?",
            (project_id, user.id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Project not found.")
        connection.execute(
            "INSERT INTO activities(user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (user.id, "delete", f"Deleted project {project_id}.", utc_now()),
        )
    return Response(status_code=204)


@app.get("/api/activity", response_model=list[ActivityResponse])
def activity(user: UserResponse = Depends(current_user)) -> list[ActivityResponse]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT action, detail, created_at FROM activities WHERE user_id = ? ORDER BY created_at DESC LIMIT 100",
            (user.id,),
        ).fetchall()
    return [ActivityResponse(**dict(row)) for row in rows]


def _saved_game_response(row: sqlite3.Row) -> SavedGameResponse:
    return SavedGameResponse(
        id=row["id"],
        title=row["title"],
        prompt=row["prompt"],
        spec=GameSpec.model_validate_json(row["spec_json"]),
        runtime_config=json.loads(row["runtime_json"]),
        game_definition=(
            GameDefinitionV2.model_validate_json(row["game_definition_json"])
            if row["game_definition_json"] not in ("", "{}")
            else None
        ),
        qa_report=(
            json.loads(row["qa_report_json"])
            if row["qa_report_json"] not in ("", "{}")
            else {}
        ),
        qa_summary=row["qa_summary"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _project_status(qa_report: dict) -> str:
    if qa_report.get("status") == "fail":
        return "failed"
    if qa_report.get("status") == "warning":
        return "warning"
    if (
        qa_report.get("status") == "pass"
        and qa_report.get("runtime_tested") is True
        and qa_report.get("gameplay_status") == "pass"
    ):
        return "ready"
    return "unverified"


def _project_response(row: sqlite3.Row) -> ProjectResponse:
    spec_json = row["spec_json"]
    definition_json = row["game_definition_json"]
    return ProjectResponse(
        id=row["id"],
        title=row["title"],
        original_prompt=row["original_prompt"],
        prompt=row["original_prompt"],
        spec=GameSpec.model_validate_json(spec_json),
        runtime_config=json.loads(row["runtime_json"]),
        game_definition=(
            GameDefinitionV2.model_validate_json(definition_json)
            if definition_json not in ("", "{}")
            else None
        ),
        qa_report=json.loads(row["qa_report_json"]),
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
