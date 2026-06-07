"""Observation panel — read-only data layer for project visualization.

Pure Python, zero dependency on Rich. Reads from ProjectOrchestrator
and produces frozen View dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.project import ProjectOrchestrator


# ── View dataclasses ──


@dataclass(frozen=True)
class TransitionView:
    """A single state transition in a task's history."""

    from_state: str
    to_state: str
    reason: str
    timestamp: str


@dataclass(frozen=True)
class TestResultsView:
    """Snapshot of test results."""

    passed: int
    failed: int
    errors: int
    coverage_pct: float
    all_green: bool


@dataclass(frozen=True)
class AuditorVerdictView:
    """Snapshot of auditor verdict."""

    verdict: str
    reasoning: str
    checklist: dict[str, bool]


@dataclass(frozen=True)
class MutationResultsView:
    """Snapshot of mutation testing results."""

    total_mutants: int
    killed: int
    survived: int
    score: float
    acceptable: bool


@dataclass(frozen=True)
class TaskStatusView:
    """Complete status snapshot of a single task."""

    task_id: str
    state: str
    attempt: int
    max_attempts: int
    touches_critical: bool
    architect_approved: bool
    plan: str
    criteria: str
    cost_total: float
    cost_budget: float
    cost_remaining: float
    cost_by_model: dict[str, float]
    test_results: TestResultsView | None
    auditor_verdict: AuditorVerdictView | None
    mutation_results: MutationResultsView | None
    history: tuple[TransitionView, ...]
    developer_log: tuple[str, ...]


@dataclass(frozen=True)
class StageTaskSummary:
    """Minimal task info inside a stage view."""

    task_id: str
    state: str


@dataclass(frozen=True)
class StageStatusView:
    """Complete status snapshot of a single stage."""

    stage_id: str
    name: str
    state: str
    architect_approved: bool
    tasks: tuple[StageTaskSummary, ...]
    e2e_results: TestResultsView | None
    cost_total: float
    cost_budget: float
    history: tuple[TransitionView, ...]


@dataclass(frozen=True)
class StageSummary:
    """Lightweight stage info for project overview."""

    stage_id: str
    name: str
    state: str
    task_count: int
    tasks_done: int
    architect_approved: bool


@dataclass(frozen=True)
class ProjectStatusView:
    """Top-level project status snapshot."""

    stages: tuple[StageSummary, ...]
    total_tasks: int
    tasks_ready: int
    tasks_blocked: int
    tasks_completed: int
    tasks_failed: int
    tasks_in_progress: int
    cost_total: float
    cost_budget: float


@dataclass(frozen=True)
class ModelCostRow:
    """Cost breakdown for a single model."""

    model: str
    cost_usd: float
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class TaskCostRow:
    """Cost breakdown for a single task."""

    task_id: str
    cost_usd: float
    budget_usd: float


@dataclass(frozen=True)
class StageCostRow:
    """Cost breakdown for a single stage."""

    stage_id: str
    cost_usd: float
    budget_usd: float


@dataclass(frozen=True)
class CostReportView:
    """Aggregated cost report across the project."""

    total_usd: float
    budget_usd: float
    by_model: tuple[ModelCostRow, ...]
    by_task: tuple[TaskCostRow, ...]
    by_stage: tuple[StageCostRow, ...]


@dataclass(frozen=True)
class PendingAction:
    """An action awaiting human input."""

    action_type: str  # "review_plan" | "accept_task" | "accept_stage"
    target_id: str
    description: str


@dataclass(frozen=True)
class TaskLogView:
    """Timeline of a task's state transitions + developer log."""

    task_id: str
    state: str
    history: tuple[TransitionView, ...]
    developer_log: tuple[str, ...]


# ── Helper converters ──


def _to_transition_view(t: object) -> TransitionView:
    """Convert a Transition to TransitionView."""
    from orchestrator.state_machine import Transition

    assert isinstance(t, Transition)
    return TransitionView(
        from_state=t.from_state.value,
        to_state=t.to_state.value,
        reason=t.reason,
        timestamp=t.timestamp,
    )


