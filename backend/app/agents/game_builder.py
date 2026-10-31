from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.game_scene_builder import GameSceneBuilderAgent
from app.agents.top_down_placement import normalize_top_down_placements
from app.models import (
    DefinitionControl,
    DefinitionLevel,
    DefinitionMechanic,
    DefinitionObject,
    DefinitionObjective,
    DefinitionPoint,
    DefinitionSize,
    DefinitionUI,
    DefinitionVisual,
    GameDefinitionV2,
    GameSpec,
    GoalSpec,
    Point,
)


class RuntimePoint(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class RuntimePlatform(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    movement: Literal["static", "horizontal", "vertical"]


class RuntimeCollectible(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: RuntimePoint
    value: int = Field(ge=1, le=1000)
    kind: Literal["coin", "gem", "star"]


class RuntimeEnemy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: RuntimePoint
    damage: int = Field(ge=1, le=100)
    movement: Literal["static", "horizontal", "vertical"]


class RuntimeScenery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: RuntimePoint
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    kind: Literal[
        "tree", "bush", "cloud", "roadside", "decor", "path", "grass", "mountain",
        "water", "flower",
        "building",
    ]
    solid: bool


class RuntimeObstacle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: RuntimePoint
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    kind: Literal["pipe", "rock", "barrier", "guard", "cone"]
    damage: int = Field(ge=1, le=100)
    movement: Literal["static", "horizontal", "vertical"]
    speed: float = Field(ge=0, le=1000)


class RuntimeHazard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: RuntimePoint
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    kind: Literal["enemy", "zombie", "spike", "car", "pipe"]
    damage: int = Field(ge=1, le=100)
    health: int = Field(ge=1, le=100)
    movement: Literal["horizontal", "vertical", "scrolling", "chasing"]
    speed: float = Field(gt=0, le=1000)


class RuntimeWall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: RuntimePoint
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class RuntimeTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: RuntimePoint
    points: int = Field(ge=1, le=1000)


class RuntimeGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: RuntimePoint
    kind: Literal["exit", "chest", "beacon"]


class RuntimeLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    background: str
    platforms: list[RuntimePlatform]
    collectibles: list[RuntimeCollectible]
    enemies: list[RuntimeEnemy]
    walls: list[RuntimeWall] = Field(default_factory=list)
    targets: list[RuntimeTarget] = Field(default_factory=list)
    scenery: list[RuntimeScenery] = Field(default_factory=list)
    obstacles: list[RuntimeObstacle] = Field(default_factory=list)
    hazards: list[RuntimeHazard] = Field(default_factory=list)
    goal: RuntimeGoal | None = None
    time_limit: int | None = Field(default=None, ge=1, le=3600)
    collectible_target: int = Field(ge=0, le=100)


class RuntimePlayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: RuntimePoint
    speed: float = Field(gt=0, le=1000)
    jump_velocity: float = Field(gt=0, le=1500)
    max_health: int = Field(ge=1, le=100)
    appearance: Literal["player", "bird", "ship", "car", "explorer"]
    movement_mode: Literal["platformer", "four_direction", "flap", "racing"]
    gravity: float = Field(ge=0, le=3000)
    flap_strength: float = Field(gt=0, le=1500)
    fire_cooldown: int = Field(ge=0, le=5000)


class RuntimePalette(BaseModel):
    model_config = ConfigDict(extra="forbid")

    background: str
    primary: str
    secondary: str
    accent: str
    player: str
    enemy: str
    obstacle: str
    collectible: str
    wall: str


class GameRuntimeConfig(BaseModel):
    """Safe data-only configuration consumed by the Phaser preview."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    title: str
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
    ]
    camera: Literal["side_scroller", "top_down", "grid", "racing"]
    theme: Literal["forest", "sky", "space", "road", "maze", "neon"]
    palette: RuntimePalette
    world_width: int = Field(default=800, ge=640, le=4096)
    world_height: int = Field(default=480, ge=360, le=3072)
    controls: Literal["keyboard"]
    fire_control: Literal["space", "enter", "x", "none"]
    player: RuntimePlayer
    levels: list[RuntimeLevel]
    win_condition: Literal["collect_target", "defeat_target", "reach_goal", "survive"]
    win_target: int | None = Field(default=None, ge=1, le=1000)
    lose_condition: Literal["health_depleted", "time_expired"]
    lose_target: int | None = Field(default=None, ge=1, le=3600)


class GameBuilderAgent:
    """Build a safe Phaser runtime configuration from a validated GameSpec."""

    supported_platform_movement = frozenset({"static", "horizontal", "vertical"})
    supported_collectible_kinds = frozenset({"coin", "gem", "star"})
    supported_enemy_movement = frozenset({"static", "horizontal", "vertical"})
    supported_hazard_kinds = frozenset({"enemy", "zombie", "spike", "car", "pipe"})
    supported_hazard_movement = frozenset(
        {"horizontal", "vertical", "scrolling", "chasing"}
    )
    placement_tolerance = 1e-9
    player_half_width = 12 / 800
    collectible_radius = 10 / 800
    mechanic_support = {
        "platform_movement": "supported",
        "jumping": "supported",
        "gravity": "supported",
        "collectibles": "supported",
        "enemy_contact": "supported",
        "entity_creation": "supported",
        "four_direction_movement": "supported",
        "goal_interaction": "supported",
        "flapping": "supported",
        "scrolling_obstacles": "supported",
        "projectiles": "supported",
        "enemy_waves": "supported",
        "chasing_enemies": "supported",
        "shooter_movement": "supported",
        "shooter_scoring": "supported",
        "racing_movement": "partial",
        "racing_obstacles": "supported",
        "timer": "partial",
        "scoring": "partial",
        "target_scoring": "supported",
    }

    def build(self, spec: GameSpec | dict) -> GameRuntimeConfig:
        try:
            validated_spec = (
                spec
                if isinstance(spec, GameSpec)
                else GameSpec.model_validate(spec)
            )
        except ValidationError as error:
            raise ValueError("Cannot build a runtime config from an invalid GameSpec.") from error

        validated_spec = normalize_top_down_placements(validated_spec)
        self._ensure_supported_mechanics(validated_spec)
        return GameRuntimeConfig(
            title=validated_spec.title,
            game_type=validated_spec.game_type,
            camera=validated_spec.camera,
            theme=validated_spec.theme,
            palette=RuntimePalette.model_validate(validated_spec.palette.model_dump()),
            world_width=validated_spec.world_width,
            world_height=validated_spec.world_height,
            controls=validated_spec.controls,
            fire_control=validated_spec.fire_control,
            player=RuntimePlayer.model_validate(validated_spec.player.model_dump()),
            levels=[
                RuntimeLevel.model_validate(
                    level.model_copy(
                        update={
                            "collectibles": [
                                collectible.model_copy(
                                    update={
                                        "position": (
                                            self._normalize_collectible_position(
                                                collectible, level.platforms
                                            )
                                            if validated_spec.game_type
                                            == "platform_adventure"
                                            else collectible.position
                                        )
                                    }
                                )
                                for collectible in level.collectibles
                            ],
                            "goal": level.goal
                            or (
                                GoalSpec(position=Point(x=0.92, y=0.15), kind="exit")
                                if validated_spec.win_condition.type == "reach_goal"
                                else None
                            ),
                        }
                    ).model_dump()
                )
                for level in validated_spec.levels
            ],
            win_condition=validated_spec.win_condition.type,
            win_target=validated_spec.win_condition.target,
            lose_condition=validated_spec.lose_condition.type,
            lose_target=validated_spec.lose_condition.target,
        )

    def to_game_definition(
        self,
        runtime: GameRuntimeConfig | dict,
        *,
        genre: str | None = None,
        requested_ui: tuple[str, ...] = (),
        ui: DefinitionUI | None = None,
    ) -> GameDefinitionV2:
        """Adapt the existing Phaser config to the new declarative contract."""
        config = (
            runtime
            if isinstance(runtime, GameRuntimeConfig)
            else GameRuntimeConfig.model_validate(runtime)
        )
        levels = []
        for level in config.levels:
            objects: list[DefinitionObject] = []

            def add_object(
                kind: str,
                position: RuntimePoint,
                color: str,
                shape: str,
                *,
                size: DefinitionSize | None = None,
                anchor: str = "center",
                solid: bool = False,
                properties: dict | None = None,
            ) -> None:
                objects.append(
                    DefinitionObject(
                        id=f"{level.id}-{kind}-{len(objects)}",
                        kind=kind,
                        position=DefinitionPoint(x=position.x, y=position.y),
                        size=size,
                        anchor=anchor,
                        visual=DefinitionVisual(shape=shape, color=color),
                        solid=solid,
                        properties=properties or {},
                    )
                )

            for platform in level.platforms:
                add_object(
                    "platform",
                    RuntimePoint(x=platform.x, y=platform.y),
                    config.palette.primary,
                    "rectangle",
                    size=DefinitionSize(width=platform.width, height=platform.height),
                    anchor="top_left",
                    solid=True,
                    properties={"movement": platform.movement},
                )
            for item in level.collectibles:
                add_object(
                    item.kind,
                    item.position,
                    config.palette.collectible,
                    "circle",
                    properties={"value": item.value},
                )
            for enemy in level.enemies:
                add_object(
                    "enemy",
                    enemy.position,
                    config.palette.enemy,
                    "enemy",
                    properties={"damage": enemy.damage, "movement": enemy.movement},
                )
            for wall in level.walls:
                add_object(
                    "wall",
                    wall.position,
                    config.palette.wall,
                    "rectangle",
                    size=DefinitionSize(width=wall.width, height=wall.height),
                    solid=True,
                )
            for target in level.targets:
                add_object(
                    "target",
                    target.position,
                    config.palette.accent,
                    "circle",
                    properties={"points": target.points},
                )
            for scenery in level.scenery:
                add_object(
                    scenery.kind,
                    scenery.position,
                    config.palette.secondary,
                    "scenery",
                    size=DefinitionSize(width=scenery.width, height=scenery.height),
                    solid=scenery.solid,
                )
            for obstacle in level.obstacles:
                add_object(
                    obstacle.kind,
                    obstacle.position,
                    config.palette.obstacle,
                    "obstacle",
                    size=DefinitionSize(width=obstacle.width, height=obstacle.height),
                    properties={
                        "damage": obstacle.damage,
                        "movement": obstacle.movement,
                        "speed": obstacle.speed,
                    },
                )
            for hazard in level.hazards:
                add_object(
                    hazard.kind,
                    hazard.position,
                    config.palette.enemy,
                    "hazard",
                    size=DefinitionSize(width=hazard.width, height=hazard.height),
                    properties={
                        "damage": hazard.damage,
                        "health": hazard.health,
                        "movement": hazard.movement,
                        "speed": hazard.speed,
                    },
                )
            if level.goal is not None:
                add_object(
                    level.goal.kind,
                    level.goal.position,
                    config.palette.accent,
                    "goal",
                )
            levels.append(
                DefinitionLevel(
                    id=level.id,
                    name=level.name,
                    background=level.background,
                    objects=objects,
                    time_limit=level.time_limit,
                )
            )

        movement = config.player.movement_mode
        mechanics_by_movement = {
            "platformer": ["platform_movement", "jumping", "gravity"],
            "four_direction": ["four_direction_movement"],
            "flap": ["flapping", "gravity"],
            "racing": ["racing_movement"],
        }
        mechanic_ids = set(mechanics_by_movement[movement])
        if any(level.collectibles for level in config.levels):
            mechanic_ids.add("collectibles")
        if any(level.enemies or level.hazards for level in config.levels):
            mechanic_ids.update({"enemy_contact", "entity_creation"})
        if any(level.goal for level in config.levels):
            mechanic_ids.add("goal_interaction")
        if any(level.targets for level in config.levels):
            mechanic_ids.update({"target_scoring", "scoring"})
        if config.fire_control != "none":
            mechanic_ids.add("projectiles")
        if any(
            hazard.movement == "chasing"
            for level in config.levels
            for hazard in level.hazards
        ):
            mechanic_ids.add("chasing_enemies")
        if any(level.hazards for level in config.levels):
            mechanic_ids.add("enemy_waves")
        if movement == "flap" and any(level.obstacles for level in config.levels):
            mechanic_ids.add("scrolling_obstacles")
        if movement == "racing" and any(level.obstacles for level in config.levels):
            mechanic_ids.add("racing_obstacles")
        if (
            any(level.time_limit for level in config.levels)
            or config.win_condition == "survive"
            or config.lose_condition == "time_expired"
        ):
            mechanic_ids.add("timer")
        if config.game_type == "space_shooter":
            mechanic_ids.update({"shooter_movement", "shooter_scoring"})
        if any(level.collectibles or level.hazards or level.targets for level in config.levels):
            mechanic_ids.add("scoring")
        mechanics = [
            DefinitionMechanic(
                kind=kind,
                support=self.mechanic_support.get(kind, "unsupported"),
                parameters=(
                    {"gravity": config.player.gravity}
                    if kind == "gravity"
                    else {}
                ),
            )
            for kind in sorted(mechanic_ids)
        ]
        controls = {
            "platformer": [
                DefinitionControl(action="move_left", keys=["ArrowLeft", "KeyA"]),
                DefinitionControl(action="move_right", keys=["ArrowRight", "KeyD"]),
                DefinitionControl(action="jump", keys=["ArrowUp", "KeyW", "Space"]),
            ],
            "four_direction": [
                DefinitionControl(action="move_left", keys=["ArrowLeft", "KeyA"]),
                DefinitionControl(action="move_right", keys=["ArrowRight", "KeyD"]),
                DefinitionControl(action="move_up", keys=["ArrowUp", "KeyW"]),
                DefinitionControl(action="move_down", keys=["ArrowDown", "KeyS"]),
            ],
            "flap": [DefinitionControl(action="flap", keys=["Space", "ArrowUp", "KeyW"])],
            "racing": [
                DefinitionControl(action="steer_left", keys=["ArrowLeft", "KeyA"]),
                DefinitionControl(action="steer_right", keys=["ArrowRight", "KeyD"]),
            ],
        }[movement]
        if config.fire_control != "none":
            controls.append(
                DefinitionControl(
                    action="fire",
                    keys=[{"space": "Space", "enter": "Enter", "x": "KeyX"}[config.fire_control]],
                )
            )
        player = config.player
        definition_genre = genre or config.game_type
        return GameDefinitionV2(
            source_schema_version="1.0",
            title=config.title,
            description=(
                f"Planned as {definition_genre} and adapted from GameSpec 1.0."
            ),
            genre=definition_genre,
            theme=config.theme,
            camera=config.camera,
            palette=config.palette.model_dump(),
            world_width=config.world_width,
            world_height=config.world_height,
            player=DefinitionObject(
                id="player",
                kind=player.appearance,
                position=DefinitionPoint(x=player.start.x, y=player.start.y),
                visual=DefinitionVisual(shape=player.appearance, color=config.palette.player),
                properties={
                    "movement_mode": movement,
                    "speed": player.speed,
                    "max_health": player.max_health,
                    "jump_velocity": player.jump_velocity,
                    "gravity": player.gravity,
                    "fire_cooldown": player.fire_cooldown,
                    "fire_control": config.fire_control,
                    "appearance": player.appearance,
                },
            ),
            controls=controls,
            physics={
                "gravity_y": player.gravity if movement in {"platformer", "flap"} else 0,
                "jump_velocity": player.jump_velocity,
                "flap_strength": player.flap_strength,
                "world_bounds": True,
            },
            mechanics=mechanics,
            levels=levels,
            ui=ui or GameSceneBuilderAgent.build_ui(
                movement_mode=player.movement_mode,
                fire_control=config.fire_control,
                win_condition=config.win_condition,
                lose_condition=config.lose_condition,
                has_timer=any(level.time_limit for level in config.levels),
                requested_ui=requested_ui,
            ),
            win=DefinitionObjective(
                condition=config.win_condition,
                target=config.win_target,
                requires=(
                    [f"collectibles:{config.levels[0].collectible_target}"]
                    if config.win_condition == "reach_goal"
                    and config.levels[0].collectible_target > 0
                    else []
                ),
            ),
            lose=DefinitionObjective(
                condition=config.lose_condition,
                target=config.lose_target,
            ),
        )

    def _ensure_supported_mechanics(self, spec: GameSpec) -> None:
        for level in spec.levels:
            for platform in level.platforms:
                if platform.x + platform.width > 1 or platform.y + platform.height > 1:
                    raise ValueError(
                        "Platform extends outside the normalized play area: "
                        "x + width and y + height must be at or below 1."
                    )
                self._require_supported(
                    platform.movement, self.supported_platform_movement, "platform movement"
                )
            for collectible in level.collectibles:
                self._require_supported(
                    collectible.kind, self.supported_collectible_kinds, "collectible kind"
                )
            for enemy in level.enemies:
                self._require_supported(
                    enemy.movement, self.supported_enemy_movement, "enemy movement"
                )
            for scenery in level.scenery:
                self._require_in_bounds(
                    scenery.position.x,
                    scenery.position.y,
                    scenery.width,
                    scenery.height,
                    "scenery",
                )
            for obstacle in level.obstacles:
                self._require_in_bounds(
                    obstacle.position.x,
                    obstacle.position.y,
                    obstacle.width,
                    obstacle.height,
                    "obstacle",
                )
            for hazard in level.hazards:
                self._require_supported(
                    hazard.kind, self.supported_hazard_kinds, "hazard kind"
                )
                self._require_supported(
                    hazard.movement,
                    self.supported_hazard_movement,
                    "hazard movement",
                )
                self._require_in_bounds(
                    hazard.position.x,
                    hazard.position.y,
                    hazard.width,
                    hazard.height,
                    "hazard",
                )
            for wall in level.walls:
                self._require_in_bounds(
                    wall.position.x, wall.position.y, wall.width, wall.height, "wall"
                )
            for goal in [level.goal] if level.goal else []:
                self._require_point_in_bounds(goal.position.x, goal.position.y, "goal")
            for target in level.targets:
                self._require_point_in_bounds(
                    target.position.x, target.position.y, "target"
                )

    def _normalize_collectible_position(self, collectible, platforms):
        if any(
            platform.x + self.player_half_width + self.collectible_radius
            <= collectible.position.x
            <= platform.x + platform.width - self.player_half_width - self.collectible_radius
            and platform.y - 0.08 - self.placement_tolerance
            <= collectible.position.y
            <= platform.y + self.placement_tolerance
            for platform in platforms
        ):
            return collectible.position

        platform = min(
            platforms,
            key=lambda candidate: (
                max(candidate.x - collectible.position.x, 0, collectible.position.x - (candidate.x + candidate.width))
                + max(candidate.y - collectible.position.y, 0, collectible.position.y - candidate.y),
                candidate.y,
            ),
        )
        left = platform.x + self.player_half_width + self.collectible_radius
        right = platform.x + platform.width - self.player_half_width - self.collectible_radius
        x = min(max(collectible.position.x, left), max(left, right))
        y = max(0, platform.y - min(0.04, platform.y))
        return collectible.position.model_copy(update={"x": x, "y": y})

    @staticmethod
    def _require_supported(value: str, supported: frozenset[str], mechanic: str) -> None:
        if value not in supported:
            raise ValueError(f"Unsupported {mechanic}: {value}")

    @staticmethod
    def _require_in_bounds(
        x: float, y: float, width: float, height: float, entity: str
    ) -> None:
        if x + width > 1 or y + height > 1:
            raise ValueError(
                f"{entity.capitalize()} extends outside the normalized play area."
            )

    @staticmethod
    def _require_point_in_bounds(x: float, y: float, entity: str) -> None:
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise ValueError(f"{entity.capitalize()} is outside the normalized play area.")
