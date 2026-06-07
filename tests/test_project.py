"""Tests for ProjectOrchestrator — stage + graph + runner coordination."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from orchestrator.config import BudgetsConfig, ProjectConfig
from orchestrator.evidence import TestResults
from orchestrator.executor.base import ExecutionResult, ExecutorAdapter
from orchestrator.graph import TaskNode
from orchestrator.project import ProjectOrchestrator
from orchestrator.runner import TaskRunner
from orchestrator.stage import InvalidStageTransitionError
from orchestrator.types import StageState, TaskState

# ── Helpers ──


def _config() -> ProjectConfig:
    return ProjectConfig(budgets=BudgetsConfig(per_task_usd=5.0))


def _success_result(
    task_id: str = "T-001", output: str = "verdict: approve\nLGTM"
) -> ExecutionResult:
    return ExecutionResult(
        task_id=task_id,
        success=True,
        output=output,
        cost_usd=0.10,
        input_tokens=100,
        output_tokens=50,
        model="claude-sonnet-4-5",
    )


def _mock_executor(result: ExecutionResult | None = None) -> ExecutorAdapter:
    mock = AsyncMock(spec=ExecutorAdapter)
    mock.execute = AsyncMock(return_value=result or _success_result())
    return mock


def _green_tests() -> TestResults:
    return TestResults(passed=10, failed=0, errors=0, coverage_pct=90.0)


def _red_tests() -> TestResults:
    return TestResults(passed=8, failed=2, errors=0, coverage_pct=70.0)


def _green_e2e() -> TestResults:
    return TestResults(passed=20, failed=0, errors=0, coverage_pct=95.0)


def _red_e2e() -> TestResults:
    return TestResults(passed=18, failed=2, errors=0, coverage_pct=90.0)


def _runner() -> TaskRunner:
    return TaskRunner(
        config=_config(),
        developer=_mock_executor(_success_result(output="+added code")),
        auditor=_mock_executor(_success_result(output="verdict: approve\nAll good")),
    )


def _orchestrator(runner: TaskRunner | None = None) -> ProjectOrchestrator:
    r = runner or _runner()
    return ProjectOrchestrator(runner=r)


def _setup_graph_with_stage(orch: ProjectOrchestrator, stage_id: str = "S1") -> None:
    """Add tasks T-001, T-002 to graph with stage_id and create the stage."""
    orch.graph.add_task(TaskNode(task_id="T-001", stage_id=stage_id))
    orch.graph.add_task(
        TaskNode(task_id="T-002", dependencies=frozenset({"T-001"}), stage_id=stage_id)
    )
    orch.create_stage(stage_id, "Stage 1", ["T-001", "T-002"])


# ── Stage creation and retrieval ──


class TestStageManagement:
    def test_create_stage(self) -> None:
        orch = _orchestrator()
        ctx = orch.create_stage("S1", "Stage 1", ["T-001", "T-002"])
        assert ctx.stage_id == "S1"
        assert ctx.state is StageState.PLANNING
        assert ctx.task_ids == ["T-001", "T-002"]

    def test_create_duplicate_raises(self) -> None:
        orch = _orchestrator()
        orch.create_stage("S1", "Stage 1", ["T-001"])
        with pytest.raises(ValueError, match="already exists"):
            orch.create_stage("S1", "Stage 1 dup", ["T-002"])

    def test_get_stage(self) -> None:
        orch = _orchestrator()
        orch.create_stage("S1", "Stage 1", ["T-001"])
        ctx = orch.get_stage("S1")
        assert ctx.name == "Stage 1"

    def test_get_missing_stage_raises(self) -> None:
        orch = _orchestrator()
        with pytest.raises(KeyError, match="not found"):
            orch.get_stage("S-999")

    def test_create_stage_custom_budget(self) -> None:
        orch = _orchestrator()
        ctx = orch.create_stage("S1", "Stage 1", ["T-001"], budget_usd=50.0)
        assert ctx.cost.budget_usd == 50.0


# ── next_tasks ──


class TestNextTasks:
    def test_next_tasks_no_stage_filter(self) -> None:
        orch = _orchestrator()
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.graph.add_task(
            TaskNode(task_id="T-002", dependencies=frozenset({"T-001"}), stage_id="S1")
        )
        # No tasks completed in runner → T-001 is ready (no deps)
        orch.runner.create_task("T-001")
        orch.runner.create_task("T-002")
        ready = orch.next_tasks()
        assert "T-001" in ready
        assert "T-002" not in ready

    def test_next_tasks_with_stage_filter(self) -> None:
        orch = _orchestrator()
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.graph.add_task(TaskNode(task_id="T-003", stage_id="S2"))
        orch.runner.create_task("T-001")
        orch.runner.create_task("T-003")
        ready = orch.next_tasks(stage_id="S1")
        assert ready == {"T-001"}


# ── check_stage_progress ──


class TestCheckStageProgress:
    def test_not_done(self) -> None:
        orch = _orchestrator()
        _setup_graph_with_stage(orch)
        orch.runner.create_task("T-001")
        orch.runner.create_task("T-002")
        assert orch.check_stage_progress("S1") is False

    def test_done(self) -> None:
        orch = _orchestrator()
        _setup_graph_with_stage(orch)
        # Create tasks and advance them to accepted
        r = orch.runner
        for tid in ["T-001", "T-002"]:
            r.create_task(tid)
            ctx = r.get_task(tid)
            ctx.state = TaskState.ACCEPTED
            ctx.architect_approved = True
        assert orch.check_stage_progress("S1") is True


# ── begin_integration ──


class TestBeginIntegration:
    def test_blocks_when_tasks_not_done(self) -> None:
        orch = _orchestrator()
        _setup_graph_with_stage(orch)
        orch.runner.create_task("T-001")
        orch.runner.create_task("T-002")
        orch.start_stage("S1")
        with pytest.raises(InvalidStageTransitionError, match="not done"):
            orch.begin_integration("S1")

    def test_passes_when_all_done(self) -> None:
        orch = _orchestrator()
        _setup_graph_with_stage(orch)
        r = orch.runner
        for tid in ["T-001", "T-002"]:
            r.create_task(tid)
            ctx = r.get_task(tid)
            ctx.state = TaskState.ACCEPTED
            ctx.architect_approved = True
        orch.start_stage("S1")
        ctx = orch.begin_integration("S1")
        assert ctx.state is StageState.INTEGRATING


# ── submit_e2e ──


class TestSubmitE2E:
    def test_green_goes_to_review(self) -> None:
        orch = _orchestrator()
        _setup_graph_with_stage(orch)
        r = orch.runner
        for tid in ["T-001", "T-002"]:
            r.create_task(tid)
            r.get_task(tid).state = TaskState.ACCEPTED
        orch.start_stage("S1")
        orch.begin_integration("S1")
        ctx = orch.submit_e2e("S1", _green_e2e())
        assert ctx.state is StageState.REVIEW

    def test_red_goes_back_to_in_progress(self) -> None:
        orch = _orchestrator()
        _setup_graph_with_stage(orch)
        r = orch.runner
        for tid in ["T-001", "T-002"]:
            r.create_task(tid)
            r.get_task(tid).state = TaskState.ACCEPTED
        orch.start_stage("S1")
        orch.begin_integration("S1")
        ctx = orch.submit_e2e("S1", _red_e2e())
        assert ctx.state is StageState.IN_PROGRESS


# ── accept_stage (INV-4) ──


class TestAcceptStage:
    def test_accept_sets_approved_and_accepted(self) -> None:
        orch = _orchestrator()
        _setup_graph_with_stage(orch)
        r = orch.runner
        for tid in ["T-001", "T-002"]:
            r.create_task(tid)
            r.get_task(tid).state = TaskState.ACCEPTED
        orch.start_stage("S1")
        orch.begin_integration("S1")
        orch.submit_e2e("S1", _green_e2e())
        ctx = orch.accept_stage("S1")
        assert ctx.state is StageState.ACCEPTED
        assert ctx.architect_approved is True

    def test_inv4_blocked_without_accept(self) -> None:
        orch = _orchestrator()
        _setup_graph_with_stage(orch)
        r = orch.runner
        for tid in ["T-001", "T-002"]:
            r.create_task(tid)
            r.get_task(tid).state = TaskState.ACCEPTED
        orch.start_stage("S1")
        orch.begin_integration("S1")
        orch.submit_e2e("S1", _green_e2e())
        # Try to transition REVIEW → ACCEPTED directly without approval
        ctx = orch.get_stage("S1")
        with pytest.raises(InvalidStageTransitionError, match="INV-4"):
            orch.stage_fsm.transition(ctx, StageState.ACCEPTED)


# ── reject_stage ──


class TestRejectStage:
    def test_reject_goes_to_in_progress(self) -> None:
        orch = _orchestrator()
        _setup_graph_with_stage(orch)
        r = orch.runner
        for tid in ["T-001", "T-002"]:
            r.create_task(tid)
            r.get_task(tid).state = TaskState.ACCEPTED
        orch.start_stage("S1")
        orch.begin_integration("S1")
        orch.submit_e2e("S1", _green_e2e())
        ctx = orch.reject_stage("S1")
        assert ctx.state is StageState.IN_PROGRESS


# ── fail_stage ──


class TestFailStage:
    def test_fail_from_in_progress(self) -> None:
        orch = _orchestrator()
        orch.create_stage("S1", "Stage 1", ["T-001"])
        orch.start_stage("S1")
        ctx = orch.fail_stage("S1", "critical error")
        assert ctx.state is StageState.FAILED

    def test_fail_from_planning(self) -> None:
        orch = _orchestrator()
        orch.create_stage("S1", "Stage 1", ["T-001"])
        ctx = orch.fail_stage("S1", "cancelled")
        assert ctx.state is StageState.FAILED


# ── Full lifecycle ──


class TestFullLifecycle:
    def test_planning_to_accepted(self) -> None:
        """Full lifecycle: PLANNING → IN_PROGRESS → INTEGRATING → REVIEW → ACCEPTED."""
        orch = _orchestrator()
        _setup_graph_with_stage(orch)
        r = orch.runner
        for tid in ["T-001", "T-002"]:
            r.create_task(tid)
            r.get_task(tid).state = TaskState.ACCEPTED
            r.get_task(tid).architect_approved = True

        orch.start_stage("S1")
        assert orch.get_stage("S1").state is StageState.IN_PROGRESS

        orch.begin_integration("S1")
        assert orch.get_stage("S1").state is StageState.INTEGRATING

        orch.submit_e2e("S1", _green_e2e())
        assert orch.get_stage("S1").state is StageState.REVIEW

        orch.accept_stage("S1")
        assert orch.get_stage("S1").state is StageState.ACCEPTED

        # Verify history
        ctx = orch.get_stage("S1")
        assert len(ctx.history) == 4


# ── project_summary ──


class TestProjectSummary:
    def test_summary_structure(self) -> None:
        orch = _orchestrator()
        orch.create_stage("S1", "Stage 1", ["T-001"])
        orch.create_stage("S2", "Stage 2", ["T-002", "T-003"])
        summary = orch.project_summary()
        assert summary["total_stages"] == 2
        stages = summary["stages"]
        assert isinstance(stages, list)
        assert len(stages) == 2
        s1 = next(s for s in stages if s["stage_id"] == "S1")
        assert s1["state"] == "planning"
        assert s1["task_count"] == 1

    def test_summary_empty(self) -> None:
        orch = _orchestrator()
        summary = orch.project_summary()
        assert summary["total_stages"] == 0
        assert summary["stages"] == []
