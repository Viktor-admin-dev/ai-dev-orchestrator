"""Stage FSM — project-level state machine for stage lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from orchestrator.cost import CostTracker
from orchestrator.types import StageState

if TYPE_CHECKING:
    from orchestrator.evidence import TestResults


class InvalidStageTransitionError(RuntimeError):
    """Raised when a stage transition violates the FSM rules."""

    def __init__(self, current: StageState, target: StageState, reason: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"{current.value} → {target.value}: {reason}")


@dataclass(frozen=True)
class StageTransition:
    """Audit-log entry for a stage transition."""

    from_state: StageState
    to_state: StageState
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class StageContext:
    """Mutable container for a stage's lifecycle state."""

    stage_id: str
    name: str
    state: StageState = StageState.PLANNING
    task_ids: list[str] = field(default_factory=list)
    task_states: dict[str, str] = field(default_factory=dict)
    e2e_results: TestResults | None = None
    architect_approved: bool = False
    history: list[StageTransition] = field(default_factory=list)
    cost: CostTracker = field(default_factory=lambda: CostTracker(budget_usd=100.0))


# ── Allowed transitions table ──

_STAGE_TRANSITIONS: dict[StageState, set[StageState]] = {
    StageState.PLANNING: {StageState.IN_PROGRESS, StageState.FAILED},
    StageState.IN_PROGRESS: {StageState.INTEGRATING, StageState.FAILED},
    StageState.INTEGRATING: {StageState.REVIEW, StageState.IN_PROGRESS, StageState.FAILED},
    StageState.REVIEW: {StageState.ACCEPTED, StageState.IN_PROGRESS, StageState.FAILED},
    StageState.ACCEPTED: set(),
    StageState.FAILED: set(),
}

_TERMINAL_STAGE_STATES = frozenset({StageState.ACCEPTED, StageState.FAILED})

_DONE_TASK_STATES = frozenset({"accepted", "pr_ready", "merged"})


class StageFSM:
    """Pure state machine logic for the stage lifecycle.

    No I/O — only validates and performs transitions on StageContext.
    """

    def allowed_transitions(self, ctx: StageContext) -> set[StageState]:
        """Return the set of states reachable from the current state."""
        return set(_STAGE_TRANSITIONS.get(ctx.state, set()))

    def can_transition(self, ctx: StageContext, target: StageState) -> tuple[bool, str]:
        """Check whether a transition is allowed, returning (ok, reason)."""
        allowed = _STAGE_TRANSITIONS.get(ctx.state, set())

        # * → FAILED is always allowed (except from terminal states)
        if target is StageState.FAILED and ctx.state not in _TERMINAL_STAGE_STATES:
            return True, "failure always allowed"

        if target not in allowed:
            return False, f"transition {ctx.state.value} → {target.value} not allowed"

        # Guard: IN_PROGRESS → INTEGRATING
        # All task_states must be done + task_ids must not be empty
        if ctx.state is StageState.IN_PROGRESS and target is StageState.INTEGRATING:
            if not ctx.task_ids:
                return False, "no tasks in stage"
            for tid in ctx.task_ids:
                ts = ctx.task_states.get(tid, "")
                if ts not in _DONE_TASK_STATES:
                    return False, f"task {tid} not done (state: {ts})"

        # Guard: INTEGRATING → REVIEW requires green E2E
        if ctx.state is StageState.INTEGRATING and target is StageState.REVIEW:
            if ctx.e2e_results is None:
                return False, "no E2E results"
            if not ctx.e2e_results.all_green:
                return False, "E2E tests not green"

        # Guard: REVIEW → ACCEPTED requires architect approval (INV-4 stage level)
        if (
            ctx.state is StageState.REVIEW
            and target is StageState.ACCEPTED
            and not ctx.architect_approved
        ):
            return False, "INV-4: architect approval required"

        return True, "ok"

    def transition(self, ctx: StageContext, target: StageState, reason: str = "") -> StageContext:
        """Execute a state transition, mutating ctx in-place and returning it.

        Raises InvalidStageTransitionError if the transition is not allowed.
        """
        ok, msg = self.can_transition(ctx, target)
        if not ok:
            raise InvalidStageTransitionError(ctx.state, target, msg)

        entry = StageTransition(
            from_state=ctx.state,
            to_state=target,
            reason=reason or msg,
        )
        ctx.history.append(entry)
        ctx.state = target
        return ctx
