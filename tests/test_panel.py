"""Tests for the observation panel data layer."""

from __future__ import annotations

from unittest.mock import AsyncMock

from orchestrator.config import BudgetsConfig, ProjectConfig
from orchestrator.cost import CostEntry
from orchestrator.evidence import (
    AuditorVerdict,
    EvidencePack,
    MutationResults,
    TestResults,
)
from orchestrator.executor.base import ExecutionResult, ExecutorAdapter
from orchestrator.graph import TaskNode
from orchestrator.panel import PanelData
from orchestrator.project import ProjectOrchestrator
from orchestrator.runner import TaskRunner
from orchestrator.types import AuditVerdict, TaskState

# ── Helpers ──


def _config() -> ProjectConfig:
    return ProjectConfig(budgets=BudgetsConfig(per_task_usd=5.0))


def _mock_executor() -> ExecutorAdapter:
    mock = AsyncMock(spec=ExecutorAdapter)
    mock.execute = AsyncMock(
        return_value=ExecutionResult(
            task_id="T-001",
            success=True,
            output="ok",
            cost_usd=0.10,
            input_tokens=100,
            output_tokens=50,
            model="claude-sonnet-4-5",
        )
    )
    return mock


def _runner() -> TaskRunner:
    return TaskRunner(config=_config(), developer=_mock_executor(), auditor=_mock_executor())


def _orchestrator(runner: TaskRunner | None = None) -> ProjectOrchestrator:
    return ProjectOrchestrator(runner=runner or _runner())


def _green_tests() -> TestResults:
    return TestResults(passed=10, failed=0, errors=0, coverage_pct=90.0)


def _green_e2e() -> TestResults:
    return TestResults(passed=20, failed=0, errors=0, coverage_pct=95.0)


# ── TestTaskStatus ──


class TestTaskStatus:
    def test_draft_task_optional_fields_none(self) -> None:
        orch = _orchestrator()
        orch.runner.create_task("T-001")
        panel = PanelData(orch)
        view = panel.task_status("T-001")
        assert view.task_id == "T-001"
        assert view.state == "draft"
        assert view.test_results is None
        assert view.auditor_verdict is None
        assert view.mutation_results is None
        assert view.developer_log == ()
        assert view.history == ()

    def test_task_with_evidence(self) -> None:
        orch = _orchestrator()
        ctx = orch.runner.create_task("T-001")
        ctx.state = TaskState.AWAIT_AUDIT
        ctx.evidence = EvidencePack(
            task_id="T-001",
            diff="+code",
            criteria="must work",
            test_results=_green_tests(),
            auditor_verdict=AuditorVerdict(
                verdict=AuditVerdict.APPROVE,
                reasoning="LGTM",
                checklist={"style": True, "tests": True},
            ),
            developer_log=["wrote code"],
        )
        panel = PanelData(orch)
        view = panel.task_status("T-001")
        assert view.test_results is not None
        assert view.test_results.all_green is True
        assert view.test_results.passed == 10
        assert view.auditor_verdict is not None
        assert view.auditor_verdict.verdict == "approve"
        assert view.auditor_verdict.checklist == {"style": True, "tests": True}
        assert view.developer_log == ("wrote code",)

    def test_task_with_mutation_results(self) -> None:
        orch = _orchestrator()
        ctx = orch.runner.create_task("T-001", touches_critical=True)
        ctx.state = TaskState.PR_READY
        ctx.evidence = EvidencePack(
            task_id="T-001",
            diff="+code",
            criteria="must work",
            test_results=_green_tests(),
            auditor_verdict=AuditorVerdict(verdict=AuditVerdict.APPROVE, reasoning="ok"),
            mutation_results=MutationResults(total_mutants=10, killed=9, survived=1, timeout=0),
        )
        panel = PanelData(orch)
        view = panel.task_status("T-001")
        assert view.mutation_results is not None
        assert view.mutation_results.score == 0.9
        assert view.mutation_results.acceptable is True

    def test_cost_fields_match_tracker(self) -> None:
        orch = _orchestrator()
        ctx = orch.runner.create_task("T-001", budget_usd=10.0)
        ctx.cost.record(
            CostEntry(
                task_id="T-001",
                model="claude-sonnet-4-5",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.50,
            )
        )
        panel = PanelData(orch)
        view = panel.task_status("T-001")
        assert view.cost_total == 0.50
        assert view.cost_budget == 10.0
        assert view.cost_remaining == 9.50
        assert view.cost_by_model == {"claude-sonnet-4-5": 0.50}

    def test_history_is_tuple_of_transition_views(self) -> None:
        orch = _orchestrator()
        orch.runner.create_task("T-001")
        orch.runner.submit_plan("T-001", "plan", "criteria")
        panel = PanelData(orch)
        view = panel.task_status("T-001")
        assert len(view.history) == 1
        t = view.history[0]
        assert t.from_state == "draft"
        assert t.to_state == "plan_review"
        assert isinstance(t.timestamp, str)

    def test_developer_log_from_evidence(self) -> None:
        orch = _orchestrator()
        ctx = orch.runner.create_task("T-001")
        ctx.evidence = EvidencePack(
            task_id="T-001",
            diff="diff",
            criteria="crit",
            developer_log=["step 1", "step 2"],
        )
        panel = PanelData(orch)
        view = panel.task_status("T-001")
        assert view.developer_log == ("step 1", "step 2")


