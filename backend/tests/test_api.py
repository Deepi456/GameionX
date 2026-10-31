import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agents.game_designer import GameDesignerAgent, gemini_game_spec_schema
from app.agents.game_builder import GameBuilderAgent, GameRuntimeConfig
from app.agents.game_qa import GameQAAgent, QAReport
from app import main
from app.main import app
from app.models import AgentResult, GameSpec, GenerateRequest, WorkflowState
from app.provider import (
    GeminiProvider,
    GeminiProviderError,
    GroqProvider,
    GroqProviderError,
    create_provider,
    groq_game_spec_schema,
)
from google.genai.errors import APIError

client = TestClient(app)


def test_health_reports_development_provider() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["provider"] == "development-mock"


def test_generation_prompt_is_limited_to_500_characters() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest(prompt="x" * 501)


def test_gemini_provider_uses_prompt_cache_and_groq_fallback(monkeypatch) -> None:
    provider = GeminiProvider(api_key="test-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    cache_key = provider._cache_key(
        prompt="Hello World",
        response_schema={},
        model_name=provider.model,
    )
    provider._cache_set(key=cache_key, value='{"title": "Cached"}')
    assert provider.generate_content(prompt="  Hello   World ", response_schema={}) == '{"title": "Cached"}'

    def fake_groq_generate(self, *, prompt: str, response_schema: dict) -> str:
        return '{"title": "Fallback"}'

    monkeypatch.setattr(GroqProvider, "generate_content", fake_groq_generate)
    assert provider._fallback_to_groq("Build a space game", {"type": "object"}) == '{"title": "Fallback"}'


def test_gemini_cache_key_and_cache_hit_miss() -> None:
    provider = GeminiProvider.__new__(GeminiProvider)
    provider.model = "gemini-test"
    provider.fallback_model = None
    provider._prompt_cache = {}
    calls = []

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return type("Response", (), {"text": '{"title":"Cached"}'})()

    provider._types = type(
        "FakeTypes",
        (),
        {
            "GenerateContentConfig": staticmethod(lambda **kwargs: kwargs),
            "HttpOptions": staticmethod(lambda **kwargs: kwargs),
        },
    )
    provider._client = type("Client", (), {"models": FakeModels()})()
    key = provider._cache_key(
        prompt="  One   prompt ",
        response_schema={"type": "object"},
        difficulty="beginner",
        game_style="maze",
        model_name="gemini-test",
    )

    assert key == provider._cache_key(
        prompt="one prompt",
        response_schema={"type": "object"},
        difficulty="beginner",
        game_style="maze",
        model_name="gemini-test",
    )
    assert key != provider._cache_key(
        prompt="one prompt",
        response_schema={"type": "object"},
        difficulty="advanced",
        game_style="maze",
        model_name="gemini-test",
    )
    assert provider._cache_get(key) is None

    options = {
        "prompt": "  One   prompt ",
        "response_schema": {"type": "object"},
        "difficulty": "beginner",
        "game_style": "maze",
    }
    assert provider.generate_content(**options) == '{"title":"Cached"}'
    assert provider._cache_get(key) == '{"title":"Cached"}'
    assert provider.generate_content(**options) == '{"title":"Cached"}'
    assert len(calls) == 1


def test_generate_returns_valid_game_spec() -> None:
    response = client.post(
        "/api/generate",
        json={"prompt": "Build a platform game where I collect gems", "mode": "beginner"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["provider"] == "development-mock"
    assert body["generation_id"] == body["workflow"]["generation_id"]
    assert body["prompt"] == "Build a platform game where I collect gems"
    assert body["spec"]["schema_version"] == "1.0"
    assert body["game_definition"]["schema_version"] == "2.0"
    assert body["game_definition"]["source_schema_version"] == "1.0"
    assert body["spec"]["levels"][0]["collectible_target"] == 3
    assert body["runtime_config"]["schema_version"] == "1.0"
    assert body["runtime_config"]["levels"][0]["collectibles"][1]["kind"] == "gem"
    assert body["qa_report"]["scope"] == "structural"
    assert body["qa_report"]["runtime_tested"] is True
    assert body["qa_report"]["gameplay_status"] in {"unverified", "pass"}
    assert body["qa_report"]["playtest_report"]["browser"].startswith("Chromium")
    assert body["qa_report"]["status"] == "unverified"
    assert body["workflow"]["final_status"] == "unverified"
    assert body["workflow"]["runtime_verified"] is False


def test_game_qa_passes_reachable_structural_spec() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "QA Pass",
            "description": "A structurally consistent game.",
            "player": {"start": {"x": 0.1, "y": 0.8}, "max_health": 3},
            "levels": [
                {
                    "id": "level-1",
                    "name": "Start",
                    "platforms": [
                        {"x": 0, "y": 0.8, "width": 1, "height": 0.1}
                    ],
                    "collectibles": [
                        {"position": {"x": 0.5, "y": 0.75}, "kind": "coin"}
                    ],
                    "collectible_target": 1,
                }
            ],
            "win_condition": {"type": "collect_target", "target": 1},
            "lose_condition": {"type": "health_depleted"},
        }
    )

    report = GameQAAgent().inspect(spec)

    assert isinstance(report, QAReport)
    assert report.status == "unverified"
    assert report.issues == []
    assert report.runtime_tested is False
    assert "not tested" in report.summary


def test_game_qa_reports_warning_for_unreachable_collectible() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "QA Warning",
            "description": "A game with a placement warning.",
            "player": {},
            "levels": [
                {
                    "id": "level-1",
                    "name": "Start",
                    "platforms": [
                        {"x": 0, "y": 0.9, "width": 0.2, "height": 0.1}
                    ],
                    "collectibles": [
                        {"position": {"x": 0.8, "y": 0.2}, "kind": "gem"}
                    ],
                    "collectible_target": 1,
                }
            ],
            "win_condition": {"type": "collect_target", "target": 1},
            "lose_condition": {"type": "health_depleted"},
        }
    )

    report = GameQAAgent().inspect(spec.model_dump())

    assert report.status == "warning"
    assert {issue.code for issue in report.issues} == {"COLLECTIBLES_NOT_ON_PLATFORMS"}


