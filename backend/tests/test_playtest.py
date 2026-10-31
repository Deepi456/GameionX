import time

import pytest

from app.agents.game_builder import GameBuilderAgent
from app.models import GameSpec
from app.playtest import BrowserPlaytestRunner, _normalized_bounds
from app.provider import DevelopmentMockProvider


def _run_platformer_layout(
    *,
    platforms: list[dict],
    collectible_position: dict,
    goal_position: dict | None,
    win_condition: dict,
):
    level = {
        "id": "layout",
        "name": "Platform boundary",
        "platforms": platforms,
        "collectibles": [{"position": collectible_position, "kind": "gem"}],
        "collectible_target": 1,
    }
    if goal_position is not None:
        level["goal"] = {"position": goal_position, "kind": "beacon"}
    spec = GameSpec.model_validate(
        {
            "title": "Platform Boundary Browser Test",
            "description": "Navigate static platforms and verify the objective.",
            "game_type": "platform_adventure",
            "camera": "side_scroller",
            "theme": "forest",
            "player": {
                "start": {"x": 0.1, "y": 0.8},
                "speed": 240,
                "jump_velocity": 450,
                "movement_mode": "platformer",
                "gravity": 900,
            },
            "levels": [level],
            "win_condition": win_condition,
            "lose_condition": {"type": "health_depleted"},
        }
    )
    builder = GameBuilderAgent()
    runtime = builder.build(spec)
    return BrowserPlaytestRunner().run(
        spec=spec.model_dump(mode="json"),
        runtime_config=runtime.model_dump(mode="json"),
        game_definition=builder.to_game_definition(runtime).model_dump(mode="json"),
    )


def test_unautomated_enemy_and_lose_checks_prevent_playable_status() -> None:
    runner = BrowserPlaytestRunner()
    tests = []

    runner._test_interactions(
        page=None,
        spec={},
        runtime_config={
            "win_condition": "reach_goal",
            "lose_condition": "health_depleted",
            "levels": [{"enemies": [{"id": "enemy"}], "hazards": []}],
        },
        tests=tests,
    )
    report = runner._report(
        tests,
        browser="Chromium",
        runtime_tested=True,
    )

    by_name = {test.name: test for test in report.tests}
    assert by_name["enemy_contact_collision"].required is True
    assert by_name["game_over_and_restart"].required is True
    assert report.status == "unverified"


@pytest.mark.parametrize(
    ("game_type", "movement_mode", "method_name"),
    [
        ("platform_adventure", "platformer", "_navigate_platformer"),
        ("maze", "four_direction", "_navigate_maze"),
        ("top_down_collection", "four_direction", "_navigate_top_down"),
    ],
)
def test_reach_goal_uses_navigation_for_game_type(
    monkeypatch: pytest.MonkeyPatch,
    game_type: str,
    movement_mode: str,
    method_name: str,
) -> None:
    runner = BrowserPlaytestRunner()
    page = object()
    canvas = object()
    level = {"id": "level-1"}
    runtime_config = {"world_width": 800, "world_height": 480}
    calls = []

    def navigate(*args):
        calls.append(args)
        return True

    monkeypatch.setattr(runner, method_name, navigate)

    assert runner._navigate_goal(
        game_type=game_type,
        movement_mode=movement_mode,
        page=page,
        canvas=canvas,
        level=level,
        target_x=320,
        target_y=180,
        runtime_config=runtime_config,
    )
    assert len(calls) == 1
    if method_name == "_navigate_platformer":
        assert calls[0] == (page, canvas, level, 320, 180, runtime_config)
    else:
        assert calls[0] == (page, canvas, 320, 180, runtime_config, level)


def test_maze_navigation_uses_rendered_wall_bounds() -> None:
    left, top, right, bottom = _normalized_bounds(
        {"x": 0.25, "y": 0.15},
        0.05,
        0.55,
        800,
        480,
    )

    assert (left, top, right, bottom) == (200, 72, 240, 336)
    assert 184 + 15 < left
    assert 191 + 15 > left
    assert 312 - 19 < bottom


def test_top_down_navigation_uses_rendered_scenery_bounds() -> None:
    left, top, right, bottom = _normalized_bounds(
        {"x": 0.46, "y": 0.54},
        0.18,
        0.09,
        1600,
        960,
    )

    assert (left, top, right, bottom) == pytest.approx(
        (736, 518.4, 1024, 604.8)
    )
    assert 956 - 15 < right
    assert 956 + 15 > left
    assert 624 - 19 > bottom
    assert 499 + 19 < bottom


