import pytest
from pydantic import ValidationError

from app.agents.game_orchestrator import GameOrchestrator, WorkflowExecutionError
from app.agents.game_qa import GameQAAgent, QAIssue, QAReport
from app.agents.game_repair import GameRepairAgent
from app.models import (
    AgentTask,
    GameSpec,
    PlaytestCase,
    PlaytestReport,
    Point,
    RepairChange,
    RepairPatch,
)
from app.playtest import BrowserPlaytestRunner
from app.provider import DevelopmentMockProvider


def _platform_landing_repair_spec() -> GameSpec:
    return GameSpec.model_validate(
        {
            "title": "Platform Landing Repair",
            "description": "Collect a gem and reach the beacon on the upper platform.",
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
                    "id": "landing-repair",
                    "name": "Landing Repair",
                    "platforms": [
                        {"x": 0, "y": 0.9, "width": 0.26, "height": 0.1},
                        {"x": 0.38, "y": 0.72, "width": 0.15, "height": 0.04},
                    ],
                    "collectibles": [
                        {"position": {"x": 0.44, "y": 0.68}, "kind": "gem"}
                    ],
                    "collectible_target": 1,
                    "goal": {
                        "position": {"x": 0.51, "y": 0.68},
                        "kind": "beacon",
                    },
                }
            ],
            "win_condition": {"type": "reach_goal"},
            "lose_condition": {"type": "health_depleted"},
        }
    )


class NotTestedPlaytester:
    @staticmethod
    def run(**kwargs) -> PlaytestReport:
        return PlaytestReport(
            browser="not available in this unit test",
            status="unverified",
            runtime_tested=False,
            tests=[
                PlaytestCase(
                    name="browser_checks",
                    expected_behavior="Browser gameplay checks pass.",
                    actual_behavior="NOT TESTED in this unit test.",
                    status="NOT TESTED",
                )
            ],
            summary="Browser checks were not run.",
        )


def test_structural_and_completed_gameplay_failures_block_but_incomplete_does_not() -> None:
    structural_failure = QAReport(
        status="fail",
        scope="structural",
        runtime_tested=False,
        structural_status="fail",
        gameplay_status="unverified",
        summary="Structural validation failed.",
        issues=[],
    )
    incomplete_failure = QAReport(
        status="fail",
        scope="structural",
        runtime_tested=False,
        structural_status="unverified",
        gameplay_status="fail",
        summary="The browser run did not complete.",
        issues=[],
    )
    incomplete_playtest = PlaytestReport(
        browser="test",
        status="unverified",
        runtime_tested=False,
        tests=[
            PlaytestCase(
                name="browser_check",
                expected_behavior="Gameplay passes.",
                actual_behavior="NOT TESTED: browser run incomplete.",
                status="NOT TESTED",
            )
        ],
        summary="Browser run did not complete.",
    )
    completed_failure = incomplete_failure.model_copy(
        update={"runtime_tested": True}
    )
    attached_incomplete = GameQAAgent.attach_playtest(
        incomplete_failure,
        incomplete_playtest,
    )

    assert GameOrchestrator._has_blocking_failure(structural_failure)
    assert not GameOrchestrator._has_blocking_failure(incomplete_failure)
    assert attached_incomplete.status == "unverified"
    assert attached_incomplete.gameplay_status == "unverified"
    assert attached_incomplete.playtest_report.tests[0].status == "NOT TESTED"
    assert GameOrchestrator._has_blocking_failure(completed_failure)