def test_game_qa_reports_runtime_failures() -> None:
    runtime = GameBuilderAgent().build(
        {
            "title": "QA Failure",
            "description": "A structurally valid source spec.",
            "player": {},
            "levels": [
                {
                    "id": "level-1",
                    "name": "Start",
                    "platforms": [
                        {"x": 0, "y": 0.9, "width": 1, "height": 0.1}
                    ],
                    "collectible_target": 0,
                }
            ],
            "win_condition": {"type": "reach_goal"},
            "lose_condition": {"type": "health_depleted"},
        }
    ).model_dump()
    runtime["levels"][0]["platforms"][0]["x"] = 0.5

    report = GameQAAgent().inspect(runtime)

    assert report.status == "fail"
    assert "PLATFORM_OUT_OF_BOUNDS" in {issue.code for issue in report.issues}


def test_game_qa_reports_unsupported_runtime_mechanics() -> None:
    runtime = GameBuilderAgent().build(
        {
            "title": "QA Mechanics",
            "description": "A runtime mechanics check.",
            "player": {},
            "levels": [
                {
                    "id": "level-1",
                    "name": "Start",
                    "platforms": [
                        {"x": 0, "y": 0.9, "width": 1, "height": 0.1}
                    ],
                }
            ],
            "win_condition": {"type": "reach_goal"},
            "lose_condition": {"type": "health_depleted"},
        }
    )
    object.__setattr__(runtime.levels[0].platforms[0], "movement", "teleport")

    report = GameQAAgent().inspect(runtime)

    assert report.status == "fail"
    assert "UNSUPPORTED_MECHANIC" in {issue.code for issue in report.issues}


def test_game_qa_rejects_invalid_input() -> None:
    with pytest.raises(ValueError, match="invalid game configuration"):
        GameQAAgent().inspect({"title": "missing required fields"})


def test_game_builder_converts_supported_mechanics() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "Builder Test",
            "description": "A supported game.",
            "player": {"start": {"x": 0.1, "y": 0.8}, "max_health": 4},
            "levels": [
                {
                    "id": "level-1",
                    "name": "Arena",
                    "background": "#172033",
                    "platforms": [
                        {
                            "x": 0.1,
                            "y": 0.8,
                            "width": 0.5,
                            "height": 0.1,
                            "movement": "horizontal",
                        }
                    ],
                    "collectibles": [
                        {
                            "position": {"x": 0.3, "y": 0.75},
                            "value": 25,
                            "kind": "star",
                        }
                    ],
                    "enemies": [
                        {
                            "position": {"x": 0.7, "y": 0.8},
                            "damage": 2,
                            "movement": "vertical",
                        }
                    ],
                    "collectible_target": 1,
                }
            ],
            "win_condition": {"type": "collect_target", "target": 1},
            "lose_condition": {"type": "health_depleted"},
        }
    )

    runtime = GameBuilderAgent().build(spec)

    assert isinstance(runtime, GameRuntimeConfig)
    assert runtime.player.max_health == 4
    assert runtime.levels[0].platforms[0].movement == "horizontal"
    assert runtime.levels[0].enemies[0].movement == "vertical"
    assert runtime.win_target == 1


def test_game_builder_rejects_invalid_spec() -> None:
    with pytest.raises(ValueError, match="invalid GameSpec"):
        GameBuilderAgent().build({"title": "missing required fields"})


def test_game_builder_rejects_unsupported_mechanic() -> None:
    with pytest.raises(ValueError, match="Unsupported collectible kind"):
        GameBuilderAgent._require_supported(
            "weapon", GameBuilderAgent.supported_collectible_kinds, "collectible kind"
        )


def test_game_builder_rejects_platform_extending_beyond_play_area() -> None:
    with pytest.raises(ValueError, match="normalized play area"):
        GameBuilderAgent().build(
            {
                "title": "Out of Bounds",
                "description": "A platform that does not fit.",
                "player": {},
                "levels": [
                    {
                        "id": "level-1",
                        "name": "Start",
                        "platforms": [
                            {"x": 0.8, "y": 0.8, "width": 0.4, "height": 0.1}
                        ],
                    }
                ],
                "win_condition": {"type": "reach_goal"},
                "lose_condition": {"type": "health_depleted"},
            }
        )


def test_game_builder_normalizes_out_of_platform_collectible() -> None:
    runtime = GameBuilderAgent().build(
        {
            "title": "Misplaced Collectible",
            "description": "A collectible away from platforms.",
            "player": {},
            "levels": [
                {
                    "id": "level-1",
                    "name": "Start",
                    "platforms": [
                        {"x": 0, "y": 0.9, "width": 0.4, "height": 0.1}
                    ],
                    "collectibles": [
                        {
                            "position": {"x": 0.8, "y": 0.8},
                            "value": 10,
                            "kind": "coin",
                        }
                    ],
                }
            ],
            "win_condition": {"type": "reach_goal"},
            "lose_condition": {"type": "health_depleted"},
        }
    )

    position = runtime.levels[0].collectibles[0].position
    assert 0 <= position.x <= 0.4
    assert 0.82 <= position.y <= 0.9


def test_generate_returns_422_for_invalid_request() -> None:
    response = client.post("/api/generate", json={"prompt": "x"})

    assert response.status_code == 422


def test_game_builder_accepts_collectible_just_above_platform() -> None:
    runtime = GameBuilderAgent().build(
        {
            "title": "Placed Collectible",
            "description": "A collectible above a platform.",
            "player": {},
            "levels": [
                {
                    "id": "level-1",
                    "name": "Start",
                    "platforms": [
                        {"x": 0.2, "y": 0.7, "width": 0.4, "height": 0.1}
                    ],
                    "collectibles": [
                        {
                            "position": {"x": 0.4, "y": 0.67},
                            "value": 10,
                            "kind": "coin",
                        }
                    ],
                }
            ],
            "win_condition": {"type": "reach_goal"},
            "lose_condition": {"type": "health_depleted"},
        }
    )

    assert runtime.levels[0].collectibles[0].position.x == 0.4


