import asyncio
import json
import logging
import os
import random
import re
import time
from collections import OrderedDict
from hashlib import sha256
from typing import Any

import httpx
from google.genai.errors import APIError

from app.models import (
    CollectibleSpec,
    EnemySpec,
    GameSpec,
    LevelSpec,
    MovingHazardSpec,
    ObstacleSpec,
    PaletteSpec,
    PlatformSpec,
    Point,
    PlayerSpec,
    WinCondition,
    LoseCondition,
    GoalSpec,
    TargetSpec,
    WallSpec,
    ScenerySpec,
)

logger = logging.getLogger(__name__)


class _RetryHelpers:
    @staticmethod
    def _retry_delay(attempt: int, retry_after: float | None) -> float:
        base = retry_after if retry_after is not None else (2.0, 5.0, 10.0)[
            min(attempt, 2)
        ]
        return base + random.uniform(0.0, 1.0)

    @staticmethod
    def _sleep_for(delay: float, deadline: float | None = None) -> None:
        if deadline is not None:
            delay = min(delay, max(0.0, deadline - time.monotonic()))
        if delay <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            time.sleep(delay)
            return
        loop.run_in_executor(None, time.sleep, delay).result()

    @staticmethod
    def _parse_retry_after(value: Any | None) -> float | None:
        headers = value if hasattr(value, "get") else getattr(value, "headers", None)
        if headers is None:
            headers = getattr(getattr(value, "response", None), "headers", None)
        if headers is None:
            return None
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after is None:
            return None
        try:
            return max(float(retry_after), 0.0)
        except (TypeError, ValueError):
            return None


