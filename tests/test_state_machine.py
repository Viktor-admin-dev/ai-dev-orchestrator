"""Tests for TaskCycleFSM — all transitions, guards, and invariants."""

from __future__ import annotations

import pytest

from orchestrator.cost import CostTracker
from orchestrator.evidence import (
    AuditorVerdict,
    EvidencePack,
    MutationResults,
    TestResults,
)
from orchestrator.state_machine import (
    InvalidTransitionError,
    TaskContext,
    TaskCycleFSM,
    Transition,
)
from orchestrator.types import AuditVerdict, TaskState

# ── Helpers ──


def _ctx(state: TaskState = TaskState.DRAFT, **kwargs: object) -> TaskContext:
    defaults: dict[str, object] = {
        "task_id": "T-001",
        "state": state,
        "cost": CostTracker(budget_usd=5.0),
    }
    defaults.update(kwargs)
    return TaskContext(**defaults)  # type: ignore[arg-type]


def _green_tests() -> TestResults:
    return TestResults(passed=10, failed=0, errors=0, coverage_pct=90.0)


def _red_tests() -> TestResults:
    return TestResults(passed=8, failed=2, errors=0, coverage_pct=70.0)


def _approve_verdict() -> AuditorVerdict:
    return AuditorVerdict(verdict=AuditVerdict.APPROVE, reasoning="LGTM")


def _request_changes_verdict() -> AuditorVerdict:
    return AuditorVerdict(verdict=AuditVerdict.REQUEST_CHANGES, reasoning="Fix naming")


def _reject_verdict() -> AuditorVerdict:
    return AuditorVerdict(verdict=AuditVerdict.REJECT, reasoning="Fundamental flaw")


def _evidence_ready() -> EvidencePack:
    return EvidencePack(
        task_id="T-001",
        diff="+code",
        criteria="Must work",
        test_results=_green_tests(),
        auditor_verdict=_approve_verdict(),
    )


def _evidence_with_mutation(acceptable: bool = True) -> EvidencePack:
    killed = 9 if acceptable else 5
    return EvidencePack(
        task_id="T-001",
        diff="+code",
        criteria="Must work",
        test_results=_green_tests(),
        auditor_verdict=_approve_verdict(),
        mutation_results=MutationResults(
            total_mutants=10, killed=killed, survived=10 - killed, timeout=0
        ),
    )


FSM = TaskCycleFSM()


# ── Allowed transitions ──