def test_game_builder_accepts_platform_inside_play_area() -> None:
    runtime = GameBuilderAgent().build(
        {
            "title": "In Bounds",
            "description": "A platform that fits.",
            "player": {},
            "levels": [
                {
                    "id": "level-1",
                    "name": "Start",
                    "platforms": [
                        {"x": 0.6, "y": 0.8, "width": 0.4, "height": 0.2}
                    ],
                }
            ],
            "win_condition": {"type": "reach_goal"},
            "lose_condition": {"type": "health_depleted"},
        }
    )

    assert runtime.levels[0].platforms[0].x == 0.6


def test_generate_rejects_malformed_request() -> None:
    response = client.post("/api/generate", json={"prompt": "x"})

    assert response.status_code == 422


def test_generate_rejects_unsupported_prompt() -> None:
    response = client.post(
        "/api/generate",
        json={"prompt": "Build a multiplayer shooter", "mode": "developer"},
    )

    assert response.status_code == 422
    assert "development mock supports" in response.json()["detail"]
    workflow = response.json()["workflow"]
    assert workflow["final_status"] == "failed"
    assert workflow["agent_outputs"][-1]["status"] == "invalid"
    assert workflow["stage_history"][-1]["name"] == "failed"


def test_failed_workflow_returns_validated_partial_game(monkeypatch: pytest.MonkeyPatch) -> None:
    prompt = "A platform game where I collect gems and reach the exit."
    spec = main._ensure_provider().generate(prompt, "beginner")
    runtime = GameBuilderAgent().build(spec)
    qa_report = GameQAAgent().inspect(runtime)
    workflow = WorkflowState(
        original_prompt=prompt,
        final_status="failed",
        validation_status="failed",
        agent_outputs=[
            AgentResult(
                task_id="design-1",
                agent="GameDesignerAgent",
                status="completed",
                output=spec.model_dump(mode="json"),
            ),
            AgentResult(
                task_id="builder-1",
                agent="GameBuilderAgent",
                status="completed",
                output=runtime.model_dump(mode="json"),
            ),
            AgentResult(
                task_id="qa-1",
                agent="GameQAAgent",
                status="completed",
                output=qa_report.model_dump(mode="json"),
            ),
        ],
    )

    def fail_generation(
        self,
        requested_prompt: str,
        mode: str,
        *,
        bypass_cache: bool = False,
    ):
        raise main.WorkflowExecutionError(
            "Browser gameplay QA failed.", workflow
        )

    monkeypatch.setattr(main.GameOrchestrator, "generate", fail_generation)
    response = client.post(
        "/api/generate",
        json={"prompt": prompt, "mode": "beginner"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["workflow"]["final_status"] == "failed"
    assert body["partial_result"]["spec"]["title"] == spec.title
    assert body["partial_result"]["runtime_config"]["title"] == spec.title
    assert body["partial_result"]["qa_report"]["scope"] == "structural"


def test_game_spec_rejects_unsupported_mechanic() -> None:
    invalid_spec = {
        "title": "Invalid",
        "description": "Invalid movement",
        "player": {},
        "levels": [
            {
                "id": "level-1",
                "name": "Invalid level",
                "platforms": [
                    {"x": 0, "y": 0.9, "width": 1, "height": 0.1, "movement": "teleport"}
                ],
            }
        ],
        "win_condition": {"type": "reach_goal"},
        "lose_condition": {"type": "health_depleted"},
    }

    try:
        GameSpec.model_validate(invalid_spec)
    except ValidationError as error:
        assert "movement" in str(error)
    else:
        raise AssertionError("Unsupported movement should fail GameSpec validation")


class MockGeminiProvider:
    name = "gemini"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def generate_content(
        self,
        *,
        prompt: str,
        response_schema: dict,
        pipeline_step: str = "designing",
        difficulty: str | None = None,
        game_style: str | None = None,
        bypass_cache: bool = False,
        deadline_seconds: float = 90.0,
    ) -> str:
        self.prompts.append(prompt)
        assert "$defs" not in str(response_schema)
        assert "$ref" not in str(response_schema)
        return self.responses.pop(0)


def test_game_designer_validates_mocked_gemini_output() -> None:
    valid_spec = {
        "title": "Gemini Garden",
        "description": "Collect stars in a garden.",
        "player": {},
        "levels": [
            {
                "id": "level-1",
                "name": "Garden",
                "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
                "collectible_target": 0,
            }
        ],
        "win_condition": {"type": "reach_goal"},
        "lose_condition": {"type": "health_depleted"},
    }
    provider = MockGeminiProvider(
        [GameSpec.model_validate(valid_spec).model_dump_json()]
    )

    result = GameDesignerAgent(provider).design("A garden game", "developer")

    assert result.title == "Gemini Garden"
    assert len(provider.prompts) == 1


def test_game_designer_handles_gamespec_missing_schema_version_safely() -> None:
    incomplete_spec = {
        "title": "Missing Version",
        "description": "A game without its schema version.",
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
    }
    provider = MockGeminiProvider(
        [json.dumps(incomplete_spec), json.dumps(incomplete_spec)]
    )

    result = GameDesignerAgent(provider).design("Make a platform game", "developer")

    assert result.schema_version == "1.0"
    assert len(provider.prompts) == 1
    assert "schema_version" in provider.prompts[0]
    assert "Return only JSON conforming to the supplied schema" in provider.prompts[0]


@pytest.mark.parametrize("missing_field", ["id", "name"])
def test_game_designer_rejects_level_missing_required_field(missing_field: str) -> None:
    level = {
        "id": "level-1",
        "name": "Start",
        "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
    }
    del level[missing_field]
    incomplete_spec = {
        "title": "Missing Level Field",
        "description": "A level missing a required property.",
        "player": {},
        "levels": [level],
        "win_condition": {"type": "reach_goal"},
        "lose_condition": {"type": "health_depleted"},
    }
    provider = MockGeminiProvider(
        [json.dumps(incomplete_spec), json.dumps(incomplete_spec)]
    )

    with pytest.raises(ValueError, match="after one retry"):
        GameDesignerAgent(provider).design("Make a platform game", "developer")

    assert len(provider.prompts) == 2
    assert "every level object includes all of these properties" in provider.prompts[0]
    assert "Every level id and name is required" in provider.prompts[0]


def test_game_designer_preserves_explicit_five_collectible_request() -> None:
    base = {
        "title": "Coin Jumper",
        "description": "Collect five golden coins.",
        "player": {},
        "levels": [
            {
                "id": "level-1",
                "name": "Jungle",
                "platforms": [
                    {"x": 0, "y": 0.9, "width": 1, "height": 0.1}
                ],
                "collectibles": [
                    {"position": {"x": x, "y": 0.85}, "kind": "coin"}
                    for x in (0.1, 0.3, 0.5, 0.7, 0.9)
                ],
                "collectible_target": 5,
            }
        ],
        "win_condition": {"type": "collect_target", "target": 5},
        "lose_condition": {"type": "health_depleted"},
    }
    provider = MockGeminiProvider(
        [GameSpec.model_validate(base).model_dump_json()]
    )

    result = GameDesignerAgent(provider).design(
        "Make a game with five golden coins", "beginner"
    )

    assert len(result.levels[0].collectibles) == 5
    assert result.levels[0].collectible_target == 5
    assert result.win_condition.target == 5
    assert "exactly 5 collectibles" in provider.prompts[0]


def test_game_designer_retries_count_mismatch() -> None:
    mismatched = {
        "title": "Coin Jumper",
        "description": "Collect five coins.",
        "player": {},
        "levels": [
            {
                "id": "level-1",
                "name": "Start",
                "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
                "collectibles": [
                    {"position": {"x": 0.2, "y": 0.85}, "kind": "coin"}
                ],
                "collectible_target": 1,
            }
        ],
        "win_condition": {"type": "collect_target", "target": 1},
        "lose_condition": {"type": "health_depleted"},
    }
    valid = {
        **mismatched,
        "levels": [
            {
                **mismatched["levels"][0],
                "collectibles": [
                    {"position": {"x": x, "y": 0.85}, "kind": "coin"}
                    for x in (0.1, 0.3, 0.5, 0.7, 0.9)
                ],
                "collectible_target": 5,
            }
        ],
        "win_condition": {"type": "collect_target", "target": 5},
    }
    provider = MockGeminiProvider(
        [
            GameSpec.model_validate(mismatched).model_dump_json(),
            GameSpec.model_validate(valid).model_dump_json(),
        ]
    )

    result = GameDesignerAgent(provider).design(
        "Make a game with five coins", "beginner"
    )

    assert len(provider.prompts) == 2
    assert len(result.levels[0].collectibles) == 5


def test_builder_preserves_normalized_collectible_positions() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "Position Mapping",
            "description": "Normalized positions.",
            "player": {},
            "levels": [
                {
                    "id": "level-1",
                    "name": "Start",
                    "platforms": [
                        {"x": 0.2, "y": 0.6, "width": 0.4, "height": 0.1}
                    ],
                    "collectibles": [
                        {"position": {"x": 0.4, "y": 0.55}, "kind": "coin"}
                    ],
                    "collectible_target": 1,
                }
            ],
            "win_condition": {"type": "collect_target", "target": 1},
            "lose_condition": {"type": "health_depleted"},
        }
    )

    runtime = GameBuilderAgent().build(spec)

    assert runtime.levels[0].collectibles[0].position.model_dump() == {
        "x": 0.4,
        "y": 0.55,
    }


