"""Tests for add-task and add-stage CLI commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app
from orchestrator.store import get_connection, load_graph, load_tasks

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


@pytest.fixture()
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a temporary project directory with an initialized DB."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    return tmp_path


# ── TestAddTask ──


class TestAddTask:
    def test_add_task_minimal(self, project_dir: Path) -> None:
        result = runner.invoke(app, ["add-task", "T-001"])
        assert result.exit_code == 0
        assert "T-001 added" in result.output

        db = project_dir / ".orchestrator" / "state.db"
        conn = get_connection(db)
        tasks = load_tasks(conn)
        conn.close()

        assert "T-001" in tasks
        ctx = tasks["T-001"]
        assert ctx.state.value == "draft"
        assert ctx.plan == ""
        assert ctx.criteria == ""

    def test_add_task_full(self, project_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "add-task",
                "T-001",
                "--plan",
                "Implement auth module",
                "--criteria",
                "JWT tokens, refresh flow",
                "--stage",
                "S1",
                "--budget",
                "10.0",
                "--critical",
                "--depends-on",
                "T-000",
            ],
        )
        # T-000 doesn't need to exist in runner for graph deps
        assert result.exit_code == 0

        db = project_dir / ".orchestrator" / "state.db"
        conn = get_connection(db)
        tasks = load_tasks(conn)
        graph = load_graph(conn)
        conn.close()

        ctx = tasks["T-001"]
        assert ctx.plan == "Implement auth module"
        assert ctx.criteria == "JWT tokens, refresh flow"
        assert ctx.cost.budget_usd == 10.0
        assert ctx.touches_critical is True

        node = graph.get_task("T-001")
        assert node.stage_id == "S1"
        assert "T-000" in node.dependencies

    def test_add_task_duplicate(self, project_dir: Path) -> None:
        result1 = runner.invoke(app, ["add-task", "T-001"])
        assert result1.exit_code == 0

        result2 = runner.invoke(app, ["add-task", "T-001"])
        assert result2.exit_code == 1
        assert "already exists" in result2.output

    def test_add_task_no_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["add-task", "T-001"])
        assert result.exit_code == 1
        assert "No database found" in result.output


# ── TestAddStage ──


class TestAddStage:
    def test_add_stage_minimal(self, project_dir: Path) -> None:
        result = runner.invoke(app, ["add-stage", "S1", "Authentication"])
        assert result.exit_code == 0
        assert "S1" in result.output
        assert "Authentication" in result.output

    def test_add_stage_with_tasks(self, project_dir: Path) -> None:
        # First add tasks
        runner.invoke(app, ["add-task", "T-001"])
        runner.invoke(app, ["add-task", "T-002"])

        result = runner.invoke(
            app,
            [
                "add-stage",
                "S1",
                "Auth & Data",
                "--task",
                "T-001",
                "--task",
                "T-002",
                "--budget",
                "15.0",
            ],
        )
        assert result.exit_code == 0
        assert "2 tasks" in result.output

    def test_add_stage_missing_task(self, project_dir: Path) -> None:
        result = runner.invoke(
            app,
            [
                "add-stage",
                "S1",
                "Auth",
                "--task",
                "T-999",
            ],
        )
        assert result.exit_code == 1
        assert "T-999" in result.output
        assert "not found" in result.output

    def test_add_stage_duplicate(self, project_dir: Path) -> None:
        result1 = runner.invoke(app, ["add-stage", "S1", "First"])
        assert result1.exit_code == 0

        result2 = runner.invoke(app, ["add-stage", "S1", "Second"])
        assert result2.exit_code == 1
        assert "already exists" in result2.output
