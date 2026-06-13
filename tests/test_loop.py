"""Tests for async runner loop (loop.py)."""

from __future__ import annotations

import unittest.mock
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from orchestrator.config import BudgetsConfig, CommandsConfig, ProjectConfig
from orchestrator.cost import BudgetExceededError
from orchestrator.evidence import TestResults
from orchestrator.executor.base import ExecutionResult, ExecutorAdapter
from orchestrator.graph import TaskNode
from orchestrator.loop import run_loop
from orchestrator.project import ProjectOrchestrator
from orchestrator.runner import TaskRunner
from orchestrator.store import get_connection, init_db, load_tasks, save_snapshot
from orchestrator.types import StageState, TaskState


def _config(**overrides: object) -> ProjectConfig:
    defaults: dict[str, object] = {"budgets": BudgetsConfig(per_task_usd=5.0)}
    defaults.update(overrides)
    return ProjectConfig(**defaults)  # type: ignore[arg-type]


def _dev_result(task_id: str = "T-001", output: str = "+code") -> ExecutionResult:
    return ExecutionResult(
        task_id=task_id,
        success=True,
        output=output,
        cost_usd=0.10,
        input_tokens=100,
        output_tokens=50,
        model="claude-sonnet-4-5",
    )


def _audit_result(task_id: str = "T-001") -> ExecutionResult:
    return ExecutionResult(
        task_id=task_id,
        success=True,
        output="verdict: approve\nAll good",
        cost_usd=0.05,
        input_tokens=50,
        output_tokens=25,
        model="claude-opus-4-6",
    )


def _make_orch(
    dev_result: ExecutionResult | None = None,
    audit_result: ExecutionResult | None = None,
) -> ProjectOrchestrator:
    config = _config()
    dev = AsyncMock(spec=ExecutorAdapter)
    dev.execute = AsyncMock(return_value=dev_result or _dev_result())
    aud = AsyncMock(spec=ExecutorAdapter)
    aud.execute = AsyncMock(return_value=audit_result or _audit_result())
    runner = TaskRunner(config=config, developer=dev, auditor=aud)
    return ProjectOrchestrator(runner=runner)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "loop_test.db"
    init_db(p)
    return p


# ── TestAutoApprove ──


class TestAutoApprove:
    async def test_single_task_to_accepted(self, db_path: Path) -> None:
        """A single task should reach ACCEPTED in auto-approve mode."""
        orch = _make_orch()
        ctx = orch.runner.create_task("T-001", budget_usd=5.0)
        ctx.plan = "Plan"
        ctx.criteria = "Criteria"
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.create_stage("S1", "Stage 1", ["T-001"])

        # Save initial state
        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        result = await run_loop(orch, db_path, auto_approve=True)
        assert result.tasks_run >= 1
        assert orch.runner.get_task("T-001").state is TaskState.ACCEPTED

    async def test_multiple_tasks(self, db_path: Path) -> None:
        """Multiple tasks with dependencies should all complete."""
        orch = _make_orch()
        for tid in ["T-001", "T-002"]:
            ctx = orch.runner.create_task(tid, budget_usd=5.0)
            ctx.plan = "Plan"
            ctx.criteria = "Criteria"
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.graph.add_task(
            TaskNode(task_id="T-002", dependencies=frozenset({"T-001"}), stage_id="S1")
        )
        orch.create_stage("S1", "Stage 1", ["T-001", "T-002"])

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        result = await run_loop(orch, db_path, auto_approve=True)
        assert result.tasks_run >= 2
        assert orch.runner.get_task("T-001").state is TaskState.ACCEPTED
        assert orch.runner.get_task("T-002").state is TaskState.ACCEPTED

    async def test_failed_task_continues(self, db_path: Path) -> None:
        """If one task fails, the loop continues with others."""

        async def _dev_side_effect(prompt: str, task_id: str) -> ExecutionResult:
            if task_id == "T-001":
                raise BudgetExceededError(5.1, 5.0)
            return _dev_result(task_id)

        dev = AsyncMock(spec=ExecutorAdapter)
        dev.execute = AsyncMock(side_effect=_dev_side_effect)
        config = _config()
        aud = AsyncMock(spec=ExecutorAdapter)
        aud.execute = AsyncMock(return_value=_audit_result())
        runner = TaskRunner(config=config, developer=dev, auditor=aud)
        orch = ProjectOrchestrator(runner=runner)

        for tid in ["T-001", "T-002"]:
            ctx = orch.runner.create_task(tid, budget_usd=5.0)
            ctx.plan = "Plan"
            ctx.criteria = "Criteria"
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.graph.add_task(TaskNode(task_id="T-002", stage_id="S1"))
        orch.create_stage("S1", "Stage 1", ["T-001", "T-002"])

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        await run_loop(orch, db_path, auto_approve=True)
        assert orch.runner.get_task("T-001").state is TaskState.FAILED
        assert orch.runner.get_task("T-002").state is TaskState.ACCEPTED

    async def test_budget_exceeded(self, db_path: Path) -> None:
        """Budget exceeded on developer call → task FAILED."""
        dev = AsyncMock(spec=ExecutorAdapter)
        dev.execute = AsyncMock(side_effect=BudgetExceededError(5.1, 5.0))
        config = _config()
        aud = AsyncMock(spec=ExecutorAdapter)
        aud.execute = AsyncMock(return_value=_audit_result())
        runner = TaskRunner(config=config, developer=dev, auditor=aud)
        orch = ProjectOrchestrator(runner=runner)

        ctx = orch.runner.create_task("T-001", budget_usd=5.0)
        ctx.plan = "Plan"
        ctx.criteria = "Criteria"
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.create_stage("S1", "Stage 1", ["T-001"])

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        await run_loop(orch, db_path, auto_approve=True)
        assert orch.runner.get_task("T-001").state is TaskState.FAILED


