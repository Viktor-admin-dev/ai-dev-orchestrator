"""Demo script — показывает наблюдательную панель с тестовыми данными."""

from unittest.mock import AsyncMock

from orchestrator.config import BudgetsConfig, ProjectConfig
from orchestrator.cost import CostEntry
from orchestrator.evidence import (
    AuditorVerdict,
    EvidencePack,
    MutationResults,
    TestResults,
)
from orchestrator.executor.base import ExecutionResult, ExecutorAdapter
from orchestrator.graph import TaskNode
from orchestrator.panel import PanelData
from orchestrator.project import ProjectOrchestrator
from orchestrator.render import (
    render_actions,
    render_cost,
    render_log,
    render_project,
    render_stage,
    render_task,
)
from orchestrator.runner import TaskRunner
from orchestrator.types import AuditVerdict as AV
from orchestrator.types import TaskState

# ── Setup ──

config = ProjectConfig(budgets=BudgetsConfig(per_task_usd=5.0))
mock_dev = AsyncMock(spec=ExecutorAdapter)
mock_aud = AsyncMock(spec=ExecutorAdapter)
runner = TaskRunner(config=config, developer=mock_dev, auditor=mock_aud)
orch = ProjectOrchestrator(runner=runner)

# Create tasks with different states
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
    auditor_verdict=AuditorVerdict(verdict=AV.APPROVE, reasoning="Clean implementation"),
    developer_log=["Implemented JWT auth", "Added refresh token flow"],
)

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
    auditor_verdict=AuditorVerdict(verdict=AV.APPROVE, reasoning="Solid approach"),
    mutation_results=MutationResults(total_mutants=20, killed=17, survived=3, timeout=0),
    developer_log=["Created migration framework", "Added rollback support"],
)

t3 = runner.create_task("T-003", budget_usd=5.0)
runner.submit_plan("T-003", "API endpoints for users", "REST, pagination, filtering")

t4 = runner.create_task("T-004", budget_usd=5.0)

t5 = runner.create_task("T-005", budget_usd=5.0)
t5.state = TaskState.FAILED

# Build graph
orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
orch.graph.add_task(TaskNode(task_id="T-002", dependencies=frozenset({"T-001"}), stage_id="S1"))
orch.graph.add_task(TaskNode(task_id="T-003", dependencies=frozenset({"T-001"}), stage_id="S1"))
orch.graph.add_task(TaskNode(task_id="T-004", dependencies=frozenset({"T-002", "T-003"}), stage_id="S2"))
orch.graph.add_task(TaskNode(task_id="T-005", stage_id="S2"))

# Create stages
orch.create_stage("S1", "Authentication & Data", ["T-001", "T-002", "T-003"], budget_usd=15.0)
orch.create_stage("S2", "Integration", ["T-004", "T-005"], budget_usd=10.0)
orch.start_stage("S1")

# Simulate some history on T-001
from orchestrator.state_machine import Transition
from orchestrator.types import TaskState as TS

t1.history = [
    Transition(from_state=TS.DRAFT, to_state=TS.PLAN_REVIEW, reason="plan submitted"),
    Transition(from_state=TS.PLAN_REVIEW, to_state=TS.PLAN_APPROVED, reason="plan approved"),
    Transition(from_state=TS.PLAN_APPROVED, to_state=TS.IN_PROGRESS, reason="start coding"),
    Transition(from_state=TS.IN_PROGRESS, to_state=TS.TESTING, reason="code written"),
    Transition(from_state=TS.TESTING, to_state=TS.AWAIT_AUDIT, reason="tests green"),
    Transition(from_state=TS.AWAIT_AUDIT, to_state=TS.PR_READY, reason="approved"),
    Transition(from_state=TS.PR_READY, to_state=TS.MERGED, reason="PR merged"),
    Transition(from_state=TS.MERGED, to_state=TS.ACCEPTED, reason="architect accepted"),
]

# ── Render ──

from rich.console import Console

console = Console()
panel = PanelData(orch)

console.rule("[bold]Project Overview")
render_project(panel.project_status(), console)

console.print()
console.rule("[bold]Stage: S1")
render_stage(panel.stage_status("S1"), console)

console.print()
console.rule("[bold]Task: T-001 (completed)")
render_task(panel.task_status("T-001"), console)

console.print()
console.rule("[bold]Task: T-002 (mutation testing)")
render_task(panel.task_status("T-002"), console)

console.print()
console.rule("[bold]Cost Report")
render_cost(panel.cost_report(), console)

console.print()
console.rule("[bold]Pending Actions")
render_actions(panel.pending_actions(), console)

console.print()
console.rule("[bold]Task Log: T-001")
render_log(panel.task_log("T-001"), console)
