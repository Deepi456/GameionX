import logging
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from app.agents.game_builder import GameBuilderAgent, GameRuntimeConfig
from app.agents.game_designer import GameDesignerAgent
from app.agents.game_planner import GamePlannerAgent
from app.agents.game_qa import GameQAAgent, QAReport
from app.agents.game_repair import GameRepairAgent
from app.agents.game_scene_builder import GameSceneBuilderAgent
from app.agents.top_down_placement import normalize_top_down_placements
from app.playtest import BrowserPlaytestRunner
from app.models import (
    AgentResult,
    AgentTask,
    DefinitionUI,
    GameDefinitionV2,
    GamePlan,
    GameSpec,
    GenerationReport,
    GenerationStage,
    RepairPatch,
    PlaytestReport,
    Requirement,
    WorkflowState,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


class WorkflowExecutionError(ValueError):
    """A failed generation that retains its validated workflow history."""

    def __init__(self, message: str, workflow_state: WorkflowState) -> None:
        super().__init__(message)
        self.workflow_state = workflow_state


class GameOrchestrator:
    def __init__(
        self,
        provider,
        designer: GameDesignerAgent | None = None,
        builder: GameBuilderAgent | None = None,
        qa: GameQAAgent | None = None,
        repair: GameRepairAgent | None = None,
        planner: GamePlannerAgent | None = None,
        playtester: BrowserPlaytestRunner | None = None,
        max_repairs: int = 1,
    ) -> None:
        if max_repairs < 0 or max_repairs > 1:
            raise ValueError("GameOrchestrator allows at most one repair.")
        self.provider = provider
        self.designer = designer
        self.builder = builder or GameBuilderAgent()
        self.qa = qa or GameQAAgent()
        self.repair = repair or GameRepairAgent(self.builder, self.qa)
        self.planner = planner or GamePlannerAgent()
        self.playtester = playtester or BrowserPlaytestRunner()
        self.max_repairs = max_repairs

    def generate(
        self,
        prompt: str,
        mode: str,
        stages: list[GenerationStage] | None = None,
        *,
        bypass_cache: bool = False,
    ) -> GenerationReport:
        stage_history = stages if stages is not None else []
        initial_plan = GamePlan(original_prompt=prompt)
        workflow = WorkflowState(
            original_prompt=prompt,
            plan=initial_plan,
            stage_history=stage_history,
        )
        workflow.stage_history = stage_history
        task_number = 0

        def next_task_id(stage: str) -> str:
            nonlocal task_number
            task_number += 1
            return f"{stage}-{workflow.generation_id}-{task_number}"

        try:
            self._stage(workflow, "planning", "started")
            plan_task = self._task(
                next_task_id("planning"),
                "planning",
                "GamePlannerAgent",
                {"prompt": prompt},
                ["prompt"],
            )
            plan = self._run_agent(
                workflow,
                plan_task,
                lambda: self.planner.plan(prompt),
                GamePlan.model_validate,
            )
            workflow.plan = plan
            self._stage(workflow, "planning", "completed")

            self._stage(workflow, "designing", "started")
            design_task = self._task(
                next_task_id("designing"),
                "designing",
                "GameDesignerAgent",
                {"prompt": prompt, "mode": mode, "plan": plan.model_dump(mode="json")},
                ["prompt", "mode", "plan"],
            )
            def validate_design(value: Any) -> GameSpec:
                validated_spec = GameSpec.model_validate(value)
                validated_spec = normalize_top_down_placements(validated_spec)
                GameDesignerAgent._validate_plan(plan, validated_spec)
                return validated_spec

            spec = self._run_agent(
                workflow,
                design_task,
                lambda: self._design(prompt, mode, plan, bypass_cache=bypass_cache),
                validate_design,
            )
            self._stage(workflow, "designing", "completed")

            self._stage(workflow, "gameplay_logic", "started")
            logic_task = self._task(
                next_task_id("gameplay_logic"),
                "gameplay_logic",
                "GameBuilderAgent",
                {"spec": spec.model_dump(mode="json")},
                ["spec"],
            )
            runtime = self._run_agent(
                workflow,
                logic_task,
                lambda: self.builder.build(spec),
                GameRuntimeConfig.model_validate,
            )
            self._stage(workflow, "gameplay_logic", "completed")

            self._stage(workflow, "world_ui_building", "started")
            ui_task = self._task(
                next_task_id("world_ui_building"),
                "world_ui_building",
                "GameSceneBuilderAgent",
                {
                    "runtime_config": runtime.model_dump(mode="json"),
                    "requested_ui": plan.requested_ui,
                },
                ["runtime_config", "requested_ui"],
            )
            ui = self._run_agent(
                workflow,
                ui_task,
                lambda: GameSceneBuilderAgent.build_ui(
                    movement_mode=runtime.player.movement_mode,
                    fire_control=runtime.fire_control,
                    win_condition=runtime.win_condition,
                    lose_condition=runtime.lose_condition,
                    has_timer=any(level.time_limit for level in runtime.levels),
                    requested_ui=tuple(plan.requested_ui),
                ),
                DefinitionUI.model_validate,
            )
            self._stage(workflow, "world_ui_building", "completed")

            definition = self._integrate_runtime(
                workflow,
                next_task_id("runtime_integration"),
                runtime,
                plan,
                ui,
            )
            self._stage(workflow, "validating", "started")
            report = self._inspect(
                workflow,
                next_task_id("validating"),
                runtime,
                plan,
            )
            self._stage(workflow, "validating", "completed")
            self._stage(workflow, "qa_playtest", "started")
            playtest = self._playtest(
                workflow,
                next_task_id("qa_playtest"),
                spec,
                runtime,
                definition,
            )
            report = GameQAAgent.attach_playtest(report, playtest)
            workflow.qa_findings = [
                issue.model_dump(mode="json") for issue in report.issues
            ]
            self._stage(workflow, "qa_playtest", "completed")

            for attempt in range(1, self.max_repairs + 1):
                if not self._has_blocking_failure(report):
                    break

                self._stage(workflow, "repairing", "started")
                repair_task = self._task(
                    next_task_id("repairing"),
                    "repairing",
                    "GameRepairAgent",
                    {
                        "spec": spec.model_dump(mode="json"),
                        "qa_report": report.model_dump(mode="json"),
                    },
                    ["spec", "qa_report"],
                )
                repaired_spec, patch = self._run_agent(
                    workflow,
                    repair_task,
                    lambda: self.repair.repair_with_patch(
                        spec,
                        report,
                        attempt=attempt,
                        affected_requirement_ids=sorted(
                            {
                                requirement_id
                                for issue in report.issues
                                for requirement_id in issue.requirement_ids
                            }
                        ),
                    ),
                    lambda value: self._validate_repair_result(value, spec),
                )
                spec = repaired_spec
                workflow.repair_history.append(patch)
                self._stage(workflow, "repairing", "completed")
                if patch.status != "applied":
                    break

                self._stage(workflow, "gameplay_logic", "started")
                gameplay_task = self._task(
                    next_task_id("gameplay_logic"),
                    "gameplay_logic",
                    "GameBuilderAgent",
                    {"spec": spec.model_dump(mode="json")},
                    ["spec"],
                )
                runtime = self._run_agent(
                    workflow,
                    gameplay_task,
                    lambda: self._integrate_gameplay(spec),
                    GameRuntimeConfig.model_validate,
                )
                self._stage(workflow, "gameplay_logic", "completed")
                definition = self._integrate_runtime(
                    workflow,
                    next_task_id("runtime_integration"),
                    runtime,
                    plan,
                    ui,
                )
                report = self._inspect(
                    workflow,
                    next_task_id("qa_retest"),
                    runtime,
                    plan,
                )
                self._stage(workflow, "qa_retest", "started")
                self._stage(workflow, "qa_playtest", "started")
                playtest = self._playtest(
                    workflow,
                    next_task_id("qa_playtest"),
                    spec,
                    runtime,
                    definition,
                )
                report = GameQAAgent.attach_playtest(report, playtest)
                workflow.qa_findings = [
                    issue.model_dump(mode="json") for issue in report.issues
                ]
                self._stage(workflow, "qa_playtest", "completed")
                self._stage(workflow, "qa_retest", "completed")

            workflow.qa_findings = [
                issue.model_dump(mode="json") for issue in report.issues
            ]
            if self._has_blocking_failure(report):
                workflow.validation_status = "failed"
                workflow.final_status = "failed"
                raise ValueError(
                    "Structural QA failed after the bounded repair attempt."
                )

            covered_plan = self._update_requirement_coverage(plan, definition, spec)
            workflow.plan = covered_plan
            workflow.implemented_mechanics = sorted(
                {
                    mechanic.kind
                    for mechanic in definition.mechanics
                    if mechanic.support == "supported"
                }
            )
            workflow.unsupported_mechanics = sorted(
                {
                    *(
                        mechanic.kind
                        for mechanic in definition.mechanics
                        if mechanic.support == "unsupported"
                    ),
                    *(
                        requirement.description
                        for requirement in covered_plan.requirements
                        if requirement.category == "mechanic"
                        and requirement.status == "unsupported"
                    ),
                }
            )
            workflow.scene_configuration = definition
            requirements_covered = all(
                requirement.status == "implemented"
                for requirement in covered_plan.requirements
            )
            runtime_verified = (
                report.status == "pass"
                and report.runtime_tested
                and requirements_covered
            )
            workflow.runtime_verified = runtime_verified
            if runtime_verified:
                workflow.validation_status = "passed"
                workflow.final_status = "ready"
            else:
                workflow.validation_status = "unverified"
                workflow.final_status = "unverified"
            self._stage(workflow, "finalizing", "started")
            self._stage(workflow, "finalizing", "completed")
            self._stage(
                workflow,
                "ready" if workflow.final_status == "ready" else "unverified",
                "completed",
            )
        except Exception as error:
            workflow.validation_status = "failed"
            workflow.final_status = "failed"
            self._stage(workflow, "failed", "failed")
            logger.error("Game generation workflow failed: %s", error)
            if isinstance(error, WorkflowExecutionError):
                raise
            raise WorkflowExecutionError(str(error), workflow) from error

        return GenerationReport(
            provider=self.provider.name,
            generation_id=workflow.generation_id,
            spec=spec,
            runtime_config=runtime.model_dump(mode="json"),
            game_definition=definition,
            qa_report=report.model_dump(mode="json"),
            stages=stage_history,
            workflow=workflow,
        )

    def _integrate_gameplay(self, spec: GameSpec) -> GameRuntimeConfig:
        return self.builder.build(spec)

    def _integrate_runtime(
        self,
        workflow: WorkflowState,
        task_id: str,
        runtime: GameRuntimeConfig,
        plan: GamePlan,
        ui: DefinitionUI,
    ) -> GameDefinitionV2:
        self._stage(workflow, "runtime_integration", "started")
        task = self._task(
            task_id,
            "runtime_integration",
            "RuntimeIntegrationModule",
            {
                "runtime_config": runtime.model_dump(mode="json"),
                "ui": ui.model_dump(mode="json"),
            },
            ["runtime_config", "ui"],
        )
        definition = self._run_agent(
            workflow,
            task,
            lambda: self.builder.to_game_definition(
                runtime,
                genre=plan.genre_intent,
                requested_ui=tuple(plan.requested_ui),
                ui=ui,
            ),
            GameDefinitionV2.model_validate,
        )
        workflow.scene_configuration = definition
        self._stage(workflow, "runtime_integration", "completed")
        return definition

    def _inspect(
        self,
        workflow: WorkflowState,
        task_id: str,
        runtime: GameRuntimeConfig,
        plan: GamePlan,
    ) -> QAReport:
        task = self._task(
            task_id,
            "validating",
            "GameQAAgent",
            {
                "runtime_config": runtime.model_dump(mode="json"),
                "requirement_ids": [
                    requirement.id for requirement in plan.requirements
                ],
            },
            ["runtime_config", "requirement_ids"],
        )
        report = self._run_agent(
            workflow,
            task,
            lambda: self.qa.inspect(runtime),
            QAReport.model_validate,
        )
        report = self._attach_requirement_ids(report, plan)
        self._replace_agent_output(workflow, task.task_id, report.model_dump(mode="json"))
        workflow.qa_findings = [
            issue.model_dump(mode="json") for issue in report.issues
        ]
        return report

    @staticmethod
    def _has_blocking_failure(report: QAReport) -> bool:
        return report.structural_status == "fail" or (
            report.status == "fail"
            and report.gameplay_status == "fail"
            and report.runtime_tested
        )

    def _playtest(
        self,
        workflow: WorkflowState,
        task_id: str,
        spec: GameSpec,
        runtime: GameRuntimeConfig,
        definition: GameDefinitionV2,
    ) -> PlaytestReport:
        task = self._task(
            task_id,
            "qa_playtest",
            "BrowserPlaytestRunner",
            {
                "spec": spec.model_dump(mode="json"),
                "runtime_config": runtime.model_dump(mode="json"),
                "game_definition": definition.model_dump(mode="json"),
            },
            ["spec", "runtime_config", "game_definition"],
        )
        return self._run_agent(
            workflow,
            task,
            lambda: self.playtester.run(
                spec=spec.model_dump(mode="json"),
                runtime_config=runtime.model_dump(mode="json"),
                game_definition=definition.model_dump(mode="json"),
            ),
            PlaytestReport.model_validate,
        )

    def _design(
        self,
        prompt: str,
        mode: str,
        plan: GamePlan,
        *,
        bypass_cache: bool = False,
    ) -> GameSpec:
        if self.designer is not None:
            if bypass_cache:
                return self.designer.design(
                    prompt, mode, plan, bypass_cache=True
                )
            return self.designer.design(prompt, mode, plan)
        return self.provider.generate(prompt, mode)

    @staticmethod
    def _task(
        task_id: str,
        stage: str,
        agent: str,
        inputs: dict[str, Any],
        required_inputs: list[str],
    ) -> AgentTask:
        return AgentTask(
            task_id=task_id,
            stage=stage,
            agent=agent,
            inputs=inputs,
            required_inputs=required_inputs,
        )

    def _run_agent(
        self,
        workflow: WorkflowState,
        task: AgentTask,
        operation: Callable[[], Any],
        validate: Callable[[Any], T],
    ) -> T:
        workflow.agent_tasks.append(task)
        try:
            if any(name not in task.inputs for name in task.required_inputs):
                raise ValueError("Agent task is missing required validated inputs.")
            result = validate(operation())
        except Exception as error:
            result_status = "invalid" if isinstance(error, (ValueError, TypeError)) else "failed"
            workflow.agent_outputs.append(
                AgentResult(
                    task_id=task.task_id,
                    agent=task.agent,
                    status=result_status,
                    error=str(error)[:1000],
                )
            )
            raise
        workflow.agent_outputs.append(
            AgentResult(
                task_id=task.task_id,
                agent=task.agent,
                status="completed",
                output=self._serialize(result),
            )
        )
        return result

    @staticmethod
    def _serialize(value: Any) -> dict[str, Any]:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return value
        if isinstance(value, tuple) and len(value) == 2:
            return {
                "spec": value[0].model_dump(mode="json"),
                "patch": value[1].model_dump(mode="json"),
            }
        raise TypeError("Agent outputs must be validated Pydantic models or objects.")

    @staticmethod
    def _validate_repair_result(
        value: Any, original_spec: GameSpec
    ) -> tuple[GameSpec, RepairPatch]:
        if not isinstance(value, tuple) or len(value) != 2:
            raise ValueError("Repair agent must return a spec and a validated patch.")
        spec = GameSpec.model_validate(value[0])
        validated_patch = RepairPatch.model_validate(value[1])
        original_data = original_spec.model_dump(mode="json")
        repaired_data = spec.model_dump(mode="json")
        if (
            validated_patch.before_digest
            != GameRepairAgent._digest(original_data)
            or validated_patch.after_digest != GameRepairAgent._digest(repaired_data)
        ):
            raise ValueError("Repair patch digest does not match the validated GameSpec.")
        if validated_patch.status == "applied":
            if spec == original_spec:
                raise ValueError("Applied repair patch did not change the GameSpec.")
            for change in validated_patch.changes:
                previous = GameOrchestrator._read_path(original_data, change.path)
                current = GameOrchestrator._read_path(repaired_data, change.path)
                if not GameOrchestrator._same_number(
                    previous, change.previous_value
                ) or not GameOrchestrator._same_number(current, change.new_value):
                    raise ValueError(
                        f"Repair patch change does not match validated path {change.path}."
                    )
        elif spec != original_spec:
            raise ValueError("A non-applied repair patch cannot change the GameSpec.")
        return spec, validated_patch

    @staticmethod
    def _read_path(data: dict[str, Any], path: str) -> Any:
        current: Any = data
        for part in path.split("."):
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise ValueError(f"Repair patch path is invalid: {path}.")
        return current

    @staticmethod
    def _same_number(left: Any, right: Any) -> bool:
        if left is None or right is None:
            return left is right
        return (
            isinstance(left, (int, float))
            and isinstance(right, (int, float))
            and float(left) == float(right)
        )

    @staticmethod
    def _replace_agent_output(
        workflow: WorkflowState, task_id: str, output: dict[str, Any]
    ) -> None:
        for index, agent_result in enumerate(workflow.agent_outputs):
            if agent_result.task_id == task_id:
                workflow.agent_outputs[index] = agent_result.model_copy(
                    update={"output": output}
                )
                return
        raise ValueError(f"No recorded agent task exists for {task_id}.")

    @staticmethod
    def _attach_requirement_ids(report: QAReport, plan: GamePlan) -> QAReport:
        issues = []
        for issue in report.issues:
            message = issue.message.lower()
            requirement_ids = [
                requirement.id
                for requirement in plan.requirements
                if requirement.description.lower() in message
            ]
            issues.append(
                issue.model_copy(
                    update={"requirement_ids": sorted(set(requirement_ids))}
                )
            )
        return report.model_copy(update={"issues": issues})

    @staticmethod
    def _update_requirement_coverage(
        plan: GamePlan, definition: GameDefinitionV2, spec: GameSpec
    ) -> GamePlan:
        mechanic_by_id = {
            mechanic.kind: mechanic.support for mechanic in definition.mechanics
        }
        object_descriptions: set[str] = set()
        for level in spec.levels:
            object_descriptions.update(item.kind for item in level.collectibles)
            object_descriptions.update("enemy" for _ in level.enemies)
            object_descriptions.update(hazard.kind for hazard in level.hazards)
            object_descriptions.update(scenery.kind for scenery in level.scenery)
            object_descriptions.update(obstacle.kind for obstacle in level.obstacles)
            object_descriptions.update(
                "building" for scenery in level.scenery if scenery.kind == "building"
            )
        object_descriptions = {description.lower() for description in object_descriptions}
        ui_values = {
            "score": definition.ui.show_score,
            "health": definition.ui.show_health,
            "timer": definition.ui.show_timer,
        }
        aliases = {
            "platformer": "platform_adventure",
            "top_down_survival": "dodge_arena",
        }
        requirements: list[Requirement] = []
        for requirement in plan.requirements:
            if requirement.category == "mechanic":
                support = mechanic_by_id.get(requirement.description)
                if requirement.description == "survival":
                    support = (
                        "supported"
                        if definition.win.condition == "survive"
                        else "unsupported"
                    )
                status = support or "unsupported"
            elif requirement.category == "entity":
                status = (
                    "implemented"
                    if requirement.description.lower() in object_descriptions
                    else "unsupported"
                )
            elif requirement.category == "ui":
                status = (
                    "implemented"
                    if ui_values.get(requirement.description, False)
                    else "unsupported"
                )
            else:
                expected = aliases.get(requirement.description, requirement.description)
                status = (
                    "implemented"
                    if requirement.description == definition.genre
                    or expected == definition.genre
                    or expected == spec.game_type
                    else "unsupported"
                )
            requirements.append(
                requirement.model_copy(update={"status": status})
            )
        return plan.model_copy(update={"requirements": requirements})

    @staticmethod
    def _stage(
        workflow: WorkflowState,
        name: str,
        status: str,
    ) -> None:
        messages = {
            "planning": "Extracting prompt requirements and stable requirement IDs.",
            "designing": "Producing a GameSpec validated against the plan.",
            "gameplay_logic": "Deterministically translating GameSpec into runtime mechanics.",
            "world_ui_building": "Building declarative controls and HUD configuration.",
            "runtime_integration": "Integrating validated runtime data and scene configuration.",
            "qa_playtest": "Running browser gameplay checks; unsupported or unavailable checks remain unverified.",
            "validating": "Running structural QA; gameplay has not been browser-verified.",
            "repairing": "Attempting one allowlisted deterministic repair.",
            "qa_retest": "Rerunning structural QA after a validated repair.",
            "finalizing": "Recording validation status and unsupported requirements.",
            "unverified": "Generation completed; browser gameplay remains unverified.",
            "ready": "Structural QA and required browser gameplay checks passed.",
            "failed": "Generation stopped safely after an agent or validation failure.",
        }
        stage = GenerationStage(name=name, status=status, message=messages[name])
        workflow.stage_history.append(stage)