# ── TestPauseMode ──


class TestPauseMode:
    async def test_pauses_at_plan_review(self, db_path: Path) -> None:
        """Default mode should pause at PLAN_REVIEW."""
        orch = _make_orch()
        ctx = orch.runner.create_task("T-001", budget_usd=5.0)
        ctx.plan = "Plan"
        ctx.criteria = "Criteria"
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.create_stage("S1", "Stage 1", ["T-001"])

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        result = await run_loop(orch, db_path, auto_approve=False)
        assert result.paused is True
        assert any("approve-plan T-001" in a for a in result.pending_actions)
        assert orch.runner.get_task("T-001").state is TaskState.PLAN_REVIEW

    async def test_pauses_at_merged(self, db_path: Path) -> None:
        """Default mode should pause at MERGED waiting for accept."""
        orch = _make_orch()
        ctx = orch.runner.create_task("T-001", budget_usd=5.0)
        ctx.plan = "Plan"
        ctx.criteria = "Criteria"
        # Pre-approve plan to skip that checkpoint
        orch.runner.submit_plan("T-001", "Plan", "Criteria")
        orch.runner.approve_plan("T-001")
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.create_stage("S1", "Stage 1", ["T-001"])

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        result = await run_loop(orch, db_path, auto_approve=False)
        assert result.paused is True
        assert orch.runner.get_task("T-001").state is TaskState.MERGED

    async def test_pauses_at_stage_review(self, db_path: Path) -> None:
        """Default mode should pause at stage REVIEW with accept-stage action."""
        orch = _make_orch()
        ctx = orch.runner.create_task("T-001", budget_usd=5.0)
        ctx.plan = "Plan"
        ctx.criteria = "Criteria"
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.create_stage("S1", "Stage 1", ["T-001"])

        # Pre-approve plan and pre-accept task to skip those checkpoints
        orch.runner.submit_plan("T-001", "Plan", "Criteria")
        orch.runner.approve_plan("T-001")

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        result = await run_loop(orch, db_path, auto_approve=False)
        assert result.paused is True
        assert any("accept-stage S1" in a for a in result.pending_actions)
        assert orch.get_stage("S1").state is StageState.REVIEW

    async def test_resume_after_stage_reject(self, db_path: Path) -> None:
        """Reject stage → IN_PROGRESS → tasks re-run → REVIEW → accept → ACCEPTED."""
        orch = _make_orch()
        ctx = orch.runner.create_task("T-001", budget_usd=5.0)
        ctx.plan = "Plan"
        ctx.criteria = "Criteria"
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.create_stage("S1", "Stage 1", ["T-001"])

        # Pre-approve plan and pre-accept task
        orch.runner.submit_plan("T-001", "Plan", "Criteria")
        orch.runner.approve_plan("T-001")

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        # First run: reaches REVIEW, pauses
        result1 = await run_loop(orch, db_path, auto_approve=False)
        assert result1.paused is True
        assert orch.get_stage("S1").state is StageState.REVIEW

        # Architect rejects
        orch.reject_stage("S1")
        assert orch.get_stage("S1").state is StageState.IN_PROGRESS

        # Second run with auto_approve: should re-advance and accept
        result2 = await run_loop(orch, db_path, auto_approve=True)
        assert result2.completed is True
        assert orch.get_stage("S1").state is StageState.ACCEPTED

    async def test_resume_after_approve(self, db_path: Path) -> None:
        """After manual approve-plan, run should continue from PLAN_APPROVED."""
        orch = _make_orch()
        ctx = orch.runner.create_task("T-001", budget_usd=5.0)
        ctx.plan = "Plan"
        ctx.criteria = "Criteria"
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.create_stage("S1", "Stage 1", ["T-001"])

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        # First run pauses at plan_review
        result1 = await run_loop(orch, db_path, auto_approve=False)
        assert result1.paused is True

        # Manual approve
        orch.runner.approve_plan("T-001")

        # Second run continues (still pauses at merged without auto-approve)
        await run_loop(orch, db_path, auto_approve=False)
        assert orch.runner.get_task("T-001").state is TaskState.MERGED


