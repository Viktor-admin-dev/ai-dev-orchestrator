"""Tests for TaskRunner — mocked executors, full lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from orchestrator.config import BudgetsConfig, ProjectConfig
from orchestrator.cost import BudgetExceededError
from orchestrator.evidence import MutationResults, TestResults
from orchestrator.executor.base import ExecutionResult, ExecutorAdapter
from orchestrator.runner import TaskRunner
from orchestrator.types import TaskState

# ── Helpers ──


def _config(**overrides: object) -> ProjectConfig:
    defaults: dict[str, object] = {
        "budgets": BudgetsConfig(per_task_usd=5.0),
    }
    defaults.update(overrides)
    return ProjectConfig(**defaults)  # type: ignore[arg-type]


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


def _green_tests() -> TestResults:
    return TestResults(passed=10, failed=0, errors=0, coverage_pct=90.0)


def _red_tests() -> TestResults:
    return TestResults(passed=8, failed=2, errors=0, coverage_pct=70.0)


def _mutation_ok() -> MutationResults:
    return MutationResults(total_mutants=10, killed=9, survived=1, timeout=0)


def _mutation_fail() -> MutationResults:
    return MutationResults(total_mutants=10, killed=5, survived=5, timeout=0)


def _mock_executor(result: ExecutionResult | None = None) -> ExecutorAdapter:
    mock = AsyncMock(spec=ExecutorAdapter)
    mock.execute = AsyncMock(return_value=result or _success_result())
    return mock


def _runner(**kwargs: object) -> TaskRunner:
    defaults: dict[str, object] = {
        "config": _config(),
        "developer": _mock_executor(_success_result(output="+added code")),
        "auditor": _mock_executor(_success_result(output="verdict: approve\nAll good")),
    }
    defaults.update(kwargs)
    return TaskRunner(**defaults)  # type: ignore[arg-type]


# ── Happy path ──


class TestHappyPath:
    async def test_full_lifecycle(self) -> None:
        """DRAFT → PLAN_REVIEW → PLAN_APPROVED → IN_PROGRESS → TESTING
        → AWAIT_AUDIT → PR_READY → MERGED → ACCEPTED."""
        r = _runner()
        r.create_task("T-001")

        r.submit_plan("T-001", "Plan A", "Criteria A")
        assert r.get_task("T-001").state is TaskState.PLAN_REVIEW

        r.approve_plan("T-001")
        assert r.get_task("T-001").state is TaskState.PLAN_APPROVED

        await r.run_developer("T-001")
        assert r.get_task("T-001").state is TaskState.TESTING

        await r.run_tests("T-001", _green_tests())
        assert r.get_task("T-001").state is TaskState.AWAIT_AUDIT

        await r.run_audit("T-001")
        assert r.get_task("T-001").state is TaskState.PR_READY

        r.mark_merged("T-001")
        assert r.get_task("T-001").state is TaskState.MERGED

        r.accept("T-001")
        assert r.get_task("T-001").state is TaskState.ACCEPTED

    async def test_full_lifecycle_with_mutation(self) -> None:
        """Critical module path: ... → AWAIT_AUDIT → MUTATION → PR_READY."""
        r = _runner()
        r.create_task("T-001", touches_critical=True)

        r.submit_plan("T-001", "Plan", "Criteria")
        r.approve_plan("T-001")
        await r.run_developer("T-001")
        await r.run_tests("T-001", _green_tests())
        await r.run_audit("T-001")
        assert r.get_task("T-001").state is TaskState.MUTATION

        await r.run_mutation("T-001", _mutation_ok())
        assert r.get_task("T-001").state is TaskState.PR_READY


# ── Rework loop ──


class TestReworkLoop:
    async def test_rework_on_failed_tests(self) -> None:
        r = _runner()
        r.create_task("T-001")
        r.submit_plan("T-001", "P", "C")
        r.approve_plan("T-001")
        await r.run_developer("T-001")

        await r.run_tests("T-001", _red_tests())
        assert r.get_task("T-001").state is TaskState.REWORK

    async def test_rework_on_request_changes(self) -> None:
        auditor = _mock_executor(_success_result(output="verdict: request-changes\nFix naming"))
        r = _runner(auditor=auditor)
        r.create_task("T-001")
        r.submit_plan("T-001", "P", "C")
        r.approve_plan("T-001")
        await r.run_developer("T-001")
        await r.run_tests("T-001", _green_tests())

        await r.run_audit("T-001")
        assert r.get_task("T-001").state is TaskState.REWORK

    async def test_rework_then_retry(self) -> None:
        """REWORK → IN_PROGRESS increments attempt."""
        auditor = _mock_executor(_success_result(output="verdict: request-changes\nFix it"))
        r = _runner(auditor=auditor)
        r.create_task("T-001")
        r.submit_plan("T-001", "P", "C")
        r.approve_plan("T-001")
        await r.run_developer("T-001")
        await r.run_tests("T-001", _green_tests())
        await r.run_audit("T-001")
        assert r.get_task("T-001").state is TaskState.REWORK

        r.rework("T-001")
        assert r.get_task("T-001").state is TaskState.IN_PROGRESS
        assert r.get_task("T-001").attempt == 1


# ── Failure paths ──


class TestFailurePaths:
    async def test_reject_goes_to_failed(self) -> None:
        auditor = _mock_executor(_success_result(output="verdict: reject\nFundamental flaw"))
        r = _runner(auditor=auditor)
        r.create_task("T-001")
        r.submit_plan("T-001", "P", "C")
        r.approve_plan("T-001")
        await r.run_developer("T-001")
        await r.run_tests("T-001", _green_tests())

        await r.run_audit("T-001")
        assert r.get_task("T-001").state is TaskState.FAILED

    async def test_budget_exceeded_developer(self) -> None:
        dev = AsyncMock(spec=ExecutorAdapter)
        dev.execute = AsyncMock(side_effect=BudgetExceededError(5.1, 5.0))
        r = _runner(developer=dev)
        r.create_task("T-001")
        r.submit_plan("T-001", "P", "C")
        r.approve_plan("T-001")

        await r.run_developer("T-001")
        assert r.get_task("T-001").state is TaskState.FAILED

    async def test_mutation_fail_goes_to_rework(self) -> None:
        r = _runner()
        r.create_task("T-001", touches_critical=True)
        r.submit_plan("T-001", "P", "C")
        r.approve_plan("T-001")
        await r.run_developer("T-001")
        await r.run_tests("T-001", _green_tests())
        await r.run_audit("T-001")

        await r.run_mutation("T-001", _mutation_fail())
        assert r.get_task("T-001").state is TaskState.REWORK

    def test_fail_explicit(self) -> None:
        r = _runner()
        r.create_task("T-001")
        r.fail("T-001", "external error")
        assert r.get_task("T-001").state is TaskState.FAILED


# ── Plan rejection ──


class TestPlanRejection:
    def test_reject_plan_returns_to_draft(self) -> None:
        r = _runner()
        r.create_task("T-001")
        r.submit_plan("T-001", "P", "C")
        r.reject_plan("T-001")
        assert r.get_task("T-001").state is TaskState.DRAFT

    def test_resubmit_after_rejection(self) -> None:
        r = _runner()
        r.create_task("T-001")
        r.submit_plan("T-001", "P1", "C1")
        r.reject_plan("T-001")
        r.submit_plan("T-001", "P2", "C2")
        ctx = r.get_task("T-001")
        assert ctx.state is TaskState.PLAN_REVIEW
        assert ctx.plan == "P2"


# ── Task management ──


class TestTaskManagement:
    def test_create_task(self) -> None:
        r = _runner()
        ctx = r.create_task("T-001")
        assert ctx.state is TaskState.DRAFT
        assert ctx.task_id == "T-001"

    def test_get_unknown_task(self) -> None:
        r = _runner()
        with pytest.raises(KeyError, match="T-999"):
            r.get_task("T-999")

    def test_custom_budget(self) -> None:
        r = _runner()
        ctx = r.create_task("T-001", budget_usd=10.0)
        assert ctx.cost.budget_usd == 10.0

    def test_cost_recorded(self) -> None:
        r = _runner()
        r.create_task("T-001")
        r.submit_plan("T-001", "P", "C")
        r.approve_plan("T-001")
        # Developer cost will be recorded after run_developer


class TestHistoryTracking:
    async def test_history_tracks_all_transitions(self) -> None:
        r = _runner()
        r.create_task("T-001")
        r.submit_plan("T-001", "P", "C")
        r.approve_plan("T-001")
        await r.run_developer("T-001")
        ctx = r.get_task("T-001")
        # DRAFT → PLAN_REVIEW → PLAN_APPROVED → IN_PROGRESS → TESTING
        assert len(ctx.history) == 4
