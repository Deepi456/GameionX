import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from app import database
from app.agents.game_builder import GameBuilderAgent
from app.auth import validate_session_configuration
from app.main import app
from app.models import GameSpec


@pytest.fixture(autouse=True)
def isolated_auth_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "auth-test.sqlite3")
    database.initialize_database()


def credentials() -> tuple[str, str]:
    return f"user-{uuid.uuid4().hex[:10]}@example.com", "correct-horse-battery"


def register(client: TestClient, email: str | None = None) -> str:
    email = email or credentials()[0]
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 201
    return email


def generated_project_payload() -> dict:
    spec = GameSpec.model_validate(
        {
            "title": "Saved Project",
            "description": "A collectible platform adventure.",
            "game_type": "platform_adventure",
            "player": {"start": {"x": 0.1, "y": 0.8}},
            "levels": [
                {
                    "id": "level-1",
                    "name": "First Level",
                    "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
                    "collectibles": [
                        {"position": {"x": 0.4, "y": 0.82}, "kind": "gem"}
                    ],
                    "collectible_target": 1,
                }
            ],
            "win_condition": {"type": "collect_target", "target": 1},
            "lose_condition": {"type": "health_depleted"},
        }
    )
    runtime = GameBuilderAgent().build(spec)
    return {
        "provider": "development-mock",
        "prompt": "Make a one-gem platform game.",
        "spec": spec.model_dump(mode="json"),
        "runtime_config": runtime.model_dump(mode="json"),
        "game_definition": GameBuilderAgent()
        .to_game_definition(runtime)
        .model_dump(mode="json"),
        "qa_report": {
            "status": "pass",
            "runtime_tested": True,
            "gameplay_status": "pass",
            "summary": "All supported browser checks passed.",
        },
    }


def test_games_require_authentication() -> None:
    client = TestClient(app)
    assert client.get("/api/games").status_code == 401
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/projects", json=generated_project_payload()).status_code == 401


def test_registration_login_duplicate_and_invalid_password() -> None:
    client = TestClient(app)
    email, password = credentials()
    response = client.post(
        "/api/auth/register", json={"email": email.upper(), "password": password}
    )
    assert response.status_code == 201
    assert response.json()["user"]["email"] == email
    assert "password" not in response.json()
    assert "password_hash" not in response.json()
    assert client.post(
        "/api/auth/register", json={"email": email, "password": password}
    ).status_code == 409
    cookie = response.cookies.get("gameionx_session")
    assert cookie
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    current = client.get("/api/auth/me")
    assert current.status_code == 200
    assert current.json()["email"] == email
    with database.connect() as connection:
        connection.execute(
            "UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'"
        )
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert client.post(
        "/api/auth/login", json={"email": email, "password": "wrong-pass"}
    ).status_code == 401
    assert client.post(
        "/api/auth/login", json={"email": email.upper(), "password": password}
    ).status_code == 200
    with database.connect() as connection:
        row = connection.execute(
            "SELECT email, username, password_hash FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    assert row["email"] == email
    assert row["password_hash"].startswith("scrypt$")
    assert row["password_hash"] != password
    assert password not in row["password_hash"]


def test_production_session_secret_must_be_configured_and_strong(monkeypatch) -> None:
    monkeypatch.setenv("GAMEIONX_ENV", "production")
    monkeypatch.delenv("GAMEIONX_SESSION_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="must be configured"):
        validate_session_configuration()

    monkeypatch.setenv("GAMEIONX_SESSION_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        validate_session_configuration()

    monkeypatch.setenv("GAMEIONX_SESSION_SECRET", "x" * 48)
    validate_session_configuration()


def test_email_and_password_validation() -> None:
    client = TestClient(app)
    assert client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "correct-horse"},
    ).status_code == 422
    assert client.post(
        "/api/auth/register",
        json={"email": "valid@example.com", "password": "short"},
    ).status_code == 422
    assert client.post(
        "/api/auth/login",
        json={"email": "valid@example.com", "username": "other", "password": "x"},
    ).status_code == 422


def test_legacy_username_registration_and_login_remain_compatible() -> None:
    client = TestClient(app)
    username, password = "legacy_user", "correct-horse-battery"
    response = client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert response.status_code == 201
    assert response.json()["user"]["email"] == (
        f"{username}@legacy.gameionx.invalid"
    )
    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).status_code == 200