# ── TestPersistence ──


class TestPersistence:
    async def test_state_saved_after_transition(self, db_path: Path) -> None:
        """Each transition should be saved to SQLite."""
        orch = _make_orch()
        ctx = orch.runner.create_task("T-001", budget_usd=5.0)
        ctx.plan = "Plan"
        ctx.criteria = "Criteria"
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.create_stage("S1", "Stage 1", ["T-001"])

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        await run_loop(orch, db_path, auto_approve=True)

        # Verify state is in the DB
        conn = get_connection(db_path)
        tasks = load_tasks(conn)
        conn.close()
        assert "T-001" in tasks
        assert tasks["T-001"].state is TaskState.ACCEPTED
        assert len(tasks["T-001"].history) > 0

    async def test_reload_and_resume(self, db_path: Path) -> None:
        """Should be able to reload from DB and continue."""
        orch = _make_orch()
        ctx = orch.runner.create_task("T-001", budget_usd=5.0)
        ctx.plan = "Plan"
        ctx.criteria = "Criteria"
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.create_stage("S1", "Stage 1", ["T-001"])

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        # Run until pause
        await run_loop(orch, db_path, auto_approve=False)

        # Save state
        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        # Reload from DB
        config = _config()
        dev = AsyncMock(spec=ExecutorAdapter)
        dev.execute = AsyncMock(return_value=_dev_result())
        aud = AsyncMock(spec=ExecutorAdapter)
        aud.execute = AsyncMock(return_value=_audit_result())
        from orchestrator.store import load_orchestrator

        orch2 = load_orchestrator(db_path, config, dev, aud)
        assert "T-001" in orch2.runner.task_ids
        t = orch2.runner.get_task("T-001")
        assert t.state is TaskState.PLAN_REVIEW


# ── TestE2E ──


def _config_with_commands(**cmd_overrides: str) -> ProjectConfig:
    defaults: dict[str, object] = {
        "budgets": BudgetsConfig(per_task_usd=5.0),
        "commands": CommandsConfig(**cmd_overrides),
    }
    return ProjectConfig(**defaults)  # type: ignore[arg-type]