def test_orchestrator_reports_ordered_generation_stages() -> None:
    report = GameOrchestrator(
        DevelopmentMockProvider(),
        playtester=NotTestedPlaytester(),
    ).generate(
        "A platform game with gems",
        "beginner",
    )

    assert [stage.name for stage in report.stages] == [
        "planning", "planning",
        "designing", "designing",
        "gameplay_logic", "gameplay_logic",
        "world_ui_building", "world_ui_building",
        "runtime_integration", "runtime_integration",
        "validating", "validating",
        "qa_playtest", "qa_playtest",
        "finalizing", "finalizing", "unverified",
    ]
    assert report.spec.schema_version == "1.0"
    assert report.qa_report["runtime_tested"] is False
    assert report.qa_report["status"] == "unverified"
    assert report.workflow is not None
    assert report.workflow.final_status == "unverified"
    assert report.workflow.runtime_verified is False
    assert report.workflow.original_prompt == "A platform game with gems"
    assert all(result.status == "completed" for result in report.workflow.agent_outputs)
    assert all(
        requirement.id.startswith("req-")
        for requirement in report.workflow.plan.requirements
    )
    qa_task = next(
        task
        for task in report.workflow.agent_tasks
        if task.agent == "GameQAAgent"
    )
    assert qa_task.inputs["requirement_ids"] == [
        requirement.id for requirement in report.workflow.plan.requirements
    ]


def test_orchestrator_returns_repaired_top_down_placements_in_spec_and_runtime() -> None:
    class ObstructedForestProvider(DevelopmentMockProvider):
        def generate(self, prompt: str, mode: str) -> GameSpec:
            spec = super().generate(prompt, mode)
            level = spec.levels[0]
            collectibles = list(level.collectibles)
            collectibles[-1] = collectibles[-1].model_copy(
                update={"position": Point(x=0.83, y=0.7)}
            )
            return spec.model_copy(
                update={
                    "levels": [
                        level.model_copy(update={"collectibles": collectibles}),
                        *spec.levels[1:],
                    ]
                }
            )

    report = GameOrchestrator(
        ObstructedForestProvider(),
        playtester=NotTestedPlaytester(),
    ).generate("A forest game to collect three gems", "beginner")

    repaired_spec_position = report.spec.levels[0].collectibles[-1].position
    runtime_position = report.runtime_config["levels"][0]["collectibles"][-1][
        "position"
    ]

    assert (repaired_spec_position.x, repaired_spec_position.y) != (0.83, 0.7)
    assert runtime_position == repaired_spec_position.model_dump()


def test_separate_generation_requests_keep_prompts_and_tasks_isolated() -> None:
    orchestrator = GameOrchestrator(
        DevelopmentMockProvider(),
        playtester=NotTestedPlaytester(),
    )
    first = orchestrator.generate("A maze with keys and a locked exit", "beginner")
    second = orchestrator.generate("A forest game to collect two gems", "beginner")

    assert first.generation_id != second.generation_id
    assert first.workflow is not None
    assert second.workflow is not None
    assert first.workflow.original_prompt == "A maze with keys and a locked exit"
    assert second.workflow.original_prompt == "A forest game to collect two gems"
    assert first.workflow.generation_id == first.generation_id
    assert second.workflow.generation_id == second.generation_id
    first_task_ids = {task.task_id for task in first.workflow.agent_tasks}
    second_task_ids = {task.task_id for task in second.workflow.agent_tasks}
    assert first_task_ids
    assert second_task_ids
    assert first_task_ids.isdisjoint(second_task_ids)
    assert all(first.generation_id in task_id for task_id in first_task_ids)
    assert all(second.generation_id in task_id for task_id in second_task_ids)


def test_orchestrator_rejects_unbounded_repairs() -> None:
    with pytest.raises(ValueError, match="at most one repair"):
        GameOrchestrator(DevelopmentMockProvider(), max_repairs=2)


def test_requirement_ids_are_stable_between_plans() -> None:
    from app.agents.game_planner import GamePlannerAgent

    planner = GamePlannerAgent()
    prompt = "A maze where I shoot drones and track my score."
    first = planner.plan(prompt)
    second = planner.plan(prompt)

    assert [item.id for item in first.requirements] == [
        item.id for item in second.requirements
    ]
    assert len({item.id for item in first.requirements}) == len(first.requirements)


