import hashlib
import json
import re

from pydantic import ValidationError

from app.agents.game_builder import GameBuilderAgent, GameRuntimeConfig
from app.agents.game_qa import GameQAAgent, QAReport
from app.models import GameSpec, RepairChange, RepairPatch


class GameRepairAgent:
    """Apply a small, deterministic allowlist of safe specification repairs."""

    def __init__(
        self,
        builder: GameBuilderAgent | None = None,
        qa: GameQAAgent | None = None,
    ) -> None:
        self.builder = builder or GameBuilderAgent()
        self.qa = qa or GameQAAgent()

    def repair_with_patch(
        self,
        spec: GameSpec,
        findings: QAReport,
        *,
        attempt: int,
        affected_requirement_ids: list[str] | None = None,
    ) -> tuple[GameSpec, RepairPatch]:
        before_data = spec.model_dump(mode="json")
        repaired_data = spec.model_dump(mode="json")
        changes: list[RepairChange] = []
        issue_codes = {issue.code for issue in findings.issues}

        if "WIN_TARGET_MISSING" in issue_codes:
            if spec.win_condition.type == "collect_target":
                count = sum(len(level.collectibles) for level in spec.levels)
                if count > 0:
                    self._set_number(
                        repaired_data,
                        "win_condition.target",
                        None,
                        count,
                        changes,
                    )
            elif spec.win_condition.type == "defeat_target":
                count = sum(
                    len(level.enemies) + len(level.hazards) for level in spec.levels
                )
                if count > 0:
                    self._set_number(
                        repaired_data,
                        "win_condition.target",
                        None,
                        count,
                        changes,
                    )

        if "PLATFORM_OUT_OF_BOUNDS" in issue_codes:
            for level_index, level in enumerate(repaired_data["levels"]):
                for platform_index, platform in enumerate(level["platforms"]):
                    for axis, dimension in (("x", "width"), ("y", "height")):
                        maximum = 1 - platform[dimension]
                        previous = platform[axis]
                        if previous > maximum:
                            platform[axis] = maximum
                            changes.append(
                                RepairChange(
                                    path=f"levels.{level_index}.platforms.{platform_index}.{axis}",
                                    previous_value=previous,
                                    new_value=maximum,
                                )
                            )

        landing_repair = self._platform_landing_repair(spec, findings)
        if landing_repair is not None:
            level_index, source_index, target_index, delta = landing_repair
            level = repaired_data["levels"][level_index]
            target = level["platforms"][target_index]
            original_target = target.copy()
            previous_x = target["x"]
            target["x"] = round(previous_x + delta, 6)
            changes.append(
                RepairChange(
                    path=f"levels.{level_index}.platforms.{target_index}.x",
                    previous_value=previous_x,
                    new_value=target["x"],
                )
            )
            for collectible_index, collectible in enumerate(level["collectibles"]):
                position = collectible["position"]
                if self._is_platform_anchored(position, original_target):
                    previous = position["x"]
                    position["x"] = round(previous + delta, 6)
                    changes.append(
                        RepairChange(
                            path=f"levels.{level_index}.collectibles.{collectible_index}.position.x",
                            previous_value=previous,
                            new_value=position["x"],
                        )
                    )
            goal = level.get("goal")
            if goal is not None and self._is_platform_anchored(
                goal["position"], original_target
            ):
                previous = goal["position"]["x"]
                goal["position"]["x"] = round(previous + delta, 6)
                changes.append(
                    RepairChange(
                        path=f"levels.{level_index}.goal.position.x",
                        previous_value=previous,
                        new_value=goal["position"]["x"],
                    )
                )

        before_digest = self._digest(before_data)
        try:
            repaired_spec = GameSpec.model_validate(repaired_data)
        except ValidationError as error:
            patch = self._patch(
                attempt=attempt,
                before_digest=before_digest,
                after_digest=before_digest,
                status="rejected",
                changes=[],
                affected_requirement_ids=affected_requirement_ids or [],
                reason=f"Repair output failed GameSpec validation: {error.error_count()} validation error(s).",
            )
            return spec, patch

        after_digest = self._digest(repaired_spec.model_dump(mode="json"))
        applied_changes = [
            change
            for change in changes
            if self._read_number(repaired_spec, change.path) == change.new_value
        ]
        if after_digest == before_digest or not applied_changes:
            return spec, self._patch(
                attempt=attempt,
                before_digest=before_digest,
                after_digest=before_digest,
                status="no_change",
                changes=[],
                affected_requirement_ids=affected_requirement_ids or [],
                reason="No supported deterministic repair applies to these findings.",
            )

        patch = self._patch(
            attempt=attempt,
            before_digest=before_digest,
            after_digest=after_digest,
            status="applied",
            changes=applied_changes,
            affected_requirement_ids=affected_requirement_ids or [],
            reason=(
                "Diagnosed an observed failed static-platform transition; moved only "
                "the planned static destination platform toward its source within "
                "the 0.08-world-unit limit, carried anchored objectives with it, "
                "and revalidated GameSpec."
                if landing_repair is not None
                else "Applied allowlisted structural corrections and revalidated GameSpec."
            ),
        )
        return repaired_spec, patch

    @staticmethod
    def _platform_landing_repair(
        spec: GameSpec, findings: QAReport
    ) -> tuple[int, int, int, float] | None:
        if not any(
            issue.code == "PLAYTEST_PLATFORMER_PLATFORM_LANDINGS"
            for issue in findings.issues
        ):
            return None
        report = findings.playtest_report
        if report is None:
            return None
        landing_test = next(
            (
                test
                for test in report.tests
                if test.required
                and test.status == "FAIL"
                and test.name == "platformer_platform_landings"
            ),
            None,
        )
        if landing_test is None:
            return None
        match = re.fullmatch(
            r"Observed platform transitions: "
            r"(?P<source>\d+)->(?P<actual>None|\d+) "
            r"\(planned (?P<target>\d+); player_x=(?P<player_x>\d+); "
            r"player_y=(?P<player_y>\d+); grounded=(?P<grounded>True|False); "
            r"game_state=(?P<game_state>playing|won|lost)\)\.",
            landing_test.actual_behavior,
        )
        if match is None:
            return None
        level_index = 0
        source_index = int(match.group("source"))
        target_index = int(match.group("target"))
        actual_index = (
            None if match.group("actual") == "None" else int(match.group("actual"))
        )
        level = spec.levels[level_index]
        if (
            source_index == target_index
            or source_index >= len(level.platforms)
            or target_index >= len(level.platforms)
        ):
            return None
        failed_below_world = (
            actual_index is None
            and match.group("grounded") == "False"
            and match.group("game_state") == "playing"
            and float(match.group("player_y")) > spec.world_height + 50
        )
        fell_below_platforms = (
            actual_index is None
            and match.group("grounded") == "True"
            and match.group("game_state") == "playing"
            and float(match.group("player_y")) >= spec.world_height - 24
        )
        returned_to_source = (
            actual_index == source_index
            and match.group("grounded") == "True"
            and match.group("game_state") == "playing"
        )
        if not (
            failed_below_world or fell_below_platforms or returned_to_source
        ):
            return None
        source = level.platforms[source_index]
        target = level.platforms[target_index]
        if (
            source.movement != "static"
            or target.movement != "static"
            or target.width < 0.05
        ):
            return None
        source_left = source.x
        source_right = source.x + source.width
        target_left = target.x
        target_right = target.x + target.width
        if target_left >= source_right:
            delta = -min(0.08, target_left - source_right)
        elif target_right <= source_left:
            delta = min(0.08, source_left - target_right)
        else:
            return None
        if abs(delta) < 0.01:
            return None
        new_x = target.x + delta
        if not 0 <= new_x <= 1 - target.width:
            return None
        return level_index, source_index, target_index, delta

    @staticmethod
    def _is_platform_anchored(position: dict, platform: dict) -> bool:
        return (
            platform["x"] - 0.02
            <= position["x"]
            <= platform["x"] + platform["width"] + 0.02
            and platform["y"] - 0.1 <= position["y"] <= platform["y"]
        )

    def repair(
        self, spec: GameSpec, findings: QAReport
    ) -> tuple[GameSpec, GameRuntimeConfig, QAReport]:
        repaired_spec, _ = self.repair_with_patch(spec, findings, attempt=1)
        runtime = self.builder.build(repaired_spec)
        return repaired_spec, runtime, self.qa.inspect(runtime)

    @staticmethod
    def _set_number(
        data: dict,
        path: str,
        previous: int | None,
        new: int,
        changes: list[RepairChange],
    ) -> None:
        parent = data
        path_parts = path.split(".")
        for part in path_parts[:-1]:
            parent = parent[part]
        parent[path_parts[-1]] = new
        changes.append(
            RepairChange(
                path=path,
                previous_value=previous,
                new_value=float(new),
            )
        )

    @staticmethod
    def _read_number(spec: GameSpec, path: str) -> float:
        current: object = spec.model_dump()
        for part in path.split("."):
            current = current[int(part)] if isinstance(current, list) else current[part]
        return float(current)

    @staticmethod
    def _digest(data: dict) -> str:
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _patch(
        cls,
        *,
        attempt: int,
        before_digest: str,
        after_digest: str,
        status: str,
        changes: list[RepairChange],
        affected_requirement_ids: list[str],
        reason: str,
    ) -> RepairPatch:
        patch_id = "repair-" + hashlib.sha256(
            f"{before_digest}:{after_digest}:{attempt}".encode("utf-8")
        ).hexdigest()[:12]
        return RepairPatch(
            patch_id=patch_id,
            attempt=attempt,
            status=status,
            before_digest=before_digest,
            after_digest=after_digest,
            changes=changes,
            affected_requirement_ids=affected_requirement_ids,
            reason=reason,
        )