def test_top_down_waypoint_release_accounts_for_observed_frame_overshoot() -> None:
    runner = BrowserPlaytestRunner()
    water_left = 0.46 * 1600
    waypoint_x = 711
    player_half_width = 15
    observed_overshoot = 13
    stop_margin = runner._waypoint_stop_margin("Top-down", 16)

    assert stop_margin == 8
    assert waypoint_x - stop_margin + observed_overshoot + player_half_width < water_left
    assert runner._waypoint_stop_margin("Top-down", 64) == 12
    assert runner._waypoint_stop_margin("Maze", 64) == 2


def test_unexpected_browser_error_returns_unverified_report(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = BrowserPlaytestRunner()
    monkeypatch.setattr(runner, "_frontend_ready", lambda: True)

    def fail_browser(**kwargs):
        raise RuntimeError("unexpected harness bug")

    monkeypatch.setattr(runner, "_run_browser", fail_browser)

    report = runner.run(
        spec={},
        runtime_config={"win_condition": "reach_goal"},
        game_definition=None,
    )

    assert report.status == "unverified"
    assert report.runtime_tested is False
    assert "unexpected harness bug" in caplog.text
    assert "Traceback" in caplog.text


def test_playtest_budget_marks_unfinished_checks_not_tested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = BrowserPlaytestRunner(budget_seconds=0.01)

    def delayed_frontend_probe() -> bool:
        time.sleep(0.02)
        return True

    monkeypatch.setattr(runner, "_frontend_ready", delayed_frontend_probe)
    report = runner.run(
        spec={},
        runtime_config={
            "win_condition": "reach_goal",
            "lose_condition": "health_depleted",
            "levels": [{"goal": {}, "collectibles": [{"id": "coin"}]}],
        },
        game_definition=None,
    )

    assert report.status == "unverified"
    assert report.runtime_tested is False
    assert {test.name for test in report.tests} >= {
        "browser_load_without_runtime_errors",
        "player_and_required_objects_render",
        "movement_controls_respond",
        "collectible_collision_and_objective_progress",
        "win_condition_completion",
    }
    assert all(test.status == "NOT TESTED" for test in report.tests)


def test_real_chrome_verifies_representative_platform_route_boundaries() -> None:
    ground = {"x": 0, "y": 0.9, "width": 0.22, "height": 0.1}
    supported_layouts = {
        "small_gap": [
            ground,
            {"x": 0.26, "y": 0.72, "width": 0.2, "height": 0.04},
        ],
        "larger_conservative_gap": [
            ground,
            {"x": 0.34, "y": 0.72, "width": 0.2, "height": 0.04},
        ],
        "different_platform_heights": [
            {"x": 0, "y": 0.9, "width": 0.28, "height": 0.1},
            {"x": 0.32, "y": 0.74, "width": 0.18, "height": 0.04},
            {"x": 0.57, "y": 0.64, "width": 0.22, "height": 0.04},
        ],
    }
    for name, platforms in supported_layouts.items():
        target_platform = platforms[-1]
        collectible = {
            "x": target_platform["x"] + target_platform["width"] * 0.45,
            "y": target_platform["y"] - 0.04,
        }
        goal = {
            "x": target_platform["x"] + target_platform["width"] * 0.75,
            "y": target_platform["y"] - 0.04,
        }
        report = _run_platformer_layout(
            platforms=platforms,
            collectible_position=collectible,
            goal_position=goal,
            win_condition={"type": "reach_goal"},
        )
        results = {case.name: case for case in report.tests}
        assert report.status == "pass", f"{name}: {report.model_dump_json(indent=2)}"
        assert results["collectible_collision_and_objective_progress"].status == "PASS", (
            f"{name}: {report.model_dump_json(indent=2)}"
        )
        assert results["win_condition_completion"].status == "PASS", (
            f"{name}: {report.model_dump_json(indent=2)}"
        )
        landing = results["platformer_platform_landings"]
        assert landing.status == "PASS", f"{name}: {landing.model_dump_json(indent=2)}"
        assert "planned" in landing.actual_behavior
        if name == "different_platform_heights":
            assert "0->1" in landing.actual_behavior
            assert "1->2" in landing.actual_behavior

    unsupported_collectible = _run_platformer_layout(
        platforms=[
            {"x": 0, "y": 0.9, "width": 1, "height": 0.1},
            {"x": 0.65, "y": 0.6, "width": 0.2, "height": 0.04},
        ],
        collectible_position={"x": 0.75, "y": 0.52},
        goal_position=None,
        win_condition={"type": "collect_target", "target": 1},
    )
    unsupported_results = {case.name: case for case in unsupported_collectible.tests}
    assert unsupported_results[
        "collectible_collision_and_objective_progress"
    ].status == "NOT TESTED", unsupported_collectible.model_dump_json(indent=2)
    assert unsupported_collectible.status == "unverified"

    unsupported_goal = _run_platformer_layout(
        platforms=[
            {"x": 0, "y": 0.9, "width": 1, "height": 0.1},
            {"x": 0.65, "y": 0.6, "width": 0.2, "height": 0.04},
        ],
        collectible_position={"x": 0.25, "y": 0.82},
        goal_position={"x": 0.75, "y": 0.52},
        win_condition={"type": "reach_goal"},
    )
    goal_results = {case.name: case for case in unsupported_goal.tests}
    assert goal_results["collectible_collision_and_objective_progress"].status == "PASS", (
        unsupported_goal.model_dump_json(indent=2)
    )
    assert goal_results["win_condition_completion"].status == "NOT TESTED", (
        unsupported_goal.model_dump_json(indent=2)
    )
    assert unsupported_goal.status == "unverified"


def test_real_chrome_verifies_platformer_movement_jump_and_collection() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "Platformer Browser Test",
            "description": "Run and jump to collect the gem.",
            "game_type": "platform_adventure",
            "camera": "side_scroller",
            "theme": "forest",
            "player": {
                "start": {"x": 0.1, "y": 0.8},
                "speed": 240,
                "jump_velocity": 450,
                "movement_mode": "platformer",
                "gravity": 900,
            },
            "levels": [
                {
                    "id": "level-1",
                    "name": "Ground Test",
                    "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
                    "collectibles": [
                        {"position": {"x": 0.45, "y": 0.82}, "kind": "gem"}
                    ],
                    "collectible_target": 1,
                }
            ],
            "win_condition": {"type": "collect_target", "target": 1},
            "lose_condition": {"type": "health_depleted"},
        }
    )
    builder = GameBuilderAgent()
    runtime = builder.build(spec)
    definition = builder.to_game_definition(runtime)

    report = BrowserPlaytestRunner().run(
        spec=spec.model_dump(mode="json"),
        runtime_config=runtime.model_dump(mode="json"),
        game_definition=definition.model_dump(mode="json"),
    )

    results = {case.name: case for case in report.tests}
    assert report.runtime_tested is True
    assert results["movement_controls_respond"].status == "PASS"
    assert results["platformer_jump"].status == "PASS", report.model_dump_json(indent=2)
    assert results["collectible_collision_and_objective_progress"].status == "PASS", (
        report.model_dump_json(indent=2)
    )