def _make_orch_with_commands(
    cmd_overrides: dict[str, str] | None = None,
    dev_result: ExecutionResult | None = None,
    audit_result: ExecutionResult | None = None,
) -> ProjectOrchestrator:
    config = _config_with_commands(**(cmd_overrides or {}))
    dev = AsyncMock(spec=ExecutorAdapter)
    dev.execute = AsyncMock(return_value=dev_result or _dev_result())
    aud = AsyncMock(spec=ExecutorAdapter)
    aud.execute = AsyncMock(return_value=audit_result or _audit_result())
    runner = TaskRunner(config=config, developer=dev, auditor=aud)
    return ProjectOrchestrator(runner=runner)


def _setup_single_task(orch: ProjectOrchestrator) -> None:
    """Create a single task T-001 in stage S1 with plan+criteria."""
    ctx = orch.runner.create_task("T-001", budget_usd=5.0)
    ctx.plan = "Plan"
    ctx.criteria = "Criteria"
    orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
    orch.create_stage("S1", "Stage 1", ["T-001"])


class TestE2E:
    async def test_e2e_green_advances_to_review(self, db_path: Path) -> None:
        """All E2E commands rc=0 → stage reaches REVIEW → ACCEPTED."""
        orch = _make_orch_with_commands({"test": "echo ok", "lint": "echo ok"})
        _setup_single_task(orch)

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        green = TestResults(passed=2, failed=0, errors=0, coverage_pct=0.0)
        with unittest.mock.patch(
            "orchestrator.loop._run_e2e_suite",
            new=AsyncMock(return_value=green),
        ):
            result = await run_loop(orch, db_path, auto_approve=True)

        assert result.completed is True
        assert orch.get_stage("S1").state is StageState.ACCEPTED

    async def test_e2e_test_fail_returns_to_in_progress(self, db_path: Path) -> None:
        """test command rc=1 → stage returns to IN_PROGRESS."""
        orch = _make_orch()
        _setup_single_task(orch)

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        red = TestResults(passed=0, failed=1, errors=0, coverage_pct=0.0, raw_output="FAILED")
        green = TestResults(passed=1, failed=0, errors=0, coverage_pct=0.0)

        e2e_mock = AsyncMock(side_effect=[red, green])
        with unittest.mock.patch("orchestrator.loop._run_e2e_suite", new=e2e_mock):
            result = await run_loop(orch, db_path, auto_approve=True)

        # First E2E red → IN_PROGRESS, second pass green → REVIEW → ACCEPTED
        assert result.completed is True
        assert e2e_mock.await_count == 2

    async def test_e2e_lint_fail_returns_to_in_progress(self, db_path: Path) -> None:
        """lint command rc=1 → stage returns to IN_PROGRESS."""
        orch = _make_orch()
        _setup_single_task(orch)

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        red = TestResults(passed=1, failed=1, errors=0, coverage_pct=0.0, raw_output="lint error")
        green = TestResults(passed=2, failed=0, errors=0, coverage_pct=0.0)

        e2e_mock = AsyncMock(side_effect=[red, green])
        with unittest.mock.patch("orchestrator.loop._run_e2e_suite", new=e2e_mock):
            result = await run_loop(orch, db_path, auto_approve=True)

        assert result.completed is True
        assert e2e_mock.await_count == 2

    async def test_e2e_no_commands_auto_green(self, db_path: Path) -> None:
        """Empty commands → auto-green, backwards compatible."""
        orch = _make_orch()  # default config has empty commands
        _setup_single_task(orch)

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        result = await run_loop(orch, db_path, auto_approve=True)
        assert result.completed is True
        assert orch.get_stage("S1").state is StageState.ACCEPTED

    async def test_e2e_results_saved_on_stage(self, db_path: Path) -> None:
        """E2E results are persisted on StageContext.e2e_results."""
        orch = _make_orch()
        _setup_single_task(orch)

        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        green = TestResults(passed=1, failed=0, errors=0, coverage_pct=0.0, raw_output="1 passed")
        with unittest.mock.patch(
            "orchestrator.loop._run_e2e_suite",
            new=AsyncMock(return_value=green),
        ):
            await run_loop(orch, db_path, auto_approve=True)

        stage = orch.get_stage("S1")
        assert stage.e2e_results is not None
        assert stage.e2e_results.all_green is True
        assert stage.e2e_results.passed >= 1
        assert "1 passed" in (stage.e2e_results.raw_output or "")