def test_game_designer_retries_once_after_invalid_output() -> None:
    valid_spec = {
        "title": "Recovered Game",
        "description": "A repaired game.",
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
    }
    provider = MockGeminiProvider(
        ["not json", GameSpec.model_validate(valid_spec).model_dump_json()]
    )

    result = GameDesignerAgent(provider).design("Repair this game", "beginner")

    assert result.title == "Recovered Game"
    assert len(provider.prompts) == 2
    assert "previous response was invalid" in provider.prompts[1]


def test_game_designer_retries_actual_invalid_gamespec_output(caplog) -> None:
    invalid_spec = GameSpec.model_json_schema()
    valid_spec = {
        "title": "Recovered Game",
        "description": "A valid repaired game.",
        "player": {},
        "levels": [
            {
                "id": "level-1",
                "name": "Recovered",
                "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
            }
        ],
        "win_condition": {"type": "reach_goal"},
        "lose_condition": {"type": "health_depleted"},
    }
    provider = MockGeminiProvider(
        [
            json.dumps(invalid_spec),
            GameSpec.model_validate(valid_spec).model_dump_json(),
        ]
    )

    with caplog.at_level("WARNING"):
        result = GameDesignerAgent(provider).design("Repair this game", "developer")

    assert result.title == "Recovered Game"
    assert len(provider.prompts) == 2
    assert "location" in caplog.text
    assert "extra_forbidden" in caplog.text


def test_game_designer_retries_numeric_schema_version_from_gemini(caplog) -> None:
    invalid_spec = {
        "schema_version": 1.0,
        "title": "Invalid Version",
        "description": "The schema version has the wrong type.",
        "game_type": "platform_adventure",
        "player": {},
        "levels": [{
            "id": "level-1",
            "name": "Start",
            "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
        }],
        "win_condition": {"type": "reach_goal"},
        "lose_condition": {"type": "health_depleted"},
        "controls": "keyboard",
    }
    valid_spec = {**invalid_spec, "schema_version": "1.0"}
    provider = MockGeminiProvider([
        json.dumps(invalid_spec),
        json.dumps(valid_spec),
    ])

    with caplog.at_level("WARNING"):
        result = GameDesignerAgent(provider).design("Make a platform game", "developer")

    assert result.schema_version == "1.0"
    assert len(provider.prompts) == 2
    assert "schema_version" in caplog.text
    assert "literal_error" in caplog.text


def test_game_designer_logs_safe_validation_details_for_both_attempts(caplog) -> None:
    invalid_coordinate = {
        "title": "Invalid Coordinates",
        "description": "Invalid first attempt.",
        "player": {"start": {"x": 1.5, "y": 0.5}},
        "levels": [
            {
                "id": "level-1",
                "name": "Start",
                "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
            }
        ],
        "win_condition": {"type": "reach_goal"},
        "lose_condition": {"type": "health_depleted"},
    }
    invalid_background = {
        **invalid_coordinate,
        "title": "Invalid Background",
        "player": {"start": {"x": 0.5, "y": 0.5}},
        "levels": [
            {
                **invalid_coordinate["levels"][0],
                "background": "jungle-green",
            }
        ],
    }
    provider = MockGeminiProvider(
        [json.dumps(invalid_coordinate), json.dumps(invalid_background)]
    )

    with caplog.at_level("WARNING"):
        with pytest.raises(ValueError, match="after one retry"):
            GameDesignerAgent(provider).design("A jungle game", "beginner")

    assert len(provider.prompts) == 2
    assert caplog.text.count("invalid GameSpec") == 2
    assert "player.start.x" in caplog.text
    assert "levels.0.background" in caplog.text
    assert "less_than_equal" in caplog.text
    assert "string_pattern_mismatch" in caplog.text


