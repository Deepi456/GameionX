# GameionX

GameionX is a spec-first prototype for generating playable browser games from
natural-language prompts. The backend supports an offline development mock,
Gemini, and Groq-backed `GameDesignerAgent`. The mock is deliberately **not
LLM-powered**; it is useful for local testing without an API key.

## Prerequisites

- Windows PowerShell
- Node.js and npm
- Python 3.11+
- Google Chrome (used by the headless Playwright browser playtest)

## Start the backend

From the repository root:

```powershell
Set-Location .\backend
& 'C:\Projects\intelligent-document-processing\.venv\Scripts\python.exe' -m pip install -r requirements.txt
Copy-Item .env.example .env
# Keep the default for offline development:
# GAMEIONX_PROVIDER=development-mock
& 'C:\Projects\intelligent-document-processing\.venv\Scripts\python.exe' -m uvicorn app.main:app --reload --port 8000
```

To use Gemini, edit `backend\.env` before starting the server:

```text
GAMEIONX_PROVIDER=gemini
GEMINI_API_KEY=your-backend-only-key
GEMINI_MODEL=gemini-3.8-flash
```

When Gemini is selected, a missing key or Gemini/model error is reported; the
server never silently falls back to the development mock.

To use Groq, edit `backend\.env` before starting the server:

```text
GAMEIONX_PROVIDER=groq
GROQ_API_KEY=your-backend-only-key
GROQ_MODEL=openai/gpt-oss-20b
```

Groq is called through its OpenAI-compatible chat completions endpoint with
strict JSON-schema output for `GameSpec`. If the selected model or account
does not support the requested structured output, the backend reports the
provider error and never falls back to fake or mock data.

The configured account was verified to list and generate structured JSON with
`gemini-3.8-flash`. `gemini-2.5-flash` was listed by the catalog but returned
HTTP 404 for generation, so it is not the active model.

Use the Python executable for your own Python 3.11 virtual environment if it
is in a different location. The API is available at
`http://localhost:8000`; interactive docs are at `/docs`.

## Start the frontend

In a second PowerShell window from the repository root:

```powershell
Set-Location .\frontend
npm install
$env:VITE_API_BASE_URL = 'http://localhost:8000'
npm run dev
```

Open the URL printed by Vite, normally `http://localhost:5173`.

## Validate locally

```powershell
Set-Location .\backend
& 'C:\Projects\intelligent-document-processing\.venv\Scripts\python.exe' -m pytest

Set-Location ..\frontend
npm run lint
npm run build
```

The frontend sends prompts to `POST /api/generate`. The response contains the
validated `GameSpec`, a data-only `runtime_config` produced by
`GameBuilderAgent`, and a deterministic structural `qa_report` produced by
`GameQAAgent`, plus a versioned `WorkflowState` recording the original prompt,
stable requirement IDs, validated agent outputs, mechanic coverage, scene
configuration, QA findings, and repair history. The ordered workflow uses a
deterministic planner and builders, with the configured model used for game
design. `GameSpec` v1 and the existing `GameDefinitionV2` adapter remain
backward-compatible. The Phaser preview consumes the controlled configuration.
Playwright tests the existing Phaser preview in Google Chrome for browser
loading, rendered entities, keyboard movement, and supported top-down
collectible objectives. Platformer tests use browser keyboard input to navigate
conservative routes across existing static platforms and verify accepted
jumps, observed landings, actual collectible progress, and goal completion.
Layouts without a safely planned route remain NOT TESTED; arbitrary platform
layouts are not assumed playable. Each test records PASS, FAIL, or NOT TESTED.
A game is not marked ready unless required browser checks and planned
requirement coverage pass.
Collectible coordinates are normalized points; the controlled preview renders
platform `x`/`y` as the normalized top-left corner and expects collectibles
horizontally within and just above a platform. Explicit collectible counts in a
prompt are preserved in the generated collectible arrays and matching targets.
Platformer browser tests record the platform actually landed on for each planned
jump. Small and conservative larger gaps plus varied platform heights have
representative Chrome scenarios; routes outside the conservative jump envelope
remain NOT TESTED rather than being treated as playable.
One browser-observed static-platform miss can trigger a single allowlisted
repair: move the planned destination toward its source by at most 0.08 normalized
world units, carrying collectibles and a goal anchored to that platform by the
same offset. The runner records the actual landing and terminal state; no repair
is attempted when the game ends before a landing can be observed. Every applied
patch is revalidated and receives one browser retest; an unsuccessful retest
remains failed.
It never evaluates generated source code. API keys remain backend-only and are
never included in frontend code or responses.

