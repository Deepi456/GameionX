import json
import logging
import math
import os
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.models import PlaytestCase, PlaytestReport

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
PLAYTEST_ROUTE = "/__gameionx_playtest_spec__"
logger = logging.getLogger(__name__)


def _normalized_bounds(
    position: dict[str, Any],
    width: float,
    height: float,
    world_width: float,
    world_height: float,
) -> tuple[float, float, float, float]:
    left = position["x"] * world_width
    top = position["y"] * world_height
    return (
        left,
        top,
        left + width * world_width,
        top + height * world_height,
    )


class BrowserPlaytestRunner:
    """Run bounded observable checks against the existing Phaser preview in Chrome."""

    startup_timeout_seconds = 20
    scenario_timeout_seconds = 8

    def __init__(
        self,
        frontend_url: str | None = None,
        budget_seconds: float | None = None,
    ) -> None:
        self.frontend_url = (
            frontend_url or os.getenv("GAMEIONX_FRONTEND_URL", "http://127.0.0.1:5173")
        ).rstrip("/")
        self.budget_seconds = float(
            budget_seconds
            if budget_seconds is not None
            else os.getenv("PLAYTEST_BUDGET_SECONDS", "40")
        )
        if not math.isfinite(self.budget_seconds) or self.budget_seconds <= 0:
            raise ValueError("PLAYTEST_BUDGET_SECONDS must be a finite positive number.")
        self._deadline: float | None = None
        self._last_platformer_detail = ""
        self._last_platformer_landings: list[
            tuple[int, int, int | None, float, float, bool, str]
        ] = []

    def run(
        self,
        *,
        spec: dict[str, Any],
        runtime_config: dict[str, Any],
        game_definition: dict[str, Any] | None,
    ) -> PlaytestReport:
        server_process = None
        self._deadline = time.monotonic() + self.budget_seconds
        try:
            if self._budget_expired():
                return self._budget_expired_report([], runtime_config)
            if not self._frontend_ready():
                server_process = self._start_frontend()
            if self._budget_expired():
                return self._budget_expired_report([], runtime_config)
            return self._run_browser(
                spec=spec,
                runtime_config=runtime_config,
                game_definition=game_definition,
            )
        except Exception as error:
            logger.exception("Browser playtest failed unexpectedly.")
            if self._budget_expired():
                return self._budget_expired_report([], runtime_config)
            return self._unavailable_report(
                spec,
                runtime_config,
                f"Browser playtest could not complete: {str(error)[:700]}",
            )
        finally:
            if server_process is not None:
                server_process.terminate()
                try:
                    server_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server_process.kill()
                    server_process.wait(timeout=5)

    def _budget_expired(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    def _remaining_timeout_ms(self, cap_seconds: float) -> int:
        remaining = (
            cap_seconds
            if self._deadline is None
            else min(cap_seconds, self._deadline - time.monotonic())
        )
        return max(1, int(remaining * 1000))

    @staticmethod
    def _waypoint_stop_margin(route_name: str, distance: float) -> float:
        if route_name != "Top-down":
            return 2
        return min(12, distance / 2)

    def _budget_expired_report(
        self,
        tests: list[PlaytestCase],
        runtime_config: dict[str, Any],
    ) -> PlaytestReport:
        reason = "The overall browser playtest time budget was exceeded."
        candidates = [
            ("browser_load_without_runtime_errors", "The Phaser scene loads without browser runtime errors."),
            ("player_and_required_objects_render", "The player and configured game objects appear."),
            ("movement_controls_respond", "The configured movement controls move the player."),
        ]
        player = runtime_config.get("player", {})
        levels = runtime_config.get("levels", [])
        movement_mode = player.get("movement_mode")
        win_condition = runtime_config.get("win_condition")
        if movement_mode == "platformer":
            candidates.append(("platformer_jump", "Space triggers a jump while grounded."))
            candidates.append(("platformer_platform_landings", "The player lands on each planned platform."))
        if win_condition in {"collect_target", "reach_goal"} and any(
            level.get("collectibles") for level in levels
        ):
            candidates.append(
                ("collectible_collision_and_objective_progress", "Collect the configured target and update progress.")
            )
        if win_condition in {"collect_target", "reach_goal", "defeat_target", "survive"}:
            candidates.append(("win_condition_completion", f"Complete the {win_condition} objective."))
        if any(level.get("enemies") or level.get("hazards") for level in levels):
            candidates.append(("enemy_contact_collision", "Enemy contact applies the configured damage."))
        if runtime_config.get("lose_condition") == "time_expired":
            candidates.append(("lose_condition_time_expired", "The time limit enters the lost state."))
        elif (
            runtime_config.get("lose_condition") == "health_depleted"
            and any(
                level.get("enemies") or level.get("hazards") or level.get("obstacles")
                for level in levels
            )
        ):
            candidates.append(("game_over_and_restart", "Health depletion loses; restart returns to play."))

        existing_names = {test.name for test in tests}
        for name, expected in candidates:
            if name in existing_names:
                continue
            tests.append(
                PlaytestCase(
                    name=name,
                    expected_behavior=expected,
                    actual_behavior="NOT TESTED: the overall browser playtest time budget was exceeded.",
                    status="NOT TESTED",
                    failure_details=reason,
                )
            )
        return self._report(
            tests,
            browser="Chromium (Google Chrome; time budget exceeded)",
            runtime_tested=False,
        )

    def _run_browser(
        self,
        *,
        spec: dict[str, Any],
        runtime_config: dict[str, Any],
        game_definition: dict[str, Any] | None,
    ) -> PlaytestReport:
        if self._budget_expired():
            return self._budget_expired_report([], runtime_config)
        payload = {
            "spec": spec,
            "runtime_config": runtime_config,
            "game_definition": game_definition,
        }
        page_errors: list[str] = []
        tests: list[PlaytestCase] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel="chrome",
                headless=True,
                timeout=self._remaining_timeout_ms(self.startup_timeout_seconds),
            )
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.route(
                f"**{PLAYTEST_ROUTE}",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(payload),
                ),
            )
            try:
                if self._budget_expired():
                    return self._budget_expired_report(tests, runtime_config)
                page.set_default_timeout(
                    self._remaining_timeout_ms(self.scenario_timeout_seconds)
                )
                response = page.goto(
                    f"{self.frontend_url}/playtest.html",
                    wait_until="domcontentloaded",
                    timeout=self._remaining_timeout_ms(self.startup_timeout_seconds),
                )
                page.get_by_test_id("playtest-ready").wait_for(
                    timeout=self._remaining_timeout_ms(self.startup_timeout_seconds)
                )
                page.locator("canvas").wait_for(
                    timeout=self._remaining_timeout_ms(self.startup_timeout_seconds)
                )
                page.wait_for_function(
                    "() => document.querySelector('canvas')?.dataset.playerPresent === 'true'",
                    timeout=self._remaining_timeout_ms(self.startup_timeout_seconds),
                )
                if self._budget_expired():
                    return self._budget_expired_report(tests, runtime_config)
                canvas = page.locator("canvas")
                initial = self._snapshot(canvas)
                tests.append(
                    PlaytestCase(
                        name="browser_load_without_runtime_errors",
                        expected_behavior="The Phaser scene loads and produces no browser page errors.",
                        actual_behavior=(
                            f"HTTP {response.status if response else 'no response'}; "
                            f"canvas rendered; {len(page_errors)} page error(s)."
                        ),
                        status="FAIL" if page_errors or response is None or response.status >= 400 else "PASS",
                        failure_details="; ".join(page_errors)[:1000] if page_errors else None,
                    )
                )

                if self._budget_expired():
                    return self._budget_expired_report(tests, runtime_config)
                object_counts = self._object_counts(runtime_config)
                actual_counts = {
                    "player": int(initial.get("playerPresent") == "true"),
                    "collectibles": int(initial.get("collectibleObjects", "0")),
                    "enemies": int(initial.get("enemyObjects", "0")),
                    "targets": int(initial.get("targetObjects", "0")),
                    "goal": int(initial.get("goalPresent") == "true"),
                    "obstacles": int(initial.get("obstacleObjects", "0")),
                    "platforms": int(initial.get("platformObjects", "0")),
                    "walls": int(initial.get("wallObjects", "0")),
                    "scenery": int(initial.get("sceneryObjects", "0")),
                    "hazards": int(initial.get("hazardObjects", "0")),
                }
                missing_objects = {
                    name: {"expected": count, "actual": actual_counts.get(name, 0)}
                    for name, count in object_counts.items()
                    if actual_counts.get(name, 0) < count
                }
                tests.append(
                    PlaytestCase(
                        name="player_and_required_objects_render",
                        expected_behavior=f"Player and configured game objects appear: {object_counts}.",
                        actual_behavior=f"Observed scene objects: {actual_counts}.",
                        status="FAIL" if missing_objects else "PASS",
                        failure_details=(
                            f"Missing or inactive scene objects: {missing_objects}"
                            if missing_objects
                            else None
                        ),
                    )
                )

                if self._budget_expired():
                    return self._budget_expired_report(tests, runtime_config)
                movement_key = self._movement_key(runtime_config, initial)
                initial_x = float(initial["playerX"])
                initial_y = float(initial["playerY"])
                page.keyboard.down(movement_key)
                page.wait_for_timeout(500)
                page.keyboard.up(movement_key)
                moved = self._snapshot(canvas)
                final_x = float(moved["playerX"])
                final_y = float(moved["playerY"])
                moved_distance = abs(final_x - initial_x) + abs(final_y - initial_y)
                tests.append(
                    PlaytestCase(
                        name="movement_controls_respond",
                        expected_behavior=f"Pressing {movement_key} starts play and moves the player.",
                        actual_behavior=(
                            f"State {initial.get('gameState')} -> {moved.get('gameState')}; "
                            f"player moved {moved_distance:.0f}px."
                        ),
                        status="FAIL" if moved_distance < 3 else "PASS",
                        failure_details=(
                            f"Player position did not change after {movement_key}."
                            if moved_distance < 3
                            else None
                        ),
                    )
                )
                if self._budget_expired():
                    return self._budget_expired_report(tests, runtime_config)
                if runtime_config.get("player", {}).get("movement_mode") == "platformer":
                    tests.append(self._test_platformer_jump(page, canvas))

                if self._budget_expired():
                    return self._budget_expired_report(tests, runtime_config)
                self._test_interactions(
                    page,
                    spec,
                    runtime_config,
                    tests,
                )
                if self._budget_expired():
                    return self._budget_expired_report(tests, runtime_config)
            finally:
                browser.close()

        if page_errors and tests:
            tests[0] = tests[0].model_copy(
                update={
                    "status": "FAIL",
                    "failure_details": "; ".join(page_errors)[:1000],
                }
            )
        return self._report(tests, browser="Chromium (Google Chrome)", runtime_tested=True)

    def _test_interactions(
        self,
        page,
        spec: dict[str, Any],
        runtime_config: dict[str, Any],
        tests: list[PlaytestCase],
    ) -> None:
        if self._budget_expired():
            return
        self._last_platformer_landings = []
        win_condition = runtime_config["win_condition"]
        levels = runtime_config.get("levels", [])
        total_collectibles = sum(len(level.get("collectibles", [])) for level in levels)
        game_type = runtime_config.get("game_type")
        movement_mode = runtime_config.get("player", {}).get("movement_mode")

        if win_condition in {"collect_target", "reach_goal"} and total_collectibles:
            if game_type == "maze":
                def navigate(page, canvas, target_x, target_y, config, level):
                    return self._navigate_maze(
                        page, canvas, target_x, target_y, config, level
                    )
            elif game_type == "top_down_collection":
                def navigate(page, canvas, target_x, target_y, config, level):
                    return self._navigate_top_down(
                        page, canvas, target_x, target_y, config, level
                    )
            elif game_type == "platform_adventure" and movement_mode == "platformer":
                def navigate(page, canvas, target_x, target_y, config, level):
                    return self._navigate_platformer(
                        page, canvas, level, target_x, target_y, config
                    )
            else:
                tests.append(
                    PlaytestCase(
                        name="collectible_collision_and_objective_progress",
                        expected_behavior="Collect the configured target and reach the win state.",
                        actual_behavior="NOT TESTED: this movement mode has no deterministic collection route.",
                        status="NOT TESTED",
                        failure_details="Automated collection currently supports top-down and ground-level platformer routes.",
                    )
                )
                return
            page.reload(wait_until="domcontentloaded")
            page.locator("canvas").wait_for()
            page.wait_for_function(
                "() => document.querySelector('canvas')?.dataset.playerPresent === 'true'",
                timeout=self.startup_timeout_seconds * 1000,
            )
            canvas = page.locator("canvas")
            target = int(
                runtime_config.get("win_target")
                or levels[0].get("collectible_target")
                or 1
            )
            route_results: list[str] = []
            goal_route_reached = False
            goal_route_supported = True
            for level in levels[:1]:
                for collectible in level.get("collectibles", []):
                    if self._budget_expired():
                        return
                    current = self._snapshot(canvas)
                    if int(current.get("collected", "0")) >= target:
                        break
                    if movement_mode == "platformer" and not self._is_ground_level_platformer_target(
                        collectible, level, runtime_config
                    ):
                        tests.append(
                            PlaytestCase(
                                name="collectible_collision_and_objective_progress",
                                expected_behavior=f"Collect {target} item(s), update objective progress, and win.",
                                actual_behavior="NOT TESTED: no safe, reachable platform route leads to this collectible.",
                                status="NOT TESTED",
                                failure_details="The static-platform jump graph found no route from the player's starting platform.",
                            )
                        )
                        break
                    if game_type == "maze":
                        try:
                            observed_positions = json.loads(
                                current.get("collectiblePositions", "[]")
                            )
                        except json.JSONDecodeError:
                            observed_positions = []
                        if not observed_positions:
                            tests.append(
                                PlaytestCase(
                                    name="collectible_collision_and_objective_progress",
                                    expected_behavior=f"Collect {target} item(s) at their observed Phaser positions.",
                                    actual_behavior="NOT TESTED: active collectible positions were unavailable in browser telemetry.",
                                    status="NOT TESTED",
                                    failure_details="The browser could not establish a safe route without observed collectible positions.",
                                )
                            )
                            break
                        target_x = float(observed_positions[0]["x"])
                        target_y = float(observed_positions[0]["y"])
                    else:
                        target_x = (
                            collectible["position"]["x"]
                            * runtime_config["world_width"]
                        )
                        target_y = (
                            collectible["position"]["y"]
                            * runtime_config["world_height"]
                        )
                    reached = navigate(
                        page,
                        canvas,
                        target_x,
                        target_y,
                        runtime_config,
                        level,
                    )
                    after_route = self._snapshot(canvas)
                    route_results.append(
                        f"target=({target_x:.0f},{target_y:.0f}), reached={reached}, "
                        f"player=({after_route.get('playerX')},{after_route.get('playerY')}), "
                        f"collected={after_route.get('collected')}, state={after_route.get('gameState')}; "
                        f"{self._last_platformer_detail}"
                    )
                    if (
                        not reached
                        and int(after_route.get("collected", "0"))
                        <= int(current.get("collected", "0"))
                        and after_route.get("gameState") not in {"won", "lost"}
                    ):
                        break
            if not any(
                case.name == "collectible_collision_and_objective_progress"
                and case.status == "NOT TESTED"
                for case in tests
            ):
                if self._budget_expired():
                    return
                current = self._snapshot(canvas)
                if int(current.get("collected", "0")) >= target:
                    goal = levels[0].get("goal")
                    if goal:
                        if (
                            movement_mode == "platformer"
                            and not self._is_ground_level_platformer_target(
                                {"position": goal["position"]},
                                levels[0],
                                runtime_config,
                            )
                        ):
                            goal_route_supported = False
                            self._last_platformer_detail = (
                                "No safe static-platform route reaches the goal."
                            )
                        else:
                            goal_route_reached = navigate(
                                page,
                                canvas,
                                goal["position"]["x"] * runtime_config["world_width"],
                                goal["position"]["y"] * runtime_config["world_height"],
                                runtime_config,
                                levels[0],
                            )
                final = self._snapshot(canvas)
                collected = int(final.get("collected", "0"))
                state = final.get("gameState", "unknown")
                collection_passed = collected >= target
                if game_type in {"maze", "top_down_collection"}:
                    route_label = "Maze" if game_type == "maze" else "Top-down"
                    route_summary = (
                        f" {route_label} route: {self._last_platformer_detail[:180]}"
                        if route_results
                        else ""
                    )
                elif self._last_platformer_landings:
                    route_summary = (
                        f" Observed platform transitions: "
                        f"{self._format_platform_landings()}."
                    )
                else:
                    route_summary = ""
                passed = (
                    collection_passed
                    and (
                        win_condition == "reach_goal"
                        or state == "won"
                    )
                )
                tests.append(
                    PlaytestCase(
                        name="collectible_collision_and_objective_progress",
                        expected_behavior=(
                            f"Collect {target} item(s) and update objective progress."
                            if win_condition == "reach_goal"
                            else f"Collect {target} item(s), update objective progress, and win."
                        ),
                        actual_behavior=(
                            f"Collected {collected}/{target}; final game state: {state}."
                            + route_summary
                        )[:500],
                        status="PASS" if passed else "FAIL",
                        failure_details=(
                            (
                                "Keyboard navigation did not trigger the required collectible collision and progress. "
                                f"Route observations: {'; '.join(route_results)}"
                            )[:1000]
                            if not passed
                            else None
                        ),
                    )
                )
                if win_condition == "reach_goal":
                    tests.append(
                        PlaytestCase(
                            name="win_condition_completion",
                            expected_behavior="Collect all required items, reach the goal, and win.",
                            actual_behavior=(
                                f"Collected {collected}/{target}; goal route reached: "
                                f"{goal_route_reached}; final game state: {state}."
                                if goal_route_supported
                                else (
                                    f"NOT TESTED: collected {collected}/{target}; no safe "
                                    f"static-platform route reaches the goal; final game "
                                    f"state: {state}."
                                )
                            ),
                            status=(
                                "NOT TESTED"
                                if not goal_route_supported
                                else (
                                    "PASS"
                                    if collected >= target
                                    and goal_route_reached
                                    and state == "won"
                                    else "FAIL"
                                )
                            ),
                            failure_details=(
                                (
                                    "No conservative static-platform route reaches the configured goal."
                                    if not goal_route_supported
                                    else "The required items, goal interaction, or win state did not complete."
                                )
                                if not goal_route_supported
                                or not (
                                    collected >= target
                                    and goal_route_reached
                                    and state == "won"
                                )
                                else None
                            ),
                        )
                    )
        elif win_condition == "survive":
            target = int(runtime_config.get("win_target") or 0)
            if 0 < target <= 5:
                canvas = page.locator("canvas")
                page.keyboard.press(self._movement_key(runtime_config, self._snapshot(canvas)))
                page.wait_for_timeout((target + 1) * 1000)
                final = self._snapshot(canvas)
                passed = final.get("gameState") == "won"
                tests.append(
                    PlaytestCase(
                        name="survival_objective_completion",
                        expected_behavior=f"Survive for {target} seconds and reach the win state.",
                        actual_behavior=f"After {target + 1}s the game state was {final.get('gameState')}.",
                        status="PASS" if passed else "FAIL",
                        failure_details=(
                            "The configured survival objective did not reach the win state."
                            if not passed
                            else None
                        ),
                    )
                )
            else:
                tests.append(
                    PlaytestCase(
                        name="survival_objective_completion",
                        expected_behavior=f"Survive for {target or 'the configured duration'} seconds and win.",
                        actual_behavior="NOT TESTED: objective duration exceeds the bounded browser test window.",
                        status="NOT TESTED",
                        failure_details="Automated objective waits are capped at five seconds.",
                    )
                )
        elif win_condition == "reach_goal":
            if self._budget_expired():
                return
            goal = levels[0].get("goal") if levels else None
            supported_route = (
                game_type in {"top_down_collection", "maze"}
                or (game_type == "platform_adventure" and movement_mode == "platformer")
            )
            if goal and supported_route:
                page.reload(wait_until="domcontentloaded")
                page.locator("canvas").wait_for()
                page.wait_for_function(
                    "() => document.querySelector('canvas')?.dataset.playerPresent === 'true'",
                    timeout=self.startup_timeout_seconds * 1000,
                )
                canvas = page.locator("canvas")
                position = goal["position"]
                if (
                    movement_mode == "platformer"
                    and not self._is_ground_level_platformer_target(
                        {"position": position}, levels[0], runtime_config
                    )
                ):
                    tests.append(
                        PlaytestCase(
                            name="win_condition_completion",
                            expected_behavior="Reach the configured goal and win.",
                            actual_behavior="NOT TESTED: no safe static-platform route reaches the goal.",
                            status="NOT TESTED",
                            failure_details="The static-platform jump graph found no conservative route from the starting platform.",
                        )
                    )
                else:
                    reached = self._navigate_goal(
                        game_type=game_type,
                        movement_mode=movement_mode,
                        page=page,
                        canvas=canvas,
                        level=levels[0],
                        target_x=position["x"] * runtime_config["world_width"],
                        target_y=position["y"] * runtime_config["world_height"],
                        runtime_config=runtime_config,
                    )
                    final = self._snapshot(canvas)
                    passed = reached and final.get("gameState") == "won"
                    tests.append(
                        PlaytestCase(
                            name="win_condition_completion",
                            expected_behavior="Reach the configured goal and win.",
                            actual_behavior=f"Goal route reached: {reached}; final game state: {final.get('gameState')}.",
                            status="PASS" if passed else "FAIL",
                            failure_details=(
                                "The goal could not be reached or the runtime did not enter the won state."
                                if not passed
                                else None
                            ),
                        )
                    )
            else:
                tests.append(
                    PlaytestCase(
                        name="win_condition_completion",
                        expected_behavior="Complete the reach_goal objective.",
                        actual_behavior="NOT TESTED: no safe goal route is available.",
                        status="NOT TESTED",
                        failure_details="A goal object or supported navigation route is missing.",
                    )
                )
        elif win_condition == "defeat_target":
            tests.append(
                PlaytestCase(
                    name="win_condition_completion",
                    expected_behavior="Defeat the configured target and win.",
                    actual_behavior="NOT TESTED: automated combat is not implemented.",
                    status="NOT TESTED",
                    failure_details="Projectile targeting and defeat-target verification remain unsupported by the harness.",
                )
            )

        if self._budget_expired():
            return
        if any(level.get("enemies") for level in levels):
            self._test_enemy_contact(page, runtime_config, tests)
        elif any(level.get("hazards") for level in levels):
            tests.append(
                PlaytestCase(
                    name="enemy_contact_collision",
                    expected_behavior="Contact with a supported enemy applies player damage.",
                    actual_behavior="NOT TESTED: the level contains hazards rather than enemy-contact objects.",
                    status="NOT TESTED",
                    failure_details="Hazard contact behavior varies by mechanic and is not covered by the enemy collider check.",
                )
            )

        if self._budget_expired():
            return
        if runtime_config.get("lose_condition") == "time_expired":
            self._test_time_expiry(page, runtime_config, tests)
        elif (
            runtime_config.get("lose_condition") == "health_depleted"
            and any(
                level.get("enemies") or level.get("hazards") or level.get("obstacles")
                for level in levels
            )
            and not any(test.name == "game_over_and_restart" for test in tests)
        ):
            tests.append(
                PlaytestCase(
                    name="game_over_and_restart",
                    expected_behavior="Health depletion enters the lost state; R restarts the game.",
                    actual_behavior="NOT TESTED: no verified health-depletion route is available.",
                    status="NOT TESTED",
                    failure_details="The current scenario did not test a supported contact route through zero health.",
                )
            )

        if self._last_platformer_landings:
            failed_landings = [
                (
                    source,
                    expected,
                    actual,
                    player_x,
                    player_y,
                    grounded,
                    state,
                )
                for source, expected, actual, player_x, player_y, grounded, state
                in self._last_platformer_landings
                if actual != expected and state == "playing"
            ]
            all_landed = all(
                actual == expected
                for _, expected, actual, _, _, _, _ in self._last_platformer_landings
            )
            landing_status = (
                "PASS"
                if all_landed
                else "FAIL"
                if failed_landings
                else "NOT TESTED"
            )
            tests.append(
                PlaytestCase(
                    name="platformer_platform_landings",
                    expected_behavior="Land on every platform selected by the conservative browser route.",
                    actual_behavior=(
                        f"Observed platform transitions: {self._format_platform_landings()}."
                    ),
                    status=landing_status,
                    failure_details=(
                        "Observed failed transition(s): "
                        + self._format_platform_landings(failed_landings)
                        if failed_landings
                        else (
                            "The game reached a terminal state before the planned landing could be observed."
                            if not all_landed
                            else None
                        )
                    ),
                )
            )

    def _navigate_goal(
        self,
        *,
        game_type: str | None,
        movement_mode: str | None,
        page,
        canvas,
        level: dict[str, Any],
        target_x: float,
        target_y: float,
        runtime_config: dict[str, Any],
    ) -> bool:
        if game_type == "platform_adventure" and movement_mode == "platformer":
            return self._navigate_platformer(
                page, canvas, level, target_x, target_y, runtime_config
            )
        if game_type == "maze":
            return self._navigate_maze(
                page, canvas, target_x, target_y, runtime_config, level
            )
        if game_type == "top_down_collection":
            return self._navigate_top_down(
                page, canvas, target_x, target_y, runtime_config, level
            )
        raise ValueError(f"No supported goal route for game type {game_type!r}.")

    def _format_platform_landings(
        self,
        landings: list[
            tuple[int, int, int | None, float, float, bool, str]
        ] | None = None,
    ) -> str:
        return ", ".join(
            f"{source}->{actual} (planned {expected}; player_x={player_x:.0f}; "
            f"player_y={player_y:.0f}; grounded={grounded}; game_state={state})"
            for source, expected, actual, player_x, player_y, grounded, state in (
                landings or self._last_platformer_landings
            )
        )

    def _test_platformer_jump(self, page, canvas) -> PlaytestCase:
        try:
            page.wait_for_function(
                "() => document.querySelector('canvas')?.dataset.playerGrounded === 'true'",
                timeout=self.scenario_timeout_seconds * 1000,
            )
        except PlaywrightTimeoutError:
            return PlaytestCase(
                name="platformer_jump",
                expected_behavior="While grounded, Space triggers an upward jump.",
                actual_behavior="The player did not reach a grounded platform state.",
                status="FAIL",
                failure_details="The player was never grounded, so jumping could not be tested.",
            )
        grounded = self._snapshot(canvas)
        start_y = float(grounded["playerY"])
        initial_jumps = int(grounded.get("jumpCount", "0"))
        page.keyboard.down("Space")
        try:
            page.wait_for_timeout(160)
            rising = self._snapshot(canvas)
        finally:
            page.keyboard.up("Space")
        rise = start_y - float(rising["playerY"])
        jump_accepted = int(rising.get("jumpCount", "0")) > initial_jumps
        passed = rise >= 12 and jump_accepted
        return PlaytestCase(
            name="platformer_jump",
            expected_behavior="While grounded, Space triggers an upward jump.",
            actual_behavior=(
                f"Grounded before input: true; jump accepted: {jump_accepted}; "
                f"player rose {rise:.0f}px during the jump."
            ),
            status="PASS" if passed else "FAIL",
            failure_details=(
                "The player did not accept the grounded jump input and rise at least 12px."
                if not passed
                else None
            ),
        )

    @staticmethod
    def _is_ground_level_platformer_target(
        target: dict[str, Any],
        level: dict[str, Any],
        runtime_config: dict[str, Any],
    ) -> bool:
        targets = BrowserPlaytestRunner._platformer_target_platforms(
            target, level, runtime_config
        )
        start = BrowserPlaytestRunner._platformer_start_platform(
            level, runtime_config
        )
        return start is not None and any(
            BrowserPlaytestRunner._platform_path(
                start, [target_index], level, runtime_config
            )
            for target_index in targets
        )

    @staticmethod
    def _platformer_start_platform(
        level: dict[str, Any],
        runtime_config: dict[str, Any],
    ) -> int | None:
        player = runtime_config["player"]
        world_width = runtime_config["world_width"]
        world_height = runtime_config["world_height"]
        start_x = player["start"]["x"] * world_width
        start_y = player["start"]["y"] * world_height
        candidates = []
        for index, platform in enumerate(level.get("platforms", [])):
            if platform.get("movement", "static") != "static":
                continue
            left = platform["x"] * world_width - 15
            right = (platform["x"] + platform["width"]) * world_width + 15
            standing_y = platform["y"] * world_height - 19
            if left <= start_x <= right:
                candidates.append((abs(standing_y - start_y), index))
        return min(candidates)[1] if candidates else None

    @staticmethod
    def _platformer_target_platforms(
        target: dict[str, Any],
        level: dict[str, Any],
        runtime_config: dict[str, Any],
    ) -> list[int]:
        position = target["position"]
        world_width = runtime_config["world_width"]
        world_height = runtime_config["world_height"]
        platform_specs = level.get("platforms", [])
        supported: list[int] = []
        for index, platform in enumerate(platform_specs):
            if platform.get("movement", "static") != "static":
                continue
            left = platform["x"] * world_width
            right = (platform["x"] + platform["width"]) * world_width
            standing_y = platform["y"] * world_height - 19
            target_x = position["x"] * world_width
            target_y = position["y"] * world_height
            if left - 5 <= target_x <= right + 5 and abs(standing_y - target_y) <= 31:
                supported.append(index)
        return supported

    @staticmethod
    def _platform_jump_duration(
        start_y: float,
        landing_y: float,
        runtime_config: dict[str, Any],
    ) -> float | None:
        gravity = float(runtime_config["player"]["gravity"])
        jump_velocity = float(runtime_config["player"]["jump_velocity"])
        if gravity <= 0:
            return None
        vertical_delta = landing_y - start_y
        discriminant = jump_velocity**2 + 2 * gravity * vertical_delta
        if discriminant < 0:
            return None
        return (jump_velocity + discriminant**0.5) / gravity

    @classmethod
    def _platform_path(
        cls,
        start_index: int,
        target_indices: list[int],
        level: dict[str, Any],
        runtime_config: dict[str, Any],
    ) -> list[int] | None:
        platforms = level.get("platforms", [])
        if not 0 <= start_index < len(platforms):
            return None
        if start_index in target_indices:
            return [start_index]
        world_width = runtime_config["world_width"]
        world_height = runtime_config["world_height"]
        half_player_width = 15
        standing_positions = [
            {
                "left": platform["x"] * world_width + half_player_width,
                "right": (platform["x"] + platform["width"]) * world_width - half_player_width,
                "y": platform["y"] * world_height - 19,
                "movement": platform.get("movement", "static"),
            }
            for platform in platforms
        ]
        queue = deque([(start_index, [start_index])])
        seen = {start_index}
        while queue:
            current_index, path = queue.popleft()
            current = standing_positions[current_index]
            if current["movement"] != "static":
                continue
            for next_index, destination in enumerate(standing_positions):
                if next_index in seen or destination["movement"] != "static":
                    continue
                duration = cls._platform_jump_duration(
                    current["y"], destination["y"], runtime_config
                )
                if duration is None:
                    continue
                maximum_rise = (
                    runtime_config["player"]["jump_velocity"] ** 2
                    / (2 * runtime_config["player"]["gravity"])
                )
                if current["y"] - destination["y"] > maximum_rise * 0.8:
                    continue
                horizontal_gap = max(
                    0.0,
                    current["left"] - destination["right"],
                    destination["left"] - current["right"],
                )
                jump_range = runtime_config["player"]["speed"] * duration
                if horizontal_gap > jump_range * 0.82:
                    continue
                next_path = [*path, next_index]
                if next_index in target_indices:
                    return next_path
                seen.add(next_index)
                queue.append((next_index, next_path))
        return None

    @classmethod
    def _standing_platform_index(
        cls,
        snapshot: dict[str, str],
        level: dict[str, Any],
        runtime_config: dict[str, Any],
    ) -> int | None:
        if snapshot.get("playerGrounded") != "true":
            return None
        player_x = float(snapshot["playerX"])
        player_y = float(snapshot["playerY"])
        world_width = runtime_config["world_width"]
        world_height = runtime_config["world_height"]
        matches = []
        for index, platform in enumerate(level.get("platforms", [])):
            left = platform["x"] * world_width - 15
            right = (platform["x"] + platform["width"]) * world_width + 15
            standing_y = platform["y"] * world_height - 19
            if left <= player_x <= right and abs(standing_y - player_y) <= 12:
                matches.append((abs(standing_y - player_y), index))
        return min(matches)[1] if matches else None

    def _navigate_platformer(
        self,
        page,
        canvas,
        level: dict[str, Any],
        target_x: float,
        target_y: float,
        runtime_config: dict[str, Any],
    ) -> bool:
        target_position = {
            "position": {
                "x": target_x / runtime_config["world_width"],
                "y": target_y / runtime_config["world_height"],
            }
        }
        supported = self._platformer_target_platforms(
            target_position,
            level,
            runtime_config,
        )
        if not supported:
            self._last_platformer_detail = "No static platform overlaps the target height and x position."
            return False
        initial = self._snapshot(canvas)
        if initial.get("gameState") == "ready":
            start_key = "ArrowRight"
            page.keyboard.press(start_key, delay=100)
            page.wait_for_function(
                "() => document.querySelector('canvas')?.dataset.playerGrounded === 'true'",
                timeout=self.scenario_timeout_seconds * 1000,
            )
        current_snapshot = self._snapshot(canvas)
        current_index = self._standing_platform_index(
            current_snapshot, level, runtime_config
        )
        if current_index is None:
            self._last_platformer_detail = (
                f"Player was not grounded on a mapped platform: {current_snapshot}."
            )
            return False
        path = self._platform_path(
            current_index, supported, level, runtime_config
        )
        if path is None:
            self._last_platformer_detail = (
                f"No jump path from platform {current_index} to candidates {supported}."
            )
            return False
        self._last_platformer_detail = f"Planned platform path {path}."
        if len(path) == 1:
            return self._walk_to_platform_target(
                page,
                canvas,
                level,
                current_index,
                target_x,
                target_y,
                runtime_config,
            )
        for next_index in path[1:]:
            if not self._jump_to_platform(
                page,
                canvas,
                level,
                current_index,
                next_index,
                runtime_config,
            ):
                self._last_platformer_detail += (
                    f" Jump from platform {current_index} to {next_index} failed: "
                    f"{self._last_platformer_detail}"
                )
                return False
            current_index = next_index

        if not self._walk_to_platform_target(
            page,
            canvas,
            level,
            current_index,
            target_x,
            target_y,
            runtime_config,
        ):
            return False
        return self._standing_platform_index(
            self._snapshot(canvas), level, runtime_config
        ) in supported

    def _walk_to_platform_target(
        self,
        page,
        canvas,
        level: dict[str, Any],
        platform_index: int,
        target_x: float,
        target_y: float,
        runtime_config: dict[str, Any],
    ) -> bool:
        snapshot = self._snapshot(canvas)
        platform = level["platforms"][platform_index]
        world_width = runtime_config["world_width"]
        world_height = runtime_config["world_height"]
        left = platform["x"] * world_width + 20
        right = (platform["x"] + platform["width"]) * world_width - 20
        walk_target = min(max(target_x, left), right)
        for _ in range(4):
            if (
                self._standing_platform_index(snapshot, level, runtime_config)
                != platform_index
                or snapshot.get("gameState") not in {"playing", "ready"}
            ):
                break
            delta_x = walk_target - float(snapshot["playerX"])
            if abs(delta_x) <= 12:
                break
            key = "ArrowRight" if delta_x > 0 else "ArrowLeft"
            page.keyboard.down(key)
            try:
                page.wait_for_timeout(
                    min(
                        1000,
                        int(
                            abs(delta_x)
                            / runtime_config["player"]["speed"]
                            * 1000
                        )
                        + 80,
                    )
                )
            finally:
                page.keyboard.up(key)
            page.wait_for_timeout(100)
            snapshot = self._snapshot(canvas)
        result = self._snapshot(canvas)
        success = (
            self._standing_platform_index(result, level, runtime_config)
            == platform_index
            and abs(float(result["playerX"]) - target_x) <= 50
            and abs(float(result["playerY"]) - target_y) <= 40
            and result.get("gameState") in {"playing", "won"}
        )
        self._last_platformer_detail += f" Walk result {result}; success={success}."
        return success

    def _jump_to_platform(
        self,
        page,
        canvas,
        level: dict[str, Any],
        source_index: int,
        target_index: int,
        runtime_config: dict[str, Any],
    ) -> bool:
        platforms = level["platforms"]
        world_width = runtime_config["world_width"]
        world_height = runtime_config["world_height"]
        source = platforms[source_index]
        destination = platforms[target_index]
        source_left = source["x"] * world_width
        source_right = (source["x"] + source["width"]) * world_width
        target_left = destination["x"] * world_width
        target_right = (destination["x"] + destination["width"]) * world_width
        snapshot = self._snapshot(canvas)
        initial_jumps = int(snapshot.get("jumpCount", "0"))
        player_x = float(snapshot["playerX"])
        source_y = source["y"] * world_height - 19
        destination_y = destination["y"] * world_height - 19
        overlapping = source_left <= target_right and target_left <= source_right
        delay_before_steering = 0.0
        if overlapping:
            source_center_min = source_left + 20
            source_center_max = source_right - 20
            if destination_y < source_y:
                takeoff_candidates = [
                    target_left - 20,
                    target_right + 20,
                ]
                takeoff_candidates = [
                    value
                    for value in takeoff_candidates
                    if source_center_min <= value <= source_center_max
                ]
                if not takeoff_candidates:
                    self._last_platformer_detail = (
                        "No safe takeoff point outside the elevated platform."
                    )
                    return False
                takeoff_x = min(
                    takeoff_candidates, key=lambda value: abs(value - player_x)
                )
                landing_x = (
                    target_left + 20
                    if takeoff_x < target_left
                    else target_right - 20
                )
                rise = source_y - destination_y
                gravity = runtime_config["player"]["gravity"]
                velocity = runtime_config["player"]["jump_velocity"]
                discriminant = velocity**2 - 2 * gravity * rise
                if discriminant < 0:
                    self._last_platformer_detail = (
                        "Destination platform exceeds the jump apex."
                    )
                    return False
                delay_before_steering = (
                    velocity - discriminant**0.5
                ) / gravity + 0.04
            else:
                landing_x = min(
                    max(player_x, target_left + 20), target_right - 20
                )
                takeoff_x = min(
                    max(landing_x, source_center_min), source_center_max
                )
        elif target_left > source_right:
            takeoff_x = source_right - 20
            landing_x = target_left + 20
        else:
            takeoff_x = source_left + 20
            landing_x = target_right - 20

        if abs(player_x - takeoff_x) > 12:
            key = "ArrowRight" if takeoff_x > player_x else "ArrowLeft"
            page.keyboard.down(key)
            try:
                page.wait_for_timeout(
                    min(
                        1500,
                        int(abs(takeoff_x - player_x)
                            / runtime_config["player"]["speed"] * 1000) + 80,
                    )
                )
            finally:
                page.keyboard.up(key)
            page.wait_for_timeout(100)
            snapshot = self._snapshot(canvas)
            if snapshot.get("playerGrounded") != "true":
                self._last_platformer_detail = (
                    f"Fell while walking to takeoff: {snapshot}."
                )
                return False
            player_x = float(snapshot["playerX"])

        flight_time = self._platform_jump_duration(
            source_y, destination_y, runtime_config
        )
        if flight_time is None:
            self._last_platformer_detail = (
                f"Jump physics cannot reach vertical destination y={destination_y}."
            )
            return False
        travel_time = abs(landing_x - player_x) / runtime_config["player"]["speed"]
        if travel_time > flight_time:
            self._last_platformer_detail = (
                f"Horizontal travel {travel_time:.2f}s exceeds flight time {flight_time:.2f}s."
            )
            return False
        direction = (
            "ArrowRight" if landing_x > player_x else "ArrowLeft"
        ) if abs(landing_x - player_x) > 8 else None
        page.keyboard.down("Space")
        try:
            page.wait_for_timeout(80)
        finally:
            page.keyboard.up("Space")
        if delay_before_steering > 0.08:
            page.wait_for_timeout(
                int((delay_before_steering - 0.08) * 1000)
            )
        if direction:
            page.keyboard.down(direction)
        try:
            page.wait_for_timeout(int(travel_time * 1000))
        finally:
            if direction:
                page.keyboard.up(direction)
        estimated_remaining = max(
            0,
            int(
                (flight_time - travel_time - max(0.08, delay_before_steering))
                * 1000
            ) + 300,
        )
        samples = []
        poll_duration = min(3500, max(estimated_remaining, 2200))
        for _ in range(max(1, (poll_duration + 99) // 100)):
            page.wait_for_timeout(100)
            sample = self._snapshot(canvas)
            samples.append(
                f"{sample.get('playerY')}:{sample.get('playerVelocityY')}:"
                f"{sample.get('playerGrounded')}:{sample.get('jumpCount')}"
            )
            if (
                sample.get("playerGrounded") == "true"
                or float(sample["playerY"]) > world_height + 50
                or sample.get("gameState") in {"won", "lost"}
            ):
                break
        landed = self._snapshot(canvas)
        landed_index = self._standing_platform_index(
            landed, level, runtime_config
        )
        success = landed_index == target_index
        self._last_platformer_detail = (
            f"Jump landed at platform {landed_index} (wanted {target_index}); "
            f"jumps accepted {initial_jumps}->{landed.get('jumpCount')}; "
            f"start={snapshot}; trajectory={'/'.join(samples)}; "
            f"snapshot={landed}; flight={flight_time:.2f}s."
        )
        self._last_platformer_landings.append(
            (
                source_index,
                target_index,
                landed_index,
                float(landed["playerX"]),
                float(landed["playerY"]),
                landed.get("playerGrounded") == "true",
                landed.get("gameState", "unknown"),
            )
        )
        return success

    def _test_enemy_contact(
        self,
        page,
        runtime_config: dict[str, Any],
        tests: list[PlaytestCase],
    ) -> None:
        game_type = runtime_config.get("game_type")
        movement_mode = runtime_config.get("player", {}).get("movement_mode")
        level = runtime_config.get("levels", [{}])[0]
        enemies = level.get("enemies", [])
        platformer_route = (
            game_type == "platform_adventure"
            and movement_mode == "platformer"
            and any(enemy.get("movement") == "static" for enemy in enemies)
        )
        top_down_route = (
            game_type == "top_down_collection"
            and any(enemy.get("movement") == "static" for enemy in enemies)
        ) or (
            game_type == "maze"
            and any(enemy.get("movement") == "static" for enemy in enemies)
        )
        if not (platformer_route or top_down_route):
            tests.append(
                PlaytestCase(
                    name="enemy_contact_collision",
                    expected_behavior="Enemy contact applies damage and updates player health.",
                    actual_behavior="NOT TESTED: no deterministic enemy-contact route is available.",
                    status="NOT TESTED",
                    failure_details="Automated contact supports static platformer enemies and top-down enemy navigation.",
                )
            )
            return
        if platformer_route and not any(
            self._is_ground_level_platformer_target(enemy, level, runtime_config)
            for enemy in enemies
            if enemy.get("movement") == "static"
        ):
            tests.append(
                PlaytestCase(
                    name="enemy_contact_collision",
                    expected_behavior="Enemy contact applies damage and updates player health.",
                    actual_behavior="NOT TESTED: enemies are not on a verified ground-level route.",
                    status="NOT TESTED",
                    failure_details="Jumping and moving-platform enemy routes are not yet automated.",
                )
            )
            return

        page.reload(wait_until="domcontentloaded")
        page.locator("canvas").wait_for()
        page.wait_for_function(
            "() => document.querySelector('canvas')?.dataset.playerPresent === 'true'",
            timeout=self.startup_timeout_seconds * 1000,
        )
        canvas = page.locator("canvas")
        initial = self._snapshot(canvas)
        reset_progress_expected = (
            runtime_config.get("win_condition") == "reach_goal"
            and bool(level.get("collectibles"))
        )
        if reset_progress_expected:
            collectible = level["collectibles"][0]
            target_x = collectible["position"]["x"] * runtime_config["world_width"]
            target_y = collectible["position"]["y"] * runtime_config["world_height"]
            if platformer_route:
                self._navigate_platformer(
                    page,
                    canvas,
                    level,
                    target_x,
                    target_y,
                    runtime_config,
                )
            elif game_type == "maze":
                self._navigate_maze(
                    page,
                    canvas,
                    target_x,
                    target_y,
                    runtime_config,
                    level,
                )
            else:
                self._navigate_top_down(
                    page,
                    canvas,
                    target_x,
                    target_y,
                    runtime_config,
                    level,
                )
            try:
                page.wait_for_function(
                    "() => Number(document.querySelector('canvas')?.dataset.collected) > 0",
                    timeout=self._remaining_timeout_ms(2),
                )
            except PlaywrightTimeoutError as error:
                logger.warning(
                    "Could not establish collectible progress before the enemy "
                    "restart check: %s",
                    error,
                )
            initial = self._snapshot(canvas)
        health_before = int(initial["health"])
        enemy = min(
            enemies,
            key=lambda item: abs(
                item["position"]["x"] * runtime_config["world_width"]
                - float(initial["playerX"])
            ),
        )
        position = enemy["position"]
        if platformer_route:
            reached = self._navigate_platformer(
                page,
                canvas,
                level,
                position["x"] * runtime_config["world_width"],
                position["y"] * runtime_config["world_height"],
                runtime_config,
            )
        elif game_type == "maze":
            reached = self._navigate_maze(
                page,
                canvas,
                position["x"] * runtime_config["world_width"],
                position["y"] * runtime_config["world_height"],
                runtime_config,
                level,
            )
        elif game_type == "top_down_collection":
            reached = self._navigate_top_down(
                page,
                canvas,
                position["x"] * runtime_config["world_width"],
                position["y"] * runtime_config["world_height"],
                runtime_config,
                level,
            )
        else:
            reached = self._navigate_top_down(
                page,
                canvas,
                position["x"] * runtime_config["world_width"],
                position["y"] * runtime_config["world_height"],
                runtime_config,
            )
        snapshot = self._snapshot(canvas)
        health_after = int(snapshot["health"])
        damaged = health_after < health_before
        player_x = float(snapshot["playerX"])
        player_y = float(snapshot["playerY"])
        enemy_x = position["x"] * runtime_config["world_width"]
        enemy_y = position["y"] * runtime_config["world_height"]
        player_in_world = (
            0 <= player_x <= runtime_config["world_width"]
            and 0 <= player_y <= runtime_config["world_height"]
        )
        collision_observed = math.hypot(player_x - enemy_x, player_y - enemy_y) <= 64
        tests.append(
            PlaytestCase(
                name="enemy_contact_collision",
                expected_behavior="Touching a reachable enemy reduces player health.",
                actual_behavior=(
                    f"Navigator completed route: {reached}; health changed "
                    f"{health_before} -> {snapshot.get('health')}; game state: "
                    f"{snapshot.get('gameState')}; player ended at "
                    f"({player_x:.0f},{player_y:.0f}), enemy at "
                    f"({enemy_x:.0f},{enemy_y:.0f}); collision position in bounds: "
                    f"{player_in_world}."
                    + (
                        " The collision ended the game before the navigator's final-position check."
                        if damaged and not reached and snapshot.get("gameState") == "lost"
                        else ""
                    )
                ),
                status=(
                    "PASS"
                    if damaged and player_in_world and collision_observed
                    else "FAIL"
                ),
                failure_details=(
                    "Enemy contact did not produce bounded, in-range damage."
                    if not (damaged and player_in_world and collision_observed)
                    else None
                ),
            )
        )
        if snapshot.get("gameState") == "lost":
            self._test_restart(
                page,
                canvas,
                runtime_config,
                tests,
                "health depletion",
                snapshot,
                reset_progress_expected,
            )
        elif runtime_config.get("lose_condition") == "health_depleted":
            tests.append(
                PlaytestCase(
                    name="game_over_and_restart",
                    expected_behavior="Health depletion enters the lost state; R restarts the game.",
                    actual_behavior="NOT TESTED: contact reduced health but did not deplete it.",
                    status="NOT TESTED",
                    failure_details="The test did not repeat collisions through the configured health total.",
                )
            )

    def _test_time_expiry(
        self,
        page,
        runtime_config: dict[str, Any],
        tests: list[PlaytestCase],
    ) -> None:
        target = int(runtime_config.get("lose_target") or 0)
        if not 0 < target <= 5:
            tests.append(
                PlaytestCase(
                    name="lose_condition_time_expired",
                    expected_behavior="The configured timer reaches the lost state.",
                    actual_behavior="NOT TESTED: timer duration exceeds the bounded browser test window.",
                    status="NOT TESTED",
                    failure_details="Automated timer tests are capped at five seconds.",
                )
            )
            return
        page.reload(wait_until="domcontentloaded")
        page.locator("canvas").wait_for()
        page.wait_for_function(
            "() => document.querySelector('canvas')?.dataset.playerPresent === 'true'",
            timeout=self.startup_timeout_seconds * 1000,
        )
        canvas = page.locator("canvas")
        page.keyboard.press(self._movement_key(runtime_config, self._snapshot(canvas)))
        page.wait_for_timeout((target + 1) * 1000)
        snapshot = self._snapshot(canvas)
        passed = snapshot.get("gameState") == "lost"
        tests.append(
            PlaytestCase(
                name="lose_condition_time_expired",
                expected_behavior=f"After {target} seconds, the game enters the lost state.",
                actual_behavior=f"After {target + 1}s the game state was {snapshot.get('gameState')}.",
                status="PASS" if passed else "FAIL",
                failure_details=(
                    "The configured timer did not enter the lost state."
                    if not passed
                    else None
                ),
            )
        )
        if passed:
            self._test_restart(
                page,
                canvas,
                runtime_config,
                tests,
                "timer expiry",
                snapshot,
                False,
            )

    def _test_restart(
        self,
        page,
        canvas,
        runtime_config: dict[str, Any],
        tests: list[PlaytestCase],
        cause: str,
        pre_restart: dict[str, str],
        reset_progress_expected: bool,
    ) -> None:
        page.keyboard.press("KeyR")
        page.wait_for_function(
            "() => document.querySelector('canvas')?.dataset.gameState === 'ready'",
            timeout=self.scenario_timeout_seconds * 1000,
        )
        restarted = self._snapshot(canvas)
        health = int(restarted.get("health", "0"))
        expected_health = int(runtime_config["player"]["max_health"])
        start_x = float(restarted["playerX"])
        start_y = float(restarted["playerY"])
        expected_start_x = (
            runtime_config["player"]["start"]["x"] * runtime_config["world_width"]
        )
        expected_start_y = (
            runtime_config["player"]["start"]["y"] * runtime_config["world_height"]
        )
        position_reset = (
            abs(start_x - expected_start_x) <= 3
            and abs(start_y - expected_start_y) <= 3
        )
        score_reset = int(restarted.get("score", "-1")) == 0
        collectibles_reset = int(restarted.get("collected", "-1")) == 0
        progress_was_observed = (
            not reset_progress_expected
            or (
                int(pre_restart.get("score", "0")) > 0
                and int(pre_restart.get("collected", "0")) > 0
            )
        )
        page.keyboard.down("ArrowLeft")
        try:
            page.wait_for_timeout(200)
        finally:
            page.keyboard.up("ArrowLeft")
        playable = self._snapshot(canvas)
        movement = abs(float(playable["playerX"]) - start_x) + abs(
            float(playable["playerY"]) - start_y
        )
        passed = (
            health == expected_health
            and position_reset
            and score_reset
            and collectibles_reset
            and progress_was_observed
            and movement >= 3
            and playable.get("gameState") == "playing"
        )
        tests.append(
            PlaytestCase(
                name="game_over_and_restart",
                expected_behavior=(
                    "Pressing R restores the spawn position, score, collectibles, "
                    "and full health; movement input starts a playable game."
                ),
                actual_behavior=(
                    f"Restart after {cause}: state {restarted.get('gameState')}, "
                    f"player ({start_x:.0f},{start_y:.0f}) expected "
                    f"({expected_start_x:.0f},{expected_start_y:.0f}), "
                    f"score {pre_restart.get('score')} -> {restarted.get('score')}, "
                    f"collectibles {pre_restart.get('collected')} -> "
                    f"{restarted.get('collected')} (pre-restart progress observed: "
                    f"{progress_was_observed}), health {health}/{expected_health}; "
                    f"after movement input, state {playable.get('gameState')} and "
                    f"player moved {movement:.0f}px."
                ),
                status="PASS" if passed else "FAIL",
                failure_details=(
                    "Restart did not restore spawn position, score, collectibles, "
                    "full health, and responsive movement from observed pre-restart "
                    "progress."
                    if not passed
                    else None
                ),
            )
        )

    def _navigate_maze(
        self,
        page,
        canvas,
        target_x: float,
        target_y: float,
        runtime_config: dict[str, Any],
        level: dict[str, Any],
        route_name: str = "Maze",
    ) -> bool:
        step = 16
        navigation_margin = 8
        half_width = 15 + navigation_margin
        half_height = 19 + navigation_margin
        world_width = float(runtime_config["world_width"])
        world_height = float(runtime_config["world_height"])
        columns = int((world_width - half_width * 2) // step) + 1
        rows = int((world_height - half_height * 2) // step) + 1
        walls = level.get("walls", [])

        def position_at(column: int, row: int) -> tuple[float, float]:
            return (
                half_width + column * step,
                half_height + row * step,
            )

        def is_walkable(position: tuple[float, float]) -> bool:
            x, y = position
            for wall in walls:
                left, top, right, bottom = _normalized_bounds(
                    wall["position"],
                    wall["width"],
                    wall["height"],
                    world_width,
                    world_height,
                )
                if (
                    x + half_width > left
                    and x - half_width < right
                    and y + half_height > top
                    and y - half_height < bottom
                ):
                    return False
            return True

        nodes = {
            (column, row): position_at(column, row)
            for row in range(rows)
            for column in range(columns)
            if is_walkable(position_at(column, row))
        }
        snapshot = self._snapshot(canvas)
        player_position = (
            float(snapshot["playerX"]),
            float(snapshot["playerY"]),
        )
        start = min(
            nodes,
            key=lambda node: (
                (nodes[node][0] - player_position[0]) ** 2
                + (nodes[node][1] - player_position[1]) ** 2
            ),
        ) if nodes else None
        goal = min(
            nodes,
            key=lambda node: (
                (nodes[node][0] - target_x) ** 2
                + (nodes[node][1] - target_y) ** 2
            ),
        ) if nodes else None
        if start is None or goal is None:
            self._last_platformer_detail = f"{route_name} walkable grid was empty."
            return False

        queue = deque([start])
        previous: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        while queue and goal not in previous:
            column, row = queue.popleft()
            for neighbor in (
                (column - 1, row),
                (column + 1, row),
                (column, row - 1),
                (column, row + 1),
            ):
                if neighbor in nodes and neighbor not in previous:
                    previous[neighbor] = (column, row)
                    queue.append(neighbor)
        if goal not in previous:
            self._last_platformer_detail = (
                f"No wall-clear {route_name.lower()} path from {nodes[start]} to target "
                f"({target_x:.0f},{target_y:.0f})."
            )
            return False

        path = []
        current = goal
        while current is not None:
            path.append(current)
            current = previous[current]
        path.reverse()
        speed = float(runtime_config["player"]["speed"])
        segments: list[tuple[str, float, str, int, float, float, float]] = []
        for current, following in zip(path, path[1:]):
            current_x, current_y = nodes[current]
            next_x, next_y = nodes[following]
            if next_x != current_x:
                key = "ArrowRight" if next_x > current_x else "ArrowLeft"
                target_value = next_x
                target_axis = "x"
                direction = 1 if next_x > current_x else -1
            else:
                key = "ArrowDown" if next_y > current_y else "ArrowUp"
                target_value = next_y
                target_axis = "y"
                direction = 1 if next_y > current_y else -1
            if segments and segments[-1][0] == key:
                previous_key, _, previous_axis, previous_direction, previous_distance, _, _ = segments[-1]
                segments[-1] = (
                    previous_key,
                    target_value,
                    previous_axis,
                    previous_direction,
                    previous_distance + abs(next_x - current_x) + abs(next_y - current_y),
                    next_x,
                    next_y,
                )
            else:
                segments.append(
                    (
                        key,
                        target_value,
                        target_axis,
                        direction,
                        abs(next_x - current_x) + abs(next_y - current_y),
                        next_x,
                        next_y,
                    )
                )

        for key, target_value, target_axis, direction, distance, expected_x, expected_y in segments:
            stop_margin = self._waypoint_stop_margin(route_name, distance)
            page.keyboard.down(key)
            try:
                page.wait_for_function(
                    """(target) => {
                      const canvas = document.querySelector('canvas')
                      const state = canvas?.dataset.gameState
                      if (state === 'won' || state === 'lost') return true
                      const position = Number(canvas?.dataset[
                        target.axis === 'x' ? 'playerX' : 'playerY'
                      ])
                      return target.direction > 0
                        ? position >= target.value - target.stopMargin
                        : position <= target.value + target.stopMargin
                    }""",
                    arg={
                        "axis": target_axis,
                        "value": target_value,
                        "direction": direction,
                        "stopMargin": stop_margin,
                    },
                    timeout=max(1000, round(distance / speed * 1000) + 1000),
                )
            except PlaywrightTimeoutError:
                terminal = self._snapshot(canvas)
                if terminal.get("gameState") == "won":
                    self._last_platformer_detail = (
                        f"{route_name} path traversed {len(path) - 1} steps and reached won at "
                        f"({terminal.get('playerX')},{terminal.get('playerY')})."
                    )
                    return True
                self._last_platformer_detail = (
                    f"{route_name} movement did not reach {target_axis}={target_value:.0f} "
                    f"using {key}; expected near ({expected_x:.0f},{expected_y:.0f}), "
                    f"observed ({terminal.get('playerX')},{terminal.get('playerY')})."
                )
                return False
            finally:
                page.keyboard.up(key)
            observed = self._snapshot(canvas)
            actual_x = float(observed["playerX"])
            actual_y = float(observed["playerY"])
            if observed.get("gameState") in {"won", "lost"}:
                self._last_platformer_detail = (
                    f"{route_name} path traversed {len(path) - 1} grid steps and ended in "
                    f"{observed.get('gameState')} at ({actual_x:.0f},{actual_y:.0f})."
                )
                return observed.get("gameState") == "won"
            if (
                abs(actual_x - expected_x) > 20
                or abs(actual_y - expected_y) > 20
            ):
                self._last_platformer_detail = (
                    f"{route_name} path deviated at ({actual_x:.0f},{actual_y:.0f}); "
                    f"expected near ({expected_x:.0f},{expected_y:.0f})."
                )
                return False

        page.wait_for_timeout(300)
        final = self._snapshot(canvas)
        final_x = float(final["playerX"])
        final_y = float(final["playerY"])
        reached = abs(final_x - target_x) + abs(final_y - target_y) <= 36
        self._last_platformer_detail = (
            f"{route_name} path traversed {len(path) - 1} observed grid steps; "
            f"player ended at ({final_x:.0f},{final_y:.0f}) for target "
            f"({target_x:.0f},{target_y:.0f})."
        )
        return reached

    def _navigate_top_down(
        self,
        page,
        canvas,
        target_x: float,
        target_y: float,
        runtime_config: dict[str, Any],
        level: dict[str, Any],
    ) -> bool:
        blockers = [
            *level.get("walls", []),
            *(
                {
                    "position": scenery["position"],
                    "width": scenery["width"],
                    "height": scenery["height"],
                }
                for scenery in level.get("scenery", [])
                if scenery.get("solid")
            ),
            *(
                {
                    "position": obstacle["position"],
                    "width": obstacle["width"],
                    "height": obstacle["height"],
                }
                for obstacle in level.get("obstacles", [])
                if obstacle.get("movement", "static") == "static"
            ),
        ]
        return self._navigate_maze(
            page,
            canvas,
            target_x,
            target_y,
            runtime_config,
            {"walls": blockers},
            route_name="Top-down",
        )

    def _start_frontend(self) -> subprocess.Popen:
        node = shutil.which("node")
        if node is None:
            raise OSError("Node.js is required to start the frontend playtest server.")
        vite = FRONTEND_ROOT / "node_modules" / "vite" / "bin" / "vite.js"
        if not vite.is_file():
            raise OSError("Frontend Vite dependencies are not installed.")
        command = [node, str(vite), "--host", "127.0.0.1", "--port", "5173", "--strictPort"]
        process = subprocess.Popen(
            command,
            cwd=FRONTEND_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + min(
            self.startup_timeout_seconds,
            max(0.0, self._deadline - time.monotonic())
            if self._deadline is not None
            else self.startup_timeout_seconds,
        )
        try:
            while time.monotonic() < deadline:
                if self._budget_expired():
                    raise TimeoutError(
                        "The overall browser playtest time budget was exceeded."
                    )
                if process.poll() is not None:
                    raise OSError("Frontend playtest server exited before becoming ready.")
                if self._frontend_ready():
                    return process
                time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
            raise TimeoutError("Frontend playtest server did not become ready in time.")
        except (OSError, TimeoutError):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise

    def _frontend_ready(self) -> bool:
        try:
            with urlopen(f"{self.frontend_url}/playtest.html", timeout=1) as response:
                return response.status == 200 and b"GameionX Browser Playtest" in response.read()
        except (URLError, OSError, TimeoutError):
            return False

    @staticmethod
    def _snapshot(canvas) -> dict[str, str]:
        return canvas.evaluate(
            "(element) => ({ ...element.dataset })"
        )

    @staticmethod
    def _object_counts(runtime: dict[str, Any]) -> dict[str, int]:
        counts = {
            "player": 1,
            "collectibles": 0,
            "enemies": 0,
            "targets": 0,
            "goal": 0,
            "obstacles": 0,
            "platforms": 0,
            "walls": 0,
            "scenery": 0,
            "hazards": 0,
        }
        for level in runtime.get("levels", []):
            counts["collectibles"] += len(level.get("collectibles", []))
            counts["enemies"] += len(level.get("enemies", []))
            counts["targets"] += len(level.get("targets", []))
            counts["goal"] += int(bool(level.get("goal")))
            obstacle_count = len(level.get("obstacles", []))
            counts["obstacles"] += (
                (obstacle_count + 1) // 2
                if runtime.get("player", {}).get("movement_mode") == "flap"
                else obstacle_count
            )
            counts["platforms"] += len(level.get("platforms", []))
            counts["walls"] += len(level.get("walls", []))
            counts["scenery"] += len(level.get("scenery", []))
            counts["hazards"] += len(level.get("hazards", []))
        return counts

    @staticmethod
    def _movement_key(runtime: dict[str, Any], snapshot: dict[str, str]) -> str:
        if runtime.get("player", {}).get("movement_mode") == "flap":
            return "Space"
        if runtime.get("player", {}).get("movement_mode") == "racing":
            return "ArrowLeft" if float(snapshot.get("playerX", 400)) > 400 else "ArrowRight"
        return "ArrowLeft" if float(snapshot.get("playerX", 400)) > 400 else "ArrowRight"

    @staticmethod
    def _unavailable_report(
        spec: dict[str, Any],
        runtime_config: dict[str, Any],
        reason: str,
    ) -> PlaytestReport:
        tests = [
            PlaytestCase(
                name="browser_load_without_runtime_errors",
                expected_behavior="The Phaser scene loads without browser runtime errors.",
                actual_behavior="NOT TESTED: browser runtime could not start.",
                status="NOT TESTED",
                failure_details=reason,
            ),
            PlaytestCase(
                name="player_and_required_objects_render",
                expected_behavior="The player and configured game objects appear.",
                actual_behavior="NOT TESTED: browser runtime could not start.",
                status="NOT TESTED",
                failure_details=reason,
            ),
            PlaytestCase(
                name="movement_controls_respond",
                expected_behavior="The configured movement controls move the player.",
                actual_behavior="NOT TESTED: browser runtime could not start.",
                status="NOT TESTED",
                failure_details=reason,
            ),
        ]
        win_condition = runtime_config.get("win_condition")
        if win_condition in {"collect_target", "reach_goal", "defeat_target", "survive"}:
            tests.append(
                PlaytestCase(
                    name="win_condition_completion",
                    expected_behavior=f"Complete the {win_condition} objective.",
                    actual_behavior="NOT TESTED: browser runtime could not start.",
                    status="NOT TESTED",
                    failure_details=reason,
                )
            )
        return BrowserPlaytestRunner._report(
            tests,
            browser="Chromium (Google Chrome; unavailable)",
            runtime_tested=False,
        )

    @staticmethod
    def _report(
        tests: list[PlaytestCase],
        *,
        browser: str,
        runtime_tested: bool,
    ) -> PlaytestReport:
        required_tests = [test for test in tests if test.required]
        if any(test.status == "FAIL" for test in required_tests):
            status = "fail"
        elif all(test.status == "PASS" for test in required_tests):
            status = "pass"
        else:
            status = "unverified"
        counts = {
            result: sum(test.status == result for test in tests)
            for result in ("PASS", "FAIL", "NOT TESTED")
        }
        return PlaytestReport(
            browser=browser,
            status=status,
            runtime_tested=runtime_tested,
            tests=tests,
            summary=(
                f"Browser playtest: {counts['PASS']} passed, {counts['FAIL']} failed, "
                f"{counts['NOT TESTED']} not tested; required checks are {status}."
            ),
        )