def test_game_designer_prompt_rejects_schema_definition_response() -> None:
    provider = MockGeminiProvider(
        [GameSpec(title="Valid", description="Valid", player={}, levels=[
            {
                "id": "level-1",
                "name": "Start",
                "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
            }
        ], win_condition={"type": "reach_goal"}, lose_condition={"type": "health_depleted"}).model_dump_json()]
    )

    GameDesignerAgent(provider).design("A platform game", "beginner")

    assert "concrete GameSpec game instance" in provider.prompts[0]
    assert "Do not return a JSON Schema definition" in provider.prompts[0]
    assert "condition objects must include a type field" in provider.prompts[0]


def test_game_designer_rejects_schema_definition_after_one_retry(caplog) -> None:
    schema_json = json.dumps(GameSpec.model_json_schema())
    provider = MockGeminiProvider([schema_json, schema_json])

    with caplog.at_level("WARNING"):
        try:
            GameDesignerAgent(provider).design("A platform game", "beginner")
        except ValueError as error:
            assert str(error) == "Gemini returned invalid GameSpec JSON after one retry."
        else:
            raise AssertionError("Schema definitions must not be accepted as GameSpec data")

    assert len(provider.prompts) == 2
    assert caplog.text.count("invalid GameSpec") == 2


def test_game_designer_does_not_retry_more_than_once() -> None:
    provider = MockGeminiProvider(["not json", "still not json", "ignored"])

    try:
        GameDesignerAgent(provider).design("Invalid game", "beginner")
    except ValueError as error:
        assert "after one retry" in str(error)
    else:
        raise AssertionError("Invalid output should fail after one retry")
    assert len(provider.prompts) == 2


def test_gemini_provider_uses_pydantic_structured_output_schema() -> None:
    valid_json = GameSpec(
        title="Configured Game",
        description="A configured game.",
        player={},
        levels=[
            {
                "id": "level-1",
                "name": "Start",
                "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
            }
        ],
        win_condition={"type": "reach_goal"},
        lose_condition={"type": "health_depleted"},
    ).model_dump_json()
    captured: dict = {}

    class FakeTypes:
        @staticmethod
        def GenerateContentConfig(**kwargs):
            captured["config"] = kwargs
            return kwargs

        @staticmethod
        def HttpOptions(**kwargs):
            return kwargs

    class FakeModels:
        def generate_content(self, **kwargs):
            captured["request"] = kwargs
            return type("Response", (), {"text": valid_json})()

    provider = GeminiProvider.__new__(GeminiProvider)
    provider.model = "gemini-test"
    provider._types = FakeTypes
    provider._client = type("Client", (), {"models": FakeModels()})()

    result = provider.generate_content(
        prompt="Make a game",
        response_schema=gemini_game_spec_schema(),
    )

    assert GameSpec.model_validate_json(result).title == "Configured Game"
    assert "response_json_schema" in captured["config"]
    assert "response_schema" not in captured["config"]
    assert captured["config"]["response_mime_type"] == "application/json"


@pytest.mark.parametrize(
    ("code", "category", "status", "expected_http", "expected_detail"),
    [
        (503, "service_unavailable", "UNAVAILABLE", 503, "temporarily unavailable"),
        (429, "quota_or_rate_limit", "RESOURCE_EXHAUSTED", 429, "quota or rate limit"),
        (403, "authentication", "PERMISSION_DENIED", 502, "authentication failed"),
        (404, "model_unavailable", "NOT_FOUND", 502, "model is unavailable"),
        (400, "invalid_request", "INVALID_ARGUMENT", 502, "rejected the generation"),
    ],
)
def test_gemini_provider_maps_api_errors_safely(
    code: int,
    category: str,
    status: str,
    expected_http: int,
    expected_detail: str,
) -> None:
    error = APIError(code, {"error": {"status": status, "message": "secret=do-not-log"}})
    mapped = GeminiProvider._map_api_error(error)

    assert mapped[0] == expected_http
    assert mapped[1] == category
    assert expected_detail in mapped[2]
    assert "secret" not in mapped[2]


def test_gemini_provider_raises_sanitized_service_error(caplog) -> None:
    captured: dict = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            raise APIError(
                503,
                {
                    "error": {
                        "status": "UNAVAILABLE",
                        "message": "high demand; key=secret-value",
                    }
                },
            )

    provider = GeminiProvider.__new__(GeminiProvider)
    provider.model = "gemini-test"
    provider._types = type(
        "FakeTypes",
        (),
        {
            "GenerateContentConfig": staticmethod(lambda **kwargs: kwargs),
            "HttpOptions": staticmethod(lambda **kwargs: kwargs),
        },
    )
    provider._client = type("Client", (), {"models": FakeModels()})()

    with pytest.raises(GeminiProviderError) as raised:
        provider.generate_content(prompt="Make a game", response_schema={})

    error = raised.value
    assert error.status_code == 503
    assert error.category == "service_unavailable"
    assert str(error) == (
        "Gemini is temporarily unavailable or experiencing high demand. "
        "Try again later."
    )
    assert "secret-value" not in caplog.text


@pytest.mark.parametrize("timeout_error", [TimeoutError("timed out")])
def test_gemini_provider_maps_timeout_to_sanitized_504(timeout_error) -> None:
    class FakeModels:
        def generate_content(self, **kwargs):
            raise timeout_error

    provider = GeminiProvider.__new__(GeminiProvider)
    provider.model = "gemini-test"
    provider._types = type(
        "FakeTypes",
        (),
        {
            "GenerateContentConfig": staticmethod(lambda **kwargs: kwargs),
            "HttpOptions": staticmethod(lambda **kwargs: kwargs),
        },
    )
    provider._client = type("Client", (), {"models": FakeModels()})()

    with pytest.raises(GeminiProviderError) as raised:
        provider.generate_content(prompt="Make a game", response_schema={})

    error = raised.value
    assert error.status_code == 504
    assert error.category == "timeout"
    assert str(error) == "Gemini did not respond before the request timeout. Try again later."
    assert "timed out" not in str(error)