def test_real_chrome_collects_elevated_items_across_platforms_and_reaches_goal() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "Elevated Platform Browser Test",
            "description": "Jump between platforms, collect all gems, and reach the beacon.",
            "game_type": "platform_adventure",
            "camera": "side_scroller",
            "theme": "forest",
            "player": {
                "start": {"x": 0.1, "y": 0.8},
                "speed": 240,
                "jump_velocity": 450,
                "max_health": 3,
                "movement_mode": "platformer",
                "gravity": 900,
            },
            "levels": [
                {
                    "id": "level-1",
                    "name": "Stepping Stones",
                    "platforms": [
                        {"x": 0, "y": 0.9, "width": 1, "height": 0.1},
                        {"x": 0.12, "y": 0.72, "width": 0.24, "height": 0.04},
                        {"x": 0.48, "y": 0.56, "width": 0.22, "height": 0.04},
                        {"x": 0.73, "y": 0.7, "width": 0.2, "height": 0.04},
                    ],
                    "collectibles": [
                        {"position": {"x": 0.2, "y": 0.64}, "kind": "coin"},
                        {"position": {"x": 0.57, "y": 0.48}, "kind": "gem"},
                        {"position": {"x": 0.82, "y": 0.62}, "kind": "coin"},
                    ],
                    "collectible_target": 3,
                    "goal": {"position": {"x": 0.9, "y": 0.62}, "kind": "beacon"},
                }
            ],
            "win_condition": {"type": "reach_goal"},
            "lose_condition": {"type": "health_depleted"},
        }
    )
    builder = GameBuilderAgent()
    runtime = builder.build(spec)
    definition = builder.to_game_definition(runtime)

    report = BrowserPlaytestRunner().run(
        spec=spec.model_dump(mode="json"),
        runtime_config=runtime.model_dump(mode="json"),
        game_definition=definition.model_dump(mode="json"),
    )

    results = {case.name: case for case in report.tests}
    assert results["platformer_jump"].status == "PASS", report.model_dump_json(
        indent=2
    )
    assert results["collectible_collision_and_objective_progress"].status == "PASS", (
        report.model_dump_json(indent=2)
    )
    assert results["win_condition_completion"].status == "PASS", (
        report.model_dump_json(indent=2)
    )
    assert report.status == "pass", report.model_dump_json(indent=2)


