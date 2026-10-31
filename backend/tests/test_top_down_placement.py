import pytest

from app.agents.game_builder import GameBuilderAgent
from app.agents.top_down_placement import (
    normalize_top_down_placements,
    validate_top_down_placements,
)
from app.models import GameSpec
from app.provider import DevelopmentMockProvider


def _top_down_spec(
    *,
    collectibles: list[dict],
    scenery: list[dict] | None = None,
    goal: dict | None = None,
    start: dict | None = None,
) -> GameSpec:
    level = {
        "id": "level-1",
        "name": "Forest",
        "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
        "collectibles": collectibles,
        "collectible_target": len(collectibles),
        "scenery": scenery or [],
    }
    if goal is not None:
        level["goal"] = goal
    return GameSpec.model_validate(
        {
            "title": "Forest Placement Test",
            "description": "Explore a forest and collect the items.",
            "game_type": "top_down_collection",
            "camera": "top_down",
            "theme": "forest",
            "world_width": 1600,
            "world_height": 960,
            "player": {
                "start": start or {"x": 0.1, "y": 0.8},
                "appearance": "explorer",
                "movement_mode": "four_direction",
                "gravity": 0,
            },
            "levels": [level],
            "win_condition": {
                "type": "collect_target",
                "target": max(1, len(collectibles)),
            },
            "lose_condition": {"type": "health_depleted"},
        }
    )


def _assert_geometry_clear(spec: GameSpec) -> None:
    for level in spec.levels:
        for scenery in level.scenery:
            if not scenery.solid:
                continue
            left = scenery.position.x * spec.world_width
            top = scenery.position.y * spec.world_height
            right = left + scenery.width * spec.world_width
            bottom = top + scenery.height * spec.world_height
            for collectible in level.collectibles:
                x = collectible.position.x * spec.world_width
                y = collectible.position.y * spec.world_height
                assert x + 14 <= left or x - 14 >= right or y + 14 <= top or y - 14 >= bottom
            if level.goal is not None:
                x = level.goal.position.x * spec.world_width
                y = level.goal.position.y * spec.world_height
                assert x + 23 <= left or x - 23 >= right or y + 23 <= top or y - 23 >= bottom


@pytest.mark.parametrize(
    ("position", "tree"),
    [
        ({"x": 0.52, "y": 0.52}, {"position": {"x": 0.45, "y": 0.44}, "width": 0.12, "height": 0.16, "kind": "tree", "solid": True}),
        ({"x": 0.572, "y": 0.52}, {"position": {"x": 0.45, "y": 0.44}, "width": 0.12, "height": 0.16, "kind": "tree", "solid": True}),
    ],
    ids=["inside-tree", "partially-overlapping-tree"],
)
def test_collectible_inside_or_overlapping_tree_is_repaired(position, tree) -> None:
    spec = _top_down_spec(
        collectibles=[{"position": position, "kind": "gem"}],
        scenery=[tree],
    )

    with pytest.raises(ValueError, match="overlaps an obstacle or is unreachable"):
        validate_top_down_placements(spec)

    repaired = normalize_top_down_placements(spec)
    validate_top_down_placements(repaired)
    _assert_geometry_clear(repaired)
    assert repaired.levels[0].collectibles[0].position != spec.levels[0].collectibles[0].position


def test_collectible_with_insufficient_player_clearance_is_repaired() -> None:
    narrow_passage = [
        {
            "position": {"x": 0.4875, "y": 0},
            "width": 0.0025,
            "height": 1,
            "kind": "tree",
            "solid": True,
        },
        {
            "position": {"x": 0.50875, "y": 0},
            "width": 0.0025,
            "height": 1,
            "kind": "tree",
            "solid": True,
        },
    ]
    spec = _top_down_spec(
        collectibles=[{"position": {"x": 0.499375, "y": 0.5}, "kind": "gem"}],
        scenery=narrow_passage,
    )

    with pytest.raises(ValueError, match="overlaps an obstacle or is unreachable"):
        validate_top_down_placements(spec)

    repaired = normalize_top_down_placements(spec)
    validate_top_down_placements(repaired)
    _assert_geometry_clear(repaired)
    position = repaired.levels[0].collectibles[0].position
    assert position.x != 0.499375 or position.y != 0.5


def test_player_spawn_inside_solid_obstacle_is_rejected() -> None:
    spec = _top_down_spec(
        collectibles=[],
        scenery=[
            {
                "position": {"x": 0.08, "y": 0.76},
                "width": 0.08,
                "height": 0.08,
                "kind": "tree",
                "solid": True,
            }
        ],
    )

    with pytest.raises(ValueError, match="player spawn overlaps a solid obstacle"):
        normalize_top_down_placements(spec)


def test_valid_collectible_placement_is_preserved() -> None:
    spec = _top_down_spec(
        collectibles=[{"position": {"x": 0.35, "y": 0.5}, "kind": "gem"}],
        goal={"position": {"x": 0.9, "y": 0.5}, "kind": "chest"},
    )

    normalized = normalize_top_down_placements(spec)

    assert normalized.levels[0].collectibles[0].position == spec.levels[0].collectibles[0].position
    assert normalized.levels[0].goal == spec.levels[0].goal
    validate_top_down_placements(normalized)
    _assert_geometry_clear(normalized)


def test_unreachable_goal_is_repaired_to_player_reachable_region() -> None:
    dividing_wall = {
        "position": {"x": 0.49, "y": 0},
        "width": 0.02,
        "height": 1,
        "kind": "tree",
        "solid": True,
    }
    spec = _top_down_spec(
        collectibles=[],
        scenery=[dividing_wall],
        goal={"position": {"x": 0.9, "y": 0.5}, "kind": "chest"},
    )

    with pytest.raises(ValueError, match="goal overlaps an obstacle or is unreachable"):
        validate_top_down_placements(spec)

    repaired = normalize_top_down_placements(spec)

    validate_top_down_placements(repaired)
    _assert_geometry_clear(repaired)
    assert repaired.levels[0].goal is not None
    assert repaired.levels[0].goal.position.x < 0.49


def test_generated_forest_places_multiple_collectibles_on_reachable_clear_ground() -> None:
    spec = DevelopmentMockProvider().generate(
        "A top-down forest collection adventure: collect three gems and reach the chest goal.",
        "beginner",
    )

    assert spec.game_type == "top_down_collection"
    original_last = spec.levels[0].collectibles[-1].position
    assert original_last.x == pytest.approx(0.83)
    assert original_last.y == pytest.approx(0.7)

    normalized = normalize_top_down_placements(spec)
    runtime = GameBuilderAgent().build(normalized)

    assert len(runtime.levels[0].collectibles) == 3
    assert runtime.levels[0].collectibles[-1].position != original_last
    validate_top_down_placements(normalized)
    _assert_geometry_clear(normalized)