def test_gemini_deadline_bounds_request_timeout_and_rejects_late_response(monkeypatch) -> None:
    import app.provider as provider_module

    class Clock:
        now = 100.0

        def monotonic(self):
            return self.now

    clock = Clock()
    monkeypatch.setattr(provider_module.time, "monotonic", clock.monotonic)
    calls = []

    class FakeTypes:
        @staticmethod
        def HttpOptions(**kwargs):
            return kwargs

        @staticmethod
        def GenerateContentConfig(**kwargs):
            return kwargs

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            timeout_ms = kwargs["config"]["http_options"]["timeout"]
            assert timeout_ms <= 1_000
            clock.now += timeout_ms / 1_000 + 0.01
            return type("Response", (), {"text": '{"title":"Too late"}'})()

    provider = GeminiProvider.__new__(GeminiProvider)
    provider.model = "gemini-test"
    provider.fallback_model = "gemini-fallback-test"
    provider._types = FakeTypes
    provider._client = type("Client", (), {"models": FakeModels()})()
    provider._prompt_cache = {}

    with pytest.raises(GeminiProviderError) as raised:
        provider.generate_content(
            prompt="Make a game",
            response_schema={},
            deadline_seconds=1.0,
            bypass_cache=True,
        )

    assert raised.value.status_code == 503
    assert raised.value.category == "service_unavailable"
    assert len(calls) == 1


def test_gemini_deadline_expiry_before_retry_stops_additional_request(monkeypatch) -> None:
    import app.provider as provider_module

    class Clock:
        now = 200.0

        def monotonic(self):
            return self.now

    clock = Clock()
    monkeypatch.setattr(provider_module.time, "monotonic", clock.monotonic)
    calls = []

    class FakeTypes:
        @staticmethod
        def HttpOptions(**kwargs):
            return kwargs

        @staticmethod
        def GenerateContentConfig(**kwargs):
            return kwargs

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            raise APIError(
                503,
                {"error": {"status": "UNAVAILABLE", "message": "temporary"}},
            )

    provider = GeminiProvider.__new__(GeminiProvider)
    provider.model = "gemini-test"
    provider.fallback_model = None
    provider._types = FakeTypes
    provider._client = type("Client", (), {"models": FakeModels()})()
    provider._prompt_cache = {}
    monkeypatch.setattr(
        GeminiProvider,
        "_sleep_for",
        staticmethod(lambda _delay, deadline=None: setattr(clock, "now", deadline)),
    )
    monkeypatch.setattr(GeminiProvider, "_retry_delay", staticmethod(lambda *_: 1.0))

    with pytest.raises(GeminiProviderError) as raised:
        provider.generate_content(
            prompt="Make a game",
            response_schema={},
            deadline_seconds=1.0,
            bypass_cache=True,
        )

    assert raised.value.status_code == 503
    assert raised.value.category == "service_unavailable"
    assert len(calls) == 1


def test_gemini_deadline_expiry_before_fallback_skips_fallback_model(monkeypatch) -> None:
    import app.provider as provider_module

    class Clock:
        now = 300.0

        def monotonic(self):
            return self.now

    clock = Clock()
    monkeypatch.setattr(provider_module.time, "monotonic", clock.monotonic)
    calls = []
    groq_calls = []
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setattr(
        GroqProvider,
        "generate_content",
        lambda self, **kwargs: groq_calls.append(kwargs),
    )

    class FakeTypes:
        @staticmethod
        def HttpOptions(**kwargs):
            return kwargs

        @staticmethod
        def GenerateContentConfig(**kwargs):
            return kwargs

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs["model"])
            clock.now = 301.0
            raise APIError(
                503,
                {"error": {"status": "UNAVAILABLE", "message": "temporary"}},
            )

    provider = GeminiProvider.__new__(GeminiProvider)
    provider.model = "gemini-test"
    provider.fallback_model = "gemini-fallback-test"
    provider._types = FakeTypes
    provider._client = type("Client", (), {"models": FakeModels()})()
    provider._prompt_cache = {}

    with pytest.raises(GeminiProviderError) as raised:
        provider.generate_content(
            prompt="Make a game",
            response_schema={},
            deadline_seconds=1.0,
            bypass_cache=True,
        )

    assert raised.value.status_code == 503
    assert raised.value.category == "service_unavailable"
    assert calls == ["gemini-test"]
    assert groq_calls == []


def test_groq_fallback_request_uses_shared_deadline(monkeypatch) -> None:
    import app.provider as provider_module

    class Clock:
        now = 400.0

        def monotonic(self):
            return self.now

    clock = Clock()
    monkeypatch.setattr(provider_module.time, "monotonic", clock.monotonic)
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs["timeout"] <= 1.0
        clock.now += kwargs["timeout"] + 0.01
        return FakeResponse()

    monkeypatch.setattr(provider_module.httpx, "post", fake_post)
    provider = GroqProvider(api_key="test-key")

    with pytest.raises(GroqProviderError) as raised:
        provider.generate_content(
            prompt="Make a game",
            response_schema={},
            bypass_cache=True,
            deadline_at=401.0,
        )

    assert raised.value.status_code == 503
    assert raised.value.category == "service_unavailable"
    assert len(calls) == 1


