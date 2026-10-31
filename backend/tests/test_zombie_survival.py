from app.agents.game_builder import GameBuilderAgent
from app.agents.game_orchestrator import GameOrchestrator
from app.agents.game_planner import GamePlannerAgent
from app.provider import DevelopmentMockProvider


ZOMBIE_PROMPT = (
    "A top-down zombie survival game in an abandoned city with visible buildings. "
    "Move with WASD and arrow keys, shoot chasing zombies, earn score, and survive."
)


def test_planner_preserves_zombie_requirements_and_does_not_relabel_racing() -> None:
    planner = GamePlannerAgent()
    plan = planner.plan(ZOMBIE_PROMPT)
    assert plan.genre_intent == "top_down_survival"
    assert plan.entities == ("zombie", "building")
    assert {"projectiles", "chasing_enemies", "survival"} <= set(plan.mechanics)
    assert planner.plan("A racing game with a timer").genre_intent == "racing"


def test_zombie_survival_composes_existing_data_driven_runtime_mechanics() -> None:
    report = GameOrchestrator(DevelopmentMockProvider()).generate(
        ZOMBIE_PROMPT, "beginner"
    )
    spec = report.spec
    runtime = GameBuilderAgent().build(spec)
    level = runtime.levels[0]

    assert spec.game_type == "dodge_arena"
    assert spec.camera == "top_down"
    assert spec.player.movement_mode == "four_direction"
    assert spec.player.appearance == "explorer"
    assert spec.fire_control == "space"
    assert spec.win_condition.type == "survive"
    assert spec.lose_condition.type == "health_depleted"
    assert len(level.hazards) == 3
    assert all(item.kind == "zombie" and item.movement == "chasing" for item in level.hazards)
    assert sum(item.kind == "building" for item in level.scenery) >= 4
    assert report.qa_report["status"] == "unverified"
    assert report.qa_report["runtime_tested"] is True
    assert report.qa_report["gameplay_status"] == "unverified"
    definition = report.game_definition
    assert definition is not None
    assert definition.genre == "top_down_survival"
    mechanics = {item.kind for item in definition.mechanics}
    assert {"four_direction_movement", "projectiles", "chasing_enemies", "enemy_waves", "timer"} <= mechanics
    assert definition.ui.show_score and definition.ui.show_health and definition.ui.show_timer
    assert sum(
        item.kind == "zombie"
        for item in definition.levels[0].objects
    ) == 3
