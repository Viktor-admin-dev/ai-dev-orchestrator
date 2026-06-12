"""Bootstrap — AI-powered ТЗ decomposition into stages and tasks."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from orchestrator.graph import CycleError, TaskGraph, TaskNode

if TYPE_CHECKING:
    from pathlib import Path

    from orchestrator.project import ProjectOrchestrator

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"

_SYSTEM_PROMPT = """\
You are a project decomposition assistant. Given a technical specification (ТЗ), \
you must decompose it into stages and tasks.

Output ONLY valid JSON (no markdown fencing, no comments). Schema:

{
  "project_name": "string",
  "summary": "1-2 sentence project summary",
  "stages": [
    {"stage_id": "S1", "name": "Stage Name", "budget_usd": 50.0}
  ],
  "tasks": [
    {
      "task_id": "T-001",
      "plan": "Detailed actionable plan for the AI agent",
      "criteria": "Verifiable acceptance criteria",
      "stage_id": "S1",
      "budget_usd": 5.0,
      "touches_critical": false,
      "dependencies": []
    }
  ]
}

Rules:
- Stage IDs: S1, S2, S3... Task IDs: T-001, T-002, T-003...
- 2-8 tasks per stage
- Budget: 3-10 USD per task, 20-100 USD per stage
- No dependency cycles allowed
- touches_critical=true ONLY for auth, payments, migrations, security
- Plans must be actionable (what to code, where, how)
- Criteria must be verifiable (tests pass, endpoint returns X, file exists)
- Task granularity: 1-4 hours of AI agent work
- Dependencies reference task_ids from the same output
"""


@dataclass
class BootstrapTask:
    task_id: str
    plan: str
    criteria: str
    stage_id: str
    budget_usd: float
    touches_critical: bool
    dependencies: list[str]


@dataclass
class BootstrapStage:
    stage_id: str
    name: str
    budget_usd: float


@dataclass
class BootstrapResult:
    stages: list[BootstrapStage]
    tasks: list[BootstrapTask]
    project_name: str
    summary: str


async def decompose_spec(spec_text: str) -> tuple[dict[str, Any], float, str]:
    """Call AI to decompose a spec into stages/tasks.

    Returns (parsed_dict, cost_usd, model_used).
    Raises RuntimeError on API/parse errors.
    """
    from orchestrator.executor.openrouter import OpenRouterClient

    model = os.environ.get("BOOTSTRAP_MODEL", _DEFAULT_MODEL)
    client = OpenRouterClient(model=model, system_prompt=_SYSTEM_PROMPT)

    result = await client.chat(spec_text, "bootstrap")

    # Strip markdown code fences if present
    text = result.output.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI returned invalid JSON: {exc}") from exc

    return data, result.cost_usd, model


def parse_result(data: dict[str, Any]) -> BootstrapResult:
    """Parse raw AI JSON dict into typed BootstrapResult."""
    stages = [
        BootstrapStage(
            stage_id=s["stage_id"],
            name=s["name"],
            budget_usd=float(s.get("budget_usd", 50.0)),
        )
        for s in data.get("stages", [])
    ]
    tasks = [
        BootstrapTask(
            task_id=t["task_id"],
            plan=t["plan"],
            criteria=t["criteria"],
            stage_id=t["stage_id"],
            budget_usd=float(t.get("budget_usd", 5.0)),
            touches_critical=bool(t.get("touches_critical", False)),
            dependencies=list(t.get("dependencies", [])),
        )
        for t in data.get("tasks", [])
    ]
    return BootstrapResult(
        stages=stages,
        tasks=tasks,
        project_name=data.get("project_name", "Untitled"),
        summary=data.get("summary", ""),
    )


def validate_result(result: BootstrapResult) -> list[str]:
    """Validate a BootstrapResult. Returns list of issues (empty = valid)."""
    issues: list[str] = []

    if not result.stages:
        issues.append("No stages defined")
    if not result.tasks:
        issues.append("No tasks defined")

    stage_ids = {s.stage_id for s in result.stages}
    task_ids = {t.task_id for t in result.tasks}

    # Check stage references
    for t in result.tasks:
        if t.stage_id not in stage_ids:
            issues.append(f"Task {t.task_id} references unknown stage {t.stage_id}")

    # Check dependency references
    for t in result.tasks:
        for dep in t.dependencies:
            if dep not in task_ids:
                issues.append(f"Task {t.task_id} depends on unknown task {dep}")

    # Check for cycles using TaskGraph
    graph = TaskGraph()
    try:
        for t in sorted(result.tasks, key=lambda x: x.task_id):
            node = TaskNode(
                task_id=t.task_id,
                dependencies=frozenset(d for d in t.dependencies if d in task_ids),
                stage_id=t.stage_id,
            )
            graph.add_task(node)
    except CycleError as exc:
        issues.append(f"Dependency cycle detected: {exc}")

    # Check tasks-per-stage counts
    for s in result.stages:
        count = sum(1 for t in result.tasks if t.stage_id == s.stage_id)
        if count == 0:
            issues.append(f"Stage {s.stage_id} has no tasks")

    return issues


def materialize(result: BootstrapResult, orch: ProjectOrchestrator, db_path: Path) -> None:
    """Create all stages, tasks, and graph nodes from a BootstrapResult.

    Persists everything to DB.
    """
    from orchestrator.store import (
        get_connection,
        save_graph_node,
        save_stage,
        save_task,
    )

    conn = get_connection(db_path)
    try:
        # Create tasks in runner
        for t in result.tasks:
            ctx = orch.runner.create_task(
                t.task_id,
                budget_usd=t.budget_usd,
                touches_critical=t.touches_critical,
            )
            ctx.plan = t.plan
            ctx.criteria = t.criteria
            save_task(conn, ctx)

        # Create graph nodes
        for t in sorted(result.tasks, key=lambda x: x.task_id):
            node = TaskNode(
                task_id=t.task_id,
                dependencies=frozenset(t.dependencies),
                stage_id=t.stage_id,
            )
            orch.graph.add_task(node)
            save_graph_node(conn, node)

        # Create stages
        for s in result.stages:
            task_ids = [t.task_id for t in result.tasks if t.stage_id == s.stage_id]
            stage_ctx = orch.create_stage(s.stage_id, s.name, task_ids, budget_usd=s.budget_usd)
            save_stage(conn, stage_ctx)
    finally:
        conn.close()
