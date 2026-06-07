"""Tests for Rich CLI rendering layer."""

from __future__ import annotations

from io import StringIO

import pytest

rich = pytest.importorskip("rich")

from rich.console import Console  # noqa: E402

from orchestrator.panel import (  # noqa: E402
    AuditorVerdictView,
    CostReportView,
    ModelCostRow,
    MutationResultsView,
    PendingAction,
    ProjectStatusView,
    StageCostRow,
    StageStatusView,
    StageSummary,
    StageTaskSummary,
    TaskCostRow,
    TaskLogView,
    TaskStatusView,
    TestResultsView,
    TransitionView,
)
from orchestrator.render import (  # noqa: E402
    render_actions,
    render_cost,
    render_log,
    render_project,
    render_stage,
    render_task,
)


def _console() -> tuple[Console, StringIO]:
    buf = StringIO()
    c = Console(file=buf, force_terminal=True, width=120)
    return c, buf


def _minimal_task() -> TaskStatusView:
    return TaskStatusView(
        task_id="T-001",
        state="draft",
        attempt=0,
        max_attempts=3,
        touches_critical=False,
        architect_approved=False,
        plan="",
        criteria="",
        cost_total=0.0,
        cost_budget=5.0,
        cost_remaining=5.0,
        cost_by_model={},
        test_results=None,
        auditor_verdict=None,
        mutation_results=None,
        history=(),
        developer_log=(),
    )


def _task_with_evidence() -> TaskStatusView:
    return TaskStatusView(
        task_id="T-002",
        state="pr_ready",
        attempt=1,
        max_attempts=3,
        touches_critical=True,
        architect_approved=False,
        plan="Plan A",
        criteria="Criteria A",
        cost_total=1.50,
        cost_budget=5.0,
        cost_remaining=3.50,
        cost_by_model={"claude-sonnet-4-5": 1.0, "claude-opus-4-6": 0.5},
        test_results=TestResultsView(
            passed=10,
            failed=0,
            errors=0,
            coverage_pct=90.0,
            all_green=True,
        ),
        auditor_verdict=AuditorVerdictView(
            verdict="approve",
            reasoning="LGTM",
            checklist={"style": True},
        ),
        mutation_results=MutationResultsView(
            total_mutants=10,
            killed=9,
            survived=1,
            score=0.9,
            acceptable=True,
        ),
        history=(
            TransitionView(
                from_state="draft",
                to_state="plan_review",
                reason="plan submitted",
                timestamp="2026-01-01T00:00:00",
            ),
        ),
        developer_log=("wrote code",),
    )


# ── TestRenderProject ──


class TestRenderProject:
    def test_renders_non_empty(self) -> None:
        c, buf = _console()
        view = ProjectStatusView(
            stages=(
                StageSummary(
                    stage_id="S1",
                    name="Auth",
                    state="in_progress",
                    task_count=3,
                    tasks_done=1,
                    architect_approved=False,
                ),
                StageSummary(
                    stage_id="S2",
                    name="API",
                    state="planning",
                    task_count=2,
                    tasks_done=0,
                    architect_approved=False,
                ),
            ),
            total_tasks=5,
            tasks_ready=2,
            tasks_blocked=1,
            tasks_completed=1,
            tasks_failed=0,
            tasks_in_progress=1,
            cost_total=2.50,
            cost_budget=100.0,
        )
        render_project(view, c)
        output = buf.getvalue()
        assert "Auth" in output
        assert "API" in output
        assert "S1" in output

    def test_empty_project_no_crash(self) -> None:
        c, buf = _console()
        view = ProjectStatusView(
            stages=(),
            total_tasks=0,
            tasks_ready=0,
            tasks_blocked=0,
            tasks_completed=0,
            tasks_failed=0,
            tasks_in_progress=0,
            cost_total=0.0,
            cost_budget=0.0,
        )
        render_project(view, c)
        output = buf.getvalue()
        assert "Project Overview" in output


# ── TestRenderTask ──


class TestRenderTask:
    def test_renders_task_card(self) -> None:
        c, buf = _console()
        render_task(_minimal_task(), c)
        output = buf.getvalue()
        assert "T-001" in output
        assert "draft" in output

    def test_renders_with_evidence(self) -> None:
        c, buf = _console()
        render_task(_task_with_evidence(), c)
        output = buf.getvalue()
        assert "T-002" in output
        assert "approve" in output
        assert "10 passed" in output
        assert "9/10 killed" in output


# ── TestRenderStage ──


class TestRenderStage:
    def test_renders_stage_panel(self) -> None:
        c, buf = _console()
        view = StageStatusView(
            stage_id="S1",
            name="Auth",
            state="in_progress",
            architect_approved=False,
            tasks=(
                StageTaskSummary(task_id="T-001", state="testing"),
                StageTaskSummary(task_id="T-002", state="draft"),
            ),
            e2e_results=None,
            cost_total=1.0,
            cost_budget=50.0,
            history=(),
        )
        render_stage(view, c)
        output = buf.getvalue()
        assert "Auth" in output
        assert "in_progress" in output


# ── TestRenderCost ──


class TestRenderCost:
    def test_renders_cost_tables(self) -> None:
        c, buf = _console()
        view = CostReportView(
            total_usd=2.50,
            budget_usd=100.0,
            by_model=(
                ModelCostRow(
                    model="claude-sonnet-4-5",
                    cost_usd=1.50,
                    input_tokens=1000,
                    output_tokens=500,
                ),
                ModelCostRow(
                    model="claude-opus-4-6",
                    cost_usd=1.00,
                    input_tokens=200,
                    output_tokens=100,
                ),
            ),
            by_task=(TaskCostRow(task_id="T-001", cost_usd=2.50, budget_usd=5.0),),
            by_stage=(StageCostRow(stage_id="S1", cost_usd=2.50, budget_usd=50.0),),
        )
        render_cost(view, c)
        output = buf.getvalue()
        assert "claude-sonnet-4-5" in output
        assert "claude-opus-4-6" in output


# ── TestRenderActions ──


class TestRenderActions:
    def test_renders_pending_actions(self) -> None:
        c, buf = _console()
        actions = [
            PendingAction(
                action_type="review_plan",
                target_id="T-001",
                description="Plan awaiting review",
            ),
            PendingAction(
                action_type="accept_stage",
                target_id="S1",
                description="Stage awaiting acceptance",
            ),
        ]
        render_actions(actions, c)
        output = buf.getvalue()
        assert "review_plan" in output
        assert "T-001" in output

    def test_renders_no_pending_actions(self) -> None:
        c, buf = _console()
        render_actions([], c)
        output = buf.getvalue()
        assert "No pending actions" in output


# ── TestRenderLog ──


class TestRenderLog:
    def test_renders_transition_timeline(self) -> None:
        c, buf = _console()
        view = TaskLogView(
            task_id="T-001",
            state="plan_approved",
            history=(
                TransitionView(
                    from_state="draft",
                    to_state="plan_review",
                    reason="plan submitted",
                    timestamp="2026-01-01T00:00:00",
                ),
                TransitionView(
                    from_state="plan_review",
                    to_state="plan_approved",
                    reason="plan approved",
                    timestamp="2026-01-01T00:01:00",
                ),
            ),
            developer_log=(),
        )
        render_log(view, c)
        output = buf.getvalue()
        assert "T-001" in output
        assert "plan submitted" in output
        assert "plan approved" in output