class TestAllowedTransitions:
    def test_draft_to_plan_review(self) -> None:
        ctx = _ctx(TaskState.DRAFT)
        FSM.transition(ctx, TaskState.PLAN_REVIEW, "plan submitted")
        assert ctx.state is TaskState.PLAN_REVIEW

    def test_plan_review_to_approved(self) -> None:
        ctx = _ctx(TaskState.PLAN_REVIEW)
        FSM.transition(ctx, TaskState.PLAN_APPROVED, "plan approved")
        assert ctx.state is TaskState.PLAN_APPROVED

    def test_plan_review_to_draft(self) -> None:
        ctx = _ctx(TaskState.PLAN_REVIEW)
        FSM.transition(ctx, TaskState.DRAFT, "plan rejected, redo")
        assert ctx.state is TaskState.DRAFT

    def test_plan_approved_to_in_progress(self) -> None:
        ctx = _ctx(TaskState.PLAN_APPROVED)
        FSM.transition(ctx, TaskState.IN_PROGRESS, "start coding")
        assert ctx.state is TaskState.IN_PROGRESS

    def test_in_progress_to_testing(self) -> None:
        ctx = _ctx(TaskState.IN_PROGRESS)
        FSM.transition(ctx, TaskState.TESTING, "code written")
        assert ctx.state is TaskState.TESTING

    def test_testing_to_await_audit_green(self) -> None:
        ctx = _ctx(
            TaskState.TESTING,
            evidence=EvidencePack(
                task_id="T-001",
                diff="+code",
                criteria="c",
                test_results=_green_tests(),
            ),
        )
        FSM.transition(ctx, TaskState.AWAIT_AUDIT, "tests green")
        assert ctx.state is TaskState.AWAIT_AUDIT

    def test_testing_to_rework(self) -> None:
        ctx = _ctx(TaskState.TESTING)
        FSM.transition(ctx, TaskState.REWORK, "tests red")
        assert ctx.state is TaskState.REWORK

    def test_await_audit_to_pr_ready(self) -> None:
        ctx = _ctx(TaskState.AWAIT_AUDIT, evidence=_evidence_ready())
        FSM.transition(ctx, TaskState.PR_READY, "approved")
        assert ctx.state is TaskState.PR_READY

    def test_await_audit_to_rework(self) -> None:
        ctx = _ctx(TaskState.AWAIT_AUDIT)
        FSM.transition(ctx, TaskState.REWORK, "request changes")
        assert ctx.state is TaskState.REWORK

    def test_await_audit_to_failed(self) -> None:
        ctx = _ctx(TaskState.AWAIT_AUDIT)
        FSM.transition(ctx, TaskState.FAILED, "reject")
        assert ctx.state is TaskState.FAILED

    def test_await_audit_to_mutation_critical(self) -> None:
        ctx = _ctx(
            TaskState.AWAIT_AUDIT,
            evidence=_evidence_ready(),
            touches_critical=True,
        )
        FSM.transition(ctx, TaskState.MUTATION, "critical module")
        assert ctx.state is TaskState.MUTATION

    def test_mutation_to_pr_ready(self) -> None:
        ctx = _ctx(
            TaskState.MUTATION,
            evidence=_evidence_with_mutation(acceptable=True),
        )
        FSM.transition(ctx, TaskState.PR_READY, "mutation ok")
        assert ctx.state is TaskState.PR_READY

    def test_mutation_to_rework(self) -> None:
        ctx = _ctx(TaskState.MUTATION)
        FSM.transition(ctx, TaskState.REWORK, "mutation fail")
        assert ctx.state is TaskState.REWORK

    def test_rework_to_in_progress(self) -> None:
        ctx = _ctx(TaskState.REWORK, attempt=0, max_attempts=3)
        FSM.transition(ctx, TaskState.IN_PROGRESS, "retry")
        assert ctx.state is TaskState.IN_PROGRESS
        assert ctx.attempt == 1

    def test_pr_ready_to_merged(self) -> None:
        ctx = _ctx(TaskState.PR_READY)
        FSM.transition(ctx, TaskState.MERGED, "PR merged")
        assert ctx.state is TaskState.MERGED

    def test_merged_to_accepted(self) -> None:
        ctx = _ctx(TaskState.MERGED, architect_approved=True)
        FSM.transition(ctx, TaskState.ACCEPTED, "architect accepted")
        assert ctx.state is TaskState.ACCEPTED


# ── Forbidden transitions ──


class TestForbiddenTransitions:
    def test_draft_to_in_progress(self) -> None:
        ctx = _ctx(TaskState.DRAFT)
        with pytest.raises(InvalidTransitionError, match="not allowed"):
            FSM.transition(ctx, TaskState.IN_PROGRESS)

    def test_draft_to_pr_ready(self) -> None:
        ctx = _ctx(TaskState.DRAFT)
        with pytest.raises(InvalidTransitionError, match="not allowed"):
            FSM.transition(ctx, TaskState.PR_READY)

    def test_testing_to_pr_ready(self) -> None:
        ctx = _ctx(TaskState.TESTING)
        with pytest.raises(InvalidTransitionError, match="not allowed"):
            FSM.transition(ctx, TaskState.PR_READY)

    def test_in_progress_to_await_audit(self) -> None:
        ctx = _ctx(TaskState.IN_PROGRESS)
        with pytest.raises(InvalidTransitionError, match="not allowed"):
            FSM.transition(ctx, TaskState.AWAIT_AUDIT)

    def test_accepted_is_terminal(self) -> None:
        ctx = _ctx(TaskState.ACCEPTED)
        with pytest.raises(InvalidTransitionError, match="not allowed"):
            FSM.transition(ctx, TaskState.DRAFT)

    def test_failed_is_terminal(self) -> None:
        ctx = _ctx(TaskState.FAILED)
        with pytest.raises(InvalidTransitionError, match="not allowed"):
            FSM.transition(ctx, TaskState.DRAFT)

    def test_cannot_go_failed_from_accepted(self) -> None:
        ctx = _ctx(TaskState.ACCEPTED)
        with pytest.raises(InvalidTransitionError):
            FSM.transition(ctx, TaskState.FAILED)

    def test_cannot_go_failed_from_failed(self) -> None:
        ctx = _ctx(TaskState.FAILED)
        with pytest.raises(InvalidTransitionError):
            FSM.transition(ctx, TaskState.FAILED)


# ── Guard conditions ──