# ── TestStageStatus ──


class TestStageStatus:
    def test_planning_stage_with_tasks(self) -> None:
        orch = _orchestrator()
        orch.runner.create_task("T-001")
        orch.runner.create_task("T-002")
        orch.create_stage("S1", "Auth", ["T-001", "T-002"])
        panel = PanelData(orch)
        view = panel.stage_status("S1")
        assert view.stage_id == "S1"
        assert view.name == "Auth"
        assert view.state == "planning"
        assert len(view.tasks) == 2
        assert view.tasks[0].task_id == "T-001"
        assert view.tasks[0].state == "draft"

    def test_stage_with_e2e_results(self) -> None:
        orch = _orchestrator()
        for tid in ["T-001", "T-002"]:
            ctx = orch.runner.create_task(tid)
            ctx.state = TaskState.ACCEPTED
            ctx.architect_approved = True
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.graph.add_task(TaskNode(task_id="T-002", stage_id="S1"))
        orch.create_stage("S1", "Auth", ["T-001", "T-002"])
        orch.start_stage("S1")
        orch.begin_integration("S1")
        orch.submit_e2e("S1", _green_e2e())

        panel = PanelData(orch)
        view = panel.stage_status("S1")
        assert view.e2e_results is not None
        assert view.e2e_results.all_green is True
        assert view.state == "review"

    def test_task_states_populated_from_runner(self) -> None:
        orch = _orchestrator()
        ctx1 = orch.runner.create_task("T-001")
        ctx1.state = TaskState.IN_PROGRESS
        ctx2 = orch.runner.create_task("T-002")
        ctx2.state = TaskState.TESTING
        orch.create_stage("S1", "Work", ["T-001", "T-002"])
        panel = PanelData(orch)
        view = panel.stage_status("S1")
        states = {t.task_id: t.state for t in view.tasks}
        assert states == {"T-001": "in_progress", "T-002": "testing"}


# ── TestProjectStatus ──


class TestProjectStatus:
    def test_empty_project(self) -> None:
        orch = _orchestrator()
        panel = PanelData(orch)
        view = panel.project_status()
        assert view.stages == ()
        assert view.total_tasks == 0
        assert view.tasks_ready == 0
        assert view.tasks_completed == 0
        assert view.cost_total == 0.0

    def test_with_stages_and_tasks(self) -> None:
        orch = _orchestrator()
        orch.runner.create_task("T-001")
        orch.runner.create_task("T-002")
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.graph.add_task(
            TaskNode(task_id="T-002", dependencies=frozenset({"T-001"}), stage_id="S1")
        )
        orch.create_stage("S1", "Stage 1", ["T-001", "T-002"])
        panel = PanelData(orch)
        view = panel.project_status()
        assert len(view.stages) == 1
        assert view.total_tasks == 2
        assert view.stages[0].task_count == 2

    def test_ready_vs_blocked_tasks(self) -> None:
        orch = _orchestrator()
        orch.runner.create_task("T-001")
        orch.runner.create_task("T-002")
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.graph.add_task(
            TaskNode(task_id="T-002", dependencies=frozenset({"T-001"}), stage_id="S1")
        )
        orch.create_stage("S1", "Stage 1", ["T-001", "T-002"])
        panel = PanelData(orch)
        view = panel.project_status()
        # T-001 is ready (no deps), T-002 is blocked (depends on T-001)
        assert view.tasks_ready == 1
        assert view.tasks_blocked == 1

    def test_cost_aggregation_across_tasks(self) -> None:
        orch = _orchestrator()
        ctx1 = orch.runner.create_task("T-001", budget_usd=10.0)
        ctx1.cost.record(
            CostEntry(task_id="T-001", model="m1", input_tokens=10, output_tokens=5, cost_usd=0.30)
        )
        ctx2 = orch.runner.create_task("T-002", budget_usd=10.0)
        ctx2.cost.record(
            CostEntry(task_id="T-002", model="m1", input_tokens=20, output_tokens=10, cost_usd=0.70)
        )
        orch.create_stage("S1", "S", ["T-001", "T-002"], budget_usd=20.0)
        panel = PanelData(orch)
        view = panel.project_status()
        # cost_total comes from stage CostTracker (starts at 0, not task sum)
        assert view.cost_total == 0.0
        assert view.cost_budget == 20.0


