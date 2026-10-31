from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agents.game_builder import GameBuilderAgent, GameRuntimeConfig
from app.models import GameSpec, PlaytestReport


class QAIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    severity: Literal["error", "warning", "info"]
    message: str = Field(min_length=1)
    suggested_fix: str = Field(min_length=1)
    location: str = Field(min_length=1)
    requirement_ids: list[str] = Field(default_factory=list, max_length=128)


class QAReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["unverified", "warning", "fail", "pass"]
    scope: Literal["structural"]
    runtime_tested: bool = False
    structural_status: Literal["unverified", "warning", "fail"] = "unverified"
    gameplay_status: Literal["unverified", "pass", "fail"] = "unverified"
    playtest_report: PlaytestReport | None = None
    summary: str = Field(min_length=1)
    issues: list[QAIssue]

    @model_validator(mode="before")
    @classmethod
    def infer_structural_status(cls, value):
        if isinstance(value, dict) and "structural_status" not in value:
            if value.get("status") in {"warning", "fail"}:
                value = {**value, "structural_status": value["status"]}
        return value


class GameQAAgent:
    """Run deterministic structural checks on a validated game configuration."""

    placement_tolerance = 1e-9
    player_half_width = 12 / 800
    collectible_radius = 10 / 800

    def inspect(self, game: GameSpec | GameRuntimeConfig | dict) -> QAReport:
        configuration = self._validate_input(game)
        issues: list[QAIssue] = []

        self._check_required_data(configuration, issues)
        self._check_platform_placement(configuration, issues)
        self._check_collectibles(configuration, issues)
        self._check_enemies_and_health(configuration, issues)
        self._check_conditions(configuration, issues)
        self._check_supported_mechanics(configuration, issues)
        self._check_game_type(configuration, issues)
        if getattr(configuration, "game_type", "platform_adventure") == "dodge_arena":
            if not (
                configuration.levels[0].enemies
                or configuration.levels[0].hazards
            ):
                self._add(
                    issues,
                    "DODGE_ENEMIES_MISSING",
                    "warning",
                    "A dodge arena should contain at least one enemy.",
                    "Add moving enemies to the level.",
                    "levels.0.enemies",
                )

        status: Literal["unverified", "warning", "fail"]
        if any(issue.severity == "error" for issue in issues):
            status = "fail"
        elif any(issue.severity == "warning" for issue in issues):
            status = "warning"
        else:
            status = "unverified"
        summary = {
            "unverified": "Structural checks found no issues; browser gameplay was not tested.",
            "warning": "Structural QA found warnings; runtime playability was not tested.",
            "fail": "Structural QA found failures; runtime playability was not tested.",
        }[status]
        return QAReport(
            status=status,
            scope="structural",
            structural_status=status,
            summary=summary,
            issues=issues,
        )

    @staticmethod
    def attach_playtest(
        report: QAReport, playtest: PlaytestReport
    ) -> QAReport:
        gameplay_issues = [
            QAIssue(
                code=f"PLAYTEST_{test.name.upper()}",
                severity="error",
                message=test.failure_details or test.actual_behavior,
                suggested_fix="Review the browser-observed behavior and repair the affected runtime configuration.",
                location=f"playtest.{test.name}",
            )
            for test in playtest.tests
            if test.required and test.status == "FAIL"
        ]
        combined_issues = [*report.issues, *gameplay_issues]
        if report.structural_status == "fail":
            return report.model_copy(
                update={
                    "runtime_tested": playtest.runtime_tested,
                    "gameplay_status": playtest.status,
                    "playtest_report": playtest,
                    "status": "fail",
                    "issues": combined_issues,
                    "summary": "Structural QA failed; browser playtest results are retained.",
                }
            )
        if playtest.status == "fail":
            status: Literal["unverified", "warning", "fail", "pass"] = "fail"
        elif report.structural_status == "warning":
            status = "warning"
        elif playtest.status == "pass":
            status = "pass"
        else:
            status = "unverified"
        summary = {
            "fail": "Browser gameplay checks failed.",
            "warning": "Structural QA found warnings; review browser checks before relying on the result.",
            "pass": "Structural QA and required browser gameplay checks passed.",
            "unverified": "Browser gameplay has required checks that were not verified.",
        }[status]
        return report.model_copy(
            update={
                "status": status,
                "runtime_tested": playtest.runtime_tested,
                "gameplay_status": playtest.status,
                "playtest_report": playtest,
                "issues": combined_issues,
                "summary": summary,
            }
        )

    @staticmethod
    def _validate_input(
        game: GameSpec | GameRuntimeConfig | dict,
    ) -> GameSpec | GameRuntimeConfig:
        if isinstance(game, (GameSpec, GameRuntimeConfig)):
            return game
        if not isinstance(game, dict):
            raise ValueError("Game QA requires a GameSpec or GameRuntimeConfig.")
        try:
            if "runtime_config" in game or "win_target" in game:
                return GameRuntimeConfig.model_validate(game)
            return GameSpec.model_validate(game)
        except ValidationError as error:
            raise ValueError("Cannot run QA on an invalid game configuration.") from error

    @staticmethod
    def _check_required_data(
        game: GameSpec | GameRuntimeConfig, issues: list[QAIssue]
    ) -> None:
        if game.world_width < 640 or game.world_height < 360:
            GameQAAgent._add(
                issues,
                "WORLD_DIMENSIONS_INVALID",
                "error",
                "The game world is smaller than the supported play area.",
                "Use world dimensions of at least 640 by 360 pixels.",
                "world_width/world_height",
            )
        if not game.player.start:
            GameQAAgent._add(
                issues,
                "PLAYER_START_MISSING",
                "error",
                "The player has no starting position.",
                "Provide a normalized player.start position.",
                "player.start",
            )
        if not game.levels:
            GameQAAgent._add(
                issues,
                "LEVELS_EMPTY",
                "error",
                "The game does not contain a level.",
                "Add at least one playable level.",
                "levels",
            )
        for index, level in enumerate(game.levels):
            location = f"levels.{index}"
            if not level.platforms and game.game_type in {
                "platform_adventure",
                "collection_challenge",
            }:
                GameQAAgent._add(
                    issues,
                    "PLATFORMS_EMPTY",
                    "error",
                    "The level has no platforms.",
                    "Add at least one platform to support movement and collisions.",
                    f"{location}.platforms",
                )

    @staticmethod
    def _check_platform_placement(
        game: GameSpec | GameRuntimeConfig, issues: list[QAIssue]
    ) -> None:
        for level_index, level in enumerate(game.levels):
            for platform_index, platform in enumerate(level.platforms):
                if platform.x + platform.width > 1 or platform.y + platform.height > 1:
                    GameQAAgent._add(
                        issues,
                        "PLATFORM_OUT_OF_BOUNDS",
                        "error",
                        "The platform extends outside the normalized play area.",
                        "Keep x + width and y + height at or below 1.",
                        f"levels.{level_index}.platforms.{platform_index}",
                    )

    @staticmethod
    def _check_collectibles(
        game: GameSpec | GameRuntimeConfig, issues: list[QAIssue]
    ) -> None:
        total_collectibles = 0
        game_type = getattr(game, "game_type", "platform_adventure")
        for level_index, level in enumerate(game.levels):
            total_collectibles += len(level.collectibles)
            if level.collectible_target > len(level.collectibles):
                GameQAAgent._add(
                    issues,
                    "LEVEL_TARGET_EXCEEDS_COLLECTIBLES",
                    "error",
                    "The level target exceeds the number of collectibles.",
                    "Lower collectible_target or add more collectibles.",
                    f"levels.{level_index}.collectible_target",
                )
            if game_type == "platform_adventure":
                misplaced_count = sum(
                    not any(
                        platform.x + GameQAAgent.player_half_width + GameQAAgent.collectible_radius
                        <= collectible.position.x
                        <= platform.x + platform.width - GameQAAgent.player_half_width - GameQAAgent.collectible_radius
                        and platform.y - 0.08 - GameQAAgent.placement_tolerance
                        <= collectible.position.y
                        <= platform.y + GameQAAgent.placement_tolerance
                        for platform in level.platforms
                    )
                    for collectible in level.collectibles
                )
                if misplaced_count:
                    GameQAAgent._add(
                        issues,
                        "COLLECTIBLES_NOT_ON_PLATFORMS",
                        "warning",
                        f"{misplaced_count} collectible(s) are not positioned just above a platform.",
                        "Place each collectible horizontally within and just above a platform; browser playability is not tested here.",
                        f"levels.{level_index}.collectibles",
                    )
            elif game_type == "maze":
                blocked_count = sum(
                    any(
                        wall.position.x <= collectible.position.x <= wall.position.x + wall.width
                        and wall.position.y <= collectible.position.y <= wall.position.y + wall.height
                        for wall in level.walls
                    )
                    for collectible in level.collectibles
                )
                if blocked_count:
                    GameQAAgent._add(
                        issues,
                        "COLLECTIBLES_IN_MAZE_WALLS",
                        "error",
                        f"{blocked_count} collectible(s) are inside maze walls.",
                        "Place each collectible in a walkable maze corridor.",
                        f"levels.{level_index}.collectibles",
                    )
        win_target = GameQAAgent._win_target(game)
        if (
            GameQAAgent._win_type(game) == "collect_target"
            and (win_target is None or win_target > total_collectibles)
        ):
            GameQAAgent._add(
                issues,
                "WIN_TARGET_UNREACHABLE",
                "error",
                "The collect_target win condition cannot be satisfied by the available collectibles.",
                "Set the target to the total collectible count or add more collectibles.",
                "win_condition",
            )

    @staticmethod
    def _check_enemies_and_health(
        game: GameSpec | GameRuntimeConfig, issues: list[QAIssue]
    ) -> None:
        for level_index, level in enumerate(game.levels):
            for enemy_index, enemy in enumerate(level.enemies):
                if enemy.damage >= game.player.max_health:
                    GameQAAgent._add(
                        issues,
                        "ENEMY_DAMAGE_HIGH",
                        "warning",
                        "A single enemy collision can deplete all player health.",
                        "Reduce enemy damage or increase player max_health.",
                        f"levels.{level_index}.enemies.{enemy_index}.damage",
                    )
            if game.game_type == "space_shooter":
                for hazard_index, hazard in enumerate(level.hazards):
                    if hazard.damage >= game.player.max_health:
                        GameQAAgent._add(
                            issues,
                            "SHOOTER_DAMAGE_LETHAL",
                            "error",
                            "A single enemy collision can deplete all player health.",
                            "Reduce enemy hazard damage below player.max_health.",
                            f"levels.{level_index}.hazards.{hazard_index}.damage",
                        )
        if game.player.max_health < 1:
            GameQAAgent._add(
                issues,
                "PLAYER_HEALTH_INVALID",
                "error",
                "The player must have positive health.",
                "Set player.max_health to at least 1.",
                "player.max_health",
            )

    @staticmethod
    def _check_conditions(
        game: GameSpec | GameRuntimeConfig, issues: list[QAIssue]
    ) -> None:
        if (
            GameQAAgent._win_type(game) == "collect_target"
            and GameQAAgent._win_target(game) is None
        ):
            GameQAAgent._add(
                issues,
                "WIN_TARGET_MISSING",
                "error",
                "collect_target requires a positive target.",
                "Set win_condition.target to a reachable positive integer.",
                "win_condition",
            )
        if (
            GameQAAgent._win_type(game) == "defeat_target"
            and GameQAAgent._win_target(game) is None
        ):
            GameQAAgent._add(
                issues,
                "DEFEAT_TARGET_MISSING",
                "error",
                "defeat_target requires a positive enemy count.",
                "Set win_condition.target to the required number of defeated enemies.",
                "win_condition",
            )
        if (
            GameQAAgent._win_type(game) == "reach_goal"
            and GameQAAgent._win_target(game) is not None
        ):
            GameQAAgent._add(
                issues,
                "WIN_TARGET_UNUSED",
                "warning",
                "This win condition does not use a collectible target.",
                "Remove the unused win target.",
                "win_condition.target",
            )
        if (
            GameQAAgent._lose_type(game) == "time_expired"
            and GameQAAgent._lose_target(game) is None
        ):
            GameQAAgent._add(
                issues,
                "LOSE_TARGET_MISSING",
                "error",
                "time_expired requires a positive time target.",
                "Set lose_condition.target in seconds.",
                "lose_condition",
            )
        if (
            GameQAAgent._lose_type(game) == "health_depleted"
            and GameQAAgent._lose_target(game) is not None
        ):
            GameQAAgent._add(
                issues,
                "LOSE_TARGET_UNUSED",
                "warning",
                "health_depleted does not use a time target.",
                "Remove the unused lose target.",
                "lose_condition.target",
            )

    @staticmethod
    def _check_supported_mechanics(
        game: GameSpec | GameRuntimeConfig, issues: list[QAIssue]
    ) -> None:
        supported_platforms = GameBuilderAgent.supported_platform_movement
        supported_collectibles = GameBuilderAgent.supported_collectible_kinds
        supported_enemies = GameBuilderAgent.supported_enemy_movement
        for level_index, level in enumerate(game.levels):
            for index, platform in enumerate(level.platforms):
                if platform.movement not in supported_platforms:
                    GameQAAgent._unsupported(
                        issues, "platform movement", platform.movement,
                        f"levels.{level_index}.platforms.{index}.movement",
                    )
            for index, collectible in enumerate(level.collectibles):
                if collectible.kind not in supported_collectibles:
                    GameQAAgent._unsupported(
                        issues, "collectible kind", collectible.kind,
                        f"levels.{level_index}.collectibles.{index}.kind",
                    )
            for index, enemy in enumerate(level.enemies):
                if enemy.movement not in supported_enemies:
                    GameQAAgent._unsupported(
                        issues, "enemy movement", enemy.movement,
                        f"levels.{level_index}.enemies.{index}.movement",
                    )

    @staticmethod
    def _check_game_type(game: GameSpec | GameRuntimeConfig, issues: list[QAIssue]) -> None:
        game_type = game.game_type
        level = game.levels[0]
        if game_type == "maze" and len(level.walls) < 2:
            GameQAAgent._add(
                issues, "MAZE_WALLS_MISSING", "error",
                "Maze games require walls that form corridors.",
                "Add at least two in-bounds maze walls.", "levels.0.walls",
            )
        if game_type == "target_arcade" and not level.targets:
            GameQAAgent._add(
                issues, "TARGETS_MISSING", "error",
                "Target arcade games require target entities.",
                "Add at least one target entity.", "levels.0.targets",
            )
        if game_type in {"maze", "top_down_collection"} and level.goal is None:
            GameQAAgent._add(
                issues, "GOAL_MISSING", "error",
                "This game type requires a reachable destination.",
                "Add a level goal.", "levels.0.goal",
            )
        if (
            game_type == "top_down_collection"
            and GameQAAgent._win_type(game) != "collect_target"
        ):
            GameQAAgent._add(
                issues,
                "FOREST_OBJECTIVE_INVALID",
                "error",
                "Forest exploration requires collecting the target before reaching its destination.",
                "Use collect_target with a reachable positive target and a destination.",
                "win_condition",
            )
        if game_type in {"dodge_arena", "collection_challenge"}:
            timer = level.time_limit or GameQAAgent._lose_target(game)
            if timer is None or timer <= 0:
                GameQAAgent._add(
                    issues, "TIMER_MISSING", "error",
                    "This game type requires a positive timer.",
                    "Set level.time_limit or lose_condition.target.", "levels.0.time_limit",
                )
        if game_type == "flappy_dodge" and not level.obstacles:
            GameQAAgent._add(
                issues,
                "FLAPPY_OBSTACLES_MISSING",
                "error",
                "Flappy games require scrolling obstacles.",
                "Add pipe or obstacle entities.",
                "levels.0.obstacles",
            )
        if game_type == "flappy_dodge":
            pipes = [obstacle for obstacle in level.obstacles if obstacle.kind == "pipe"]
            has_pipe_pair = any(
                abs(upper.position.x - lower.position.x)
                <= GameQAAgent.placement_tolerance
                and upper.position.y < lower.position.y
                and upper.position.y + upper.height <= lower.position.y
                for upper in pipes
                for lower in pipes
                if upper is not lower
            )
            if not has_pipe_pair:
                GameQAAgent._add(
                    issues,
                    "FLAPPY_PIPE_PAIR_INVALID",
                    "error",
                    "Flappy games require an upper and lower pipe at the same horizontal position.",
                    "Add a pipe pair with a clear, reachable vertical gap.",
                    "levels.0.obstacles",
                )
        if any(
            hazard.kind == "zombie"
            for level in game.levels
            for hazard in level.hazards
        ):
            if game.player.movement_mode != "four_direction":
                GameQAAgent._add(
                    issues,
                    "ZOMBIE_MOVEMENT_UNSUPPORTED",
                    "error",
                    "Zombie survival requires top-down four-direction player movement.",
                    "Configure player.movement_mode as four_direction.",
                    "player.movement_mode",
                )
            if game.fire_control == "none":
                GameQAAgent._add(
                    issues,
                    "ZOMBIE_COMBAT_CONTROL_MISSING",
                    "error",
                    "Zombie hazards require a configured projectile control.",
                    "Set fire_control to a supported key.",
                    "fire_control",
                )
            if not any(
                hazard.kind == "zombie" and hazard.movement == "chasing"
                for level in game.levels
                for hazard in level.hazards
            ):
                GameQAAgent._add(
                    issues,
                    "ZOMBIE_CHASE_MISSING",
                    "error",
                    "Zombie hazards need at least one chasing enemy.",
                    "Set a zombie hazard movement to chasing.",
                    "levels[].hazards",
                )
        if game_type == "flappy_dodge" and game.player.movement_mode != "flap":
            GameQAAgent._add(
                issues,
                "FLAPPY_MOVEMENT_INVALID",
                "error",
                "Flappy games require flap movement.",
                "Set player.movement_mode to flap.",
                "player.movement_mode",
            )
        if game_type == "flappy_dodge" and game.player.gravity <= 0:
            GameQAAgent._add(
                issues,
                "FLAPPY_GRAVITY_INVALID",
                "error",
                "Flappy games require positive gravity for the bird's flight arc.",
                "Set player.gravity to a positive value and retain a reachable pipe gap.",
                "player.gravity",
            )
        if game_type == "space_shooter" and not level.hazards:
            GameQAAgent._add(
                issues,
                "SHOOTER_ENEMIES_MISSING",
                "error",
                "Space shooters require moving enemy hazards.",
                "Add enemy hazards to the level.",
                "levels.0.hazards",
            )
        if game_type == "space_shooter" and getattr(game, "fire_control", "none") == "none":
            GameQAAgent._add(
                issues,
                "SHOOTER_FIRE_CONTROL_MISSING",
                "error",
                "Space shooters require a firing control.",
                "Set fire_control to space, enter, or x.",
                "fire_control",
            )
        if (
            game_type == "space_shooter"
            and GameQAAgent._win_type(game) != "defeat_target"
        ):
            GameQAAgent._add(
                issues,
                "SHOOTER_OBJECTIVE_INVALID",
                "error",
                "Space shooters must be completed by defeating the configured enemy target.",
                "Use win_condition.type defeat_target and a positive target count.",
                "win_condition",
            )
        if game_type == "space_shooter" and game.player.movement_mode != "four_direction":
            GameQAAgent._add(
                issues,
                "SHOOTER_MOVEMENT_INVALID",
                "error",
                "Space shooters require four-direction ship movement.",
                "Set player.movement_mode to four_direction.",
                "player.movement_mode",
            )
        if game_type == "space_shooter":
            player_half_width = 18 / game.world_width
            player_half_height = 20 / game.world_height
            for index, hazard in enumerate(level.hazards):
                hazard_x = hazard.position.x + hazard.width / 2
                hazard_y = hazard.position.y + hazard.height / 2
                if (
                    abs(hazard_x - game.player.start.x)
                    < player_half_width + hazard.width / 2
                    and abs(hazard_y - game.player.start.y)
                    < player_half_height + hazard.height / 2
                ):
                    GameQAAgent._add(
                        issues,
                        "SHOOTER_SPAWN_OVERLAP",
                        "error",
                        "An enemy hazard overlaps the player's spawn position.",
                        "Move the enemy away from player.start so the player can begin safely.",
                        f"levels.0.hazards.{index}.position",
                    )
            for index, enemy in enumerate(level.enemies):
                if (
                    abs(enemy.position.x - game.player.start.x)
                    < player_half_width + 17 / game.world_width
                    and abs(enemy.position.y - game.player.start.y)
                    < player_half_height + 16 / game.world_height
                ):
                    GameQAAgent._add(
                        issues,
                        "SHOOTER_SPAWN_OVERLAP",
                        "error",
                        "An enemy overlaps the player's spawn position.",
                        "Move the enemy away from player.start so the player can begin safely.",
                        f"levels.0.enemies.{index}.position",
                    )
        if game_type == "racing" and not level.obstacles:
            GameQAAgent._add(
                issues,
                "RACING_OBSTACLES_MISSING",
                "error",
                "Racing games require road obstacles.",
                "Add obstacle entities to the track.",
                "levels.0.obstacles",
            )
        if game_type == "racing" and game.player.movement_mode != "racing":
            GameQAAgent._add(
                issues,
                "RACING_MOVEMENT_INVALID",
                "error",
                "Racing games require racing movement.",
                "Set player.movement_mode to racing.",
                "player.movement_mode",
            )
        if level.goal and (
            abs(level.goal.position.x - game.player.start.x)
            <= 52 / game.world_width
            and abs(level.goal.position.y - game.player.start.y)
            <= 58 / game.world_height
        ):
            GameQAAgent._add(
                issues, "STARTS_ON_GOAL", "error",
                "The player starts on the destination and could win immediately.",
                "Place the goal away from player.start.", "levels.0.goal",
            )

    @staticmethod
    def _win_type(game: GameSpec | GameRuntimeConfig) -> str:
        return (
            game.win_condition.type
            if isinstance(game, GameSpec)
            else game.win_condition
        )

    @staticmethod
    def _win_target(game: GameSpec | GameRuntimeConfig) -> int | None:
        return (
            game.win_condition.target
            if isinstance(game, GameSpec)
            else game.win_target
        )

    @staticmethod
    def _lose_type(game: GameSpec | GameRuntimeConfig) -> str:
        return (
            game.lose_condition.type
            if isinstance(game, GameSpec)
            else game.lose_condition
        )

    @staticmethod
    def _lose_target(game: GameSpec | GameRuntimeConfig) -> int | None:
        return (
            game.lose_condition.target
            if isinstance(game, GameSpec)
            else game.lose_target
        )

    @staticmethod
    def _unsupported(
        issues: list[QAIssue], mechanic: str, value: str, location: str
    ) -> None:
        GameQAAgent._add(
            issues,
            "UNSUPPORTED_MECHANIC",
            "error",
            f"Unsupported {mechanic}: {value}.",
            "Use one of the mechanics supported by GameBuilderAgent.",
            location,
        )

    @staticmethod
    def _add(
        issues: list[QAIssue],
        code: str,
        severity: Literal["error", "warning", "info"],
        message: str,
        suggested_fix: str,
        location: str,
    ) -> None:
        issues.append(
            QAIssue(
                code=code,
                severity=severity,
                message=message,
                suggested_fix=suggested_fix,
                location=location,
            )
        )
