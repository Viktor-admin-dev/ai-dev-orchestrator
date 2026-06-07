"""Tests for SQLite persistence layer (store.py)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from orchestrator.config import BudgetsConfig, ProjectConfig
from orchestrator.cost import CostEntry, CostTracker
from orchestrator.evidence import (
    AuditorVerdict,
    EvidencePack,
    TestResults,
)
from orchestrator.executor.base import ExecutorAdapter
from orchestrator.graph import TaskNode
from orchestrator.project import ProjectOrchestrator
from orchestrator.runner import TaskRunner
from orchestrator.stage import StageContext, StageTransition
from orchestrator.state_machine import TaskContext, Transition
from orchestrator.store import (
    _evidence_from_json,
    get_connection,
    init_db,
    load_graph,
    load_orchestrator,
    load_stages,
    load_tasks,
    save_cost_entry,
    save_graph_node,
    save_snapshot,
    save_stage,
    save_stage_transition,
    save_task,
    save_task_transition,
)
from orchestrator.types import AuditVerdict as AuditVerdictEnum
from orchestrator.types import StageState, TaskState


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    init_db(p)
    return p


# ── TestInitDb ──


class TestInitDb:
    def test_creates_tables(self, tmp_path: Path) -> None:
        p = tmp_path / "new.db"
        init_db(p)
        assert p.exists()
        conn = get_connection(p)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "meta" in tables
        assert "tasks" in tables
        assert "task_transitions" in tables
        assert "stages" in tables
        assert "stage_transitions" in tables
        assert "cost_entries" in tables
        assert "graph_nodes" in tables

    def test_idempotent(self, tmp_path: Path) -> None:
        p = tmp_path / "idem.db"
        init_db(p)
        init_db(p)  # Should not raise
        conn = get_connection(p)
        ver = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        conn.close()
        assert ver is not None
        assert ver["value"] == "1"


# ── TestSaveLoadTask ──


class TestSaveLoadTask:
    def test_minimal_task(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        ctx = TaskContext(task_id="T-001")
        save_task(conn, ctx)
        loaded = load_tasks(conn)
        conn.close()
        assert "T-001" in loaded
        assert loaded["T-001"].state is TaskState.DRAFT
        assert loaded["T-001"].plan == ""

    def test_task_with_evidence(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        ctx = TaskContext(task_id="T-002", state=TaskState.PR_READY)
        ctx.plan = "My plan"
        ctx.criteria = "My criteria"
        ctx.evidence = EvidencePack(
            task_id="T-002",
            diff="+code",
            criteria="My criteria",
            plan="My plan",
            test_results=TestResults(passed=10, failed=0, errors=0, coverage_pct=90.0),
            auditor_verdict=AuditorVerdict(
                verdict=AuditVerdictEnum.APPROVE,
                reasoning="LGTM",
                checklist={"style": True},
                raw_response="verdict: approve\nLGTM",
            ),
            developer_log=["step 1", "step 2"],
        )
        save_task(conn, ctx)
        loaded = load_tasks(conn)
        conn.close()
        t = loaded["T-002"]
        assert t.state is TaskState.PR_READY
        assert t.evidence is not None
        assert t.evidence.test_results is not None
        assert t.evidence.test_results.passed == 10
        assert t.evidence.auditor_verdict is not None
        assert t.evidence.auditor_verdict.verdict is AuditVerdictEnum.APPROVE
        assert t.evidence.auditor_verdict.raw_response == "verdict: approve\nLGTM"
        assert t.evidence.developer_log == ["step 1", "step 2"]

    def test_task_with_cost(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        ctx = TaskContext(task_id="T-003", cost=CostTracker(budget_usd=10.0))
        save_task(conn, ctx)
        entry = CostEntry("T-003", "claude-sonnet-4-5", 1000, 500, 0.50)
        save_cost_entry(conn, entry)
        loaded = load_tasks(conn)
        conn.close()
        t = loaded["T-003"]
        assert t.cost.budget_usd == 10.0
        assert len(t.cost.entries) == 1
        assert t.cost.total_usd == pytest.approx(0.50)

    def test_task_with_history(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        ctx = TaskContext(task_id="T-004", state=TaskState.PLAN_REVIEW)
        ctx.history = [
            Transition(
                from_state=TaskState.DRAFT,
                to_state=TaskState.PLAN_REVIEW,
                reason="submitted",
            )
        ]
        save_task(conn, ctx)
        save_task_transition(conn, "T-004", ctx.history[0])
        loaded = load_tasks(conn)
        conn.close()
        t = loaded["T-004"]
        assert len(t.history) == 1
        assert t.history[0].from_state is TaskState.DRAFT
        assert t.history[0].to_state is TaskState.PLAN_REVIEW

    def test_budget_no_error_on_load(self, db_path: Path) -> None:
        """Loading a task whose cost exceeds budget should not raise."""
        conn = get_connection(db_path)
        ctx = TaskContext(task_id="T-005", cost=CostTracker(budget_usd=1.0))
        save_task(conn, ctx)
        # Save cost that exceeds budget
        save_cost_entry(conn, CostEntry("T-005", "m", 100, 50, 1.50))
        loaded = load_tasks(conn)
        conn.close()
        t = loaded["T-005"]
        assert t.cost.total_usd == pytest.approx(1.50)
        assert t.cost.budget_usd == 1.0
        assert t.cost._warning_fired is True


# ── TestSaveLoadStage ──


class TestSaveLoadStage:
    def test_minimal_stage(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        ctx = StageContext(stage_id="S1", name="Stage 1")
        save_stage(conn, ctx)
        loaded = load_stages(conn)
        conn.close()
        assert "S1" in loaded
        assert loaded["S1"].name == "Stage 1"
        assert loaded["S1"].state is StageState.PLANNING

    def test_stage_with_e2e(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        ctx = StageContext(
            stage_id="S2",
            name="Stage 2",
            state=StageState.REVIEW,
            e2e_results=TestResults(passed=5, failed=0, errors=0, coverage_pct=80.0),
        )
        save_stage(conn, ctx)
        loaded = load_stages(conn)
        conn.close()
        s = loaded["S2"]
        assert s.e2e_results is not None
        assert s.e2e_results.passed == 5

    def test_stage_with_history(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        ctx = StageContext(stage_id="S3", name="Stage 3", state=StageState.IN_PROGRESS)
        t = StageTransition(
            from_state=StageState.PLANNING,
            to_state=StageState.IN_PROGRESS,
            reason="started",
        )
        ctx.history = [t]
        save_stage(conn, ctx)
        save_stage_transition(conn, "S3", t)
        loaded = load_stages(conn)
        conn.close()
        s = loaded["S3"]
        assert len(s.history) == 1
        assert s.history[0].to_state is StageState.IN_PROGRESS


# ── TestSaveLoadGraph ──


class TestSaveLoadGraph:
    def test_graph_with_deps(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        n1 = TaskNode(task_id="T-001", stage_id="S1")
        n2 = TaskNode(task_id="T-002", dependencies=frozenset({"T-001"}), stage_id="S1")
        save_graph_node(conn, n1)
        save_graph_node(conn, n2)
        graph = load_graph(conn)
        conn.close()
        assert "T-001" in graph.task_ids
        assert "T-002" in graph.task_ids
        assert graph.get_task("T-002").dependencies == frozenset({"T-001"})

    def test_graph_empty_deps(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        n = TaskNode(task_id="T-001")
        save_graph_node(conn, n)
        graph = load_graph(conn)
        conn.close()
        assert graph.get_task("T-001").dependencies == frozenset()


# ── TestLoadOrchestrator ──


class TestLoadOrchestrator:
    def test_full_restore(self, db_path: Path) -> None:
        # Build an orchestrator
        config = ProjectConfig(budgets=BudgetsConfig(per_task_usd=5.0))
        dev = AsyncMock(spec=ExecutorAdapter)
        aud = AsyncMock(spec=ExecutorAdapter)
        runner = TaskRunner(config=config, developer=dev, auditor=aud)
        orch = ProjectOrchestrator(runner=runner)

        ctx = runner.create_task("T-001", budget_usd=5.0)
        ctx.plan = "Plan A"
        ctx.criteria = "Criteria A"
        orch.graph.add_task(TaskNode(task_id="T-001", stage_id="S1"))
        orch.create_stage("S1", "Stage 1", ["T-001"])

        # Save snapshot
        conn = get_connection(db_path)
        save_snapshot(conn, orch)
        conn.close()

        # Load
        restored = load_orchestrator(db_path, config, dev, aud)
        assert "T-001" in restored.runner.task_ids
        assert restored.runner.get_task("T-001").plan == "Plan A"
        assert "S1" in restored.stage_ids
        assert "T-001" in restored.graph.task_ids


# ── TestEvidenceFromJson ──


class TestEvidenceFromJson:
    def test_minimal(self) -> None:
        raw = json.dumps({"task_id": "T-001", "diff": "d", "criteria": "c", "timestamp": "ts"})
        ep = _evidence_from_json(raw)
        assert ep.task_id == "T-001"
        assert ep.test_results is None
        assert ep.developer_log == []

    def test_full(self) -> None:
        raw = json.dumps({
            "task_id": "T-001",
            "diff": "d",
            "criteria": "c",
            "timestamp": "ts",
            "plan": "p",
            "developer_log": ["log1"],
            "test_results": {
                "passed": 10,
                "failed": 0,
                "errors": 0,
                "coverage_pct": 90.0,
                "raw_output": "ok",
            },
            "auditor_verdict": {
                "verdict": "approve",
                "reasoning": "good",
                "checklist": {"a": True},
                "raw_response": "raw",
            },
            "mutation_results": {
                "total_mutants": 10,
                "killed": 9,
                "survived": 1,
                "timeout": 0,
                "raw_output": "mut",
            },
        })
        ep = _evidence_from_json(raw)
        assert ep.plan == "p"
        assert ep.developer_log == ["log1"]
        assert ep.test_results is not None
        assert ep.test_results.raw_output == "ok"
        assert ep.auditor_verdict is not None
        assert ep.auditor_verdict.raw_response == "raw"
        assert ep.mutation_results is not None
        assert ep.mutation_results.raw_output == "mut"
