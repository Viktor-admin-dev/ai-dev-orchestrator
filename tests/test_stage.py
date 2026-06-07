"""Tests for StageFSM — transitions, guards, and INV-4."""

from __future__ import annotations

import pytest

from orchestrator.cost import CostTracker
from orchestrator.evidence import TestResults
from orchestrator.stage import (
    InvalidStageTransitionError,
    StageFSM,
    StageContext,
    StageTransition,
)
from orchestrator.types import StageState

# ── Helpers ──


def _ctx(
    state: StageState = StageState.PLANNING,
    **kwargs: object,
) -> StageContext:
    defaults: dict[str, object] = {
        "stage_id": "S-001",
        "name": "Stage 1",
        "state": state,
        "cost": CostTracker(budget_usd=100.0),
    }
    defaults.update(kwargs)
    return StageContext(**defaults)  # type: ignore[arg-type]


def _green_e2e() -> TestResults:
    return TestResults(passed=20, failed=0, errors=0, coverage_pct=95.0)


def _red_e2e() -> TestResults:
    return TestResults(passed=18, failed=2, errors=0, coverage_pct=90.0)


def _all_tasks_done() -> dict[str, str]:
    return {"T-001": "accepted", "T-002": "pr_ready"}


FSM = StageFSM()


# ── Allowed transitions ──


class TestAllowedTransitions:
    def test_planning_to_in_progress(self) -> None:
        ctx = _ctx(StageState.PLANNING)
        FSM.transition(ctx, StageState.IN_PROGRESS, "start stage")
        assert ctx.state is StageState.IN_PROGRESS

    def test_in_progress_to_integrating(self) -> None:
        ctx = _ctx(
            StageState.IN_PROGRESS,
            task_ids=["T-001", "T-002"],
            task_states=_all_tasks_done(),
        )
        FSM.transition(ctx, StageState.INTEGRATING, "all tasks done")
        assert ctx.state is StageState.INTEGRATING

    def test_integrating_to_review_green(self) -> None:
        ctx = _ctx(StageState.INTEGRATING, e2e_results=_green_e2e())
        FSM.transition(ctx, StageState.REVIEW, "E2E green")
        assert ctx.state is StageState.REVIEW

    def test_integrating_to_in_progress_red(self) -> None:
        ctx = _ctx(StageState.INTEGRATING)
        FSM.transition(ctx, StageState.IN_PROGRESS, "E2E red")
        assert ctx.state is StageState.IN_PROGRESS

    def test_review_to_accepted(self) -> None:
        ctx = _ctx(StageState.REVIEW, architect_approved=True)
        FSM.transition(ctx, StageState.ACCEPTED, "architect accepted")
        assert ctx.state is StageState.ACCEPTED

    def test_review_to_in_progress_rejected(self) -> None:
        ctx = _ctx(StageState.REVIEW)
        FSM.transition(ctx, StageState.IN_PROGRESS, "architect rejected")
        assert ctx.state is StageState.IN_PROGRESS


# ── Forbidden transitions ──


class TestForbiddenTransitions:
    def test_planning_to_integrating(self) -> None:
        ctx = _ctx(StageState.PLANNING)
        with pytest.raises(InvalidStageTransitionError, match="not allowed"):
            FSM.transition(ctx, StageState.INTEGRATING)

    def test_planning_to_review(self) -> None:
        ctx = _ctx(StageState.PLANNING)
        with pytest.raises(InvalidStageTransitionError, match="not allowed"):
            FSM.transition(ctx, StageState.REVIEW)

    def test_planning_to_accepted(self) -> None:
        ctx = _ctx(StageState.PLANNING)
        with pytest.raises(InvalidStageTransitionError, match="not allowed"):
            FSM.transition(ctx, StageState.ACCEPTED)

    def test_in_progress_to_review(self) -> None:
        ctx = _ctx(StageState.IN_PROGRESS)
        with pytest.raises(InvalidStageTransitionError, match="not allowed"):
            FSM.transition(ctx, StageState.REVIEW)

    def test_in_progress_to_accepted(self) -> None:
        ctx = _ctx(StageState.IN_PROGRESS)
        with pytest.raises(InvalidStageTransitionError, match="not allowed"):
            FSM.transition(ctx, StageState.ACCEPTED)

    def test_accepted_is_terminal(self) -> None:
        ctx = _ctx(StageState.ACCEPTED)
        with pytest.raises(InvalidStageTransitionError, match="not allowed"):
            FSM.transition(ctx, StageState.PLANNING)

    def test_failed_is_terminal(self) -> None:
        ctx = _ctx(StageState.FAILED)
        with pytest.raises(InvalidStageTransitionError, match="not allowed"):
            FSM.transition(ctx, StageState.PLANNING)

    def test_cannot_go_failed_from_accepted(self) -> None:
        ctx = _ctx(StageState.ACCEPTED)
        with pytest.raises(InvalidStageTransitionError):
            FSM.transition(ctx, StageState.FAILED)

    def test_cannot_go_failed_from_failed(self) -> None:
        ctx = _ctx(StageState.FAILED)
        with pytest.raises(InvalidStageTransitionError):
            FSM.transition(ctx, StageState.FAILED)