The React application uses a fixed navy/violet workspace shell across the
dashboard, generation, project library, playground, settings, and profile.
Generation remains inside that shell. `POST /api/generate` currently returns
after the full workflow rather than streaming progress; the UI labels the
request as running, shows actual elapsed time, and displays confirmed stage
history only after the response. A failed workflow response retains its
original `detail` and `workflow` fields and may include `partial_result` when
validated game and runtime data exist, so failed QA and repair history remain
visible alongside the playable preview without being reported as success.
Projects are saved only through the authenticated owner-scoped project API.

## Accounts and saved games

The backend uses its existing SQLite data layer and creates or upgrades the
database at startup. The default persistent database is
`backend\gameionx.sqlite3` (ignored by Git); set `GAMEIONX_DATABASE_PATH` to
choose another location. Existing username accounts and saved games are
preserved when the email/session/project schema is added.

Register or sign in with a normalized email address from the landing page.
Passwords are stored as salted scrypt hashes and are never returned by the API.
Sessions use random, opaque HttpOnly, SameSite=Lax cookies; only a keyed hash
of each session token is stored in SQLite, and sessions expire after seven
days. Local development uses a development-only session secret. Before
deployment, set `GAMEIONX_ENV=production` and configure
`GAMEIONX_SESSION_SECRET` to a random value of at least 32 bytes; production
startup fails if that secret is missing or too short. Secure cookies default
on in production; `GAMEIONX_COOKIE_SECURE=0|1` can explicitly override that
behavior. Set comma-separated `GAMEIONX_ALLOWED_ORIGINS` for a deployed
frontend.

Authentication endpoints are `POST /api/auth/register`,
`POST /api/auth/login`, `GET /api/auth/me`, and `POST /api/auth/logout`.
Email-based requests are the current interface. The old username-shaped
register/login requests remain accepted for API compatibility; those legacy
accounts receive an internal `@legacy.gameionx.invalid` address and can
continue signing in with their username.

New projects are stored through `POST/GET /api/projects` and
`GET/PATCH/DELETE /api/projects/{id}`. Project rows reference the authenticated
owner and preserve the original prompt, validated GameSpec, runtime
configuration, GameDefinition, QA report, status, and timestamps as JSON or
typed columns. Every read or mutation is scoped to the current session owner;
the owner is never accepted from request data. Existing `/api/games` routes
remain available for compatibility. Saved-game and activity endpoints also
require authentication.

Generation runs through `GameDesignerAgent`, `GameBuilderAgent`,
`GameQAAgent`, `BrowserPlaytestRunner`, and a bounded `GameRepairAgent`
coordinated by `GameOrchestrator`. The runner starts the local Vite frontend
when it is not already available. Set `GAMEIONX_FRONTEND_URL` to use an
existing frontend server at a different address. If Chrome or the frontend
cannot be started, required checks remain NOT TESTED and the game cannot be
marked ready. The preview supports keyboard movement, jumping, platform
collisions, collectibles, enemy damage, collect/reach/time goals, level
transitions, and restart with `R` after a win or loss.

GameSpec v1 now also includes backward-compatible genre fields (`game_type`,
`camera`, `theme`, player movement settings, and a validated nine-color palette).
The Phaser preview renders theme-specific vector backgrounds (forest, sky,
space, road, maze, or neon), scenery and entity silhouettes from normalized
GameSpec coordinates. Bird, forest exploration, space shooter, and racing
prompts have capability-specific scene requirements in the designer prompt;
the designer rejects and retries a mismatched genre or required mechanics
instead of relabeling a generic game. Supported runtime genres are
`platform_adventure`, `top_down_collection`, `maze`, `dodge_arena`,
`collection_challenge`, `target_arcade`, `flappy_dodge`, `space_shooter`, and
`racing`. Platform adventures use gravity and jumping; top-down and maze games
use four-direction movement with walls or scenery; flappy games use gravity,
flap input, scrolling obstacles, and scoring; space shooters use movement,
firing, projectiles, enemy hazards, and health; racing uses road movement,
obstacles, collisions, and a survival timer. The designer must select a type
matching the prompt, and the controlled preview interprets the validated data
without executing generated code. Structural QA remains separate from browser
runtime verification.