def test_agent_task_rejects_missing_required_input() -> None:
    with pytest.raises(ValidationError, match="Missing required task inputs"):
        AgentTask(
            task_id="designing-1",
            stage="designing",
            agent="GameDesignerAgent",
            required_inputs=["plan"],
            inputs={"prompt": "A game"},
        )


def test_invalid_agent_output_is_recorded_and_stops_workflow() -> None:
    class InvalidProvider:
        name = "development-mock"

        @staticmethod
        def generate(prompt: str, mode: str) -> dict:
            return {"title": "Missing required game data"}

    with pytest.raises(WorkflowExecutionError) as failure:
        GameOrchestrator(InvalidProvider()).generate("A new game please", "beginner")

    state = failure.value.workflow_state
    assert state.final_status == "failed"
    assert state.agent_outputs[-1].agent == "GameDesignerAgent"
    assert state.agent_outputs[-1].status == "invalid"
    assert state.stage_history[-1].name == "failed"


def test_repair_patch_is_validated_and_changes_a_known_issue() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "Collect",
            "description": "Collect the coin.",
            "player": {},
            "levels": [
                {
                    "id": "level-1",
                    "name": "Start",
                    "platforms": [{"x": 0, "y": 0.9, "width": 1, "height": 0.1}],
                    "collectibles": [{"position": {"x": 0.5, "y": 0.8}}],
                    "collectible_target": 1,
                }
            ],
            "win_condition": {"type": "collect_target"},
            "lose_condition": {"type": "health_depleted"},
        }
    )
    findings = QAReport(
        status="fail",
        scope="structural",
        summary="Missing target.",
        issues=[
            QAIssue(
                code="WIN_TARGET_MISSING",
                severity="error",
                message="collect_target requires a positive target.",
                suggested_fix="Set a positive target.",
                location="win_condition",
            )
        ],
    )

    repaired, patch = GameRepairAgent().repair_with_patch(
        spec, findings, attempt=1
    )

    assert patch.status == "applied"
    assert patch.before_digest != patch.after_digest
    assert repaired.win_condition.target == 1
    assert patch.changes[0].path == "win_condition.target"


def test_unrepairable_findings_stop_without_claiming_validation() -> None:
    class AlwaysFailQA:
        calls = 0

        def inspect(self, game) -> QAReport:
            self.calls += 1
            return QAReport(
                status="fail",
                scope="structural",
                summary="Unsupported test finding.",
                issues=[
                    QAIssue(
                        code="UNRESOLVED",
                        severity="error",
                        message="No deterministic repair is available.",
                        suggested_fix="Fix the unsupported finding.",
                        location="game",
                    )
                ],
            )

    qa = AlwaysFailQA()
    with pytest.raises(WorkflowExecutionError) as failure:
        GameOrchestrator(
            DevelopmentMockProvider(), qa=qa, playtester=NotTestedPlaytester()
        ).generate(
            "A platform game with gems", "beginner"
        )

    state = failure.value.workflow_state
    assert qa.calls == 1
    assert len(state.repair_history) == 1
    assert state.repair_history[0].status == "no_change"
    assert state.final_status == "failed"
    assert state.runtime_verified is False


def test_repair_patch_with_mismatched_digest_is_rejected() -> None:
    class AlwaysFailQA:
        @staticmethod
        def inspect(game) -> QAReport:
            return QAReport(
                status="fail",
                scope="structural",
                summary="Unsupported test finding.",
                issues=[
                    QAIssue(
                        code="UNRESOLVED",
                        severity="error",
                        message="No deterministic repair is available.",
                        suggested_fix="Fix the unsupported finding.",
                        location="game",
                    )
                ],
            )

    class ForgedRepair:
        @staticmethod
        def repair_with_patch(spec, findings, *, attempt, affected_requirement_ids):
            return spec, RepairPatch(
                patch_id="repair-000000000000",
                attempt=attempt,
                status="no_change",
                before_digest="0" * 64,
                after_digest="0" * 64,
                reason="Forged patch for validation test.",
            )

    with pytest.raises(WorkflowExecutionError) as failure:
        GameOrchestrator(
            DevelopmentMockProvider(),
            qa=AlwaysFailQA(),
            repair=ForgedRepair(),
            playtester=NotTestedPlaytester(),
        ).generate("A platform game with gems", "beginner")

    state = failure.value.workflow_state
    assert state.repair_history == []
    assert state.agent_outputs[-1].status == "invalid"
    assert "digest" in state.agent_outputs[-1].error.lower()