def test_saved_games_are_isolated_between_users() -> None:
    owner = TestClient(app)
    other = TestClient(app)
    owner_email = register(owner)
    register(other)
    generated = owner.post(
        "/api/generate",
        json={"prompt": "A platform game with three gems", "mode": "beginner"},
    )
    assert generated.status_code == 200
    saved = owner.post("/api/games", json=generated.json())
    assert saved.status_code == 201
    game_id = saved.json()["id"]
    assert other.get(f"/api/games/{game_id}").status_code == 404
    assert other.delete(f"/api/games/{game_id}").status_code == 404


def test_saved_game_can_be_renamed_and_deleted_by_owner() -> None:
    client = TestClient(app)
    register(client)
    generated = client.post(
        "/api/generate", json={"prompt": "A small platform game", "mode": "beginner"}
    )
    saved = client.post("/api/games", json=generated.json())
    game_id = saved.json()["id"]
    renamed = client.patch(
        f"/api/games/{game_id}", json={"title": "My renamed game"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "My renamed game"
    assert client.delete(f"/api/games/{game_id}").status_code == 204
    assert client.get(f"/api/games/{game_id}").status_code == 404


def test_projects_persist_full_generation_data_and_enforce_owner_isolation() -> None:
    owner = TestClient(app)
    other = TestClient(app)
    register(owner)
    register(other)

    payload = generated_project_payload()
    created = owner.post("/api/projects", json=payload)
    assert created.status_code == 201, created.text
    project = created.json()
    project_id = project["id"]
    assert project["original_prompt"] == payload["prompt"]
    assert project["spec"]["title"] == payload["spec"]["title"]
    assert project["runtime_config"]["game_type"] == "platform_adventure"
    assert project["game_definition"]["schema_version"] == "2.0"
    assert project["qa_report"] == payload["qa_report"]
    assert project["status"] == "ready"

    with database.connect() as connection:
        stored = connection.execute(
            "SELECT owner_id, original_prompt, spec_json, runtime_json, game_definition_json, qa_report_json "
            "FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    owner_id = owner.get("/api/auth/me").json()["id"]
    assert stored["owner_id"] == owner_id
    assert stored["original_prompt"] == payload["prompt"]
    assert '"title":"Saved Project"' in stored["spec_json"]
    assert '"game_type":"platform_adventure"' in stored["runtime_json"]
    assert '"schema_version":"2.0"' in stored["game_definition_json"]
    assert '"gameplay_status":"pass"' in stored["qa_report_json"]

    assert [item["id"] for item in owner.get("/api/projects").json()] == [project_id]
    assert owner.get(f"/api/projects/{project_id}").status_code == 200
    assert other.get("/api/projects").json() == []
    assert other.get(f"/api/projects/{project_id}").status_code == 404
    assert other.patch(
        f"/api/projects/{project_id}", json={"title": "Stolen"}
    ).status_code == 404
    assert other.delete(f"/api/projects/{project_id}").status_code == 404

    renamed = owner.patch(
        f"/api/projects/{project_id}", json={"title": "Renamed Project"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed Project"
    assert renamed.json()["updated_at"] >= project["updated_at"]
    assert owner.delete(f"/api/projects/{project_id}").status_code == 204
    assert owner.get(f"/api/projects/{project_id}").status_code == 404


def test_project_owner_is_never_taken_from_request_body() -> None:
    owner = TestClient(app)
    register(owner)
    payload = generated_project_payload()
    payload["owner_id"] = 9999
    assert owner.post("/api/projects", json=payload).status_code == 422


def test_existing_database_is_migrated_without_losing_users_or_games(
    tmp_path, monkeypatch
) -> None:
    legacy_path = tmp_path / "legacy.sqlite3"
    monkeypatch.setattr(database, "DATABASE_PATH", legacy_path)
    connection = sqlite3.connect(legacy_path)
    connection.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL
        );
        CREATE TABLE saved_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            title TEXT NOT NULL,
            prompt TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            runtime_json TEXT NOT NULL DEFAULT '{}',
            qa_summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            action TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        INSERT INTO users(username, password_hash, created_at)
        VALUES ('old_user', 'scrypt$existing-hash', '2026-01-01T00:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()

    database.initialize_database()
    with database.connect() as migrated:
        user = migrated.execute(
            "SELECT id, username, email FROM users WHERE username = 'old_user'"
        ).fetchone()
        columns = {
            row["name"] for row in migrated.execute("PRAGMA table_info(sessions)")
        }
        project_table = migrated.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
        ).fetchone()
    assert dict(user) == {
        "id": 1,
        "username": "old_user",
        "email": "old_user@legacy.gameionx.invalid",
    }
    assert "expires_at" in columns
    assert project_table is not None