def _to_stage_transition_view(t: object) -> TransitionView:
    """Convert a StageTransition to TransitionView."""
    from orchestrator.stage import StageTransition

    assert isinstance(t, StageTransition)
    return TransitionView(
        from_state=t.from_state.value,
        to_state=t.to_state.value,
        reason=t.reason,
        timestamp=t.timestamp,
    )


def _to_test_results_view(tr: object) -> TestResultsView:
    """Convert TestResults to TestResultsView."""
    from orchestrator.evidence import TestResults

    assert isinstance(tr, TestResults)
    return TestResultsView(
        passed=tr.passed,
        failed=tr.failed,
        errors=tr.errors,
        coverage_pct=tr.coverage_pct,
        all_green=tr.all_green,
    )


def _to_auditor_verdict_view(av: object) -> AuditorVerdictView:
    """Convert AuditorVerdict to AuditorVerdictView."""
    from orchestrator.evidence import AuditorVerdict

    assert isinstance(av, AuditorVerdict)
    return AuditorVerdictView(
        verdict=av.verdict.value,
        reasoning=av.reasoning,
        checklist=dict(av.checklist),
    )


def _to_mutation_results_view(mr: object) -> MutationResultsView:
    """Convert MutationResults to MutationResultsView."""
    from orchestrator.evidence import MutationResults

    assert isinstance(mr, MutationResults)
    return MutationResultsView(
        total_mutants=mr.total_mutants,
        killed=mr.killed,
        survived=mr.survived,
        score=mr.score,
        acceptable=mr.acceptable,
    )


# ── PanelData ──


_COMPLETED_STATES = frozenset({"accepted", "pr_ready", "merged"})
_FAILED_STATES = frozenset({"failed"})
_IN_PROGRESS_STATES = frozenset(
    {
        "in_progress",
        "testing",
        "await_audit",
        "mutation",
        "rework",
    }
)