def test_playtest_failure_details_are_sent_to_bounded_repair() -> None:
    class FailedPlaytester:
        calls = 0

        def run(self, **kwargs) -> PlaytestReport:
            self.calls += 1
            return PlaytestReport(
                browser="Chromium",
                status="fail",
                runtime_tested=True,
                tests=[
                    PlaytestCase(
                        name="movement_controls_respond",
                        expected_behavior="Player moves after a direction key.",
                        actual_behavior="Player remained stationary.",
                        status="FAIL",
                        failure_details="ArrowRight did not move the player.",
                    )
                ],
                summary="Movement failed.",
            )

    playtester = FailedPlaytester()
    with pytest.raises(WorkflowExecutionError) as failure:
        GameOrchestrator(
            DevelopmentMockProvider(),
            playtester=playtester,
        ).generate("A platform game with gems", "beginner")

    workflow = failure.value.workflow_state
    repair_task = next(
        task for task in workflow.agent_tasks if task.agent == "GameRepairAgent"
    )
    repair_qa = repair_task.inputs["qa_report"]
    assert playtester.calls == 1
    assert repair_qa["playtest_report"]["tests"][0]["failure_details"] == (
        "ArrowRight did not move the player."
    )
    assert workflow.repair_history[0].status == "no_change"
    assert workflow.final_status == "failed"


def test_playtest_repeats_after_one_repair_and_only_then_allows_ready() -> None:
    class RepairThenPassQA:
        calls = 0

        def inspect(self, game) -> QAReport:
            self.calls += 1
            return QAReport(
                status="unverified",
                scope="structural",
                summary="No structural issues.",
                issues=[],
            )

    class AppliedRepair:
        def repair_with_patch(
            self,
            spec: GameSpec,
            findings: QAReport,
            *,
            attempt: int,
            affected_requirement_ids: list[str] | None = None,
        ) -> tuple[GameSpec, RepairPatch]:
            repaired_spec = spec.model_copy(
                update={
                    "player": spec.player.model_copy(
                        update={"speed": spec.player.speed + 1}
                    )
                }
            )
            before_digest = GameRepairAgent._digest(
                spec.model_dump(mode="json")
            )
            after_digest = GameRepairAgent._digest(
                repaired_spec.model_dump(mode="json")
            )
            patch = GameRepairAgent._patch(
                attempt=attempt,
                before_digest=before_digest,
                after_digest=after_digest,
                status="applied",
                changes=[
                    RepairChange(
                        path="player.speed",
                        previous_value=spec.player.speed,
                        new_value=repaired_spec.player.speed,
                    )
                ],
                affected_requirement_ids=affected_requirement_ids or [],
                reason="Applied a test repair to exercise the browser retest path.",
            )
            return repaired_spec, patch

    class FailThenPassPlaytester:
        calls = 0

        def run(self, **kwargs) -> PlaytestReport:
            self.calls += 1
            status = "FAIL" if self.calls == 1 else "PASS"
            return PlaytestReport(
                browser="Chromium",
                status="fail" if self.calls == 1 else "pass",
                runtime_tested=True,
                tests=[
                    PlaytestCase(
                        name="objective_interaction",
                        expected_behavior="The game interaction and objective succeed.",
                        actual_behavior=(
                            "The first test run failed."
                            if self.calls == 1
                            else "The repaired game passed."
                        ),
                        status=status,
                        failure_details=(
                            "Objective did not progress." if self.calls == 1 else None
                        ),
                    )
                ],
                summary="Browser run completed.",
            )

    qa = RepairThenPassQA()
    playtester = FailThenPassPlaytester()
    report = GameOrchestrator(
        DevelopmentMockProvider(),
        qa=qa,
        playtester=playtester,
        repair=AppliedRepair(),
    ).generate("A platform game with gems", "beginner")

    assert qa.calls == 2
    assert playtester.calls == 2
    assert len(report.workflow.repair_history) == 1
    assert report.workflow.repair_history[0].status == "applied"
    assert report.qa_report["gameplay_status"] == "pass"
    assert report.workflow.final_status == "ready"
    assert report.workflow.runtime_verified is True