def test_real_chrome_verifies_enemy_damage_game_over_and_restart() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "Enemy Contact Browser Test",
            "description": "Run into the enemy to test health and restart.",
            "game_type": "platform_adventure",
            "camera": "side_scroller",
            "theme": "forest",
            "player": {
                "start": {"x": 0.1, "y": 0.8},
                "speed": 240,
                "max_health": 1,
                "movement_mode": "platformer",
                "gravity": 900,
            },
            "levels": [
                {
                    "id": "level-1",
                    "name": "Enemy Test",
                    "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
                    "enemies": [
                        {
                            "position": {"x": 0.45, "y": 0.82},
                            "damage": 1,
                            "movement": "static",
                        }
                    ],
                    "collectibles": [
                        {"position": {"x": 0.18, "y": 0.82}, "kind": "gem"}
                    ],
                    "collectible_target": 1,
                }
            ],
            "win_condition": {"type": "reach_goal"},
            "lose_condition": {"type": "health_depleted"},
        }
    )
    builder = GameBuilderAgent()
    runtime = builder.build(spec)
    definition = builder.to_game_definition(runtime)

    report = BrowserPlaytestRunner().run(
        spec=spec.model_dump(mode="json"),
        runtime_config=runtime.model_dump(mode="json"),
        game_definition=definition.model_dump(mode="json"),
    )

    results = {case.name: case for case in report.tests}
    assert results.get("enemy_contact_collision") is not None, report.model_dump_json(
        indent=2
    )
    assert results.get("enemy_contact_collision").status == "PASS", (
        report.model_dump_json(indent=2)
    )
    assert results.get("game_over_and_restart") is not None, report.model_dump_json(
        indent=2
    )
    assert results.get("game_over_and_restart").status == "PASS", (
        report.model_dump_json(indent=2)
    )
    restart = results["game_over_and_restart"].actual_behavior
    assert "score 10 -> 0" in restart
    assert "collectibles 1 -> 0" in restart
    assert "player (80,384) expected (80,384)" in restart
    assert "health 1/1" in restart
    assert "state playing" in restart
    assert report.status != "pass"


def test_real_chrome_enemy_contact_reduces_health_before_game_over() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "Enemy Damage Browser Test",
            "description": "Touch the enemy and verify one health point is lost.",
            "game_type": "platform_adventure",
            "camera": "side_scroller",
            "theme": "forest",
            "player": {
                "start": {"x": 0.1, "y": 0.8},
                "speed": 240,
                "max_health": 2,
                "movement_mode": "platformer",
                "gravity": 900,
            },
            "levels": [
                {
                    "id": "level-1",
                    "name": "Damage Test",
                    "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
                    "enemies": [
                        {
                            "position": {"x": 0.2, "y": 0.82},
                            "damage": 1,
                            "movement": "static",
                        }
                    ],
                }
            ],
            "win_condition": {"type": "reach_goal"},
            "lose_condition": {"type": "health_depleted"},
        }
    )
    builder = GameBuilderAgent()
    runtime = builder.build(spec)
    report = BrowserPlaytestRunner().run(
        spec=spec.model_dump(mode="json"),
        runtime_config=runtime.model_dump(mode="json"),
        game_definition=builder.to_game_definition(runtime).model_dump(mode="json"),
    )

    results = {case.name: case for case in report.tests}
    damage = results["enemy_contact_collision"]
    assert damage.status == "PASS", report.model_dump_json(indent=2)
    assert "health changed 2 -> 1" in damage.actual_behavior
    assert "game state: playing" in damage.actual_behavior


