"""SQLite persistence for orchestrator state."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import TYPE_CHECKING

from orchestrator.cost import CostEntry, CostTracker
from orchestrator.evidence import (
    AuditorVerdict,
    EvidencePack,
    MutationResults,
    TestResults,
)
from orchestrator.graph import TaskGraph, TaskNode
from orchestrator.stage import StageContext, StageTransition
from orchestrator.state_machine import TaskContext, Transition
from orchestrator.types import AuditVerdict as AuditVerdictEnum
from orchestrator.types import StageState, TaskState

if TYPE_CHECKING:
    from pathlib import Path

    from orchestrator.config import ProjectConfig
    from orchestrator.executor.base import ExecutorAdapter
    from orchestrator.project import ProjectOrchestrator

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS graph_nodes (
    task_id TEXT PRIMARY KEY,
    stage_id TEXT NOT NULL DEFAULT '',
    dependencies TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT '',
    criteria TEXT NOT NULL DEFAULT '',
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    touches_critical INTEGER NOT NULL DEFAULT 0,
    architect_approved INTEGER NOT NULL DEFAULT 0,
    budget_usd REAL NOT NULL DEFAULT 5.0,
    evidence_json TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stages (
    stage_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    state TEXT NOT NULL,
    task_ids TEXT NOT NULL DEFAULT '[]',
    task_states TEXT NOT NULL DEFAULT '{}',
    architect_approved INTEGER NOT NULL DEFAULT 0,
    budget_usd REAL NOT NULL DEFAULT 100.0,
    e2e_results_json TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stage_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id TEXT NOT NULL REFERENCES stages(stage_id),
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL
);
"""


