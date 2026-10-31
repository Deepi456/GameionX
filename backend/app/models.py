from datetime import datetime
import re
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Point(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class PlatformSpec(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    movement: Literal["static", "horizontal", "vertical"] = "static"


class CollectibleSpec(BaseModel):
    position: Point
    value: int = Field(default=10, ge=1, le=1000)
    kind: Literal["coin", "gem", "star"] = "coin"


class EnemySpec(BaseModel):
    position: Point
    damage: int = Field(default=1, ge=1, le=100)
    movement: Literal["static", "horizontal", "vertical"] = "horizontal"


class WallSpec(BaseModel):
    position: Point
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class TargetSpec(BaseModel):
    position: Point
    points: int = Field(default=10, ge=1, le=1000)


class GoalSpec(BaseModel):
    position: Point
    kind: Literal["exit", "chest", "beacon"] = "exit"


class ScenerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: Point
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    kind: Literal[
        "tree", "bush", "cloud", "roadside", "decor", "path", "grass", "mountain",
        "water", "flower", "building",
    ] = "decor"
    solid: bool = False


class ObstacleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: Point
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    kind: Literal["pipe", "rock", "barrier", "guard", "cone"] = "barrier"
    damage: int = Field(default=1, ge=1, le=100)
    movement: Literal["static", "horizontal", "vertical"] = "static"
    speed: float = Field(default=0, ge=0, le=1000)


class MovingHazardSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: Point
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    kind: Literal["enemy", "zombie", "spike", "car", "pipe"] = "enemy"
    damage: int = Field(default=1, ge=1, le=100)
    health: int = Field(default=1, ge=1, le=100)
    movement: Literal["horizontal", "vertical", "scrolling", "chasing"] = "horizontal"
    speed: float = Field(default=60, gt=0, le=1000)


class PlayerSpec(BaseModel):
    start: Point = Point(x=0.1, y=0.8)
    speed: float = Field(default=240, gt=0, le=1000)
    jump_velocity: float = Field(default=450, gt=0, le=1500)
    max_health: int = Field(default=3, ge=1, le=100)
    appearance: Literal["player", "bird", "ship", "car", "explorer"] = "player"
    movement_mode: Literal["platformer", "four_direction", "flap", "racing"] = "platformer"
    gravity: float = Field(default=900, ge=0, le=3000)
    flap_strength: float = Field(default=360, gt=0, le=1500)
    fire_cooldown: int = Field(default=250, ge=0, le=5000)


class PaletteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    background: str = Field(default="#172033", pattern=r"^#[0-9a-fA-F]{6}$")
    primary: str = Field(default="#61d7b0", pattern=r"^#[0-9a-fA-F]{6}$")
    secondary: str = Field(default="#8b7cff", pattern=r"^#[0-9a-fA-F]{6}$")
    accent: str = Field(default="#f3c969", pattern=r"^#[0-9a-fA-F]{6}$")
    player: str = Field(default="#ffd166", pattern=r"^#[0-9a-fA-F]{6}$")
    enemy: str = Field(default="#ef476f", pattern=r"^#[0-9a-fA-F]{6}$")
    obstacle: str = Field(default="#8d99ae", pattern=r"^#[0-9a-fA-F]{6}$")
    collectible: str = Field(default="#a78bfa", pattern=r"^#[0-9a-fA-F]{6}$")
    wall: str = Field(default="#64748b", pattern=r"^#[0-9a-fA-F]{6}$")


class LevelSpec(BaseModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    name: str = Field(min_length=1, max_length=80)
    background: str = Field(default="#172033", pattern=r"^#[0-9a-fA-F]{6}$")
    platforms: list[PlatformSpec] = Field(min_length=1, max_length=50)
    collectibles: list[CollectibleSpec] = Field(default_factory=list, max_length=100)
    enemies: list[EnemySpec] = Field(default_factory=list, max_length=50)
    walls: list[WallSpec] = Field(default_factory=list, max_length=100)
    targets: list[TargetSpec] = Field(default_factory=list, max_length=100)
    scenery: list[ScenerySpec] = Field(default_factory=list, max_length=200)
    obstacles: list[ObstacleSpec] = Field(default_factory=list, max_length=100)
    hazards: list[MovingHazardSpec] = Field(default_factory=list, max_length=100)
    goal: GoalSpec | None = None
    time_limit: int | None = Field(default=None, ge=1, le=3600)
    collectible_target: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def target_cannot_exceed_collectibles(self) -> "LevelSpec":
        if self.collectible_target > len(self.collectibles):
            raise ValueError("collectible_target cannot exceed the number of collectibles")
        return self


class WinCondition(BaseModel):
    type: Literal["collect_target", "defeat_target", "reach_goal", "survive"]
    target: int | None = Field(default=None, ge=1, le=1000)


class LoseCondition(BaseModel):
    type: Literal["health_depleted", "time_expired"]
    target: int | None = Field(default=None, ge=1, le=3600)


class DefinitionPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class DefinitionSize(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class DefinitionVisual(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shape: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    accent_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    asset_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")


class DefinitionObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$")
    kind: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    position: DefinitionPoint
    size: DefinitionSize | None = None
    anchor: Literal["center", "top_left"] = "center"
    visual: DefinitionVisual
    solid: bool = False
    properties: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict, max_length=32
    )


class DefinitionLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    name: str = Field(min_length=1, max_length=80)
    background: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    objects: list[DefinitionObject] = Field(default_factory=list, max_length=1000)
    time_limit: int | None = Field(default=None, ge=1, le=3600)


class DefinitionControl(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    keys: list[str] = Field(min_length=1, max_length=8)


class DefinitionMechanic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    version: str = Field(default="1", pattern=r"^[0-9]+(?:\.[0-9]+){0,2}$")
    support: Literal["supported", "partial", "unsupported"] = "unsupported"
    parameters: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict, max_length=32
    )


class DefinitionObjective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    target: int | float | None = Field(default=None, gt=0)
    requires: list[str] = Field(default_factory=list, max_length=32)


class DefinitionUI(BaseModel):
    model_config = ConfigDict(extra="forbid")

    show_score: bool = True
    show_health: bool = True
    show_timer: bool = True
    objective_label: str = Field(default="Objective", min_length=1, max_length=40)
    instructions: str = Field(default="Use the arrow keys or WASD to move.", max_length=160)
    start_prompt: str = Field(default="Press a movement key to start", max_length=100)
    victory_message: str = Field(default="You win!", max_length=80)
    game_over_message: str = Field(default="Game over", max_length=80)


class GameDefinitionV2(BaseModel):
    """Versioned declarative game design; it contains no executable code."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = "2.0"
    source_schema_version: Literal["1.0"] | None = None
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    genre: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    theme: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    camera: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    palette: PaletteSpec
    world_width: int = Field(ge=640, le=4096)
    world_height: int = Field(ge=360, le=3072)
    player: DefinitionObject
    controls: list[DefinitionControl] = Field(default_factory=list, max_length=32)
    physics: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict, max_length=32
    )
    mechanics: list[DefinitionMechanic] = Field(default_factory=list, max_length=64)
    levels: list[DefinitionLevel] = Field(min_length=1, max_length=10)
    ui: DefinitionUI = Field(default_factory=DefinitionUI)
    win: DefinitionObjective
    lose: DefinitionObjective


GameDefinition = GameDefinitionV2


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^req-[a-f0-9]{12}$")
    category: Literal["genre", "entity", "mechanic", "ui"]
    description: str = Field(min_length=1, max_length=160)
    source: Literal["prompt"] = "prompt"
    status: Literal["required", "implemented", "partial", "unsupported"] = "required"


class GamePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    original_prompt: str = Field(min_length=3, max_length=500)
    genre_intent: str | None = Field(default=None, max_length=40)
    entities: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    mechanics: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    requested_ui: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    requirements: list[Requirement] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def requirement_ids_are_unique(self) -> "GamePlan":
        requirement_ids = [requirement.id for requirement in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("GamePlan requirement IDs must be unique.")
        return self

    def as_prompt(self) -> str:
        return (
            f"Intent: {self.genre_intent or 'unspecified'}; "
            f"required entities: {', '.join(self.entities) or 'none explicitly named'}; "
            f"required mechanics: {', '.join(self.mechanics) or 'infer from prompt'}; "
            f"requested UI: {', '.join(self.requested_ui) or 'use objective-appropriate HUD'}."
        )


class AgentTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    task_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,79}$")
    stage: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    agent: str = Field(min_length=1, max_length=80)
    required_inputs: list[str] = Field(default_factory=list, max_length=32)
    inputs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def required_inputs_are_present(self) -> "AgentTask":
        missing = [name for name in self.required_inputs if name not in self.inputs]
        if missing:
            raise ValueError(f"Missing required task inputs: {', '.join(missing)}")
        return self


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    task_id: str
    agent: str
    status: Literal["completed", "failed", "invalid"]
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def failure_includes_error(self) -> "AgentResult":
        if self.status != "completed" and not self.error:
            raise ValueError("Failed or invalid agent results must include an error.")
        return self


class RepairChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=160)
    previous_value: int | float | None
    new_value: float

    @model_validator(mode="after")
    def value_must_change(self) -> "RepairChange":
        if self.previous_value == self.new_value:
            raise ValueError("Repair changes must change the target value.")
        return self


class RepairPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    patch_id: str = Field(pattern=r"^repair-[a-f0-9]{12}$")
    attempt: int = Field(ge=1, le=3)
    status: Literal["applied", "no_change", "rejected"]
    before_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    after_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    changes: list[RepairChange] = Field(default_factory=list, max_length=64)
    affected_requirement_ids: list[str] = Field(default_factory=list, max_length=128)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def patch_content_matches_status(self) -> "RepairPatch":
        if (self.status == "applied") != bool(self.changes):
            raise ValueError("Only applied repair patches may contain changes.")
        if self.status != "applied" and self.before_digest != self.after_digest:
            raise ValueError("Rejected or empty repair patches cannot change the spec.")
        return self


class PlaytestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    expected_behavior: str = Field(min_length=1, max_length=300)
    actual_behavior: str = Field(min_length=1, max_length=500)
    status: Literal["PASS", "FAIL", "NOT TESTED"]
    required: bool = True
    failure_details: str | None = Field(default=None, max_length=1000)


class PlaytestReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    browser: str = Field(min_length=1, max_length=80)
    status: Literal["pass", "fail", "unverified"]
    runtime_tested: bool
    tests: list[PlaytestCase] = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def status_matches_required_checks(self) -> "PlaytestReport":
        required = [test for test in self.tests if test.required]
        if not self.runtime_tested and any(test.status != "NOT TESTED" for test in required):
            raise ValueError("A non-runtime playtest may only contain untested checks.")
        if any(test.status == "FAIL" for test in required):
            if self.status != "fail":
                raise ValueError("Failed required checks must fail the playtest report.")
        elif any(test.status != "PASS" for test in required):
            if self.status != "unverified":
                raise ValueError("Untested required checks must leave playtesting unverified.")
        elif self.status != "pass" or not self.runtime_tested:
            raise ValueError("Playtesting passes only after all required browser checks pass.")
        return self


class GameSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    game_type: Literal[
        "platform_adventure",
        "top_down_collection",
        "maze",
        "dodge_arena",
        "collection_challenge",
        "target_arcade",
        "flappy_dodge",
        "space_shooter",
        "racing",
    ] = "platform_adventure"
    camera: Literal["side_scroller", "top_down", "grid", "racing"] = "side_scroller"
    theme: Literal["forest", "sky", "space", "road", "maze", "neon"] = "neon"
    palette: PaletteSpec = Field(default_factory=PaletteSpec)
    world_width: int = Field(default=800, ge=640, le=4096)
    world_height: int = Field(default=480, ge=360, le=3072)
    player: PlayerSpec
    levels: list[LevelSpec] = Field(min_length=1, max_length=10)
    win_condition: WinCondition
    lose_condition: LoseCondition
    controls: Literal["keyboard"] = "keyboard"
    fire_control: Literal["space", "enter", "x", "none"] = "none"


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=500)
    mode: Literal["beginner", "developer"] = "beginner"
    regenerate: bool = False
    bypass_cache: bool = False


class GenerateResponse(BaseModel):
    provider: Literal["development-mock", "gemini", "groq"]
    prompt: str | None = None
    spec: GameSpec
    runtime_config: dict
    game_definition: GameDefinitionV2 | None = None
    qa_report: dict
    stages: list["GenerationStage"] = Field(default_factory=list)
    workflow: "WorkflowState | None" = None
    generation_id: str | None = None


class GenerationStage(BaseModel):
    name: Literal[
        "planning", "designing", "gameplay_logic", "world_ui_building",
        "runtime_integration", "qa_playtest", "repairing", "qa_retest",
        "finalizing", "unverified", "building", "validating",
        "scene_building", "ready", "failed",
    ]
    status: Literal["started", "completed", "failed", "skipped"]
    message: str


class WorkflowState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    generation_id: str = Field(
        default_factory=lambda: str(uuid4()),
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )
    original_prompt: str = Field(min_length=3, max_length=500)
    plan: GamePlan | None = None
    agent_tasks: list[AgentTask] = Field(default_factory=list, max_length=64)
    agent_outputs: list[AgentResult] = Field(default_factory=list, max_length=64)
    implemented_mechanics: list[str] = Field(default_factory=list, max_length=64)
    unsupported_mechanics: list[str] = Field(default_factory=list, max_length=64)
    scene_configuration: GameDefinitionV2 | None = None
    qa_findings: list[dict[str, Any]] = Field(default_factory=list, max_length=512)
    repair_history: list[RepairPatch] = Field(default_factory=list, max_length=3)
    stage_history: list[GenerationStage] = Field(default_factory=list, max_length=128)
    validation_status: Literal["unverified", "passed", "failed"] = "unverified"
    final_status: Literal["unverified", "ready", "failed"] = "unverified"
    runtime_verified: bool = False

    @model_validator(mode="after")
    def ready_requires_runtime_verification(self) -> "WorkflowState":
        if self.final_status == "ready" and (
            not self.runtime_verified or self.validation_status != "passed"
        ):
            raise ValueError("A workflow cannot be ready without verified runtime QA.")
        if self.final_status == "unverified" and self.validation_status != "unverified":
            raise ValueError("Unverified workflows must retain unverified validation status.")
        if self.final_status == "failed" and self.validation_status != "failed":
            raise ValueError("Failed workflows must retain failed validation status.")
        if self.plan is not None and self.plan.original_prompt != self.original_prompt:
            raise ValueError("Workflow plan must preserve the original user prompt.")
        return self


class GenerationReport(BaseModel):
    provider: Literal["development-mock", "gemini", "groq"]
    generation_id: str
    spec: GameSpec
    runtime_config: dict
    game_definition: GameDefinitionV2 | None = None
    qa_report: dict
    stages: list[GenerationStage]
    workflow: WorkflowState | None = None


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, max_length=254)
    username: str | None = Field(
        default=None, min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_-]+$"
    )
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold()
        if not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            r"(?:\.[A-Za-z]{2,63})+",
            normalized,
        ):
            raise ValueError("Enter a valid email address.")
        return normalized

    @model_validator(mode="after")
    def require_email_or_legacy_username(self) -> "RegisterRequest":
        if self.email is None and self.username is None:
            raise ValueError("Email is required.")
        return self


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, max_length=254)
    username: str | None = Field(default=None, min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold()
        if not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            r"(?:\.[A-Za-z]{2,63})+",
            normalized,
        ):
            raise ValueError("Enter a valid email address.")
        return normalized

    @model_validator(mode="after")
    def require_one_login_identifier(self) -> "LoginRequest":
        if (self.email is None) == (self.username is None):
            raise ValueError("Provide either an email address or username.")
        return self


class UserResponse(BaseModel):
    id: int
    username: str
    email: str | None = None


class AuthResponse(BaseModel):
    user: UserResponse


class SavedGameResponse(BaseModel):
    id: int
    title: str
    prompt: str
    spec: GameSpec
    runtime_config: dict
    game_definition: GameDefinitionV2 | None = None
    qa_report: dict = Field(default_factory=dict)
    qa_summary: str
    status: Literal["ready", "unverified", "warning", "failed"]
    created_at: datetime
    updated_at: datetime


class ProjectResponse(BaseModel):
    id: int
    title: str
    original_prompt: str
    prompt: str
    spec: GameSpec
    runtime_config: dict
    game_definition: GameDefinitionV2 | None = None
    qa_report: dict
    status: Literal["ready", "unverified", "warning", "failed"]
    created_at: datetime
    updated_at: datetime


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["development-mock", "gemini", "groq"] | None = None
    prompt: str = Field(min_length=3, max_length=500)
    spec: GameSpec
    runtime_config: dict
    game_definition: GameDefinitionV2 | None = None
    qa_report: dict


class UpdateGameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class ActivityResponse(BaseModel):
    action: Literal["login", "logout", "generate", "save", "delete"]
    detail: str
    created_at: datetime


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    provider: Literal["development-mock", "gemini", "groq"]
    message: str


AnyGameSpec = Annotated[GameSpec, Field(discriminator=None)]
