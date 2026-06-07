"""CLI interface for the orchestrator.

Usage:
    orchestrator init                       — create .orchestrator/state.db
    orchestrator run [--auto-approve]       — start/resume the runner loop
    orchestrator add-task T-001 [options]   — add a task to the database
    orchestrator add-stage S1 "Name" [opt]  — add a stage to the database
    orchestrator approve-plan <task_id>     — approve a task plan
    orchestrator reject-plan <task_id>      — reject a task plan
    orchestrator accept-task <task_id>      — accept a merged task
    orchestrator accept-stage <stage_id>    — accept a stage in review
    orchestrator project                    — project overview
    orchestrator stage <id>                 — stage details
    orchestrator task <id>                  — task details
    orchestrator cost                       — cost report
    orchestrator actions                    — pending actions
    orchestrator log <task_id>              — task transition log
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from orchestrator.panel import PanelData
    from orchestrator.project import ProjectOrchestrator

app = typer.Typer(
    name="orchestrator",
    help="AI Dev Orchestrator — observation panel & runner CLI",
    no_args_is_help=True,
)

DB_PATH = Path(".orchestrator/state.db")


def _load_orchestrator_from_db(
    *,
    real_executors: bool = False,
) -> ProjectOrchestrator:  # noqa: F821
    """Load orchestrator state from SQLite.

    Args:
        real_executors: If True, instantiate DeveloperExecutor and
            AuditorExecutor backed by Claude Agent SDK.  Default is
            False — lightweight AsyncMock stubs for read-only CLI
            commands that never invoke agent work.
    """
    from orchestrator.config import ProjectConfig
    from orchestrator.store import load_orchestrator

    if not DB_PATH.exists():
        typer.echo("No database found. Run 'orchestrator init' first.", err=True)
        raise typer.Exit(1)

    config_path = Path("orchestrator.yaml")
    if config_path.exists():
        config = ProjectConfig.from_yaml(config_path.read_text())
    else:
        config = ProjectConfig()

    if real_executors:
        from orchestrator.executor.auditor import AuditorExecutor
        from orchestrator.executor.developer import DeveloperExecutor

        developer = DeveloperExecutor(config)
        auditor = AuditorExecutor(config)
    else:
        from unittest.mock import AsyncMock

        from orchestrator.executor.base import ExecutorAdapter

        developer = AsyncMock(spec=ExecutorAdapter)
        auditor = AsyncMock(spec=ExecutorAdapter)

    return load_orchestrator(DB_PATH, config, developer, auditor)


def _load_orchestrator() -> ProjectOrchestrator:  # noqa: F821
    """Load orchestrator — from DB if exists, otherwise demo."""
    if DB_PATH.exists():
        return _load_orchestrator_from_db()
    return _create_demo()


def _create_demo() -> ProjectOrchestrator:  # noqa: F821
    """Create demo orchestrator with sample data for testing the CLI."""
    from unittest.mock import AsyncMock

    from orchestrator.config import BudgetsConfig, ProjectConfig
    from orchestrator.cost import CostEntry
    from orchestrator.evidence import (
        AuditorVerdict,
        EvidencePack,
        MutationResults,
        TestResults,
    )
    from orchestrator.executor.base import ExecutorAdapter
    from orchestrator.graph import TaskNode
    from orchestrator.project import ProjectOrchestrator
    from orchestrator.runner import TaskRunner
    from orchestrator.state_machine import Transition
    from orchestrator.types import AuditVerdict as AuditVerdictEnum
    from orchestrator.types import TaskState

    config = ProjectConfig(budgets=BudgetsConfig(per_task_usd=5.0))
    runner = TaskRunner(
        config=config,
        developer=AsyncMock(spec=ExecutorAdapter),
        auditor=AsyncMock(spec=ExecutorAdapter),
    )
    orch = ProjectOrchestrator(runner=runner)

    # Tasks
    t1 = runner.create_task("T-001", budget_usd=5.0)
    t1.state = TaskState.ACCEPTED
    t1.architect_approved = True
    t1.plan = "Implement auth module"
    t1.criteria = "JWT tokens, refresh flow"
    t1.cost.record(CostEntry("T-001", "claude-sonnet-4-5", 5000, 2000, 0.80))
    t1.cost.record(CostEntry("T-001", "claude-opus-4-6", 3000, 1500, 1.20))
    t1.evidence = EvidencePack(
        task_id="T-001",
        diff="+auth code",
        criteria="JWT",
        test_results=TestResults(passed=42, failed=0, errors=0, coverage_pct=94.5),
        auditor_verdict=AuditorVerdict(
            verdict=AuditVerdictEnum.APPROVE,
            reasoning="Clean implementation",
        ),
        developer_log=["Implemented JWT auth", "Added refresh token flow"],
    )
    _s = TaskState
    t1.history = [
        Transition(_s.DRAFT, _s.PLAN_REVIEW, "plan submitted"),
        Transition(_s.PLAN_REVIEW, _s.PLAN_APPROVED, "plan approved"),
        Transition(_s.PLAN_APPROVED, _s.IN_PROGRESS, "start coding"),
        Transition(_s.IN_PROGRESS, _s.TESTING, "code written"),
        Transition(_s.TESTING, _s.AWAIT_AUDIT, "tests green"),
        Transition(_s.AWAIT_AUDIT, _s.PR_READY, "approved"),
        Transition(_s.PR_READY, _s.MERGED, "PR merged"),
        Transition(_s.MERGED, _s.ACCEPTED, "architect accepted"),
    ]

    t2 = runner.create_task("T-002", budget_usd=5.0, touches_critical=True)
    t2.state = TaskState.MUTATION
    t2.plan = "Database migration layer"
    t2.criteria = "Zero-downtime migrations"
    t2.cost.record(CostEntry("T-002", "claude-sonnet-4-5", 8000, 3000, 1.50))
    t2.evidence = EvidencePack(
        task_id="T-002",
        diff="+migration code",
        criteria="Zero-downtime",
        test_results=TestResults(passed=30, failed=0, errors=0, coverage_pct=88.0),
        auditor_verdict=AuditorVerdict(
            verdict=AuditVerdictEnum.APPROVE,
            reasoning="Solid approach",
        ),
        mutation_results=MutationResults(total_mutants=20, killed=17, survived=3, timeout=0),
        developer_log=["Created migration framework", "Added rollback support"],
    )

    t3 = runner.create_task("T-003", budget_usd=5.0)
    runner.submit_plan("T-003", "API endpoints for users", "REST, pagination, filtering")

    runner.create_task("T-004", budget_usd=5.0)

    t5 = runner.create_task("T-005", budget_usd=5.0)
    t5.state = TaskState.FAILED

    # Graph
    orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
    orch.graph.add_task(TaskNode(task_id="T-002", dependencies=frozenset({"T-001"}), stage_id="S1"))
    orch.graph.add_task(TaskNode(task_id="T-003", dependencies=frozenset({"T-001"}), stage_id="S1"))
    orch.graph.add_task(
        TaskNode(task_id="T-004", dependencies=frozenset({"T-002", "T-003"}), stage_id="S2")
    )
    orch.graph.add_task(TaskNode(task_id="T-005", stage_id="S2"))

    # Stages
    orch.create_stage("S1", "Authentication & Data", ["T-001", "T-002", "T-003"], budget_usd=15.0)
    orch.create_stage("S2", "Integration", ["T-004", "T-005"], budget_usd=10.0)
    orch.start_stage("S1")

    # Suppress unused variable warnings
    _ = t2, t3, t5

    return orch


def _panel() -> PanelData:  # noqa: F821
    from orchestrator.panel import PanelData

    return PanelData(_load_orchestrator())


# ── New persistence commands ──


@app.command()
def init() -> None:
    """Initialize the orchestrator database at .orchestrator/state.db."""
    from orchestrator.store import init_db

    if DB_PATH.exists():
        typer.echo(f"Database already exists at {DB_PATH}")
        return
    init_db(DB_PATH)
    typer.echo(f"Initialized database at {DB_PATH}")


@app.command("add-task")
def add_task(
    task_id: Annotated[str, typer.Argument(help="Task ID (e.g., T-001)")],
    plan: Annotated[str, typer.Option(help="Task plan")] = "",
    criteria: Annotated[str, typer.Option(help="Acceptance criteria")] = "",
    stage: Annotated[str, typer.Option(help="Stage ID to assign")] = "",
    budget: Annotated[float, typer.Option(help="Budget in USD")] = 5.0,
    critical: Annotated[
        bool, typer.Option("--critical", help="Mark as touching critical code")
    ] = False,
    depends_on: Annotated[
        list[str] | None, typer.Option("--depends-on", help="Dependency task IDs")
    ] = None,
) -> None:
    """Add a task to the database."""
    from orchestrator.graph import TaskNode
    from orchestrator.store import get_connection, save_graph_node, save_task

    orch = _load_orchestrator_from_db()
    deps = depends_on or []

    # Check for duplicate
    if task_id in orch.runner.task_ids:
        typer.echo(f"Task '{task_id}' already exists", err=True)
        raise typer.Exit(1)

    ctx = orch.runner.create_task(task_id, budget_usd=budget, touches_critical=critical)
    if plan:
        ctx.plan = plan
    if criteria:
        ctx.criteria = criteria

    node = TaskNode(task_id=task_id, stage_id=stage, dependencies=frozenset(deps))
    orch.graph.add_task(node)

    conn = get_connection(DB_PATH)
    try:
        save_task(conn, ctx)
        save_graph_node(conn, node)
    finally:
        conn.close()

    typer.echo(f"Task {task_id} added (state=DRAFT, budget=${budget:.2f})")


@app.command("add-stage")
def add_stage(
    stage_id: Annotated[str, typer.Argument(help="Stage ID (e.g., S1)")],
    name: Annotated[str, typer.Argument(help="Stage name")],
    task: Annotated[list[str] | None, typer.Option("--task", help="Task IDs to include")] = None,
    budget: Annotated[float, typer.Option(help="Budget in USD")] = 100.0,
) -> None:
    """Add a stage to the database."""
    from orchestrator.graph import TaskNode
    from orchestrator.store import get_connection, save_graph_node, save_stage

    orch = _load_orchestrator_from_db()
    task_ids = task or []

    # Check for duplicate
    if stage_id in orch.stage_ids:
        typer.echo(f"Stage '{stage_id}' already exists", err=True)
        raise typer.Exit(1)

    # Verify all referenced tasks exist
    for tid in task_ids:
        if tid not in orch.runner.task_ids:
            typer.echo(f"Task '{tid}' not found", err=True)
            raise typer.Exit(1)

    stage_ctx = orch.create_stage(stage_id, name, task_ids, budget_usd=budget)

    conn = get_connection(DB_PATH)
    try:
        save_stage(conn, stage_ctx)
        # Update graph nodes with stage_id
        for tid in task_ids:
            node = orch.graph.get_task(tid)
            updated = TaskNode(
                task_id=node.task_id,
                dependencies=node.dependencies,
                stage_id=stage_id,
            )
            save_graph_node(conn, updated)
    finally:
        conn.close()

    typer.echo(f"Stage {stage_id} '{name}' added ({len(task_ids)} tasks, budget=${budget:.2f})")


@app.command()
def run(
    auto_approve: Annotated[
        bool, typer.Option("--auto-approve", help="Auto-approve plans and accept tasks")
    ] = False,
) -> None:
    """Start or resume the runner loop."""
    from orchestrator.loop import run_loop
    from orchestrator.store import get_connection, save_snapshot

    if not DB_PATH.exists():
        typer.echo("No database found. Run 'orchestrator init' first.", err=True)
        raise typer.Exit(1)

    orch = _load_orchestrator_from_db(real_executors=True)

    # Check vendor independence (SPEC §1)
    warnings = orch.runner.config.check_vendor_independence()
    for w in warnings:
        typer.echo(f"WARNING: {w}", err=True)

    result = asyncio.run(run_loop(orch, DB_PATH, auto_approve=auto_approve))

    # Save final state
    conn = get_connection(DB_PATH)
    try:
        save_snapshot(conn, orch)
    finally:
        conn.close()

    if result.error:
        typer.echo(f"Error: {result.error}", err=True)
    typer.echo(f"Tasks run: {result.tasks_run}")
    if result.completed:
        typer.echo("All stages completed!")
    elif result.paused:
        typer.echo("Paused. Pending actions:")
        for action in result.pending_actions:
            typer.echo(f"  - orchestrator {action}")
    else:
        typer.echo("No more progress possible.")


@app.command("approve-plan")
def approve_plan(
    task_id: Annotated[str, typer.Argument(help="Task ID (e.g., T-001)")],
) -> None:
    """Approve a task plan: PLAN_REVIEW -> PLAN_APPROVED."""
    from orchestrator.store import get_connection, save_task, save_task_transition

    orch = _load_orchestrator_from_db()
    try:
        ctx = orch.runner.get_task(task_id)
    except KeyError:
        typer.echo(f"Task '{task_id}' not found", err=True)
        raise typer.Exit(1) from None

    prev_len = len(ctx.history)
    try:
        orch.runner.approve_plan(task_id)
    except Exception as exc:
        typer.echo(f"Cannot approve: {exc}", err=True)
        raise typer.Exit(1) from exc

    conn = get_connection(DB_PATH)
    try:
        for t in ctx.history[prev_len:]:
            save_task_transition(conn, task_id, t)
        save_task(conn, ctx)
    finally:
        conn.close()

    typer.echo(f"Plan approved for {task_id}")


@app.command("reject-plan")
def reject_plan(
    task_id: Annotated[str, typer.Argument(help="Task ID (e.g., T-001)")],
) -> None:
    """Reject a task plan: PLAN_REVIEW -> DRAFT."""
    from orchestrator.store import get_connection, save_task, save_task_transition

    orch = _load_orchestrator_from_db()
    try:
        ctx = orch.runner.get_task(task_id)
    except KeyError:
        typer.echo(f"Task '{task_id}' not found", err=True)
        raise typer.Exit(1) from None

    prev_len = len(ctx.history)
    try:
        orch.runner.reject_plan(task_id)
    except Exception as exc:
        typer.echo(f"Cannot reject: {exc}", err=True)
        raise typer.Exit(1) from exc

    conn = get_connection(DB_PATH)
    try:
        for t in ctx.history[prev_len:]:
            save_task_transition(conn, task_id, t)
        save_task(conn, ctx)
    finally:
        conn.close()

    typer.echo(f"Plan rejected for {task_id}")


@app.command("accept-task")
def accept_task(
    task_id: Annotated[str, typer.Argument(help="Task ID (e.g., T-001)")],
) -> None:
    """Accept a merged task: MERGED -> ACCEPTED."""
    from orchestrator.store import get_connection, save_task, save_task_transition

    orch = _load_orchestrator_from_db()
    try:
        ctx = orch.runner.get_task(task_id)
    except KeyError:
        typer.echo(f"Task '{task_id}' not found", err=True)
        raise typer.Exit(1) from None

    prev_len = len(ctx.history)
    try:
        orch.runner.accept(task_id)
    except Exception as exc:
        typer.echo(f"Cannot accept: {exc}", err=True)
        raise typer.Exit(1) from exc

    conn = get_connection(DB_PATH)
    try:
        for t in ctx.history[prev_len:]:
            save_task_transition(conn, task_id, t)
        save_task(conn, ctx)
    finally:
        conn.close()

    typer.echo(f"Task {task_id} accepted")


@app.command("accept-stage")
def accept_stage(
    stage_id: Annotated[str, typer.Argument(help="Stage ID (e.g., S1)")],
) -> None:
    """Accept a stage in review: REVIEW -> ACCEPTED."""
    from orchestrator.store import get_connection, save_stage, save_stage_transition

    orch = _load_orchestrator_from_db()
    try:
        stage_ctx = orch.get_stage(stage_id)
    except KeyError:
        typer.echo(f"Stage '{stage_id}' not found", err=True)
        raise typer.Exit(1) from None

    prev_len = len(stage_ctx.history)
    try:
        orch.accept_stage(stage_id)
    except Exception as exc:
        typer.echo(f"Cannot accept: {exc}", err=True)
        raise typer.Exit(1) from exc

    conn = get_connection(DB_PATH)
    try:
        for t in stage_ctx.history[prev_len:]:
            save_stage_transition(conn, stage_id, t)
        save_stage(conn, stage_ctx)
    finally:
        conn.close()

    typer.echo(f"Stage {stage_id} accepted")


# ── Observation panel commands ──


@app.command()
def project() -> None:
    """Show project overview — stages, task counts, cost."""
    from orchestrator.render import render_project

    render_project(_panel().project_status())


@app.command()
def stage(
    stage_id: Annotated[str, typer.Argument(help="Stage ID (e.g., S1)")],
) -> None:
    """Show stage details — tasks, state, E2E results."""
    from orchestrator.render import render_stage

    try:
        render_stage(_panel().stage_status(stage_id))
    except KeyError:
        typer.echo(f"Stage '{stage_id}' not found", err=True)
        raise typer.Exit(1) from None


@app.command()
def task(
    task_id: Annotated[str, typer.Argument(help="Task ID (e.g., T-001)")],
) -> None:
    """Show task details — state, evidence, cost."""
    from orchestrator.render import render_task

    try:
        render_task(_panel().task_status(task_id))
    except KeyError:
        typer.echo(f"Task '{task_id}' not found", err=True)
        raise typer.Exit(1) from None


@app.command()
def cost() -> None:
    """Show cost report — by model, task, stage."""
    from orchestrator.render import render_cost

    render_cost(_panel().cost_report())


@app.command()
def actions() -> None:
    """Show pending actions awaiting human input."""
    from orchestrator.render import render_actions

    render_actions(_panel().pending_actions())


@app.command()
def log(
    task_id: Annotated[str, typer.Argument(help="Task ID (e.g., T-001)")],
) -> None:
    """Show task transition log — timeline of state changes."""
    from orchestrator.render import render_log

    try:
        render_log(_panel().task_log(task_id))
    except KeyError:
        typer.echo(f"Task '{task_id}' not found", err=True)
        raise typer.Exit(1) from None


@app.command()
def demo() -> None:
    """Show full demo — all panels with sample data."""
    from rich.console import Console

    from orchestrator.render import (
        render_actions,
        render_cost,
        render_log,
        render_project,
        render_stage,
        render_task,
    )

    console = Console()
    from orchestrator.panel import PanelData

    panel_data = PanelData(_create_demo())

    console.rule("[bold]Project Overview")
    render_project(panel_data.project_status(), console)

    console.print()
    console.rule("[bold]Stage: S1")
    render_stage(panel_data.stage_status("S1"), console)

    console.print()
    console.rule("[bold]Task: T-001 (completed)")
    render_task(panel_data.task_status("T-001"), console)

    console.print()
    console.rule("[bold]Task: T-002 (mutation testing)")
    render_task(panel_data.task_status("T-002"), console)

    console.print()
    console.rule("[bold]Cost Report")
    render_cost(panel_data.cost_report(), console)

    console.print()
    console.rule("[bold]Pending Actions")
    render_actions(panel_data.pending_actions(), console)

    console.print()
    console.rule("[bold]Task Log: T-001")
    render_log(panel_data.task_log("T-001"), console)


if __name__ == "__main__":
    app()