def groq_game_spec_schema() -> dict[str, Any]:
    """Return a strict JSON Schema compatible with Groq structured outputs."""
    source = GameSpec.model_json_schema()
    definitions = source.get("$defs", {})
    supported_keywords = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "anyOf",
    }

    def transform(schema: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in schema:
            return transform(definitions[schema["$ref"].rsplit("/", 1)[-1]])
        result = {
            key: schema[key]
            for key in supported_keywords
            if key in schema and key not in {"properties", "items", "anyOf"}
        }
        if "const" in schema:
            result["enum"] = [schema["const"]]
        if schema.get("type") == "object":
            properties = {
                name: transform(value)
                for name, value in schema.get("properties", {}).items()
            }
            result["type"] = "object"
            result["properties"] = properties
            result["required"] = list(properties)
            result["additionalProperties"] = False
        elif schema.get("type") == "array" and "items" in schema:
            result["items"] = transform(schema["items"])
        elif "anyOf" in schema:
            result["anyOf"] = [
                transform(item) if isinstance(item, dict) else item
                for item in schema["anyOf"]
            ]
        return result

    return transform(source)


class GeminiProviderError(RuntimeError):
    """A safe, non-secret error raised when Gemini cannot serve a request."""

    def __init__(
        self,
        status_code: int,
        category: str,
        detail: str,
        *,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.category = category
        self.retry_after = retry_after


class GroqProviderError(RuntimeError):
    """A safe, non-secret error raised when Groq cannot serve a request."""

    def __init__(
        self,
        status_code: int,
        category: str,
        detail: str,
        *,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.category = category
        self.retry_after = retry_after


class DevelopmentMockProvider:
    """Development-only provider; it is not an LLM and does not call a model API."""

    name = "development-mock"

    def generate(self, prompt: str, mode: str) -> GameSpec:
        normalized = prompt.lower()
        if any(word in normalized for word in ("unsupported", "multiplayer", "crafting")):
            raise ValueError(
                "The development mock supports platform, top-down forest, maze, "
                "bird/flappy, space shooter, racing, collection, and arena mechanics. "
                "The requested mechanic is not supported by this mock provider."
            )

        zombie_game = bool(re.search(r"\bzombies?\b", normalized))
        arena = "arena" in normalized or "dodge" in normalized or "survive" in normalized
        if any(word in normalized for word in ("racing", "race", "racetrack")):
            game_type = "racing"
        elif zombie_game:
            game_type = "dodge_arena"
        elif any(word in normalized for word in ("maze", "labyrinth")):
            game_type = "maze"
        elif any(word in normalized for word in ("platform", "platformer", "jumping")):
            game_type = "platform_adventure"
        elif arena:
            game_type = "dodge_arena"
        elif any(word in normalized for word in ("flappy", "flap", "bird", "pipes")):
            game_type = "flappy_dodge"
        elif any(word in normalized for word in ("shooter", "space", "projectile", "laser")):
            game_type = "space_shooter"
        elif any(word in normalized for word in ("racing", "race", "car", "road")):
            game_type = "racing"
        elif any(word in normalized for word in ("target", "bullseye", "arcade")):
            game_type = "target_arcade"
        elif any(word in normalized for word in (
            "top-down", "top down", "overhead", "forest", "woods", "exploration"
        )):
            game_type = "top_down_collection"
        elif any(word in normalized for word in ("coin challenge", "coin rush", "timed collection")):
            game_type = "collection_challenge"
        else:
            game_type = "platform_adventure"
        title = self._title_from_prompt(prompt)
        description = prompt.strip()
        platforms = [
            PlatformSpec(x=0, y=0.9, width=1, height=0.1),
            PlatformSpec(x=0.12, y=0.72, width=0.24, height=0.04),
            PlatformSpec(x=0.48, y=0.56, width=0.22, height=0.04),
            PlatformSpec(x=0.73, y=0.74, width=0.2, height=0.04),
        ]
        collectibles = [
            CollectibleSpec(position=Point(x=0.2, y=0.64), value=10, kind="coin"),
            CollectibleSpec(position=Point(x=0.57, y=0.48), value=25, kind="gem"),
            CollectibleSpec(position=Point(x=0.82, y=0.66), value=10, kind="coin"),
        ]
        if game_type == "platform_adventure":
            collectibles = [
                item.model_copy(
                    update={
                        "position": Point(
                            x=platform.x + platform.width / 2,
                            y=platform.y - 0.04,
                        )
                    }
                )
                for item, platform in zip(collectibles, platforms[1:])
            ]
        requested_collectibles = self._requested_collectible_count(prompt)
        if requested_collectibles is not None:
            kind = self._requested_collectible_kind(prompt)
            usable_platforms = platforms[1:]
            per_platform = (requested_collectibles + len(usable_platforms) - 1) // len(usable_platforms)
            collectibles = []
            for index in range(requested_collectibles):
                platform_index = index % len(usable_platforms)
                slot = index // len(usable_platforms)
                platform = usable_platforms[platform_index]
                collectibles.append(
                    CollectibleSpec(
                        position=Point(
                            x=platform.x + platform.width * (slot + 1) / (per_platform + 1),
                            y=platform.y - 0.04,
                        ),
                        value=10,
                        kind=kind,
                    )
                )
        enemies = [
            EnemySpec(
                position=Point(x=0.38, y=0.84),
                damage=1,
                movement="horizontal" if not arena else "vertical",
            )
        ]
        if game_type == "platform_adventure" and re.search(r"\benemies?\b", normalized):
            enemy_count = self._requested_enemy_count(prompt) or 2
            enemies = [
                EnemySpec(
                    position=Point(
                        x=0.2 + 0.6 * (index + 0.5) / enemy_count,
                        y=0.84,
                    ),
                    damage=1,
                    movement="horizontal",
                )
                for index in range(enemy_count)
            ]
        level = LevelSpec(
            id="level-1",
            name="First Run",
            background="#172033" if not arena else "#24172f",
            platforms=platforms,
            collectibles=collectibles,
            enemies=enemies,
            collectible_target=len(collectibles),
        )
        if game_type == "dodge_arena":
            win_condition = WinCondition(type="survive", target=30)
            lose_condition = LoseCondition(type="time_expired", target=45)
        elif game_type in {"maze", "target_arcade"}:
            win_condition = WinCondition(type="reach_goal")
            lose_condition = LoseCondition(type="health_depleted")
        elif game_type == "platform_adventure" and re.search(
            r"\b(?:finish(?:\s+flag)?|flag|reach\s+(?:the\s+)?(?:goal|exit)|exit)\b",
            normalized,
        ):
            win_condition = WinCondition(type="reach_goal")
            lose_condition = LoseCondition(type="health_depleted")
        elif game_type == "space_shooter":
            win_condition = WinCondition(
                type="defeat_target",
                target=self._requested_enemy_count(prompt) or 8,
            )
            lose_condition = LoseCondition(type="health_depleted")
        elif game_type in {"flappy_dodge", "racing"}:
            win_condition = WinCondition(type="survive", target=30)
            lose_condition = LoseCondition(
                type="time_expired" if game_type == "racing" else "health_depleted",
                target=60 if game_type == "racing" else None,
            )
        else:
            win_condition = WinCondition(type="collect_target", target=len(collectibles))
            lose_condition = LoseCondition(type="health_depleted")
        goal_requested = bool(
            re.search(r"\b(?:finish(?:\s+flag)?|flag|goal|exit)\b", normalized)
        )
        goal = (
            GoalSpec(
                position=Point(x=0.9, y=0.15),
                kind="exit" if "flag" in normalized or "exit" in normalized else "chest",
            )
            if goal_requested
            else None
        )
        if game_type == "platform_adventure" and goal is not None:
            final_platform = platforms[-1]
            goal = goal.model_copy(
                update={
                    "position": Point(
                        x=final_platform.x + final_platform.width * 0.8,
                        y=final_platform.y - 0.04,
                    )
                }
            )
        walls = []
        targets = []
        time_limit = None
        camera = "side_scroller"
        theme = "neon"
        palette = PaletteSpec()
        fire_control = "none"
        player = PlayerSpec()
        scenery = []
        world_width = 800
        world_height = 480
        if game_type == "maze":
            camera = "top_down"
            player = PlayerSpec(movement_mode="four_direction", gravity=0)
            theme = "maze"
            palette = PaletteSpec(
                background="#111827",
                primary="#334155",
                secondary="#475569",
                accent="#facc15",
                player="#67e8f9",
                enemy="#fb7185",
                obstacle="#8d99ae",
                collectible="#facc15",
                wall="#64748b",
            )
            walls = [
                WallSpec(position=Point(x=0.25, y=0.15), width=0.05, height=0.55),
                WallSpec(position=Point(x=0.5, y=0.3), width=0.05, height=0.55),
                WallSpec(position=Point(x=0.75, y=0.15), width=0.05, height=0.55),
            ]
            goal = GoalSpec(position=Point(x=0.9, y=0.85), kind="exit")
        elif game_type == "target_arcade":
            targets = [
                TargetSpec(position=Point(x=0.25, y=0.3), points=10),
                TargetSpec(position=Point(x=0.5, y=0.55), points=20),
                TargetSpec(position=Point(x=0.75, y=0.3), points=10),
            ]
            win_condition = WinCondition(type="collect_target", target=len(targets))
        elif game_type == "collection_challenge":
            time_limit = 60
            lose_condition = LoseCondition(type="time_expired", target=time_limit)
        elif game_type == "top_down_collection":
            goal = GoalSpec(position=Point(x=0.9, y=0.15), kind="chest")
            camera = "top_down"
            theme = "forest"
            world_width = 1600
            world_height = 960
            palette = PaletteSpec(
                background="#173b2b",
                primary="#2d6a4f",
                secondary="#74a96a",
                accent="#b7e4c7",
                player="#f4d35e",
                enemy="#d95d39",
                obstacle="#70543e",
                collectible="#8be9fd",
                wall="#3f6b4b",
            )
            player = PlayerSpec(
                movement_mode="four_direction",
                appearance="explorer",
                gravity=0,
            )
            scenery = [
                ScenerySpec(
                    position=Point(x=0.08, y=0.12),
                    width=0.1,
                    height=0.16,
                    kind="tree",
                    solid=True,
                ),
                ScenerySpec(
                    position=Point(x=0.68, y=0.18),
                    width=0.12,
                    height=0.14,
                    kind="bush",
                    solid=True,
                ),
                ScenerySpec(
                    position=Point(x=0.12, y=0.76),
                    width=0.72,
                    height=0.08,
                    kind="path",
                ),
                ScenerySpec(
                    position=Point(x=0.39, y=0.17),
                    width=0.1,
                    height=0.15,
                    kind="tree",
                    solid=True,
                ),
                ScenerySpec(
                    position=Point(x=0.78, y=0.64),
                    width=0.1,
                    height=0.15,
                    kind="tree",
                    solid=True,
                ),
                ScenerySpec(
                    position=Point(x=0.46, y=0.54),
                    width=0.18,
                    height=0.09,
                    kind="water",
                    solid=True,
                ),
                ScenerySpec(
                    position=Point(x=0.06, y=0.3),
                    width=0.08,
                    height=0.04,
                    kind="grass",
                ),
                ScenerySpec(
                    position=Point(x=0.72, y=0.39),
                    width=0.07,
                    height=0.05,
                    kind="flower",
                ),
            ]
        elif game_type == "platform_adventure" and any(
            word in normalized for word in ("forest", "woods", "jungle")
        ):
            theme = "forest"
            palette = PaletteSpec(
                background="#a8d889",
                primary="#558b3f",
                secondary="#276749",
                accent="#b7e4c7",
                player="#f4d35e",
                enemy="#d95d39",
                obstacle="#70543e",
                collectible="#ffd166",
                wall="#3f6b4b",
            )
            scenery = [
                ScenerySpec(position=Point(x=0.02, y=0.44), width=0.11, height=0.18, kind="tree"),
                ScenerySpec(position=Point(x=0.4, y=0.32), width=0.12, height=0.2, kind="tree"),
                ScenerySpec(position=Point(x=0.86, y=0.45), width=0.11, height=0.18, kind="tree"),
                ScenerySpec(position=Point(x=0.1, y=0.84), width=0.08, height=0.05, kind="grass"),
                ScenerySpec(position=Point(x=0.64, y=0.83), width=0.07, height=0.05, kind="flower"),
            ]
        elif game_type == "flappy_dodge":
            camera = "side_scroller"
            theme = "sky"
            palette = PaletteSpec(
                background="#79c9f2",
                primary="#9bdcf5",
                secondary="#d8f3ff",
                accent="#ffd166",
                player="#ffbd35",
                enemy="#ef476f",
                obstacle="#2f9e62",
                collectible="#ffffff",
                wall="#47875d",
            )
            player = PlayerSpec(
                start=Point(x=0.2, y=0.48),
                movement_mode="flap",
                appearance="bird",
                gravity=700,
                flap_strength=220,
            )
            scenery = [
                ScenerySpec(
                    position=Point(x=0.15, y=0.12),
                    width=0.16,
                    height=0.08,
                    kind="cloud",
                ),
                ScenerySpec(
                    position=Point(x=0.72, y=0.2),
                    width=0.12,
                    height=0.06,
                    kind="cloud",
                ),
            ]
            obstacles = [
                ObstacleSpec(
                    position=Point(x=0.68, y=0.08),
                    width=0.1,
                    height=0.28,
                    kind="pipe",
                ),
                ObstacleSpec(
                    position=Point(x=0.68, y=0.72),
                    width=0.1,
                    height=0.22,
                    kind="pipe",
                ),
            ]
            level = level.model_copy(update={"obstacles": obstacles})
        elif game_type == "space_shooter":
            camera = "top_down"
            theme = "space"
            palette = PaletteSpec(
                background="#080d25",
                primary="#101b46",
                secondary="#263b79",
                accent="#f8d66d",
                player="#54e5ff",
                enemy="#ff5277",
                obstacle="#6978c8",
                collectible="#a78bfa",
                wall="#374151",
            )
            fire_control = "space"
            player = PlayerSpec(
                start=Point(x=0.5, y=0.84),
                movement_mode="four_direction",
                appearance="ship",
                speed=280,
                gravity=0,
            )
            hazards = [
                MovingHazardSpec(
                    position=Point(x=0.75, y=0.25),
                    width=0.06,
                    height=0.06,
                    kind="enemy",
                    health=2,
                    movement="vertical",
                    speed=80,
                ),
                MovingHazardSpec(
                    position=Point(x=0.38, y=0.16),
                    width=0.055,
                    height=0.055,
                    kind="enemy",
                    health=1,
                    movement="horizontal",
                    speed=55,
                ),
                MovingHazardSpec(
                    position=Point(x=0.58, y=0.42),
                    width=0.05,
                    height=0.05,
                    kind="enemy",
                    health=1,
                    movement="vertical",
                    speed=70,
                ),
            ]
            level = level.model_copy(update={"hazards": hazards})
        elif game_type == "racing":
            camera = "racing"
            theme = "road"
            palette = PaletteSpec(
                background="#4b9257",
                primary="#30363d",
                secondary="#49515a",
                accent="#f5d547",
                player="#f05252",
                enemy="#f97316",
                obstacle="#f5f5f4",
                collectible="#fef08a",
                wall="#e5e7eb",
            )
            player = PlayerSpec(
                start=Point(x=0.5, y=0.82),
                movement_mode="racing",
                appearance="car",
                speed=260,
            )
            obstacles = [
                ObstacleSpec(
                    position=Point(x=0.55, y=0.35),
                    width=0.08,
                    height=0.12,
                    kind="cone",
                    damage=1,
                )
            ]
            time_limit = 60
            scenery = [
                ScenerySpec(
                    position=Point(x=0.08, y=0),
                    width=0.08,
                    height=1,
                    kind="roadside",
                ),
                ScenerySpec(
                    position=Point(x=0.84, y=0),
                    width=0.08,
                    height=1,
                    kind="roadside",
                ),
            ]
            level = level.model_copy(update={"obstacles": obstacles, "time_limit": time_limit})
        if zombie_game:
            theme = "neon"
            camera = "top_down"
            world_width = 800
            world_height = 480
            palette = PaletteSpec(
                background="#242b30",
                primary="#3b4b40",
                secondary="#69756a",
                accent="#e6c66a",
                player="#e5c58a",
                enemy="#86a86e",
                obstacle="#68594c",
                collectible="#d1ad63",
                wall="#20272a",
            )
            player = PlayerSpec(
                start=Point(x=0.5, y=0.55),
                movement_mode="four_direction",
                appearance="explorer",
                speed=220,
                max_health=5,
                gravity=0,
                fire_cooldown=300,
            )
            fire_control = "space"
            win_condition = WinCondition(type="survive", target=45)
            lose_condition = LoseCondition(type="health_depleted")
            time_limit = 45
            scenery = [
                ScenerySpec(position=Point(x=0.18, y=0.2), width=0.2, height=0.22, kind="building"),
                ScenerySpec(position=Point(x=0.78, y=0.18), width=0.18, height=0.24, kind="building"),
                ScenerySpec(position=Point(x=0.18, y=0.78), width=0.2, height=0.2, kind="building"),
                ScenerySpec(position=Point(x=0.78, y=0.75), width=0.18, height=0.22, kind="building"),
            ]
            zombies = [
                MovingHazardSpec(
                    position=Point(x=0.27, y=0.38), width=0.045, height=0.055,
                    kind="zombie", health=2, movement="chasing", speed=62,
                ),
                MovingHazardSpec(
                    position=Point(x=0.75, y=0.48), width=0.045, height=0.055,
                    kind="zombie", health=2, movement="chasing", speed=58,
                ),
                MovingHazardSpec(
                    position=Point(x=0.38, y=0.83), width=0.045, height=0.055,
                    kind="zombie", health=2, movement="chasing", speed=66,
                ),
            ]
            level = level.model_copy(
                update={
                    "background": palette.background,
                    "scenery": scenery,
                    "hazards": zombies,
                    "enemies": [],
                    "collectibles": [],
                    "collectible_target": 0,
                    "goal": None,
                    "time_limit": time_limit,
                }
            )
        level = level.model_copy(
            update={
                "background": palette.background,
                "walls": walls,
                "targets": targets,
                "scenery": scenery,
                "goal": goal,
                "time_limit": time_limit,
                "collectible_target": (
                    0
                    if zombie_game
                    else requested_collectibles
                    if game_type == "platform_adventure"
                    and win_condition.type == "reach_goal"
                    and requested_collectibles is not None
                    else len(collectibles)
                ),
            }
        )
        if game_type in {"flappy_dodge", "space_shooter", "racing"}:
            level = level.model_copy(
                update={
                    "collectibles": [],
                    "collectible_target": 0,
                    "enemies": [],
                    "goal": None,
                }
            )
        return GameSpec(
            title=title,
            description=description,
            game_type=game_type,
            camera=camera,
            theme=theme,
            palette=palette,
            world_width=world_width,
            world_height=world_height,
            fire_control=fire_control,
            player=player.model_copy(
                update={"max_health": 5 if mode == "beginner" else player.max_health}
            ),
            levels=[level],
            win_condition=win_condition,
            lose_condition=lose_condition,
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
        matches = re.findall(
            r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
            r"\s+(?:\w+\s+){0,2}(?:coins?|gems?|stars?|collectibles?)\b",
            prompt.lower(),
        )
        if not matches:
            return None
        value = matches[-1]
        return int(value) if value.isdigit() else number_words[value]

    @staticmethod
    def _requested_collectible_kind(prompt: str) -> str:
        normalized = prompt.lower()
        for kind in ("coin", "gem", "star"):
            if re.search(rf"\b{kind}s?\b", normalized):
                return kind
        return "coin"

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
        matches = re.findall(
            r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
            r"\s+(?:\w+\s+){0,2}(?:enemies|enemy|ships|drones|foes)\b",
            prompt.lower(),
        )
        if not matches:
            return None
        value = matches[-1]
        return int(value) if value.isdigit() else number_words[value]

    @staticmethod
    def _title_from_prompt(prompt: str) -> str:
        title = re.sub(
            r"^\s*(?:create|make|build|design)\s+(?:me\s+)?(?:a|an|the)\s+",
            "",
            prompt.strip(),
            flags=re.IGNORECASE,
        )
        title = re.split(r"[.!?]", title, maxsplit=1)[0].strip()
        if not title:
            title = prompt.strip()
        suffix = sha256(prompt.strip().lower().encode("utf-8")).hexdigest()[:4]
        return f"{title[:72].rstrip()} {suffix}"[:80]


class GeminiProvider(_RetryHelpers):
    """Gemini adapter using backend-only environment configuration."""

    name = "gemini"

    cache_ttl_seconds = 600.0
    cache_max_entries = 128

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        fallback_model: str | None = None,
    ) -> None:
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is required when GAMEIONX_PROVIDER=gemini.")
        self.model = (
            model or os.getenv("GEMINI_MODEL") or "gemini-2.0-flash-lite"
        )
        fallback_value = (
            fallback_model
            or os.getenv("GEMINI_FALLBACK_MODEL")
            or "gemini-2.0-flash-lite"
        )
        self.fallback_model = fallback_value or None
        if self.fallback_model == self.model:
            self.fallback_model = None
        self._prompt_cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=60_000),
        )

    @staticmethod
    def _normalize_prompt(prompt: str) -> str:
        return re.sub(r"\s+", " ", prompt or "").strip().lower()

    @staticmethod
    def _cache_buster(*, difficulty: str | None, game_style: str | None) -> str:
        return "|".join(
            [
                difficulty or "",
                game_style or "",
            ]
        )

    def _cache_key(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any],
        difficulty: str | None = None,
        game_style: str | None = None,
        model_name: str | None = None,
    ) -> str:
        schema_digest = sha256(
            json.dumps(response_schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        return f"{model_name or self.model}:{self._normalize_prompt(prompt)}|{difficulty or ''}|{game_style or ''}|{schema_digest}"

    def _cache_get(self, key: str) -> str | None:
        value = self._prompt_cache.get(key)
        if value is None:
            return None
        cached_text, cached_at = value
        if time.monotonic() - cached_at > self.cache_ttl_seconds:
            self._prompt_cache.pop(key, None)
            return None
        self._prompt_cache.move_to_end(key)
        return cached_text

    def _cache_set(self, *, key: str, value: str) -> None:
        self._prompt_cache[key] = (value, time.monotonic())
        self._prompt_cache.move_to_end(key)
        while len(self._prompt_cache) > self.cache_max_entries:
            self._prompt_cache.popitem(last=False)

    def _log_call(self, *, provider: str, model: str, status: str, attempt: int, pipeline_step: str, error: Exception | None = None) -> None:
        details = f" error_type={type(error).__name__ if error is not None else 'none'}"
        logger.warning(
            "AI call provider=%s model=%s status=%s attempt=%d pipeline_step=%s%s",
            provider,
            model,
            status,
            attempt,
            pipeline_step,
            details,
        )

    def _generate_with_model(
        self,
        model_name: str,
        *,
        prompt: str,
        response_schema: dict[str, Any],
        pipeline_step: str = "designing",
        deadline: float | None = None,
    ) -> str:
        attempts = 0
        last_error: GeminiProviderError | None = None
        while attempts < 3:
            request_config: dict[str, Any] = {
                "response_mime_type": "application/json",
                "response_json_schema": response_schema,
            }
            if deadline is not None and time.monotonic() >= deadline:
                raise GeminiProviderError(
                    503,
                    "service_unavailable",
                    "The AI is busy right now. Please try again in a minute.",
                )
            if deadline is not None:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    raise GeminiProviderError(
                        503,
                        "service_unavailable",
                        "The AI is busy right now. Please try again in a minute.",
                    )
                request_config["http_options"] = self._types.HttpOptions(
                    timeout=min(60_000, remaining_ms)
                )
            try:
                response = self._client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=self._types.GenerateContentConfig(**request_config),
                )
            except (TimeoutError, httpx.TimeoutException) as error:
                detail = "Gemini did not respond before the request timeout. Try again later."
                last_error = GeminiProviderError(504, "timeout", detail)
                self._log_call(
                    provider=self.name,
                    model=model_name,
                    status="failed",
                    attempt=attempts + 1,
                    pipeline_step=pipeline_step,
                    error=error,
                )
                if attempts < 2 and (deadline is None or time.monotonic() < deadline):
                    delay = self._retry_delay(attempts, None)
                    logger.warning("Retrying Gemini timeout after %.1fs (model=%s, step=%s)", delay, model_name, pipeline_step)
                    self._sleep_for(delay, deadline)
                    attempts += 1
                    continue
                raise last_error from error
            except APIError as error:
                status_code, category, detail = self._map_api_error(error)
                retry_after = self._parse_retry_after(error)
                last_error = GeminiProviderError(
                    status_code,
                    category,
                    detail,
                    retry_after=retry_after,
                )
                self._log_call(
                    provider=self.name,
                    model=model_name,
                    status="failed",
                    attempt=attempts + 1,
                    pipeline_step=pipeline_step,
                    error=error,
                )
                if status_code in {500, 503, 429} and attempts < 2 and (deadline is None or time.monotonic() < deadline):
                    delay = self._retry_delay(attempts, retry_after)
                    logger.warning("Retrying Gemini after HTTP %s in %.1fs (model=%s, step=%s)", status_code, delay, model_name, pipeline_step)
                    self._sleep_for(delay, deadline)
                    attempts += 1
                    continue
                raise last_error from error
            except Exception as error:  # pragma: no cover - defensive guard for unexpected provider errors
                self._log_call(
                    provider=self.name,
                    model=model_name,
                    status="failed",
                    attempt=attempts + 1,
                    pipeline_step=pipeline_step,
                    error=error,
                )
                raise

            if deadline is not None and time.monotonic() >= deadline:
                raise GeminiProviderError(
                    503,
                    "service_unavailable",
                    "The AI generation deadline expired. Please try again.",
                )

            text = response.text
            if not text:
                raise ValueError("Gemini returned an empty response.")
            logger.info(
                "AI call provider=%s model=%s status=success attempt=%d pipeline_step=%s",
                self.name,
                model_name,
                attempts + 1,
                pipeline_step,
            )
            self._cache_set(key=self._cache_key(prompt=prompt, response_schema=response_schema, model_name=model_name), value=text)
            return text

        raise last_error or ValueError("Gemini request failed without a response.")

    def generate_content(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any],
        pipeline_step: str = "designing",
        difficulty: str | None = None,
        game_style: str | None = None,
        bypass_cache: bool = False,
        deadline_seconds: float = 90.0,
    ) -> str:
        cache = getattr(self, "_prompt_cache", OrderedDict())
        self._prompt_cache = (
            cache if isinstance(cache, OrderedDict) else OrderedDict(cache)
        )
        self.fallback_model = getattr(self, "fallback_model", None)
        normalized_prompt = self._normalize_prompt(prompt)
        cache_key = self._cache_key(
            prompt=prompt,
            response_schema=response_schema,
            difficulty=difficulty,
            game_style=game_style,
            model_name=self.model,
        )
        if not bypass_cache:
            cached = self._cache_get(cache_key)
            if cached is not None:
                logger.info(
                    "AI call provider=%s model=%s status=cache_hit prompt=%s pipeline_step=%s",
                    self.name,
                    self.model,
                    normalized_prompt[:80],
                    pipeline_step,
                )
                return cached

        models_to_try = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models_to_try.append(self.fallback_model)

        deadline = time.monotonic() + max(deadline_seconds, 1.0)
        last_error: GeminiProviderError | None = None
        for model_index, model_name in enumerate(models_to_try):
            if time.monotonic() >= deadline:
                if last_error is None:
                    last_error = GeminiProviderError(
                        503,
                        "service_unavailable",
                        "The AI generation deadline expired. Please try again.",
                    )
                break
            try:
                result = self._generate_with_model(
                    model_name,
                    prompt=prompt,
                    response_schema=response_schema,
                    pipeline_step=pipeline_step,
                    deadline=deadline,
                )
                self._cache_set(
                    key=self._cache_key(
                        prompt=prompt,
                        response_schema=response_schema,
                        difficulty=difficulty,
                        game_style=game_style,
                        model_name=model_name,
                    ),
                    value=result,
                )
                return result
            except GeminiProviderError as error:
                last_error = error
                self._log_call(
                    provider=self.name,
                    model=model_name,
                    status="fallback",
                    attempt=model_index + 1,
                    pipeline_step=pipeline_step,
                    error=error,
                )
                if (
                    model_index < len(models_to_try) - 1
                    and time.monotonic() < deadline
                ):
                    logger.warning(
                        "Falling back to Gemini model %s after failure on %s; pipeline_step=%s",
                        models_to_try[model_index + 1],
                        model_name,
                        pipeline_step,
                    )
                    continue
                break

        if (
            last_error is not None
            and last_error.status_code in {429, 500, 503}
            and time.monotonic() < deadline
        ):
            groq_key = (os.getenv("GROQ_API_KEY") or "").strip()
            if groq_key:
                logger.warning(
                    "Falling back to Groq after Gemini failures for pipeline_step=%s model=%s",
                    pipeline_step,
                    self.model,
                )
                try:
                    return GroqProvider(
                        api_key=groq_key,
                        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
                    ).generate_content(
                        prompt=prompt,
                        response_schema=response_schema,
                        difficulty=difficulty,
                        game_style=game_style,
                        bypass_cache=bypass_cache,
                        deadline_at=deadline,
                    )
                except GroqProviderError as groq_error:
                    logger.warning("Groq fallback also failed for pipeline_step=%s: %s", pipeline_step, groq_error)
                    raise last_error from groq_error
            logger.warning(
                "Skipping Groq fallback because GROQ_API_KEY is not configured. pipeline_step=%s",
                pipeline_step,
            )
            raise last_error from last_error

        raise last_error or ValueError("Gemini request failed without a response.")

    def _fallback_to_groq(
        self, prompt: str, response_schema: dict[str, Any]
    ) -> str:
        groq_key = (os.getenv("GROQ_API_KEY") or "").strip()
        if not groq_key:
            raise GeminiProviderError(
                503,
                "service_unavailable",
                "AI quota reached, please try again in a few minutes",
            )
        logger.warning("Falling back to Groq via OpenAI-compatible endpoint.")
        return GroqProvider(
            api_key=groq_key,
            model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
        ).generate_content(prompt=prompt, response_schema=response_schema)

    @staticmethod
    def _map_api_error(error: APIError) -> tuple[int, str, str]:
        code = error.code
        if code in {401, 403}:
            return (
                502,
                "authentication",
                "Gemini authentication failed. Check the backend Gemini credentials.",
            )
        if code == 404:
            return (
                502,
                "model_unavailable",
                "The configured Gemini model is unavailable for this account.",
            )
        if code == 429:
            return (
                429,
                "quota_or_rate_limit",
                "Gemini quota or rate limit reached. Wait and try again later.",
            )
        if code in {400, 413, 422}:
            return (
                502,
                "invalid_request",
                "Gemini rejected the generation request. Check the model or structured output configuration.",
            )
        if code in {500, 502, 503, 504}:
            return (
                503,
                "service_unavailable",
                "Gemini is temporarily unavailable or experiencing high demand. Try again later.",
            )
        return (
            502,
            "provider_error",
            f"Gemini request failed with status {code}.",
        )


