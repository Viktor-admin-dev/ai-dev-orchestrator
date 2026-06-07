"""Rich CLI rendering for observation panel views.

Depends on ``rich>=13.0``. All functions accept View dataclasses
from ``panel.py`` and an optional Console for output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

if TYPE_CHECKING:
    from orchestrator.panel import (
        CostReportView,
        PendingAction,
        ProjectStatusView,
        StageStatusView,
        TaskLogView,
        TaskStatusView,
    )

# ── Color map ──

_STATE_COLORS: dict[str, str] = {
    "draft": "dim",
    "planning": "dim",
    "plan_review": "yellow",
    "review": "yellow",
    "await_audit": "yellow",
    "plan_approved": "cyan",
    "in_progress": "blue",
    "testing": "magenta",
    "integrating": "magenta",
    "mutation": "magenta",
    "rework": "red",
    "pr_ready": "green",
    "merged": "green",
    "accepted": "bold green",
    "failed": "bold red",
}


def _styled(state: str) -> str:
    """Wrap state in Rich markup with color."""
    color = _STATE_COLORS.get(state, "white")
    return f"[{color}]{state}[/{color}]"


# ── Render functions ──


def render_project(view: ProjectStatusView, console: Console | None = None) -> None:
    """Render project overview as a table of stages."""
    c = console or Console()

    table = Table(title="Project Overview", show_lines=True)
    table.add_column("Stage", style="bold")
    table.add_column("Name")
    table.add_column("State")
    table.add_column("Tasks", justify="right")
    table.add_column("Done", justify="right")
    table.add_column("Approved")

    for s in view.stages:
        table.add_row(
            s.stage_id,
            s.name,
            _styled(s.state),
            str(s.task_count),
            str(s.tasks_done),
            "yes" if s.architect_approved else "no",
        )

    c.print(table)
    c.print(
        f"Tasks: {view.total_tasks} total, "
        f"{view.tasks_ready} ready, "
        f"{view.tasks_in_progress} in progress, "
        f"{view.tasks_completed} completed, "
        f"{view.tasks_failed} failed, "
        f"{view.tasks_blocked} blocked"
    )
    c.print(f"Cost: ${view.cost_total:.2f} / ${view.cost_budget:.2f}")


def render_task(view: TaskStatusView, console: Console | None = None) -> None:
    """Render a task status card."""
    c = console or Console()

    lines: list[str] = [
        f"State: {_styled(view.state)}",
        f"Attempt: {view.attempt}/{view.max_attempts}",
        f"Critical: {'yes' if view.touches_critical else 'no'}",
        f"Approved: {'yes' if view.architect_approved else 'no'}",
        f"Cost: ${view.cost_total:.4f} / ${view.cost_budget:.2f} "
        f"(${view.cost_remaining:.4f} remaining)",
    ]

    if view.test_results is not None:
        tr = view.test_results
        color = "green" if tr.all_green else "red"
        lines.append(
            f"Tests: [{color}]{tr.passed} passed, {tr.failed} failed, "
            f"{tr.errors} errors, {tr.coverage_pct:.1f}% coverage[/{color}]"
        )

    if view.auditor_verdict is not None:
        av = view.auditor_verdict
        lines.append(f"Verdict: {av.verdict} — {av.reasoning[:80]}")

    if view.mutation_results is not None:
        mr = view.mutation_results
        color = "green" if mr.acceptable else "red"
        lines.append(
            f"Mutation: [{color}]{mr.killed}/{mr.total_mutants} killed "
            f"(score: {mr.score:.1%})[/{color}]"
        )

    content = "\n".join(lines)
    c.print(Panel(content, title=f"Task {view.task_id}", border_style="blue"))


def render_stage(view: StageStatusView, console: Console | None = None) -> None:
    """Render a stage panel with task table."""
    c = console or Console()

    table = Table(show_header=True)
    table.add_column("Task", style="bold")
    table.add_column("State")

    for t in view.tasks:
        table.add_row(t.task_id, _styled(t.state))

    lines: list[str] = [
        f"State: {_styled(view.state)}",
        f"Approved: {'yes' if view.architect_approved else 'no'}",
        f"Cost: ${view.cost_total:.2f} / ${view.cost_budget:.2f}",
    ]

    if view.e2e_results is not None:
        e = view.e2e_results
        color = "green" if e.all_green else "red"
        lines.append(f"E2E: [{color}]{e.passed} passed, {e.failed} failed[/{color}]")

    header = "\n".join(lines)

    c.print(Panel(header, title=f"Stage {view.stage_id}: {view.name}", border_style="cyan"))
    c.print(table)


def render_cost(view: CostReportView, console: Console | None = None) -> None:
    """Render cost report as three tables."""
    c = console or Console()

    # By model
    t1 = Table(title="Cost by Model")
    t1.add_column("Model", style="bold")
    t1.add_column("Cost ($)", justify="right")
    t1.add_column("Input tokens", justify="right")
    t1.add_column("Output tokens", justify="right")
    for r in view.by_model:
        t1.add_row(r.model, f"{r.cost_usd:.4f}", f"{r.input_tokens:,}", f"{r.output_tokens:,}")
    c.print(t1)

    # By task
    t2 = Table(title="Cost by Task")
    t2.add_column("Task", style="bold")
    t2.add_column("Cost ($)", justify="right")
    t2.add_column("Budget ($)", justify="right")
    for tr in view.by_task:
        t2.add_row(tr.task_id, f"{tr.cost_usd:.4f}", f"{tr.budget_usd:.2f}")
    c.print(t2)

    # By stage
    t3 = Table(title="Cost by Stage")
    t3.add_column("Stage", style="bold")
    t3.add_column("Cost ($)", justify="right")
    t3.add_column("Budget ($)", justify="right")
    for sr in view.by_stage:
        t3.add_row(sr.stage_id, f"{sr.cost_usd:.4f}", f"{sr.budget_usd:.2f}")
    c.print(t3)

    c.print(f"\nTotal: ${view.total_usd:.4f} / ${view.budget_usd:.2f}")


def render_actions(actions: list[PendingAction], console: Console | None = None) -> None:
    """Render pending actions table."""
    c = console or Console()

    if not actions:
        c.print("No pending actions")
        return

    table = Table(title="Pending Actions")
    table.add_column("Type", style="bold")
    table.add_column("Target")
    table.add_column("Description")

    for a in actions:
        table.add_row(a.action_type, a.target_id, a.description)

    c.print(table)


def render_log(view: TaskLogView, console: Console | None = None) -> None:
    """Render task transition timeline as a tree."""
    c = console or Console()

    tree = Tree(f"Task {view.task_id} — {_styled(view.state)}")

    for t in view.history:
        tree.add(
            f"{_styled(t.from_state)} → {_styled(t.to_state)}  "
            f"[dim]{t.reason}[/dim]  [dim italic]{t.timestamp}[/dim italic]"
        )

    c.print(tree)
