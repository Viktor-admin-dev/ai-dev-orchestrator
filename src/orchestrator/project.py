"""ProjectOrchestrator — coordinates stages, task graph, and task runner."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from orchestrator.cost import CostTracker
from orchestrator.graph import TaskGraph
from orchestrator.stage import StageContext, StageFSM
from orchestrator.types import StageState

if TYPE_CHECKING:
    from orchestrator.evidence import TestResults
    from orchestrator.runner import TaskRunner

logger = logging.getLogger(__name__)


@dataclass
class ProjectOrchestrator:
    """Coordinates TaskRunner + TaskGraph + StageFSM.

    Project-level orchestrator that manages stages of work,
    each containing tasks organized in a dependency graph.
    """

    runner: TaskRunner
    graph: TaskGraph = field(default_factory=TaskGraph)
    stage_fsm: StageFSM = field(default_factory=StageFSM)
    _stages: dict[str, StageContext] = field(default_factory=dict, repr=False)

    def create_stage(
        self,
        stage_id: str,
        name: str,
        task_ids: list[str],
        *,
        budget_usd: float = 100.0,
    ) -> StageContext:
        """Create a new stage in PLANNING state."""
        if stage_id in self._stages:
            raise ValueError(f"Stage {stage_id} already exists")
        ctx = StageContext(
            stage_id=stage_id,
            name=name,
            task_ids=list(task_ids),
            cost=CostTracker(budget_usd=budget_usd),
        )
        self._stages[stage_id] = ctx
        return ctx

    def get_stage(self, stage_id: str) -> StageContext:
        """Retrieve a stage context by ID."""
        if stage_id not in self._stages:
            raise KeyError(f"Stage {stage_id} not found")
        return self._stages[stage_id]

    def start_stage(self, stage_id: str) -> StageContext:
        """Start a stage: PLANNING → IN_PROGRESS."""
        ctx = self.get_stage(stage_id)
        self.stage_fsm.transition(ctx, StageState.IN_PROGRESS, "stage started")
        return ctx

    def next_tasks(self, stage_id: str | None = None) -> set[str]:
        """Return ready tasks from the graph, optionally filtered by stage."""
        # Collect completed task IDs from the runner
        completed = self._completed_task_ids()

        if stage_id is not None:
            stage_task_ids = self.graph.stage_tasks(stage_id)
            # Only return ready tasks that belong to this stage
            all_ready = self.graph.ready_tasks(completed)
            return all_ready & stage_task_ids
        return self.graph.ready_tasks(completed)

    def check_stage_progress(self, stage_id: str) -> bool:
        """Check if all tasks in the stage are done.

        Syncs task states and returns True if all done.
        """
        ctx = self.get_stage(stage_id)
        self._sync_task_states(ctx)
        return self.graph.is_stage_complete(stage_id, self._completed_task_ids())

    def begin_integration(self, stage_id: str) -> StageContext:
        """Transition to INTEGRATING: IN_PROGRESS → INTEGRATING.

        Syncs task_states first, then validates via guard.
        """
        ctx = self.get_stage(stage_id)
        self._sync_task_states(ctx)
        self.stage_fsm.transition(ctx, StageState.INTEGRATING, "all tasks done")
        return ctx

    def submit_e2e(self, stage_id: str, e2e_results: TestResults) -> StageContext:
        """Submit E2E results: INTEGRATING → REVIEW (green) or IN_PROGRESS (red)."""
        ctx = self.get_stage(stage_id)
        ctx.e2e_results = e2e_results

        if e2e_results.all_green:
            self.stage_fsm.transition(ctx, StageState.REVIEW, "E2E green")
        else:
            self.stage_fsm.transition(ctx, StageState.IN_PROGRESS, "E2E red, rework needed")

        return ctx

    def accept_stage(self, stage_id: str) -> StageContext:
        """Architect accepts stage: REVIEW → ACCEPTED (INV-4)."""
        ctx = self.get_stage(stage_id)
        ctx.architect_approved = True
        self.stage_fsm.transition(ctx, StageState.ACCEPTED, "architect accepted")
        return ctx

    def reject_stage(self, stage_id: str) -> StageContext:
        """Architect rejects stage: REVIEW → IN_PROGRESS."""
        ctx = self.get_stage(stage_id)
        self.stage_fsm.transition(ctx, StageState.IN_PROGRESS, "architect rejected")
        return ctx

    def fail_stage(self, stage_id: str, reason: str) -> StageContext:
        """Force stage to FAILED state."""
        ctx = self.get_stage(stage_id)
        self.stage_fsm.transition(ctx, StageState.FAILED, reason)
        return ctx

    def project_summary(self) -> dict[str, object]:
        """Return a summary of all stages and their states."""
        stages: list[dict[str, object]] = []
        for ctx in self._stages.values():
            stages.append(
                {
                    "stage_id": ctx.stage_id,
                    "name": ctx.name,
                    "state": ctx.state.value,
                    "task_count": len(ctx.task_ids),
                    "architect_approved": ctx.architect_approved,
                }
            )
        return {
            "total_stages": len(self._stages),
            "stages": stages,
        }

    def _sync_task_states(self, ctx: StageContext) -> None:
        """Read task states from TaskRunner and write them as strings to ctx.task_states."""
        for tid in ctx.task_ids:
            try:
                task_ctx = self.runner.get_task(tid)
                ctx.task_states[tid] = task_ctx.state.value
            except KeyError:
                ctx.task_states[tid] = ""

    def _completed_task_ids(self) -> set[str]:
        """Collect task IDs that are in a done state from the runner."""
        done: set[str] = set()
        for tid in self.graph.task_ids:
            try:
                task_ctx = self.runner.get_task(tid)
                if task_ctx.state.value in {"accepted", "pr_ready", "merged"}:
                    done.add(tid)
            except KeyError:
                pass
        return done