class TestGuards:
    def test_testing_to_await_audit_no_evidence(self) -> None:
        ctx = _ctx(TaskState.TESTING)
        with pytest.raises(InvalidTransitionError, match="no test results"):
            FSM.transition(ctx, TaskState.AWAIT_AUDIT)

    def test_testing_to_await_audit_red_tests(self) -> None:
        ctx = _ctx(
            TaskState.TESTING,
            evidence=EvidencePack(
                task_id="T-001",
                diff="+code",
                criteria="c",
                test_results=_red_tests(),
            ),
        )
        with pytest.raises(InvalidTransitionError, match="tests not green"):
            FSM.transition(ctx, TaskState.AWAIT_AUDIT)

    def test_inv1_pr_ready_blocked_without_evidence(self) -> None:
        """INV-1: PR_READY requires green tests + approve."""
        ctx = _ctx(TaskState.AWAIT_AUDIT)
        with pytest.raises(InvalidTransitionError, match="INV-1"):
            FSM.transition(ctx, TaskState.PR_READY)

    def test_inv1_pr_ready_blocked_request_changes(self) -> None:
        ep = EvidencePack(
            task_id="T-001",
            diff="+code",
            criteria="c",
            test_results=_green_tests(),
            auditor_verdict=_request_changes_verdict(),
        )
        ctx = _ctx(TaskState.AWAIT_AUDIT, evidence=ep)
        with pytest.raises(InvalidTransitionError, match="INV-1"):
            FSM.transition(ctx, TaskState.PR_READY)

    def test_inv4_accepted_blocked_without_architect(self) -> None:
        """INV-4: ACCEPTED requires architect approval."""
        ctx = _ctx(TaskState.MERGED, architect_approved=False)
        with pytest.raises(InvalidTransitionError, match="INV-4"):
            FSM.transition(ctx, TaskState.ACCEPTED)

    def test_rework_max_attempts_exceeded(self) -> None:
        ctx = _ctx(TaskState.REWORK, attempt=3, max_attempts=3)
        with pytest.raises(InvalidTransitionError, match="max attempts"):
            FSM.transition(ctx, TaskState.IN_PROGRESS)

    def test_mutation_gate_critical_blocks_pr_ready(self) -> None:
        """Critical modules must go through mutation testing."""
        ctx = _ctx(
            TaskState.AWAIT_AUDIT,
            evidence=_evidence_ready(),
            touches_critical=True,
        )
        with pytest.raises(InvalidTransitionError, match="critical modules"):
            FSM.transition(ctx, TaskState.PR_READY)

    def test_mutation_not_allowed_non_critical(self) -> None:
        ctx = _ctx(
            TaskState.AWAIT_AUDIT,
            evidence=_evidence_ready(),
            touches_critical=False,
        )
        with pytest.raises(InvalidTransitionError, match="mutation only"):
            FSM.transition(ctx, TaskState.MUTATION)

    def test_mutation_blocked_without_approve(self) -> None:
        ep = EvidencePack(
            task_id="T-001",
            diff="+code",
            criteria="c",
            test_results=_green_tests(),
            auditor_verdict=_request_changes_verdict(),
        )
        ctx = _ctx(TaskState.AWAIT_AUDIT, evidence=ep, touches_critical=True)
        with pytest.raises(InvalidTransitionError, match="auditor did not approve"):
            FSM.transition(ctx, TaskState.MUTATION)

    def test_mutation_pr_ready_blocked_low_score(self) -> None:
        ctx = _ctx(
            TaskState.MUTATION,
            evidence=_evidence_with_mutation(acceptable=False),
        )
        with pytest.raises(InvalidTransitionError, match="mutation score"):
            FSM.transition(ctx, TaskState.PR_READY)

    def test_mutation_pr_ready_blocked_no_results(self) -> None:
        ctx = _ctx(
            TaskState.MUTATION,
            evidence=_evidence_ready(),  # no mutation_results
        )
        with pytest.raises(InvalidTransitionError, match="no mutation results"):
            FSM.transition(ctx, TaskState.PR_READY)


# ── Criteria-first enforcement ──


