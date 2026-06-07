"""Task cycle state machine — pure logic, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from orchestrator.cost import CostTracker
from orchestrator.types import AuditVerdict, TaskState

if TYPE_CHECKING:
    from orchestrator.evidence import EvidencePack


class InvalidTransitionError(RuntimeError):
    """Raised when a state transition violates the FSM rules."""

    def __init__(self, current: TaskState, target: TaskState, reason: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"{current.value} → {target.value}: {reason}")


@dataclass(frozen=True)
class Transition:
    """Audit-log entry for a state transition."""

    from_state: TaskState
    to_state: TaskState
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class TaskContext:
    """Mutable container for a task's lifecycle state."""

    task_id: str
    state: TaskState = TaskState.DRAFT
    plan: str = ""
    criteria: str = ""
    evidence: EvidencePack | None = None
    cost: CostTracker = field(default_factory=lambda: CostTracker(budget_usd=5.0))
    history: list[Transition] = field(default_factory=list)
    attempt: int = 0
    max_attempts: int = 3
    touches_critical: bool = False
    architect_approved: bool = False


# ── Allowed transitions table ──

_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.DRAFT: {TaskState.PLAN_REVIEW, TaskState.FAILED},
    TaskState.PLAN_REVIEW: {TaskState.PLAN_APPROVED, TaskState.DRAFT, TaskState.FAILED},
    TaskState.PLAN_APPROVED: {TaskState.IN_PROGRESS, TaskState.FAILED},
    TaskState.IN_PROGRESS: {TaskState.TESTING, TaskState.FAILED},
    TaskState.TESTING: {TaskState.AWAIT_AUDIT, TaskState.REWORK, TaskState.FAILED},
    TaskState.AWAIT_AUDIT: {
        TaskState.MUTATION,
        TaskState.PR_READY,
        TaskState.REWORK,
        TaskState.FAILED,
    },
    TaskState.MUTATION: {TaskState.PR_READY, TaskState.REWORK, TaskState.FAILED},
    TaskState.REWORK: {TaskState.IN_PROGRESS, TaskState.FAILED},
    TaskState.PR_READY: {TaskState.MERGED, TaskState.FAILED},
    TaskState.MERGED: {TaskState.ACCEPTED, TaskState.FAILED},
    TaskState.ACCEPTED: set(),
    TaskState.FAILED: set(),
}


class TaskCycleFSM:
    """Pure state machine logic for the task lifecycle.

    No I/O — only validates and performs transitions on TaskContext.
    """

    def allowed_transitions(self, ctx: TaskContext) -> set[TaskState]:
        """Return the set of states reachable from the current state."""
        return set(_TRANSITIONS.get(ctx.state, set()))

    def can_transition(self, ctx: TaskContext, target: TaskState) -> tuple[bool, str]:
        """Check whether a transition is allowed, returning (ok, reason)."""
        allowed = _TRANSITIONS.get(ctx.state, set())

        # * → FAILED is always allowed (except from terminal states)
        if target is TaskState.FAILED and ctx.state not in (
            TaskState.ACCEPTED,
            TaskState.FAILED,
        ):
            return True, "failure always allowed"

        if target not in allowed:
            return False, f"transition {ctx.state.value} → {target.value} not allowed"

        # Guard: TESTING → AWAIT_AUDIT requires green tests
        if ctx.state is TaskState.TESTING and target is TaskState.AWAIT_AUDIT:
            if ctx.evidence is None or ctx.evidence.test_results is None:
                return False, "no test results"
            if not ctx.evidence.test_results.all_green:
                return False, "tests not green"

        # Guard: AWAIT_AUDIT → PR_READY requires INV-1 (green tests + approve)
        if ctx.state is TaskState.AWAIT_AUDIT and target is TaskState.PR_READY:
            if ctx.evidence is None or not ctx.evidence.is_ready_for_pr():
                return False, "INV-1: evidence not ready for PR"
            if ctx.touches_critical:
                return False, "critical modules require mutation testing"

        # Guard: AWAIT_AUDIT → MUTATION requires approve + touches_critical
        if ctx.state is TaskState.AWAIT_AUDIT and target is TaskState.MUTATION:
            if not ctx.touches_critical:
                return False, "mutation only for critical modules"
            if ctx.evidence is None or ctx.evidence.auditor_verdict is None:
                return False, "no auditor verdict"
            if ctx.evidence.auditor_verdict.verdict is not AuditVerdict.APPROVE:
                return False, "auditor did not approve"

        # Guard: MUTATION → PR_READY requires acceptable mutation results
        if ctx.state is TaskState.MUTATION and target is TaskState.PR_READY:
            if ctx.evidence is None or ctx.evidence.mutation_results is None:
                return False, "no mutation results"
            if not ctx.evidence.mutation_results.acceptable:
                return False, "mutation score below threshold"

        # Guard: REWORK → IN_PROGRESS checks max_attempts
        if (
            ctx.state is TaskState.REWORK
            and target is TaskState.IN_PROGRESS
            and ctx.attempt >= ctx.max_attempts
        ):
            return False, f"max attempts ({ctx.max_attempts}) reached"

        # Guard: MERGED → ACCEPTED requires architect approval (INV-4)
        if (
            ctx.state is TaskState.MERGED
            and target is TaskState.ACCEPTED
            and not ctx.architect_approved
        ):
            return False, "INV-4: architect approval required"

        return True, "ok"

    def transition(self, ctx: TaskContext, target: TaskState, reason: str = "") -> TaskContext:
        """Execute a state transition, mutating ctx in-place and returning it.

        Raises InvalidTransitionError if the transition is not allowed.
        """
        ok, msg = self.can_transition(ctx, target)
        if not ok:
            raise InvalidTransitionError(ctx.state, target, msg)

        entry = Transition(
            from_state=ctx.state,
            to_state=target,
            reason=reason or msg,
        )
        ctx.history.append(entry)

        # Increment attempt counter on rework → in_progress
        if ctx.state is TaskState.REWORK and target is TaskState.IN_PROGRESS:
            ctx.attempt += 1

        ctx.state = target
        return ctx