class GroqProvider(_RetryHelpers):
    """Groq adapter using its OpenAI-compatible chat completions API."""

    name = "groq"
    endpoint = "https://api.groq.com/openai/v1/chat/completions"
    max_completion_tokens = 16_384

    def __init__(self, api_key: str, model: str = "openai/gpt-oss-20b") -> None:
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is required when GAMEIONX_PROVIDER=groq.")
        self.api_key = api_key
        self.model = model or "openai/gpt-oss-20b"

    def generate_content(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any],
        difficulty: str | None = None,
        game_style: str | None = None,
        bypass_cache: bool = False,
        deadline_seconds: float = 90.0,
        deadline_at: float | None = None,
    ) -> str:
        normalized_prompt = re.sub(r"\s+", " ", prompt or "").strip().lower()
        cache_key = f"groq:{self.model}:{normalized_prompt}|{difficulty or ''}|{game_style or ''}"
        if not bypass_cache:
            cached = self._lookup_cache(cache_key)
            if cached is not None:
                logger.info(
                    "AI call provider=%s model=%s status=cache_hit prompt=%s pipeline_step=designing",
                    self.name,
                    self.model,
                    prompt[:80],
                )
                return cached
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": self.max_completion_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "gamespec",
                    "strict": True,
                    "schema": groq_game_spec_schema(),
                },
            },
        }
        deadline = (
            deadline_at
            if deadline_at is not None
            else time.monotonic() + max(deadline_seconds, 0.0)
        )

        for attempt in range(3):
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise GroqProviderError(503, "service_unavailable", "AI quota reached, please try again in a few minutes")
            try:
                response = httpx.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=min(60.0, remaining_seconds),
                )
                response.raise_for_status()
            except (TimeoutError, httpx.TimeoutException) as error:
                logger.warning(
                    "AI call provider=%s model=%s status=failed attempt=%d pipeline_step=designing error_type=%s",
                    self.name,
                    self.model,
                    attempt + 1,
                    type(error).__name__,
                )
                if attempt < 2 and time.monotonic() < deadline:
                    delay = self._retry_delay(attempt, None)
                    logger.warning("Retrying Groq timeout after %.1fs (model=%s)", delay, self.model)
                    self._sleep_for(delay, deadline)
                    continue
                raise GroqProviderError(
                    504,
                    "timeout",
                    "Groq did not respond within 60 seconds. Try again later.",
                ) from error
            except httpx.HTTPStatusError as error:
                status_code, category, detail = self._map_http_error(
                    error.response.status_code
                )
                retry_after = self._parse_retry_after(error.response.headers)
                if status_code in {429, 500, 503} and attempt < 2 and time.monotonic() < deadline:
                    delay = self._retry_delay(attempt, retry_after)
                    logger.warning(
                        "AI call provider=%s model=%s status=retrying attempt=%d pipeline_step=designing error_type=%s retry_after=%.1fs",
                        self.name,
                        self.model,
                        attempt + 1,
                        type(error).__name__,
                        delay,
                    )
                    self._sleep_for(delay, deadline)
                    continue
                error_code, error_message = self._safe_http_error_details(error.response)
                logger.warning(
                    "Groq request failed: status=%d category=%s provider_status=%d "
                    "error_code=%s error_message=%s retry_after=%s",
                    status_code,
                    category,
                    error.response.status_code,
                    error_code,
                    error_message,
                    retry_after,
                )
                raise GroqProviderError(
                    status_code,
                    category,
                    detail,
                    retry_after=retry_after,
                ) from error
            except httpx.ConnectError as error:
                if attempt < 2 and time.monotonic() < deadline:
                    delay = self._retry_delay(attempt, None)
                    logger.warning("Retrying Groq connect error after %.1fs (model=%s)", delay, self.model)
                    self._sleep_for(delay, deadline)
                    continue
                logger.warning("Groq request failed: status=502 category=connection_error")
                raise GroqProviderError(
                    502,
                    "connection_error",
                    "Groq connection failed before a response was received.",
                ) from error
            except httpx.ReadError as error:
                if attempt < 2 and time.monotonic() < deadline:
                    delay = self._retry_delay(attempt, None)
                    logger.warning("Retrying Groq read error after %.1fs (model=%s)", delay, self.model)
                    self._sleep_for(delay, deadline)
                    continue
                logger.warning("Groq request failed: status=502 category=read_error")
                raise GroqProviderError(
                    502,
                    "read_error",
                    "Groq response could not be read.",
                ) from error
            except httpx.WriteError as error:
                if attempt < 2 and time.monotonic() < deadline:
                    delay = self._retry_delay(attempt, None)
                    logger.warning("Retrying Groq write error after %.1fs (model=%s)", delay, self.model)
                    self._sleep_for(delay, deadline)
                    continue
                logger.warning("Groq request failed: status=502 category=write_error")
                raise GroqProviderError(
                    502,
                    "write_error",
                    "Groq request could not be sent.",
                ) from error
            except httpx.NetworkError as error:
                if attempt < 2 and time.monotonic() < deadline:
                    delay = self._retry_delay(attempt, None)
                    logger.warning("Retrying Groq network error after %.1fs (model=%s)", delay, self.model)
                    self._sleep_for(delay, deadline)
                    continue
                logger.warning("Groq request failed: status=502 category=network_error")
                raise GroqProviderError(
                    502,
                    "network_error",
                    "Groq network request failed before a response was received.",
                ) from error
            except httpx.HTTPError as error:
                if attempt < 2 and time.monotonic() < deadline:
                    delay = self._retry_delay(attempt, None)
                    logger.warning("Retrying Groq transport error after %.1fs (model=%s)", delay, self.model)
                    self._sleep_for(delay, deadline)
                    continue
                logger.warning("Groq request failed: status=502 category=transport_error")
                raise GroqProviderError(
                    502,
                    "transport_error",
                    "Groq transport failed before a response was received.",
                ) from error

            if time.monotonic() >= deadline:
                raise GroqProviderError(
                    503,
                    "service_unavailable",
                    "The AI generation deadline expired. Please try again.",
                )

            try:
                message = response.json()["choices"][0]["message"]
                text = message["content"]
            except (KeyError, IndexError, TypeError, ValueError) as error:
                raise ValueError("Groq returned an invalid chat completion response.") from error
            if not isinstance(text, str) or not text:
                raise ValueError("Groq returned an empty response.")
            logger.info(
                "AI call provider=%s model=%s status=success attempt=%d pipeline_step=designing",
                self.name,
                self.model,
                attempt + 1,
            )
            self._store_cache(cache_key, text)
            return text

        raise GroqProviderError(
            503,
            "service_unavailable",
            "Groq could not complete the request after retries.",
        )

    @staticmethod
    def _lookup_cache(key: str) -> str | None:
        expires = getattr(GroqProvider, "_cache_store", {})
        payload = expires.get(key)
        if payload is None:
            return None
        value, expires_at = payload
        if time.monotonic() > expires_at:
            expires.pop(key, None)
            return None
        return value

    @staticmethod
    def _store_cache(key: str, value: str) -> None:
        cache = getattr(GroqProvider, "_cache_store", None)
        if cache is None:
            cache = {}
            GroqProvider._cache_store = cache
        cache[key] = (value, time.monotonic() + 600)
        if len(cache) > 128:
            expired = [item for item, (_, expiry) in cache.items() if time.monotonic() > expiry]
            for item in expired:
                cache.pop(item, None)
            if len(cache) > 128:
                for key_to_drop in list(cache)[: len(cache) - 128]:
                    cache.pop(key_to_drop, None)

    @staticmethod
    def _safe_http_error_details(response: httpx.Response) -> tuple[str, str]:
        try:
            payload = response.json()
        except ValueError:
            return "unparseable", "Groq returned a non-JSON error response."

        error = payload.get("error", payload) if isinstance(payload, dict) else {}
        if not isinstance(error, dict):
            return "unknown", "Groq returned an invalid error payload."

        code = error.get("code") or error.get("type") or "unknown"
        message = error.get("message") or "Groq returned no error message."
        safe_message = re.sub(
            r"(?i)(bearer\s+|gsk_[A-Za-z0-9_-]+|api[_ -]?key\s*[:=]\s*\S+)",
            "[REDACTED]",
            str(message),
        )
        return str(code)[:80], safe_message[:240]

    @staticmethod
    def _map_http_error(
        status_code: int,
    ) -> tuple[int, str, str]:
        if status_code in {401, 403}:
            return (
                502,
                "authentication",
                "Groq authentication failed. Check the backend Groq credentials.",
            )
        if status_code == 404:
            return (
                502,
                "model_unavailable",
                "The configured Groq model or endpoint is unavailable.",
            )
        if status_code == 429:
            return (
                429,
                "quota_or_rate_limit",
                "Groq quota or rate limit reached. Wait and try again later.",
            )
        if status_code in {400, 413, 422}:
            return (
                502,
                "invalid_request",
                "Groq rejected the generation request. Check model structured-output support.",
            )
        if status_code in {500, 502, 503, 504}:
            return (
                503,
                "service_unavailable",
                "Groq is temporarily unavailable or experiencing high demand. Try again later.",
            )
        return (
            502,
            "provider_error",
            f"Groq request failed with status {status_code}.",
        )


def create_provider() -> DevelopmentMockProvider | GeminiProvider | GroqProvider:
    selected = os.getenv("GAMEIONX_PROVIDER", "development-mock").lower()
    if selected == "development-mock":
        return DevelopmentMockProvider()
    if selected == "gemini":
        return GeminiProvider(
            api_key=os.getenv("GEMINI_API_KEY", ""),
            model=os.getenv("GEMINI_MODEL") or "gemini-2.0-flash-lite",
        )
    if selected == "groq":
        return GroqProvider(
            api_key=os.getenv("GROQ_API_KEY", ""),
            model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
        )
    raise RuntimeError(
        f"Unsupported GAMEIONX_PROVIDER '{selected}'. "
        "Use 'development-mock', 'gemini', or 'groq'."
    )