# ── Guard conditions ──


class TestGuards:
    def test_integrating_blocked_no_tasks(self) -> None:
        ctx = _ctx(StageState.IN_PROGRESS, task_ids=[])
        with pytest.raises(InvalidStageTransitionError, match="no tasks"):
            FSM.transition(ctx, StageState.INTEGRATING)

    def test_integrating_blocked_tasks_not_done(self) -> None:
        ctx = _ctx(
            StageState.IN_PROGRESS,
            task_ids=["T-001", "T-002"],
            task_states={"T-001": "accepted", "T-002": "in_progress"},
        )
        with pytest.raises(InvalidStageTransitionError, match="T-002 not done"):
            FSM.transition(ctx, StageState.INTEGRATING)

    def test_integrating_blocked_missing_task_state(self) -> None:
        ctx = _ctx(
            StageState.IN_PROGRESS,
            task_ids=["T-001"],
            task_states={},
        )
        with pytest.raises(InvalidStageTransitionError, match="T-001 not done"):
            FSM.transition(ctx, StageState.INTEGRATING)

    def test_integrating_allows_pr_ready_state(self) -> None:
        ctx = _ctx(
            StageState.IN_PROGRESS,
            task_ids=["T-001"],
            task_states={"T-001": "pr_ready"},
        )
        FSM.transition(ctx, StageState.INTEGRATING)
        assert ctx.state is StageState.INTEGRATING

    def test_integrating_allows_merged_state(self) -> None:
        ctx = _ctx(
            StageState.IN_PROGRESS,
            task_ids=["T-001"],
            task_states={"T-001": "merged"},
        )
        FSM.transition(ctx, StageState.INTEGRATING)
        assert ctx.state is StageState.INTEGRATING

    def test_review_blocked_no_e2e(self) -> None:
        ctx = _ctx(StageState.INTEGRATING)
        with pytest.raises(InvalidStageTransitionError, match="no E2E"):
            FSM.transition(ctx, StageState.REVIEW)

    def test_review_blocked_red_e2e(self) -> None:
        ctx = _ctx(StageState.INTEGRATING, e2e_results=_red_e2e())
        with pytest.raises(InvalidStageTransitionError, match="E2E tests not green"):
            FSM.transition(ctx, StageState.REVIEW)

    def test_inv4_accepted_blocked_without_architect(self) -> None:
        ctx = _ctx(StageState.REVIEW, architect_approved=False)
        with pytest.raises(InvalidStageTransitionError, match="INV-4"):
            FSM.transition(ctx, StageState.ACCEPTED)


# ── * → FAILED parametrized ──


class TestFailedTransition:
    @pytest.mark.parametrize(
        "state",
        [
            StageState.PLANNING,
            StageState.IN_PROGRESS,
            StageState.INTEGRATING,
            StageState.REVIEW,
        ],
    )
    def test_any_non_terminal_to_failed(self, state: StageState) -> None:
        ctx = _ctx(state)
        FSM.transition(ctx, StageState.FAILED, "external failure")
        assert ctx.state is StageState.FAILED


# ── History and timestamps ──


class TestHistory:
    def test_transition_records_history(self) -> None:
        ctx = _ctx(StageState.PLANNING)
        FSM.transition(ctx, StageState.IN_PROGRESS, "go")
        assert len(ctx.history) == 1
        t = ctx.history[0]
        assert t.from_state is StageState.PLANNING
        assert t.to_state is StageState.IN_PROGRESS
        assert t.reason == "go"

    def test_multiple_transitions_accumulate(self) -> None:
        ctx = _ctx(
            StageState.PLANNING,
            task_ids=["T-001"],
            task_states={"T-001": "accepted"},
        )
        FSM.transition(ctx, StageState.IN_PROGRESS, "start")
        FSM.transition(ctx, StageState.INTEGRATING, "integrate")
        assert len(ctx.history) == 2

    def test_failed_transition_not_recorded(self) -> None:
        ctx = _ctx(StageState.PLANNING)
        with pytest.raises(InvalidStageTransitionError):
            FSM.transition(ctx, StageState.REVIEW)
        assert len(ctx.history) == 0

    def test_timestamp_auto_set(self) -> None:
        t = StageTransition(
            from_state=StageState.PLANNING,
            to_state=StageState.IN_PROGRESS,
            reason="test",
        )
        assert t.timestamp
        assert "T" in t.timestamp


# ── can_transition API ──


class TestCanTransition:
    def test_returns_ok(self) -> None:
        ctx = _ctx(StageState.PLANNING)
        ok, reason = FSM.can_transition(ctx, StageState.IN_PROGRESS)
        assert ok is True
        assert reason == "ok"

    def test_forbidden_returns_reason(self) -> None:
        ctx = _ctx(StageState.PLANNING)
        ok, reason = FSM.can_transition(ctx, StageState.REVIEW)
        assert ok is False
        assert "not allowed" in reason

    def test_allowed_transitions_set(self) -> None:
        ctx = _ctx(StageState.PLANNING)
        allowed = FSM.allowed_transitions(ctx)
        assert StageState.IN_PROGRESS in allowed
        assert StageState.FAILED in allowed
        assert StageState.REVIEW not in allowed