def test_real_browser_failure_is_repaired_and_retested_with_history_preserved() -> None:
    spec = GameSpec.model_validate(
        {
            "title": "Missing Objective Target",
            "description": "Collect one gem to win.",
            "game_type": "platform_adventure",
            "camera": "side_scroller",
            "theme": "forest",
            "player": {
                "start": {"x": 0.1, "y": 0.8},
                "movement_mode": "platformer",
                "gravity": 900,
            },
            "levels": [
                {
                    "id": "repair-test",
                    "name": "Repair Test",
                    "platforms": [
                        {"x": 0, "y": 0.9, "width": 1, "height": 0.1}
                    ],
                    "collectibles": [
                        {"position": {"x": 0.3, "y": 0.82}, "kind": "gem"}
                    ],
                    "collectible_target": 1,
                }
            ],
            "win_condition": {"type": "collect_target"},
            "lose_condition": {"type": "health_depleted"},
        }
    )

    class FixedDesigner:
        @staticmethod
        def design(prompt, mode, plan):
            return spec

    report = GameOrchestrator(
        DevelopmentMockProvider(),
        designer=FixedDesigner(),
        playtester=BrowserPlaytestRunner(),
    ).generate("A platformer where you collect one gem to win.", "beginner")

    workflow = report.workflow
    assert workflow is not None
    playtest_outputs = [
        result.output
        for result in workflow.agent_outputs
        if result.agent == "BrowserPlaytestRunner"
    ]
    assert [output["status"] for output in playtest_outputs] == ["fail", "pass"]
    original_collection = next(
        test
        for test in playtest_outputs[0]["tests"]
        if test["name"] == "collectible_collision_and_objective_progress"
    )
    assert original_collection["status"] == "FAIL"
    assert "Collected 1/1" in original_collection["actual_behavior"]
    assert "playing" in original_collection["actual_behavior"]
    assert original_collection["failure_details"]

    repair_task = next(
        task for task in workflow.agent_tasks if task.agent == "GameRepairAgent"
    )
    assert repair_task.inputs["qa_report"]["gameplay_status"] == "fail"
    assert any(
        issue["code"] == "PLAYTEST_COLLECTIBLE_COLLISION_AND_OBJECTIVE_PROGRESS"
        for issue in repair_task.inputs["qa_report"]["issues"]
    )
    assert len(workflow.repair_history) == 1
    assert workflow.repair_history[0].status == "applied"
    assert workflow.repair_history[0].changes[0].path == "win_condition.target"
    retested_collection = next(
        test
        for test in playtest_outputs[1]["tests"]
        if test["name"] == "collectible_collision_and_objective_progress"
    )
    assert retested_collection["status"] == "PASS"
    assert "won" in retested_collection["actual_behavior"]
    assert report.spec.win_condition.target == 1
    assert report.qa_report["gameplay_status"] == "pass"
    assert workflow.final_status == "ready"
    assert workflow.runtime_verified is True


class _ShortSteeringPlaytester(BrowserPlaytestRunner):
    def __init__(self, steering_scale: float) -> None:
        super().__init__()
        self.steering_scale = steering_scale

    def _jump_to_platform(
        self, page, canvas, level, source_index, target_index, runtime_config
    ) -> bool:
        configured_speed = runtime_config["player"]["speed"]
        runtime_config["player"]["speed"] = configured_speed * self.steering_scale
        try:
            return super()._jump_to_platform(
                page,
                canvas,
                level,
                source_index,
                target_index,
                runtime_config,
            )
        finally:
            runtime_config["player"]["speed"] = configured_speed