class PanelData:
    """Read-only data extractor for ProjectOrchestrator.

    All methods return frozen View dataclasses — safe for rendering.
    """

    def __init__(self, orchestrator: ProjectOrchestrator) -> None:
        self._orch = orchestrator

    def task_status(self, task_id: str) -> TaskStatusView:
        """Build a complete status view for a task."""
        ctx = self._orch.runner.get_task(task_id)

        cost_by_model = ctx.cost.summary()

        test_results: TestResultsView | None = None
        auditor_verdict: AuditorVerdictView | None = None
        mutation_results: MutationResultsView | None = None
        developer_log: tuple[str, ...] = ()

        if ctx.evidence is not None:
            if ctx.evidence.test_results is not None:
                test_results = _to_test_results_view(ctx.evidence.test_results)
            if ctx.evidence.auditor_verdict is not None:
                auditor_verdict = _to_auditor_verdict_view(ctx.evidence.auditor_verdict)
            if ctx.evidence.mutation_results is not None:
                mutation_results = _to_mutation_results_view(ctx.evidence.mutation_results)
            developer_log = tuple(ctx.evidence.developer_log)

        history = tuple(_to_transition_view(t) for t in ctx.history)

        return TaskStatusView(
            task_id=ctx.task_id,
            state=ctx.state.value,
            attempt=ctx.attempt,
            max_attempts=ctx.max_attempts,
            touches_critical=ctx.touches_critical,
            architect_approved=ctx.architect_approved,
            plan=ctx.plan,
            criteria=ctx.criteria,
            cost_total=ctx.cost.total_usd,
            cost_budget=ctx.cost.budget_usd,
            cost_remaining=ctx.cost.remaining_usd,
            cost_by_model=cost_by_model,
            test_results=test_results,
            auditor_verdict=auditor_verdict,
            mutation_results=mutation_results,
            history=history,
            developer_log=developer_log,
        )

    def stage_status(self, stage_id: str) -> StageStatusView:
        """Build a complete status view for a stage."""
        ctx = self._orch.get_stage(stage_id)

        # Sync task states from runner
        tasks: list[StageTaskSummary] = []
        for tid in ctx.task_ids:
            try:
                task_ctx = self._orch.runner.get_task(tid)
                state = task_ctx.state.value
            except KeyError:
                state = ""
            tasks.append(StageTaskSummary(task_id=tid, state=state))

        e2e: TestResultsView | None = None
        if ctx.e2e_results is not None:
            e2e = _to_test_results_view(ctx.e2e_results)

        history = tuple(_to_stage_transition_view(t) for t in ctx.history)

        return StageStatusView(
            stage_id=ctx.stage_id,
            name=ctx.name,
            state=ctx.state.value,
            architect_approved=ctx.architect_approved,
            tasks=tuple(tasks),
            e2e_results=e2e,
            cost_total=ctx.cost.total_usd,
            cost_budget=ctx.cost.budget_usd,
            history=history,
        )

    def project_status(self) -> ProjectStatusView:
        """Build a top-level project status view."""
        stages: list[StageSummary] = []
        total_cost = 0.0
        total_budget = 0.0

        all_task_ids: set[str] = set()

        for stage_id in self._orch.stage_ids:
            ctx = self._orch.get_stage(stage_id)
            done_count = 0
            for tid in ctx.task_ids:
                all_task_ids.add(tid)
                try:
                    task_ctx = self._orch.runner.get_task(tid)
                    if task_ctx.state.value in _COMPLETED_STATES:
                        done_count += 1
                except KeyError:
                    pass

            stages.append(
                StageSummary(
                    stage_id=ctx.stage_id,
                    name=ctx.name,
                    state=ctx.state.value,
                    task_count=len(ctx.task_ids),
                    tasks_done=done_count,
                    architect_approved=ctx.architect_approved,
                )
            )
            total_cost += ctx.cost.total_usd
            total_budget += ctx.cost.budget_usd

        # Count task states across all runner tasks
        tasks_ready = 0
        tasks_blocked = 0
        tasks_completed = 0
        tasks_failed = 0
        tasks_in_progress = 0

        completed_ids = set[str]()
        for tid in self._orch.runner.task_ids:
            task_ctx = self._orch.runner.get_task(tid)
            sv = task_ctx.state.value
            if sv in _COMPLETED_STATES:
                tasks_completed += 1
                completed_ids.add(tid)
            elif sv in _FAILED_STATES:
                tasks_failed += 1
            elif sv in _IN_PROGRESS_STATES:
                tasks_in_progress += 1

        # Ready = in graph, not completed, all deps satisfied
        try:
            ready = self._orch.graph.ready_tasks(completed_ids)
            # Only count tasks that are not already in progress
            for tid in ready:
                try:
                    task_ctx = self._orch.runner.get_task(tid)
                    sv = task_ctx.state.value
                    if sv not in _COMPLETED_STATES and sv not in _IN_PROGRESS_STATES:
                        tasks_ready += 1
                except KeyError:
                    tasks_ready += 1
        except Exception:  # noqa: BLE001
            pass

        # Blocked = in graph, not completed, not ready, not failed, not in progress
        for tid in self._orch.graph.task_ids:
            if tid in completed_ids or tid in ready:
                continue
            try:
                task_ctx = self._orch.runner.get_task(tid)
                sv = task_ctx.state.value
                if sv not in _FAILED_STATES and sv not in _IN_PROGRESS_STATES:
                    tasks_blocked += 1
            except KeyError:
                tasks_blocked += 1

        return ProjectStatusView(
            stages=tuple(stages),
            total_tasks=len(self._orch.runner.task_ids),
            tasks_ready=tasks_ready,
            tasks_blocked=tasks_blocked,
            tasks_completed=tasks_completed,
            tasks_failed=tasks_failed,
            tasks_in_progress=tasks_in_progress,
            cost_total=total_cost,
            cost_budget=total_budget,
        )

    def cost_report(self) -> CostReportView:
        """Build an aggregated cost report."""
        # Aggregate by model across all tasks
        model_costs: dict[str, float] = {}
        model_input: dict[str, int] = {}
        model_output: dict[str, int] = {}
        task_rows: list[TaskCostRow] = []
        total_usd = 0.0
        total_budget = 0.0

        for tid in self._orch.runner.task_ids:
            task_ctx = self._orch.runner.get_task(tid)
            task_rows.append(
                TaskCostRow(
                    task_id=tid,
                    cost_usd=task_ctx.cost.total_usd,
                    budget_usd=task_ctx.cost.budget_usd,
                )
            )
            total_usd += task_ctx.cost.total_usd
            total_budget += task_ctx.cost.budget_usd

            for entry in task_ctx.cost.entries:
                model_costs[entry.model] = model_costs.get(entry.model, 0.0) + entry.cost_usd
                model_input[entry.model] = model_input.get(entry.model, 0) + entry.input_tokens
                model_output[entry.model] = model_output.get(entry.model, 0) + entry.output_tokens

        by_model = tuple(
            ModelCostRow(
                model=m,
                cost_usd=model_costs[m],
                input_tokens=model_input[m],
                output_tokens=model_output[m],
            )
            for m in sorted(model_costs)
        )

        # By stage
        stage_rows: list[StageCostRow] = []
        for stage_id in self._orch.stage_ids:
            ctx = self._orch.get_stage(stage_id)
            stage_cost = 0.0
            for tid in ctx.task_ids:
                try:
                    task_ctx = self._orch.runner.get_task(tid)
                    stage_cost += task_ctx.cost.total_usd
                except KeyError:
                    pass
            stage_rows.append(
                StageCostRow(
                    stage_id=stage_id,
                    cost_usd=stage_cost,
                    budget_usd=ctx.cost.budget_usd,
                )
            )

        task_rows.sort(key=lambda r: r.task_id)

        return CostReportView(
            total_usd=total_usd,
            budget_usd=total_budget,
            by_model=by_model,
            by_task=tuple(task_rows),
            by_stage=tuple(stage_rows),
        )

    def pending_actions(self) -> list[PendingAction]:
        """Find tasks/stages awaiting human input."""
        actions: list[PendingAction] = []

        for tid in self._orch.runner.task_ids:
            task_ctx = self._orch.runner.get_task(tid)

            if task_ctx.state.value == "plan_review":
                actions.append(
                    PendingAction(
                        action_type="review_plan",
                        target_id=tid,
                        description=f"Task {tid} has a plan awaiting review",
                    )
                )

            if task_ctx.state.value == "merged" and not task_ctx.architect_approved:
                actions.append(
                    PendingAction(
                        action_type="accept_task",
                        target_id=tid,
                        description=f"Task {tid} is merged, awaiting architect acceptance",
                    )
                )

        for stage_id in self._orch.stage_ids:
            ctx = self._orch.get_stage(stage_id)
            if ctx.state.value == "review" and not ctx.architect_approved:
                actions.append(
                    PendingAction(
                        action_type="accept_stage",
                        target_id=stage_id,
                        description=f"Stage {stage_id} is in review, awaiting architect acceptance",
                    )
                )

        return actions

    def task_log(self, task_id: str) -> TaskLogView:
        """Build a timeline view for a task."""
        ctx = self._orch.runner.get_task(task_id)

        history = tuple(_to_transition_view(t) for t in ctx.history)
        developer_log: tuple[str, ...] = ()
        if ctx.evidence is not None:
            developer_log = tuple(ctx.evidence.developer_log)

        return TaskLogView(
            task_id=ctx.task_id,
            state=ctx.state.value,
            history=history,
            developer_log=developer_log,
        )