@pytest.mark.parametrize(
    ("game_type", "camera", "theme", "world_width", "world_height"),
    [
        ("maze", "grid", "maze", 800, 480),
        ("top_down_collection", "top_down", "forest", 1600, 960),
    ],
)
def test_real_chrome_four_direction_enemy_contact_reduces_health_in_bounds(
    game_type: str,
    camera: str,
    theme: str,
    world_width: int,
    world_height: int,
) -> None:
    spec = GameSpec.model_validate(
        {
            "title": "Four-direction Enemy Contact Browser Test",
            "description": "Navigate into a stationary enemy and take damage.",
            "game_type": game_type,
            "camera": camera,
            "theme": theme,
            "world_width": world_width,
            "world_height": world_height,
            "player": {
                "start": {"x": 0.1, "y": 0.5},
                "speed": 240,
                "max_health": 1,
                "movement_mode": "four_direction",
                "gravity": 0,
            },
            "levels": [
                {
                    "id": "four-direction-enemy",
                    "name": "Enemy Contact",
                    "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
                    "enemies": [
                        {
                            "position": {"x": 0.3, "y": 0.5},
                            "damage": 1,
                            "movement": "static",
                        }
                    ],
                    "collectibles": [
                        {"position": {"x": 0.2, "y": 0.5}, "kind": "gem"}
                    ],
                    "collectible_target": 1,
                }
            ],
            "win_condition": {"type": "reach_goal"},
            "lose_condition": {"type": "health_depleted"},
        }
    )
    builder = GameBuilderAgent()
    runtime = builder.build(spec)
    report = BrowserPlaytestRunner().run(
        spec=spec.model_dump(mode="json"),
        runtime_config=runtime.model_dump(mode="json"),
        game_definition=builder.to_game_definition(runtime).model_dump(mode="json"),
    )

    results = {case.name: case for case in report.tests}
    damage = results["enemy_contact_collision"]
    assert damage.status == "PASS", report.model_dump_json(indent=2)
    assert "health changed 1 -> 0" in damage.actual_behavior
    assert "game state: lost" in damage.actual_behavior
    assert "collision position in bounds: True" in damage.actual_behavior
    restart = results["game_over_and_restart"]
    assert restart.status == "PASS", report.model_dump_json(indent=2)
    assert "score 10 -> 0" in restart.actual_behavior
    assert "collectibles 1 -> 0" in restart.actual_behavior
    assert "pre-restart progress observed: True" in restart.actual_behavior


def test_real_chrome_navigates_generated_maze_collectibles_and_goal() -> None:
    spec = DevelopmentMockProvider().generate(
        "A maze where I collect three coins and reach the exit.",
        "beginner",
    )
    assert spec.game_type == "maze"
    assert spec.player.movement_mode == "four_direction"
    builder = GameBuilderAgent()
    runtime = builder.build(spec)
    report = BrowserPlaytestRunner().run(
        spec=spec.model_dump(mode="json"),
        runtime_config=runtime.model_dump(mode="json"),
        game_definition=builder.to_game_definition(runtime).model_dump(mode="json"),
    )

    results = {case.name: case for case in report.tests}
    assert "collectible_collision_and_objective_progress" in results, (
        report.model_dump_json(indent=2)
    )
    collection = results["collectible_collision_and_objective_progress"]
    assert collection.status == "PASS", report.model_dump_json(indent=2)
    assert "Collected 3/3" in collection.actual_behavior
    assert "Maze path traversed" in collection.actual_behavior
    assert results["win_condition_completion"].status == "PASS", (
        report.model_dump_json(indent=2)
    )
    assert results["enemy_contact_collision"].status == "NOT TESTED"
    assert report.status == "unverified"


def test_real_chrome_navigates_generated_platformer_collectibles_and_goal() -> None:
    spec = DevelopmentMockProvider().generate(
        "A platformer where I collect three coins and reach the exit.",
        "beginner",
    )
    builder = GameBuilderAgent()
    runtime = builder.build(spec)
    report = BrowserPlaytestRunner().run(
        spec=spec.model_dump(mode="json"),
        runtime_config=runtime.model_dump(mode="json"),
        game_definition=builder.to_game_definition(runtime).model_dump(mode="json"),
    )

    results = {case.name: case for case in report.tests}
    assert "collectible_collision_and_objective_progress" in results, (
        report.model_dump_json(indent=2)
    )
    collection = results["collectible_collision_and_objective_progress"]
    assert collection.status == "PASS", report.model_dump_json(indent=2)
    assert "Collected 3/3" in collection.actual_behavior
    assert "Observed platform transitions" in collection.actual_behavior
    assert results["platformer_platform_landings"].status == "PASS", (
        report.model_dump_json(indent=2)
    )
    assert results["win_condition_completion"].status == "PASS", (
        report.model_dump_json(indent=2)
    )
    assert results["enemy_contact_collision"].status == "NOT TESTED"
    assert report.status == "unverified"