class TestCriteriaFirst:
    def test_plan_approved_allows_in_progress(self) -> None:
        ctx = _ctx(TaskState.PLAN_APPROVED)
        FSM.transition(ctx, TaskState.IN_PROGRESS)
        assert ctx.state is TaskState.IN_PROGRESS

    def test_draft_to_in_progress_forbidden(self) -> None:
        ctx = _ctx(TaskState.DRAFT)
        with pytest.raises(InvalidTransitionError):
            FSM.transition(ctx, TaskState.IN_PROGRESS)

    def test_plan_review_to_in_progress_forbidden(self) -> None:
        ctx = _ctx(TaskState.PLAN_REVIEW)
        with pytest.raises(InvalidTransitionError):
            FSM.transition(ctx, TaskState.IN_PROGRESS)


# ── * → FAILED ──


class TestFailedTransition:
    @pytest.mark.parametrize(
        "state",
        [
            TaskState.DRAFT,
            TaskState.PLAN_REVIEW,
            TaskState.PLAN_APPROVED,
            TaskState.IN_PROGRESS,
            TaskState.TESTING,
            TaskState.AWAIT_AUDIT,
            TaskState.MUTATION,
            TaskState.REWORK,
            TaskState.PR_READY,
            TaskState.MERGED,
        ],
    )
    def test_any_non_terminal_to_failed(self, state: TaskState) -> None:
        """INV-5: any non-terminal state can transition to FAILED."""
        ctx = _ctx(state)
        FSM.transition(ctx, TaskState.FAILED, "budget exceeded")
        assert ctx.state is TaskState.FAILED


# ── History / audit log ──


class TestHistory:
    def test_transition_records_history(self) -> None:
        ctx = _ctx(TaskState.DRAFT)
        FSM.transition(ctx, TaskState.PLAN_REVIEW, "submit plan")
        assert len(ctx.history) == 1
        t = ctx.history[0]
        assert t.from_state is TaskState.DRAFT
        assert t.to_state is TaskState.PLAN_REVIEW
        assert t.reason == "submit plan"

    def test_multiple_transitions_accumulate(self) -> None:
        ctx = _ctx(TaskState.DRAFT)
        FSM.transition(ctx, TaskState.PLAN_REVIEW, "submit")
        FSM.transition(ctx, TaskState.PLAN_APPROVED, "approve")
        FSM.transition(ctx, TaskState.IN_PROGRESS, "start")
        assert len(ctx.history) == 3

    def test_failed_transition_not_recorded(self) -> None:
        ctx = _ctx(TaskState.DRAFT)
        with pytest.raises(InvalidTransitionError):
            FSM.transition(ctx, TaskState.PR_READY)
        assert len(ctx.history) == 0


# ── Rework counter ──


class TestReworkCounter:
    def test_rework_increments_attempt(self) -> None:
        ctx = _ctx(TaskState.REWORK, attempt=0, max_attempts=3)
        FSM.transition(ctx, TaskState.IN_PROGRESS, "retry")
        assert ctx.attempt == 1

    def test_multiple_reworks(self) -> None:
        ctx = _ctx(TaskState.REWORK, attempt=1, max_attempts=3)
        FSM.transition(ctx, TaskState.IN_PROGRESS, "retry 2")
        assert ctx.attempt == 2

    def test_rework_at_limit_fails(self) -> None:
        ctx = _ctx(TaskState.REWORK, attempt=3, max_attempts=3)
        with pytest.raises(InvalidTransitionError, match="max attempts"):
            FSM.transition(ctx, TaskState.IN_PROGRESS)


# ── can_transition API ──


class TestCanTransition:
    def test_returns_tuple(self) -> None:
        ctx = _ctx(TaskState.DRAFT)
        ok, reason = FSM.can_transition(ctx, TaskState.PLAN_REVIEW)
        assert ok is True
        assert reason == "ok"

    def test_forbidden_returns_reason(self) -> None:
        ctx = _ctx(TaskState.DRAFT)
        ok, reason = FSM.can_transition(ctx, TaskState.PR_READY)
        assert ok is False
        assert "not allowed" in reason

    def test_allowed_transitions_set(self) -> None:
        ctx = _ctx(TaskState.DRAFT)
        allowed = FSM.allowed_transitions(ctx)
        assert TaskState.PLAN_REVIEW in allowed
        assert TaskState.FAILED in allowed
        assert TaskState.PR_READY not in allowed


# ── Transition dataclass ──


class TestTransitionDataclass:
    def test_timestamp_auto_set(self) -> None:
        t = Transition(
            from_state=TaskState.DRAFT,
            to_state=TaskState.PLAN_REVIEW,
            reason="test",
        )
        assert t.timestamp  # non-empty ISO string
        assert "T" in t.timestamp  # ISO format