def test_groq_provider_uses_openai_compatible_structured_output(monkeypatch) -> None:
    valid_json = GameSpec(
        title="Groq Game",
        description="A configured game.",
        player={},
        levels=[
            {
                "id": "level-1",
                "name": "Start",
                "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
            }
        ],
        win_condition={"type": "reach_goal"},
        lose_condition={"type": "health_depleted"},
    ).model_dump_json()
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": valid_json}}
                ]
            },
        )

    monkeypatch.setattr("app.provider.httpx.post", fake_post)
    provider = GroqProvider("test-key", "test-model")

    result = provider.generate_content(
        prompt="Make a game",
        response_schema=gemini_game_spec_schema(),
    )

    assert GameSpec.model_validate_json(result).title == "Groq Game"
    assert captured["url"] == GroqProvider.endpoint
    assert captured["kwargs"]["json"]["model"] == "test-model"
    assert (
        captured["kwargs"]["json"]["max_completion_tokens"]
        == GroqProvider.max_completion_tokens
        == 16_384
    )
    assert captured["kwargs"]["json"]["response_format"]["type"] == "json_schema"
    assert captured["kwargs"]["json"]["response_format"]["json_schema"]["strict"] is True
    assert "$defs" not in str(
        captured["kwargs"]["json"]["response_format"]["json_schema"]["schema"]
    )
    authorization = captured["kwargs"]["headers"]["Authorization"]
    assert authorization.startswith("Bearer ")
    assert "test-key" in authorization
    assert authorization != "Bearer "
    schema = captured["kwargs"]["json"]["response_format"]["json_schema"]["schema"]
    assert schema == groq_game_spec_schema()
    assert "schema_version" in schema["required"]
    assert "controls" in schema["required"]
    level_schema = schema["properties"]["levels"]["items"]
    assert level_schema["required"] == list(level_schema["properties"])
    assert {"id", "name", "platforms"}.issubset(level_schema["required"])


@pytest.mark.parametrize("status_code", [429, 503])
def test_groq_retries_rate_limit_and_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    calls = []
    retry_after_values = []

    def fake_post(url, **kwargs):
        calls.append(url)
        request = httpx.Request("POST", url)
        if len(calls) == 1:
            return httpx.Response(
                status_code,
                headers={"Retry-After": "3"},
                request=request,
                json={"error": {"message": "try again"}},
            )
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"title":"Retried"}'}}]},
        )

    def retry_delay(attempt, retry_after):
        retry_after_values.append(retry_after)
        return 0

    monkeypatch.setattr("app.provider.httpx.post", fake_post)
    monkeypatch.setattr(
        GroqProvider,
        "_sleep_for",
        staticmethod(lambda delay, deadline=None: None),
    )
    monkeypatch.setattr(GroqProvider, "_retry_delay", staticmethod(retry_delay))

    result = GroqProvider("test-key").generate_content(
        prompt="retry this",
        response_schema={"type": "object"},
        bypass_cache=True,
    )

    assert '"Retried"' in result
    assert len(calls) == 2
    assert retry_after_values == [3.0]


def test_groq_timeout_retries_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("app.provider.httpx.post", fake_post)
    monkeypatch.setattr(
        GroqProvider,
        "_sleep_for",
        staticmethod(lambda delay, deadline=None: None),
    )
    monkeypatch.setattr(
        GroqProvider,
        "_retry_delay",
        staticmethod(lambda attempt, retry_after: 0),
    )

    with pytest.raises(GroqProviderError) as raised:
        GroqProvider("test-key").generate_content(
            prompt="timeout",
            response_schema={"type": "object"},
            bypass_cache=True,
        )

    assert raised.value.category == "timeout"
    assert len(calls) == 3


def test_groq_schema_is_strict_at_every_object_level() -> None:
    schema = groq_game_spec_schema()
    unsupported = {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "const",
        "default",
    }
    seen_types: set[str] = set()
    seen_enums = False
    seen_nullable = False

    def assert_strict(value: dict) -> None:
        nonlocal seen_enums, seen_nullable
        assert unsupported.isdisjoint(value)
        if value.get("type") == "object":
            properties = value["properties"]
            assert value["additionalProperties"] is False
            assert value["required"] == list(properties)
            for property_schema in properties.values():
                assert_strict(property_schema)
        elif value.get("type") == "array":
            seen_types.add("array")
            assert_strict(value["items"])
        if "enum" in value:
            seen_enums = True
        for option in value.get("anyOf", []):
            if isinstance(option, dict):
                if option.get("type") == "null":
                    seen_nullable = True
                assert_strict(option)

    assert "$defs" not in str(schema)
    assert "$ref" not in str(schema)
    assert_strict(schema)
    assert seen_types == {"array"}
    assert seen_enums
    assert seen_nullable