def test_observed_browser_landing_failure_gets_bounded_repair_and_passes_retest() -> None:
    spec = _platform_landing_repair_spec()

    class FixedDesigner:
        @staticmethod
        def design(prompt, mode, plan):
            return spec

    report = GameOrchestrator(
        DevelopmentMockProvider(),
        designer=FixedDesigner(),
        playtester=_ShortSteeringPlaytester(steering_scale=1.5),
    ).generate("A platformer with an elevated gem and beacon.", "beginner")
    workflow = report.workflow
    assert workflow is not None

    browser_outputs = [
        result.output
        for result in workflow.agent_outputs
        if result.agent == "BrowserPlaytestRunner"
    ]
    assert [output["status"] for output in browser_outputs] == ["fail", "pass"]
    initial_landing = next(
        test
        for test in browser_outputs[0]["tests"]
        if test["name"] == "platformer_platform_landings"
    )
    assert initial_landing["status"] == "FAIL"
    assert "0->None (planned 1" in initial_landing["actual_behavior"]
    assert "player_y=461" in initial_landing["actual_behavior"]
    assert "grounded=True; game_state=playing" in initial_landing["actual_behavior"]

    repair_task = next(
        task for task in workflow.agent_tasks if task.agent == "GameRepairAgent"
    )
    assert any(
        issue["code"] == "PLAYTEST_PLATFORMER_PLATFORM_LANDINGS"
        for issue in repair_task.inputs["qa_report"]["issues"]
    )
    patch = workflow.repair_history[0]
    assert patch.status == "applied"
    assert patch.reason.startswith("Diagnosed an observed failed static-platform")
    assert [change.path for change in patch.changes] == [
        "levels.0.platforms.1.x",
        "levels.0.collectibles.0.position.x",
        "levels.0.goal.position.x",
    ]
    assert report.spec.levels[0].platforms[1].x == 0.3
    assert report.spec.levels[0].collectibles[0].position.x == 0.36
    assert report.spec.levels[0].goal.position.x == 0.43
    retest = {test["name"]: test for test in browser_outputs[1]["tests"]}
    assert retest["platformer_platform_landings"]["status"] == "PASS"
    assert retest["collectible_collision_and_objective_progress"]["status"] == "PASS"
    assert retest["win_condition_completion"]["status"] == "PASS"
    assert workflow.final_status == "ready"
    assert workflow.runtime_verified is True


def test_applied_platform_repair_preserves_failed_browser_retest() -> None:
    spec = _platform_landing_repair_spec()

    class FixedDesigner:
        @staticmethod
        def design(prompt, mode, plan):
            return spec

    with pytest.raises(WorkflowExecutionError) as failure:
        GameOrchestrator(
            DevelopmentMockProvider(),
            designer=FixedDesigner(),
            playtester=_ShortSteeringPlaytester(steering_scale=3.0),
        ).generate("A platformer with an elevated gem and beacon.", "beginner")

    workflow = failure.value.workflow_state
    browser_outputs = [
        result.output
        for result in workflow.agent_outputs
        if result.agent == "BrowserPlaytestRunner"
    ]
    assert [output["status"] for output in browser_outputs] == ["fail", "fail"]
    assert workflow.repair_history[0].status == "applied"
    first_landing = next(
        test
        for test in browser_outputs[0]["tests"]
        if test["name"] == "platformer_platform_landings"
    )
    retest_landing = next(
        test
        for test in browser_outputs[1]["tests"]
        if test["name"] == "platformer_platform_landings"
    )
    assert first_landing["status"] == "FAIL"
    assert retest_landing["status"] == "FAIL"
    assert workflow.final_status == "failed"
    assert workflow.runtime_verified is False