def test_real_chrome_navigates_generated_forest_collection_game() -> None:
    spec = DevelopmentMockProvider().generate(
        "A forest exploration game where I collect two gems.",
        "beginner",
    )
    assert spec.game_type == "top_down_collection"
    assert spec.player.movement_mode == "four_direction"
    builder = GameBuilderAgent()
    runtime = builder.build(spec)
    report = BrowserPlaytestRunner().run(
        spec=spec.model_dump(mode="json"),
        runtime_config=runtime.model_dump(mode="json"),
        game_definition=builder.to_game_definition(runtime).model_dump(mode="json"),
    )

    results = {case.name: case for case in report.tests}
    collection = results["collectible_collision_and_objective_progress"]
    assert collection.status == "PASS", report.model_dump_json(indent=2)
    assert "Collected 2/2" in collection.actual_behavior
    assert "Top-down path traversed" in collection.actual_behavior
    assert "final game state: won" in collection.actual_behavior
    assert report.status == "unverified"


def test_real_chrome_verifies_goal_time_expiry_and_restart() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "Goal and Timer Browser Test",
            "description": "Reach the beacon before the timer expires.",
            "game_type": "platform_adventure",
            "camera": "side_scroller",
            "theme": "forest",
            "player": {
                "start": {"x": 0.1, "y": 0.8},
                "speed": 240,
                "movement_mode": "platformer",
                "gravity": 900,
            },
            "levels": [
                {
                    "id": "level-1",
                    "name": "Goal Test",
                    "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
                    "goal": {"position": {"x": 0.55, "y": 0.82}, "kind": "beacon"},
                }
            ],
            "win_condition": {"type": "reach_goal"},
            "lose_condition": {"type": "time_expired", "target": 3},
        }
    )
    builder = GameBuilderAgent()
    runtime = builder.build(spec)
    definition = builder.to_game_definition(runtime)

    report = BrowserPlaytestRunner().run(
        spec=spec.model_dump(mode="json"),
        runtime_config=runtime.model_dump(mode="json"),
        game_definition=definition.model_dump(mode="json"),
    )

    results = {case.name: case for case in report.tests}
    assert results["win_condition_completion"].status == "PASS", (
        report.model_dump_json(indent=2)
    )
    assert results["lose_condition_time_expired"].status == "PASS", (
        report.model_dump_json(indent=2)
    )
    assert results["game_over_and_restart"].status == "PASS", report.model_dump_json(
        indent=2
    )


def test_real_chrome_playtest_checks_loading_controls_collection_and_win() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "Browser Collection Test",
            "description": "Move right to collect the gem and win.",
            "game_type": "top_down_collection",
            "camera": "top_down",
            "theme": "forest",
            "player": {
                "start": {"x": 0.1, "y": 0.5},
                "speed": 240,
                "movement_mode": "four_direction",
                "gravity": 0,
            },
            "levels": [
                {
                    "id": "level-1",
                    "name": "Test Field",
                    "platforms": [
                        {"x": 0, "y": 0.9, "width": 1, "height": 0.1}
                    ],
                    "collectibles": [
                        {"position": {"x": 0.4, "y": 0.5}, "kind": "gem"}
                    ],
                    "collectible_target": 1,
                    "goal": {"position": {"x": 0.85, "y": 0.5}},
                }
            ],
            "win_condition": {"type": "collect_target", "target": 1},
            "lose_condition": {"type": "health_depleted"},
        }
    )
    builder = GameBuilderAgent()
    runtime = builder.build(spec)
    definition = builder.to_game_definition(runtime)

    report = BrowserPlaytestRunner().run(
        spec=spec.model_dump(mode="json"),
        runtime_config=runtime.model_dump(mode="json"),
        game_definition=definition.model_dump(mode="json"),
    )

    results = {case.name: case for case in report.tests}
    assert report.runtime_tested is True
    assert report.browser == "Chromium (Google Chrome)"
    assert report.status == "pass", report.model_dump_json(indent=2)
    assert results["browser_load_without_runtime_errors"].status == "PASS"
    assert results["player_and_required_objects_render"].status == "PASS"
    assert results["movement_controls_respond"].status == "PASS"
    assert results["collectible_collision_and_objective_progress"].status == "PASS"
