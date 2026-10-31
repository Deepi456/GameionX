import pytest
from pydantic import ValidationError

from app.agents.game_builder import GameBuilderAgent
from app.models import GameDefinitionV2, GameSpec, GenerateResponse


def test_game_spec_v1_adapts_to_versioned_declarative_definition() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "Forest Explorer",
            "description": "Collect the coin and reach the chest.",
            "game_type": "platform_adventure",
            "theme": "forest",
            "world_width": 1200,
            "world_height": 720,
            "player": {"start": {"x": 0.1, "y": 0.8}, "movement_mode": "platformer"},
            "levels": [
                {
                    "id": "level-1",
                    "name": "Forest",
                    "background": "#123456",
                    "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
                    "collectibles": [{"position": {"x": 0.5, "y": 0.85}, "kind": "coin"}],
                    "collectible_target": 1,
                    "goal": {"position": {"x": 0.9, "y": 0.8}, "kind": "chest"},
                }
            ],
            "win_condition": {"type": "reach_goal"},
            "lose_condition": {"type": "health_depleted"},
        }
    )

    builder = GameBuilderAgent()
    runtime = builder.build(spec)
    definition = builder.to_game_definition(runtime)

    assert definition.schema_version == "2.0"
    assert definition.source_schema_version == spec.schema_version
    assert definition.genre == spec.game_type
    assert definition.theme == "forest"
    assert (definition.world_width, definition.world_height) == (1200, 720)
    assert definition.physics["gravity_y"] == spec.player.gravity
    assert definition.win.condition == "reach_goal"
    assert definition.win.requires == ["collectibles:1"]
    assert {obj.kind for obj in definition.levels[0].objects} >= {
        "platform",
        "coin",
        "chest",
    }
    platform = next(obj for obj in definition.levels[0].objects if obj.kind == "platform")
    assert platform.anchor == "top_left"
    assert platform.position.x == 0
    assert platform.size is not None and platform.size.width == 1
    assert {mechanic.kind for mechanic in definition.mechanics} >= {
        "platform_movement",
        "jumping",
        "gravity",
        "goal_interaction",
    }
    assert definition.ui.show_score is True
    assert definition.ui.show_health is True


def test_v2_definition_rejects_executable_or_unknown_fields() -> None:
    definition = GameBuilderAgent().to_game_definition(
        GameBuilderAgent().build(
            {
                "title": "Safe",
                "description": "Declarative only.",
                "player": {},
                "levels": [
                    {
                        "id": "level-1",
                        "name": "Start",
                        "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
                    }
                ],
                "win_condition": {"type": "survive", "target": 10},
                "lose_condition": {"type": "health_depleted"},
            }
        )
    ).model_dump()

    definition["script"] = "return new Function('...')()"
    with pytest.raises(ValidationError):
        GameDefinitionV2.model_validate(definition)


def test_time_expiry_loss_condition_registers_timer_capability() -> None:
    builder = GameBuilderAgent()
    runtime = builder.build(
        {
            "title": "Timed Game",
            "description": "The player loses when time expires.",
            "player": {},
            "levels": [
                {
                    "id": "level-1",
                    "name": "Start",
                    "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
                }
            ],
            "win_condition": {"type": "reach_goal"},
            "lose_condition": {"type": "time_expired", "target": 3},
        }
    )
    definition = builder.to_game_definition(runtime)

    assert "timer" in {mechanic.kind for mechanic in definition.mechanics}


def test_legacy_generate_response_remains_valid_without_v2_definition() -> None:
    response = GenerateResponse.model_validate(
        {
            "provider": "development-mock",
            "spec": {
                "title": "Legacy",
                "description": "Existing client response.",
                "player": {},
                "levels": [
                    {
                        "id": "level-1",
                        "name": "Start",
                        "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
                    }
                ],
                "win_condition": {"type": "reach_goal"},
                "lose_condition": {"type": "health_depleted"},
            },
            "runtime_config": {},
            "qa_report": {},
        }
    )

    assert response.spec.schema_version == "1.0"
    assert response.game_definition is None