def init_db(db_path: Path) -> None:
    """Create the database and tables if they don't exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", str(_SCHEMA_VERSION)),
        )
        conn.commit()
    finally:
        conn.close()


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Open a WAL-mode connection with Row factory."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Save functions ──


def save_task(conn: sqlite3.Connection, ctx: TaskContext) -> None:
    """Upsert a task context."""
    evidence_json = ctx.evidence.to_json() if ctx.evidence is not None else None
    conn.execute(
        """\
        INSERT INTO tasks (task_id, state, plan, criteria, attempt, max_attempts,
                           touches_critical, architect_approved, budget_usd,
                           evidence_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(task_id) DO UPDATE SET
            state=excluded.state, plan=excluded.plan, criteria=excluded.criteria,
            attempt=excluded.attempt, max_attempts=excluded.max_attempts,
            touches_critical=excluded.touches_critical,
            architect_approved=excluded.architect_approved,
            budget_usd=excluded.budget_usd,
            evidence_json=excluded.evidence_json,
            updated_at=excluded.updated_at
        """,
        (
            ctx.task_id,
            ctx.state.value,
            ctx.plan,
            ctx.criteria,
            ctx.attempt,
            ctx.max_attempts,
            int(ctx.touches_critical),
            int(ctx.architect_approved),
            ctx.cost.budget_usd,
            evidence_json,
        ),
    )
    conn.commit()


def save_task_transition(conn: sqlite3.Connection, task_id: str, t: Transition) -> None:
    """Insert a single task transition record."""
    conn.execute(
        """\
        INSERT INTO task_transitions (task_id, from_state, to_state, reason, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (task_id, t.from_state.value, t.to_state.value, t.reason, t.timestamp),
    )
    conn.commit()


def save_cost_entry(conn: sqlite3.Connection, entry: CostEntry) -> None:
    """Insert a single cost entry."""
    conn.execute(
        """\
        INSERT INTO cost_entries (task_id, model, input_tokens, output_tokens, cost_usd)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry.task_id, entry.model, entry.input_tokens, entry.output_tokens, entry.cost_usd),
    )
    conn.commit()


def save_stage(conn: sqlite3.Connection, ctx: StageContext) -> None:
    """Upsert a stage context."""
    e2e_json: str | None = None
    if ctx.e2e_results is not None:
        e2e_json = json.dumps(
            {
                "passed": ctx.e2e_results.passed,
                "failed": ctx.e2e_results.failed,
                "errors": ctx.e2e_results.errors,
                "coverage_pct": ctx.e2e_results.coverage_pct,
                "raw_output": ctx.e2e_results.raw_output,
            }
        )
    conn.execute(
        """\
        INSERT INTO stages (stage_id, name, state, task_ids, task_states,
                            architect_approved, budget_usd, e2e_results_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(stage_id) DO UPDATE SET
            name=excluded.name, state=excluded.state,
            task_ids=excluded.task_ids, task_states=excluded.task_states,
            architect_approved=excluded.architect_approved,
            budget_usd=excluded.budget_usd,
            e2e_results_json=excluded.e2e_results_json,
            updated_at=excluded.updated_at
        """,
        (
            ctx.stage_id,
            ctx.name,
            ctx.state.value,
            json.dumps(ctx.task_ids),
            json.dumps(ctx.task_states),
            int(ctx.architect_approved),
            ctx.cost.budget_usd,
            e2e_json,
        ),
    )
    conn.commit()


def save_stage_transition(conn: sqlite3.Connection, stage_id: str, t: StageTransition) -> None:
    """Insert a single stage transition record."""
    conn.execute(
        """\
        INSERT INTO stage_transitions (stage_id, from_state, to_state, reason, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (stage_id, t.from_state.value, t.to_state.value, t.reason, t.timestamp),
    )
    conn.commit()


def save_graph_node(conn: sqlite3.Connection, node: TaskNode) -> None:
    """Upsert a graph node."""
    conn.execute(
        """\
        INSERT INTO graph_nodes (task_id, stage_id, dependencies)
        VALUES (?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            stage_id=excluded.stage_id, dependencies=excluded.dependencies
        """,
        (node.task_id, node.stage_id, json.dumps(sorted(node.dependencies))),
    )
    conn.commit()


def save_snapshot(conn: sqlite3.Connection, orch: ProjectOrchestrator) -> None:
    """Save a full orchestrator snapshot — all tasks, stages, graph, costs."""
    # Graph nodes
    for tid in orch.graph.task_ids:
        save_graph_node(conn, orch.graph.get_task(tid))

    # Tasks + transitions + costs
    for tid in orch.runner.task_ids:
        ctx = orch.runner.get_task(tid)
        save_task(conn, ctx)
        # Clear and re-save all transitions
        conn.execute("DELETE FROM task_transitions WHERE task_id = ?", (tid,))
        for t in ctx.history:
            save_task_transition(conn, tid, t)
        # Clear and re-save all cost entries
        conn.execute("DELETE FROM cost_entries WHERE task_id = ?", (tid,))
        for entry in ctx.cost.entries:
            save_cost_entry(conn, entry)

    # Stages + transitions
    for sid in orch.stage_ids:
        sctx = orch.get_stage(sid)
        save_stage(conn, sctx)
        conn.execute("DELETE FROM stage_transitions WHERE stage_id = ?", (sid,))
        for st in sctx.history:
            save_stage_transition(conn, sid, st)

    conn.commit()


# ── Load functions ──


def _evidence_from_json(raw: str) -> EvidencePack:
    """Reconstruct EvidencePack from JSON string."""
    d = json.loads(raw)
    test_results: TestResults | None = None
    if "test_results" in d:
        tr = d["test_results"]
        test_results = TestResults(
            passed=tr["passed"],
            failed=tr["failed"],
            errors=tr["errors"],
            coverage_pct=tr["coverage_pct"],
            raw_output=tr.get("raw_output", ""),
        )
    auditor_verdict: AuditorVerdict | None = None
    if "auditor_verdict" in d:
        av = d["auditor_verdict"]
        auditor_verdict = AuditorVerdict(
            verdict=AuditVerdictEnum(av["verdict"]),
            reasoning=av["reasoning"],
            checklist=av.get("checklist", {}),
            raw_response=av.get("raw_response", ""),
        )
    mutation_results: MutationResults | None = None
    if "mutation_results" in d:
        mr = d["mutation_results"]
        mutation_results = MutationResults(
            total_mutants=mr["total_mutants"],
            killed=mr["killed"],
            survived=mr["survived"],
            timeout=mr["timeout"],
            raw_output=mr.get("raw_output", ""),
        )
    return EvidencePack(
        task_id=d["task_id"],
        diff=d["diff"],
        criteria=d["criteria"],
        plan=d.get("plan", ""),
        test_results=test_results,
        auditor_verdict=auditor_verdict,
        mutation_results=mutation_results,
        developer_log=d.get("developer_log", []),
        timestamp=d.get("timestamp", ""),
    )


def load_tasks(conn: sqlite3.Connection) -> dict[str, TaskContext]:
    """Load all tasks from the database."""
    tasks: dict[str, TaskContext] = {}
    rows = conn.execute("SELECT * FROM tasks").fetchall()
    for row in rows:
        tid = row["task_id"]
        budget = row["budget_usd"]

        # Load cost entries and reconstruct CostTracker without re-raising
        cost_rows = conn.execute(
            "SELECT * FROM cost_entries WHERE task_id = ? ORDER BY id", (tid,)
        ).fetchall()
        tracker = CostTracker(budget_usd=budget)
        for cr in cost_rows:
            entry = CostEntry(
                task_id=cr["task_id"],
                model=cr["model"],
                input_tokens=cr["input_tokens"],
                output_tokens=cr["output_tokens"],
                cost_usd=cr["cost_usd"],
            )
            tracker._entries.append(entry)
            tracker._total_usd += entry.cost_usd
        tracker._warning_fired = tracker._total_usd >= budget * 0.8

        # Evidence
        evidence: EvidencePack | None = None
        if row["evidence_json"]:
            evidence = _evidence_from_json(row["evidence_json"])

        # History
        hist_rows = conn.execute(
            "SELECT * FROM task_transitions WHERE task_id = ? ORDER BY id", (tid,)
        ).fetchall()
        history = [
            Transition(
                from_state=TaskState(h["from_state"]),
                to_state=TaskState(h["to_state"]),
                reason=h["reason"],
                timestamp=h["timestamp"],
            )
            for h in hist_rows
        ]

        ctx = TaskContext(
            task_id=tid,
            state=TaskState(row["state"]),
            plan=row["plan"],
            criteria=row["criteria"],
            evidence=evidence,
            cost=tracker,
            history=history,
            attempt=row["attempt"],
            max_attempts=row["max_attempts"],
            touches_critical=bool(row["touches_critical"]),
            architect_approved=bool(row["architect_approved"]),
        )
        tasks[tid] = ctx
    return tasks


def load_stages(conn: sqlite3.Connection) -> dict[str, StageContext]:
    """Load all stages from the database."""
    stages: dict[str, StageContext] = {}
    rows = conn.execute("SELECT * FROM stages").fetchall()
    for row in rows:
        sid = row["stage_id"]
        budget = row["budget_usd"]

        # E2E results
        e2e: TestResults | None = None
        if row["e2e_results_json"]:
            ed = json.loads(row["e2e_results_json"])
            e2e = TestResults(
                passed=ed["passed"],
                failed=ed["failed"],
                errors=ed["errors"],
                coverage_pct=ed["coverage_pct"],
                raw_output=ed.get("raw_output", ""),
            )

        # History
        hist_rows = conn.execute(
            "SELECT * FROM stage_transitions WHERE stage_id = ? ORDER BY id", (sid,)
        ).fetchall()
        history = [
            StageTransition(
                from_state=StageState(h["from_state"]),
                to_state=StageState(h["to_state"]),
                reason=h["reason"],
                timestamp=h["timestamp"],
            )
            for h in hist_rows
        ]

        tracker = CostTracker(budget_usd=budget)

        ctx = StageContext(
            stage_id=sid,
            name=row["name"],
            state=StageState(row["state"]),
            task_ids=json.loads(row["task_ids"]),
            task_states=json.loads(row["task_states"]),
            e2e_results=e2e,
            architect_approved=bool(row["architect_approved"]),
            history=history,
            cost=tracker,
        )
        stages[sid] = ctx
    return stages


def load_graph(conn: sqlite3.Connection) -> TaskGraph:
    """Load the task dependency graph from the database."""
    graph = TaskGraph()
    rows = conn.execute("SELECT * FROM graph_nodes").fetchall()
    # Sort by task_id to ensure deterministic order for topological add
    sorted_rows = sorted(rows, key=lambda r: r["task_id"])
    for row in sorted_rows:
        deps = frozenset(json.loads(row["dependencies"]))
        node = TaskNode(
            task_id=row["task_id"],
            dependencies=deps,
            stage_id=row["stage_id"],
        )
        graph.add_task(node)
    return graph


def load_orchestrator(
    db_path: Path,
    config: ProjectConfig,
    developer: ExecutorAdapter,
    auditor: ExecutorAdapter,
) -> ProjectOrchestrator:
    """Reconstruct a full ProjectOrchestrator from SQLite."""
    from orchestrator.project import ProjectOrchestrator
    from orchestrator.runner import TaskRunner

    conn = get_connection(db_path)
    try:
        tasks = load_tasks(conn)
        stages = load_stages(conn)
        graph = load_graph(conn)
    finally:
        conn.close()

    runner = TaskRunner(config=config, developer=developer, auditor=auditor)
    runner._tasks = tasks

    orch = ProjectOrchestrator(runner=runner, graph=graph)
    orch._stages = stages

    return orch