def test_create_groq_provider_requires_credentials(monkeypatch) -> None:
    monkeypatch.setenv("GAMEIONX_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="GROQ_API_KEY is required"):
        create_provider()


@pytest.mark.parametrize(
    ("exception", "expected_status", "expected_category", "expected_detail"),
    [
        (httpx.ReadTimeout("timed out"), 504, "timeout", "did not respond"),
        (httpx.ConnectError("connection failed"), 502, "connection_error", "connection failed"),
        (httpx.ReadError("read failed"), 502, "read_error", "could not be read"),
        (httpx.WriteError("write failed"), 502, "write_error", "could not be sent"),
        (httpx.NetworkError("network failed"), 502, "network_error", "network request failed"),
    ],
)
def test_groq_provider_maps_timeout_safely(
    monkeypatch, exception, expected_status, expected_category, expected_detail
) -> None:
    def fake_post(*args, **kwargs):
        raise exception

    monkeypatch.setattr("app.provider.httpx.post", fake_post)
    provider = GroqProvider("test-key")

    with pytest.raises(GroqProviderError) as raised:
        provider.generate_content(prompt="Make a game", response_schema={})

    assert raised.value.status_code == expected_status
    assert raised.value.category == expected_category
    assert expected_detail in str(raised.value)
    assert "test-key" not in str(raised.value)
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize(
    ("provider_status", "expected_status", "expected_category", "expected_detail"),
    [
        (401, 502, "authentication", "authentication failed"),
        (429, 429, "quota_or_rate_limit", "quota or rate limit"),
        (500, 503, "service_unavailable", "temporarily unavailable"),
    ],
)
def test_groq_provider_maps_http_errors_safely(
    monkeypatch,
    provider_status,
    expected_status,
    expected_category,
    expected_detail,
) -> None:
    def fake_post(*args, **kwargs):
        return httpx.Response(
            provider_status,
            request=httpx.Request("POST", GroqProvider.endpoint),
            json={"error": "secret-value"},
        )

    monkeypatch.setattr("app.provider.httpx.post", fake_post)
    provider = GroqProvider("test-key")

    with pytest.raises(GroqProviderError) as raised:
        provider.generate_content(prompt="Make a game", response_schema={})

    assert raised.value.status_code == expected_status
    assert raised.value.category == expected_category
    assert expected_detail in str(raised.value)
    assert "secret-value" not in str(raised.value)


def test_groq_provider_logs_sanitized_http_error_details(
    monkeypatch, caplog
) -> None:
    def fake_post(*args, **kwargs):
        return httpx.Response(
            400,
            request=httpx.Request("POST", GroqProvider.endpoint),
            json={
                "error": {
                    "code": "invalid_request",
                    "message": "api_key=secret-value; schema rejected",
                }
            },
        )

    monkeypatch.setattr("app.provider.httpx.post", fake_post)
    provider = GroqProvider("test-key")

    with pytest.raises(GroqProviderError):
        provider.generate_content(prompt="Make a game", response_schema={})

    assert "provider_status=400" in caplog.text
    assert "error_code=invalid_request" in caplog.text
    assert "schema rejected" in caplog.text
    assert "secret-value" not in caplog.text
    assert "test-key" not in caplog.text


def test_gemini_schema_flattens_nested_models_and_preserves_contract() -> None:
    schema = gemini_game_spec_schema()

    assert "$defs" not in str(schema)
    assert "$ref" not in str(schema)
    assert schema["required"] == [
        "title",
        "description",
        "player",
        "levels",
        "win_condition",
        "lose_condition",
    ]
    assert schema["properties"]["levels"]["items"]["properties"]["platforms"][
        "items"
    ]["properties"]["movement"]["enum"] == ["static", "horizontal", "vertical"]
    assert "Normalized coordinate" in schema["properties"]["player"]["properties"][
        "start"
    ]["properties"]["x"]["description"]
    assert "lowercase level identifier" in schema["properties"]["levels"]["items"][
        "properties"
    ]["id"]["description"].lower()
    assert "explicit count" in schema["properties"]["levels"]["items"]["properties"][
        "collectibles"
    ]["description"]
    assert "just above" in GameDesignerAgent._build_prompt(
        "Make five coins", "beginner"
    )
    assert schema["properties"]["schema_version"]["enum"] == ["1.0"]
    assert schema["properties"]["controls"]["enum"] == ["keyboard"]
    assert schema["properties"]["win_condition"]["properties"]["target"]["nullable"] is True
    assert "defeat_target" in schema["properties"]["win_condition"]["properties"]["type"]["enum"]
    assert "palette" in schema["properties"]
    assert schema["properties"]["world_width"]["type"] == "integer"
    assert schema["properties"]["world_height"]["type"] == "integer"
    assert schema["properties"]["palette"]["properties"]["background"]["type"] == "string"
    prompt = GameDesignerAgent._build_prompt("A space shooter", "beginner")
    assert "prompt-specific level layout" in prompt
    assert "projectiles" in prompt
    assert "defeat_target objective" in prompt
    assert "bird appearance" in GameDesignerAgent._build_prompt("A bird game", "beginner")


def test_gamespec_rejects_each_live_failure_shape() -> None:
    base = {
        "title": "Constraint Test",
        "description": "A representative game.",
        "player": {},
        "levels": [
            {
                "id": "level-1",
                "name": "Start",
                "background": "#172033",
                "platforms": [{"x": 0.1, "y": 0.9, "width": 0.5, "height": 0.1}],
            }
        ],
        "win_condition": {"type": "reach_goal"},
        "lose_condition": {"type": "health_depleted"},
    }
    invalid_cases = [
        ("coordinate", {"player": {"start": {"x": 1.1, "y": 0.5}}}),
        (
            "dimension",
            {
                "levels": [
                    {
                        **base["levels"][0],
                        "platforms": [
                            {"x": 0.1, "y": 0.9, "width": 1.1, "height": 0.1}
                        ],
                    }
                ]
            },
        ),
        ("id", {"levels": [{**base["levels"][0], "id": "Level 1"}]}),
        ("background", {"levels": [{**base["levels"][0], "background": "blue"}]}),
        (
            "lose-target",
            {"lose_condition": {"type": "health_depleted", "target": 0}},
        ),
    ]
    for name, changes in invalid_cases:
        with pytest.raises(ValidationError):
            GameSpec.model_validate({**base, **changes})


def test_representative_generated_gamespec_validates() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "Normalized Gem Run",
            "description": "Collect gems across a compact platform level.",
            "player": {"start": {"x": 0.1, "y": 0.8}, "max_health": 3},
            "levels": [
                {
                    "id": "level-1",
                    "name": "Gem Run",
                    "background": "#172033",
                    "platforms": [
                        {
                            "x": 0,
                            "y": 0.9,
                            "width": 1,
                            "height": 0.1,
                            "movement": "static",
                        }
                    ],
                    "collectibles": [
                        {
                            "position": {"x": 0.4, "y": 0.8},
                            "value": 10,
                            "kind": "gem",
                        }
                    ],
                    "enemies": [
                        {
                            "position": {"x": 0.7, "y": 0.8},
                            "damage": 1,
                            "movement": "horizontal",
                        }
                    ],
                    "collectible_target": 1,
                }
            ],
            "win_condition": {"type": "collect_target", "target": 1},
            "lose_condition": {"type": "health_depleted"},
            "controls": "keyboard",
        }
    )

    assert spec.levels[0].collectible_target == 1


def test_generate_endpoint_uses_gemini_agent_without_mock_fallback(
    monkeypatch,
) -> None:
    valid_spec = {
        "title": "Agent Generated",
        "description": "A generated level.",
        "player": {},
        "levels": [
            {
                "id": "level-1",
                "name": "Generated",
                "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
            }
        ],
        "win_condition": {"type": "reach_goal"},
        "lose_condition": {"type": "health_depleted"},
    }
    mocked_provider = MockGeminiProvider(
        [GameSpec.model_validate(valid_spec).model_dump_json()]
    )
    monkeypatch.setattr(main, "provider", mocked_provider)
    monkeypatch.setattr(main, "designer_agent", GameDesignerAgent(mocked_provider))

    response = client.post(
        "/api/generate",
        json={"prompt": "Generate a level", "mode": "developer"},
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "gemini"
    assert response.json()["spec"]["title"] == "Agent Generated"
