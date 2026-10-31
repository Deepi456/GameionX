import json
import logging
import re
from typing import Any, Protocol

from pydantic import ValidationError

from app.agents.game_planner import GamePlan
from app.models import GameSpec

logger = logging.getLogger(__name__)


class GameDesignProvider(Protocol):
    name: str

    def generate_content(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any],
        difficulty: str | None = None,
        game_style: str | None = None,
        bypass_cache: bool = False,
        deadline_seconds: float = 90.0,
    ) -> str:
        """Generate JSON text for a GameSpec."""
        ...


class PromptConsistencyError(ValueError):
    """The model output contradicts an explicit user request."""


def gemini_game_spec_schema() -> dict[str, Any]:
    """Return a flattened schema using Gemini's supported JSON Schema subset."""
    source = GameSpec.model_json_schema()
    definitions = source.get("$defs", {})

    def flatten(schema: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in schema:
            return flatten(definitions[schema["$ref"].rsplit("/", 1)[-1]])
        if "anyOf" in schema:
            non_null = [
                item for item in schema["anyOf"] if item.get("type") != "null"
            ]
            flattened = flatten(non_null[0]) if non_null else {"type": "string"}
            if any(item.get("type") == "null" for item in schema["anyOf"]):
                flattened["nullable"] = True
            return flattened
        result: dict[str, Any] = {}
        for key in ("type", "enum"):
            if key in schema:
                result[key] = schema[key]
        if "const" in schema:
            result["enum"] = [schema["const"]]
        if schema.get("type") == "object":
            result["properties"] = {
                name: flatten(value)
                for name, value in schema.get("properties", {}).items()
            }
            if schema.get("required"):
                result["required"] = schema["required"]
        elif schema.get("type") == "array" and "items" in schema:
            result["items"] = flatten(schema["items"])
        return result

    schema = flatten(source)

    def describe_fields(value: dict[str, Any], field_name: str | None = None) -> None:
        if field_name in {"x", "y"}:
            value["description"] = (
                "Normalized coordinate from 0 to 1 inclusive. Never use pixels."
            )
        elif field_name in {"width", "height"}:
            value["description"] = (
                "Normalized dimension greater than 0 and at most 1. Never use pixels."
            )
        elif field_name == "id":
            value["description"] = (
                "Lowercase level identifier using only a-z, 0-9, and hyphens; "
                "for example level-1."
            )
        elif field_name == "background":
            value["description"] = (
                "Six-digit hexadecimal color including #, for example #172033."
            )
        elif field_name == "target":
            value["description"] = (
                "Omit or use null unless the condition requires a positive integer "
                "target. Targets must be within the condition's documented range."
            )
        elif field_name == "collectibles":
            value["description"] = (
                "Array of collectible objects. Preserve any explicit count from the "
                "user prompt exactly; do not invent a different count."
            )
        elif field_name == "collectible_target":
            value["description"] = (
                "Number of collectibles required in this level; it must not exceed "
                "the level's collectible array length."
            )
        if value.get("type") == "object":
            for name, child in value.get("properties", {}).items():
                describe_fields(child, name)
        elif value.get("type") == "array":
            describe_fields(value.get("items", {}))

    describe_fields(schema)
    return schema


class GameDesignerAgent:
    """Convert a natural-language game prompt into a validated GameSpec."""

    def __init__(self, provider: GameDesignProvider, max_retries: int = 1) -> None:
        if max_retries < 0 or max_retries > 1:
            raise ValueError("GameDesignerAgent allows at most one retry.")
        self.provider = provider
        self.max_retries = max_retries

    def design(
        self,
        prompt: str,
        mode: str,
        plan: GamePlan | None = None,
        *,
        bypass_cache: bool = False,
        deadline_seconds: float = 90.0,
    ) -> GameSpec:
        base_prompt = self._build_prompt(prompt, mode, plan)
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            request_prompt = base_prompt
            if attempt:
                request_prompt += (
                    "\nYour previous response was invalid. Return only corrected JSON "
                    "that conforms exactly to the supplied schema."
                )
            try:
                raw_result = self.provider.generate_content(
                    prompt=request_prompt,
                    response_schema=gemini_game_spec_schema(),
                    difficulty=mode,
                    game_style="general",
                    bypass_cache=bypass_cache,
                    deadline_seconds=deadline_seconds,
                )
                spec = GameSpec.model_validate_json(raw_result)
                self._validate_prompt_consistency(prompt, spec)
                self._validate_prompt_capabilities(prompt, spec)
                if plan is not None:
                    self._validate_plan(plan, spec)
                return spec
            except json.JSONDecodeError as error:
                logger.warning(
                    "GameDesignerAgent received invalid JSON on attempt %d: %s",
                    attempt + 1,
                    error.msg,
                )
                last_error = error
            except ValidationError as error:
                logger.warning(
                    "GameDesignerAgent received invalid GameSpec on attempt %d: %s",
                    attempt + 1,
                    [
                        {
                            "location": ".".join(str(part) for part in item["loc"]),
                            "type": item["type"],
                            "message": item["msg"],
                        }
                        for item in error.errors()
                    ],
                )
                last_error = error
            except PromptConsistencyError as error:
                logger.warning(
                    "GameDesignerAgent received inconsistent GameSpec on attempt %d: %s",
                    attempt + 1,
                    str(error),
                )
                last_error = error
        raise ValueError(
            f"{self.provider.name.title()} returned invalid GameSpec JSON after one retry."
        ) from last_error

    @staticmethod
    def _build_prompt(
        prompt: str, mode: str, plan: GamePlan | None = None
    ) -> str:
        requested_count = GameDesignerAgent._requested_collectible_count(prompt)
        requested_enemy_count = GameDesignerAgent._requested_enemy_count(prompt)
        count_instruction = (
            f"The user explicitly requested exactly {requested_count} collectibles. "
            "Return exactly that many collectible objects across all levels and set "
            "each level collectible_target consistently. Do not substitute an "
            "arbitrary count. "
            if requested_count is not None
            else
            "If the user specifies a collectible count, preserve it exactly in the "
            "collectibles arrays and matching targets; never substitute an arbitrary "
            "count. "
        )
        enemy_count_instruction = (
            f"The user explicitly requested defeating {requested_enemy_count} enemies. "
            "For a space shooter, set win_condition.type to defeat_target and its "
            "target to exactly that count. "
            if requested_enemy_count is not None
            else ""
        )
        collection_goal_instruction = (
            f"The user requested {requested_count} collectibles and a finish/goal. "
            "Use win_condition.type reach_goal, include the requested goal, and set "
            "the level collectible_target to the requested count so collecting the "
            "items is required before the player can finish. "
            if requested_count is not None
            and re.search(r"\b(?:finish|flag|reach\s+(?:the\s+)?(?:goal|exit)|exit)\b", prompt.lower())
            else ""
        )
        plan_instruction = (
            f"Preserve this planner checklist exactly: {plan.as_prompt()} "
            "Do not satisfy a requested entity with a generic substitute. "
            if plan is not None
            else ""
        )
        return (
            "You are the GameionX game designer. Convert the user's idea into one "
            "concrete GameSpec game instance as exactly one JSON object. Do not "
            "return a JSON Schema definition, schema metadata, markdown, or source "
            "code. The response must be a playable game data instance, not a "
            "description of the schema. Never output top-level or nested schema "
            "keywords such as properties, $defs, $ref, required, enum, "
            "exclusiveMinimum, additionalProperties, or minItems. Return exactly "
            "one JSON object matching the supplied GameSpec schema version 1.0. "
            "Always include every top-level GameSpec property: schema_version "
            "(the string \"1.0\"), title, description, game_type, player, levels, win_condition, "
            "lose_condition, controls (keyboard), camera, theme, palette, world_width, "
            "world_height, and fire_control. "
            "Never omit schema_version "
            "or any other property, even when its schema value has a default. "
            "Return only JSON conforming to the supplied schema; do not return "
            "explanations, markdown, or partial JSON. Use the supported enum values "
            "only: platform/enemy movement is static, horizontal, or vertical; "
            "collectible kind is coin, gem, or star; win type is collect_target, "
            "defeat_target, "
            "reach_goal, or survive; lose type is health_depleted or time_expired; "
            "controls is keyboard. Include nested player, level, platform, "
            "collectible, enemy, point, win-condition, and lose-condition objects "
            "with the exact field names and value types from the schema. The "
            "condition objects must include a type field using the allowed condition "
            "enum; this is game data, not a JSON Schema keyword. Ensure "
            "every level object includes all of these properties: id, name, "
            "background, platforms, collectibles, enemies, walls, targets, scenery, "
            "obstacles, hazards, goal, time_limit, and collectible_target. For platform games walls and "
            "targets may be empty and goal may be null; maze games require walls "
            "and a goal; target_arcade games require targets; dodge and collection "
            "games require a positive time_limit. "
            "Every level id and name is required; never omit either one. Every "
            "player object includes start, speed, jump_velocity, max_health, "
            "appearance, movement_mode, gravity, flap_strength, and fire_cooldown; "
            "speed, jump_velocity, gravity, and flap_strength are gameplay values, not "
            "normalized values; use speed from 180 through 320 and jump_velocity "
            "from 350 through 600 for responsive controls. "
            "every platform includes x, y, width, height, and movement; every "
            "collectible includes position, value, and kind; every enemy includes "
            "position, damage, and movement; every point includes x and y; and "
            "every win or lose condition includes type and includes target as a "
            "positive integer or null according to the supplied schema. "
            "Include default-valued properties explicitly rather than omitting them. "
            "all x and y values are normalized numbers from 0 through 1 inclusive, "
            "camera must be side_scroller, top_down, grid, or racing; theme must be "
            "forest, sky, space, road, maze, or neon; fire_control must be space, "
            "enter, x, or none. "
            "Scenery kinds include tree, bush, path, grass, water, flower, and building. "
            "Hazards may use kind zombie and movement chasing when supported by the "
            "planned mechanics; never replace a requested zombie with a generic enemy. "
            "game_type must be one of platform_adventure, top_down_collection, maze, "
            "dodge_arena, collection_challenge, target_arcade, flappy_dodge, "
            "space_shooter, or racing and must match the "
            "requested mechanics and objective. "
            "world_width and world_height are pixel dimensions from their supplied "
            "schema bounds. Use a larger world for top-down forest exploration and "
            "keep the player's start, collectibles, and destination reachable. "
            "Generate a prompt-specific level layout and concise title derived "
            "from the requested game; do not reuse a generic title or copy a fixed "
            "layout. Bird or pipe prompts require flappy_dodge, a bird appearance, "
            "sky theme, flap movement, gravity, paired pipe obstacles, and scoring. "
            "For flappy games use gravity 600 through 900 and flap_strength 200 "
            "through 250; place the starting bird safely inside the first pipe gap. "
            "A forest platformer prompt keeps platform_adventure movement but uses "
            "the forest theme, a coordinated palette, and visible forest scenery. "
            "Forest exploration prompts require top_down_collection, an explorer, "
            "four-direction movement without gravity, forest theme, trees, grass or "
            "paths, collectibles, and a destination. Space shooter prompts require "
            "space_shooter, a ship, space theme, enemies, projectiles, a fire "
            "control, and a defeat_target objective. Racing prompts require racing, a car, road theme, and road "
            "obstacles. Maze prompts require maze, a grid camera, visible walls, "
            "keys or collectibles when requested, and an exit. Choose entity "
            "positions from the actual prompt's scene rather than generic fixed "
            "coordinates. "
            "all platform width and height values are greater than 0 and at most 1, "
            "and every platform must fit fully inside the play area: x + width and "
            "y + height must each be at or below 1. "
            "and never use pixel coordinates or dimensions. Use lowercase level IDs "
            "matching ^[a-z0-9-]+$, such as level-1. Use six-digit hexadecimal "
            "background colors such as #172033. collectible_target must be an "
            "integer from 0 through 100 and cannot exceed the number of collectibles. "
            "Collectible positions are normalized center points. For platform "
            "adventures place them just above reachable platform tops; for top-down "
            "and maze games place them in walkable space; for other genres place "
            "them where that genre's movement can reach them. "
            "For collect_target, target must be a positive integer from 1 through "
            "1000. For time_expired, target must be a positive integer from 1 "
            "through 3600. For reach_goal, survive, and health_depleted, omit "
            "target or set it to null; target is an integer only when the "
            "condition requires one. "
            "Do not include extra fields. "
            "palette must contain six-digit hex strings for background, primary, "
            "secondary, accent, player, enemy, obstacle, collectible, and wall; "
            "coordinate all colors with the selected theme. "
            + count_instruction
            + enemy_count_instruction
            + collection_goal_instruction
            + plan_instruction
            + f"Experience mode: {mode}.\nUser game idea: {prompt}"
        )

    @staticmethod
    def _requested_collectible_count(prompt: str) -> int | None:
        number_words = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
        }
        pattern = (
            r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
            r"\s+(?:\w+\s+){0,2}(?:coins?|gems?|stars?|collectibles?)\b"
        )
        matches = re.findall(pattern, prompt.lower())
        if not matches:
            return None
        value = matches[-1]
        return int(value) if value.isdigit() else number_words[value]

    @staticmethod
    def _requested_enemy_count(prompt: str) -> int | None:
        number_words = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
        }
        pattern = (
            r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
            r"\s+(?:\w+\s+){0,2}(?:enemies|enemy|ships|drones|foes)\b"
        )
        matches = re.findall(pattern, prompt.lower())
        if not matches:
            return None
        value = matches[-1]
        return int(value) if value.isdigit() else number_words[value]

    @staticmethod
    def _validate_prompt_consistency(prompt: str, spec: GameSpec) -> None:
        requested_count = GameDesignerAgent._requested_collectible_count(prompt)
        if requested_count is not None:
            actual_count = sum(len(level.collectibles) for level in spec.levels)
            if actual_count != requested_count:
                raise PromptConsistencyError(
                    f"prompt requested {requested_count} collectibles but the model "
                    f"returned {actual_count}"
                )
            if spec.win_condition.type == "collect_target" and (
                spec.win_condition.target != requested_count
            ):
                raise PromptConsistencyError(
                    f"collect_target must equal the requested count {requested_count}"
                )
            if (
                spec.game_type == "platform_adventure"
                and spec.win_condition.type == "reach_goal"
                and re.search(
                    r"\b(?:finish|flag|reach\s+(?:the\s+)?(?:goal|exit)|exit)\b",
                    prompt.lower(),
                )
                and sum(level.collectible_target for level in spec.levels)
                != requested_count
            ):
                raise PromptConsistencyError(
                    "platformer collectible_target must equal the requested count "
                    f"{requested_count} before reaching the goal"
                )
        requested_enemy_count = GameDesignerAgent._requested_enemy_count(prompt)
        if requested_enemy_count is not None and spec.game_type == "space_shooter" and (
            spec.win_condition.type != "defeat_target"
            or spec.win_condition.target != requested_enemy_count
        ):
            raise PromptConsistencyError(
                "space shooter defeat_target must equal the requested enemy count "
                f"{requested_enemy_count}"
            )

    @staticmethod
    def _validate_prompt_capabilities(prompt: str, spec: GameSpec) -> None:
        normalized = prompt.lower()
        requested_type = None
        if any(term in normalized for term in ("racing", "race car", "road race")):
            requested_type = "racing"
        elif any(term in normalized for term in ("maze", "labyrinth")):
            requested_type = "maze"
        elif any(term in normalized for term in ("flappy", "flap", "bird", "pipes")):
            requested_type = "flappy_dodge"
        elif (
            any(term in normalized for term in ("shooter", "projectile", "laser"))
            and not re.search(r"\bzombies?\b", normalized)
        ):
            requested_type = "space_shooter"
        elif any(term in normalized for term in ("platformer", "platform adventure")):
            requested_type = "platform_adventure"
        elif any(term in normalized for term in ("forest adventure", "forest exploration", "woods adventure")):
            requested_type = "top_down_collection"

        if requested_type and spec.game_type != requested_type:
            raise PromptConsistencyError(
                f"prompt requires {requested_type} mechanics but the model returned "
                f"{spec.game_type}"
            )

        level = spec.levels[0]
        if (
            requested_type == "platform_adventure"
            and any(term in normalized for term in ("forest", "woods", "jungle"))
            and (
                spec.theme != "forest"
                or not any(item.kind == "tree" for item in level.scenery)
            )
        ):
            raise PromptConsistencyError(
                "forest platformers require a forest theme and visible tree scenery"
            )
        if requested_type == "flappy_dodge" and not (
            spec.theme == "sky"
            and spec.player.appearance == "bird"
            and spec.player.movement_mode == "flap"
            and spec.player.gravity > 0
            and any(obstacle.kind == "pipe" for obstacle in level.obstacles)
        ):
            raise PromptConsistencyError(
                "bird prompts require a sky scene, flapping bird, gravity, and pipes"
            )
        if requested_type == "top_down_collection" and not (
            spec.theme == "forest"
            and spec.player.appearance == "explorer"
            and spec.player.movement_mode == "four_direction"
            and spec.player.gravity == 0
            and any(item.kind == "tree" for item in level.scenery)
            and any(item.kind == "path" for item in level.scenery)
            and level.collectibles
            and level.goal is not None
        ):
            raise PromptConsistencyError(
                "forest prompts require an explorer, walkable forest scenery, "
                "collectibles, and a destination"
            )
        if requested_type == "space_shooter" and not (
            spec.theme == "space"
            and spec.player.appearance == "ship"
            and spec.player.movement_mode == "four_direction"
            and level.hazards
            and spec.fire_control != "none"
        ):
            raise PromptConsistencyError(
                "space shooter prompts require a ship, enemies, and a fire control"
            )
        if requested_type == "racing" and not (
            spec.theme == "road"
            and spec.player.appearance == "car"
            and spec.player.movement_mode == "racing"
            and level.obstacles
        ):
            raise PromptConsistencyError(
                "racing prompts require a car, road theme, and obstacles"
            )

    @staticmethod
    def _validate_plan(plan: GamePlan, spec: GameSpec) -> None:
        for entity in plan.entities:
            exists = any(
                (entity == "zombie" and any(
                    hazard.kind == "zombie" for hazard in level.hazards
                ))
                or (entity == "building" and any(
                    scenery.kind == "building" for scenery in level.scenery
                ))
                for level in spec.levels
            )
            if not exists:
                raise PromptConsistencyError(
                    f"Planner-required entity '{entity}' is missing from the game."
                )
        if "projectiles" in plan.mechanics and spec.fire_control == "none":
            raise PromptConsistencyError(
                "The planned shooting mechanic requires a configured fire control."
            )
        if "chasing_enemies" in plan.mechanics and not any(
            hazard.movement == "chasing"
            for level in spec.levels
            for hazard in level.hazards
        ):
            raise PromptConsistencyError(
                "The planned enemy chase mechanic requires a chasing hazard."
            )
        if plan.genre_intent == "racing" and spec.game_type != "racing":
            raise PromptConsistencyError(
                "Racing intent must remain a racing game, not be reclassified."
            )
        if plan.genre_intent == "top_down_survival" and not (
            spec.camera == "top_down"
            and spec.player.movement_mode == "four_direction"
            and spec.win_condition.type == "survive"
            and spec.lose_condition.type == "health_depleted"
        ):
            raise PromptConsistencyError(
                "Zombie survival requires top-down movement, a survival objective, "
                "and health-based loss."
            )
