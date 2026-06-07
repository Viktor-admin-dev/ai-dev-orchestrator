"""TaskRunner — orchestrates a full task cycle with I/O."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from orchestrator.cost import BudgetExceededError, CostEntry, CostTracker
from orchestrator.evidence import (
    EvidencePack,
    MutationResults,
    TestResults,
    parse_verdict,
)
from orchestrator.state_machine import TaskContext, TaskCycleFSM
from orchestrator.types import AuditVerdict, TaskState

if TYPE_CHECKING:
    from orchestrator.config import ProjectConfig
    from orchestrator.executor.base import ExecutionResult, ExecutorAdapter

logger = logging.getLogger(__name__)


@dataclass
class TaskRunner:
    """Orchestrates the full task lifecycle using the FSM.

    This is the only class with side-effects: it calls executors,
    runs tests, and records costs. The FSM enforces all invariants.
    """

    config: ProjectConfig
    developer: ExecutorAdapter
    auditor: ExecutorAdapter
    fsm: TaskCycleFSM = field(default_factory=TaskCycleFSM)
    _tasks: dict[str, TaskContext] = field(default_factory=dict, repr=False)

    @property
    def task_ids(self) -> set[str]:
        """Return the set of all task IDs."""
        return set(self._tasks)

    def create_task(
        self,
        task_id: str,
        *,
        budget_usd: float | None = None,
        max_attempts: int = 3,
        touches_critical: bool | None = None,
    ) -> TaskContext:
        """Create a new task in DRAFT state."""
        budget = budget_usd if budget_usd is not None else self.config.budgets.per_task_usd
        critical = touches_critical if touches_critical is not None else False
        ctx = TaskContext(
            task_id=task_id,
            cost=CostTracker(budget_usd=budget),
            max_attempts=max_attempts,
            touches_critical=critical,
        )
        self._tasks[task_id] = ctx
        return ctx

    def get_task(self, task_id: str) -> TaskContext:
        """Retrieve a task context by ID."""
        if task_id not in self._tasks:
            raise KeyError(f"Task {task_id} not found")
        return self._tasks[task_id]

    def submit_plan(self, task_id: str, plan: str, criteria: str) -> TaskContext:
        """Submit a plan for review: DRAFT → PLAN_REVIEW."""
        ctx = self.get_task(task_id)
        ctx.plan = plan
        ctx.criteria = criteria
        self.fsm.transition(ctx, TaskState.PLAN_REVIEW, "plan submitted")
        return ctx

    def approve_plan(self, task_id: str) -> TaskContext:
        """Approve a plan: PLAN_REVIEW → PLAN_APPROVED."""
        ctx = self.get_task(task_id)
        self.fsm.transition(ctx, TaskState.PLAN_APPROVED, "plan approved")
        return ctx

    def reject_plan(self, task_id: str) -> TaskContext:
        """Reject a plan: PLAN_REVIEW → DRAFT."""
        ctx = self.get_task(task_id)
        self.fsm.transition(ctx, TaskState.DRAFT, "plan rejected")
        return ctx

    async def run_developer(self, task_id: str) -> TaskContext:
        """Run developer executor: PLAN_APPROVED → IN_PROGRESS → TESTING.

        Transitions to IN_PROGRESS, executes developer, then to TESTING.
        On budget exceeded → FAILED.
        """
        ctx = self.get_task(task_id)
        self.fsm.transition(ctx, TaskState.IN_PROGRESS, "start coding")

        prompt = f"## Plan\n{ctx.plan}\n\n## Acceptance Criteria\n{ctx.criteria}"

        try:
            # Escalate to critical model when task touches critical modules
            if hasattr(self.developer, "set_critical"):
                self.developer.set_critical(ctx.touches_critical)
            result = await self.developer.execute(prompt, task_id)
            self._record_cost(ctx, result)

            ctx.evidence = EvidencePack(
                task_id=task_id,
                diff=result.output,
                criteria=ctx.criteria,
                plan=ctx.plan,
                developer_log=[result.output],
            )

            self.fsm.transition(ctx, TaskState.TESTING, "code written")
        except BudgetExceededError:
            self.fsm.transition(ctx, TaskState.FAILED, "budget exceeded")

        return ctx

    async def run_tests(self, task_id: str, test_results: TestResults) -> TaskContext:
        """Process test results: TESTING → AWAIT_AUDIT or REWORK.

        The caller runs tests externally and passes TestResults.
        """
        ctx = self.get_task(task_id)

        if ctx.evidence is not None:
            ep = ctx.evidence
            ctx.evidence = EvidencePack(
                task_id=ep.task_id,
                diff=ep.diff,
                criteria=ep.criteria,
                plan=ep.plan,
                test_results=test_results,
                developer_log=ep.developer_log,
            )

        if test_results.all_green:
            self.fsm.transition(ctx, TaskState.AWAIT_AUDIT, "tests green")
        else:
            self.fsm.transition(ctx, TaskState.REWORK, "tests red")

        return ctx

    async def run_audit(self, task_id: str) -> TaskContext:
        """Run auditor: AWAIT_AUDIT → PR_READY / MUTATION / REWORK / FAILED.

        Calls the auditor executor, parses the verdict, then transitions.
        """
        ctx = self.get_task(task_id)

        if ctx.evidence is None:
            raise RuntimeError(f"Task {task_id}: no evidence for audit")

        try:
            prompt = ctx.evidence.to_auditor_input()
            result = await self.auditor.execute(prompt, task_id)
            self._record_cost(ctx, result)

            verdict_data = parse_verdict(result.output)
            ep = ctx.evidence
            ctx.evidence = EvidencePack(
                task_id=ep.task_id,
                diff=ep.diff,
                criteria=ep.criteria,
                plan=ep.plan,
                test_results=ep.test_results,
                auditor_verdict=verdict_data,
                developer_log=ep.developer_log,
            )

            verdict = verdict_data.verdict

            if verdict is AuditVerdict.REJECT:
                self.fsm.transition(ctx, TaskState.FAILED, "auditor rejected")
            elif verdict is AuditVerdict.REQUEST_CHANGES:
                self.fsm.transition(ctx, TaskState.REWORK, "auditor: request changes")
            elif verdict is AuditVerdict.APPROVE:
                if ctx.touches_critical:
                    self.fsm.transition(ctx, TaskState.MUTATION, "approved, needs mutation")
                else:
                    self.fsm.transition(ctx, TaskState.PR_READY, "approved")
        except BudgetExceededError:
            self.fsm.transition(ctx, TaskState.FAILED, "budget exceeded during audit")

        return ctx

    async def run_mutation(self, task_id: str, mutation_results: MutationResults) -> TaskContext:
        """Process mutation results: MUTATION → PR_READY or REWORK.

        The caller runs mutation testing externally and passes MutationResults.
        """
        ctx = self.get_task(task_id)

        if ctx.evidence is not None:
            ep = ctx.evidence
            ctx.evidence = EvidencePack(
                task_id=ep.task_id,
                diff=ep.diff,
                criteria=ep.criteria,
                plan=ep.plan,
                test_results=ep.test_results,
                auditor_verdict=ep.auditor_verdict,
                mutation_results=mutation_results,
                developer_log=ep.developer_log,
            )

        if mutation_results.acceptable:
            self.fsm.transition(ctx, TaskState.PR_READY, "mutation ok")
        else:
            self.fsm.transition(ctx, TaskState.REWORK, "mutation score too low")

        return ctx

    def rework(self, task_id: str) -> TaskContext:
        """Re-enter development: REWORK → IN_PROGRESS."""
        ctx = self.get_task(task_id)
        self.fsm.transition(ctx, TaskState.IN_PROGRESS, "rework")
        return ctx

    def mark_merged(self, task_id: str) -> TaskContext:
        """Mark PR as merged: PR_READY → MERGED."""
        ctx = self.get_task(task_id)
        self.fsm.transition(ctx, TaskState.MERGED, "PR merged")
        return ctx

    def accept(self, task_id: str) -> TaskContext:
        """Architect accepts: MERGED → ACCEPTED (INV-4)."""
        ctx = self.get_task(task_id)
        ctx.architect_approved = True
        self.fsm.transition(ctx, TaskState.ACCEPTED, "architect accepted")
        return ctx

    def fail(self, task_id: str, reason: str) -> TaskContext:
        """Force task to FAILED state."""
        ctx = self.get_task(task_id)
        self.fsm.transition(ctx, TaskState.FAILED, reason)
        return ctx

    def _record_cost(self, ctx: TaskContext, result: ExecutionResult) -> None:
        """Record execution cost in the task's CostTracker."""
        if result.cost_usd > 0:
            ctx.cost.record(
                CostEntry(
                    task_id=ctx.task_id,
                    model=result.model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cost_usd=result.cost_usd,
                )
            )