# ── TestCostReport ──


class TestCostReport:
    def test_empty_costs(self) -> None:
        orch = _orchestrator()
        panel = PanelData(orch)
        view = panel.cost_report()
        assert view.total_usd == 0.0
        assert view.by_model == ()
        assert view.by_task == ()
        assert view.by_stage == ()

    def test_per_model_breakdown(self) -> None:
        orch = _orchestrator()
        ctx = orch.runner.create_task("T-001", budget_usd=10.0)
        ctx.cost.record(
            CostEntry(
                task_id="T-001", model="sonnet", input_tokens=100, output_tokens=50, cost_usd=0.30
            )
        )
        ctx.cost.record(
            CostEntry(
                task_id="T-001", model="opus", input_tokens=200, output_tokens=100, cost_usd=0.70
            )
        )
        panel = PanelData(orch)
        view = panel.cost_report()
        models = {r.model: r for r in view.by_model}
        assert "sonnet" in models
        assert "opus" in models
        assert models["sonnet"].cost_usd == 0.30
        assert models["sonnet"].input_tokens == 100
        assert models["opus"].cost_usd == 0.70
        assert models["opus"].output_tokens == 100

    def test_per_task_breakdown(self) -> None:
        orch = _orchestrator()
        ctx1 = orch.runner.create_task("T-001", budget_usd=10.0)
        ctx1.cost.record(
            CostEntry(task_id="T-001", model="m", input_tokens=10, output_tokens=5, cost_usd=0.50)
        )
        orch.runner.create_task("T-002", budget_usd=5.0)
        panel = PanelData(orch)
        view = panel.cost_report()
        tasks = {r.task_id: r for r in view.by_task}
        assert tasks["T-001"].cost_usd == 0.50
        assert tasks["T-001"].budget_usd == 10.0
        assert tasks["T-002"].cost_usd == 0.0
        assert view.total_usd == 0.50

    def test_per_stage_breakdown(self) -> None:
        orch = _orchestrator()
        ctx1 = orch.runner.create_task("T-001", budget_usd=10.0)
        ctx1.cost.record(
            CostEntry(task_id="T-001", model="m", input_tokens=10, output_tokens=5, cost_usd=1.0)
        )
        orch.runner.create_task("T-002", budget_usd=10.0)
        orch.create_stage("S1", "Stage", ["T-001", "T-002"], budget_usd=20.0)
        panel = PanelData(orch)
        view = panel.cost_report()
        assert len(view.by_stage) == 1
        assert view.by_stage[0].stage_id == "S1"
        assert view.by_stage[0].cost_usd == 1.0
        assert view.by_stage[0].budget_usd == 20.0


# ── TestPendingActions ──


class TestPendingActions:
    def test_no_actions_when_all_draft(self) -> None:
        orch = _orchestrator()
        orch.runner.create_task("T-001")
        orch.runner.create_task("T-002")
        panel = PanelData(orch)
        actions = panel.pending_actions()
        assert actions == []

    def test_plan_review_generates_action(self) -> None:
        orch = _orchestrator()
        orch.runner.create_task("T-001")
        orch.runner.submit_plan("T-001", "plan", "criteria")
        panel = PanelData(orch)
        actions = panel.pending_actions()
        assert len(actions) == 1
        assert actions[0].action_type == "review_plan"
        assert actions[0].target_id == "T-001"

    def test_merged_not_approved_generates_action(self) -> None:
        orch = _orchestrator()
        ctx = orch.runner.create_task("T-001")
        ctx.state = TaskState.MERGED
        ctx.architect_approved = False
        panel = PanelData(orch)
        actions = panel.pending_actions()
        assert len(actions) == 1
        assert actions[0].action_type == "accept_task"
        assert actions[0].target_id == "T-001"


# ── TestTaskLog ──


class TestTaskLog:
    def test_task_log_with_history(self) -> None:
        orch = _orchestrator()
        orch.runner.create_task("T-001")
        orch.runner.submit_plan("T-001", "plan", "criteria")
        orch.runner.approve_plan("T-001")
        panel = PanelData(orch)
        view = panel.task_log("T-001")
        assert view.task_id == "T-001"
        assert view.state == "plan_approved"
        assert len(view.history) == 2
        assert view.history[0].from_state == "draft"
        assert view.history[1].to_state == "plan_approved"
