"""Tests for the web dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from orchestrator.store import init_db, save_snapshot
from orchestrator.web.app import create_app
from orchestrator.web.serializers import state_css, view_to_dict

if TYPE_CHECKING:
    from pathlib import Path


# ── Serializer tests ──


def test_state_css_known() -> None:
    assert "emerald" in state_css("accepted")
    assert "red" in state_css("failed")
    assert "yellow" in state_css("plan_review")


def test_state_css_unknown() -> None:
    result = state_css("nonexistent_state")
    assert "gray" in result


def test_view_to_dict_simple() -> None:
    from orchestrator.panel import PendingAction

    action = PendingAction(action_type="review_plan", target_id="T-001", description="test")
    d = view_to_dict(action)
    assert d == {"action_type": "review_plan", "target_id": "T-001", "description": "test"}


def test_view_to_dict_nested() -> None:
    from orchestrator.panel import ProjectStatusView, StageSummary

    view = ProjectStatusView(
        stages=(
            StageSummary(
                stage_id="S1",
                name="Test",
                state="planning",
                task_count=2,
                tasks_done=1,
                architect_approved=False,
            ),
        ),
        total_tasks=2,
        tasks_ready=1,
        tasks_blocked=0,
        tasks_completed=1,
        tasks_failed=0,
        tasks_in_progress=0,
        cost_total=1.5,
        cost_budget=10.0,
    )
    d = view_to_dict(view)
    assert isinstance(d["stages"], list)
    assert d["stages"][0]["stage_id"] == "S1"
    assert d["total_tasks"] == 2


# ── App / route tests ──


def _create_demo_db(tmp_path: Path) -> Path:
    """Create a demo DB and return its path."""
    from orchestrator.cli import _create_demo
    from orchestrator.store import get_connection

    db_path = tmp_path / "state.db"
    init_db(db_path)

    orch = _create_demo()
    conn = get_connection(db_path)
    try:
        save_snapshot(conn, orch)
    finally:
        conn.close()
    return db_path


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """TestClient with a demo database."""
    db_path = _create_demo_db(tmp_path)
    app = create_app(db_path)
    return TestClient(app)


@pytest.fixture()
def empty_client(tmp_path: Path) -> TestClient:
    """TestClient with no database."""
    db_path = tmp_path / "nonexistent.db"
    app = create_app(db_path)
    return TestClient(app)


class TestPageRoutes:
    def test_dashboard(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Total Tasks" in resp.text

    def test_dashboard_no_db(self, empty_client: TestClient) -> None:
        resp = empty_client.get("/")
        assert resp.status_code == 200
        assert "No database found" in resp.text

    def test_stage_page(self, client: TestClient) -> None:
        resp = client.get("/stage/S1")
        assert resp.status_code == 200
        assert "S1" in resp.text

    def test_stage_not_found(self, client: TestClient) -> None:
        resp = client.get("/stage/NONEXISTENT")
        assert resp.status_code == 404

    def test_task_page(self, client: TestClient) -> None:
        resp = client.get("/task/T-001")
        assert resp.status_code == 200
        assert "T-001" in resp.text

    def test_task_not_found(self, client: TestClient) -> None:
        resp = client.get("/task/NONEXISTENT")
        assert resp.status_code == 404

    def test_cost_page(self, client: TestClient) -> None:
        resp = client.get("/cost")
        assert resp.status_code == 200
        assert "Cost Report" in resp.text

    def test_actions_page(self, client: TestClient) -> None:
        resp = client.get("/actions")
        assert resp.status_code == 200
        assert "Pending Actions" in resp.text

    def test_log_page(self, client: TestClient) -> None:
        resp = client.get("/log/T-001")
        assert resp.status_code == 200
        assert "T-001" in resp.text

    def test_log_not_found(self, client: TestClient) -> None:
        resp = client.get("/log/NONEXISTENT")
        assert resp.status_code == 404


class TestJsonApi:
    def test_api_project(self, client: TestClient) -> None:
        resp = client.get("/api/project")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_tasks" in data
        assert "stages" in data

    def test_api_task(self, client: TestClient) -> None:
        resp = client.get("/api/task/T-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "T-001"

    def test_api_task_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/task/NONEXISTENT")
        assert resp.status_code == 404

    def test_api_actions(self, client: TestClient) -> None:
        resp = client.get("/api/actions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestMutations:
    def test_approve_plan(self, client: TestClient) -> None:
        # T-003 is in PLAN_REVIEW in demo data
        resp = client.post("/approve-plan/T-003", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/actions"

    def test_reject_plan(self, tmp_path: Path) -> None:
        # Need a fresh DB since approve_plan may have changed T-003
        db_path = _create_demo_db(tmp_path)
        app = create_app(db_path)
        c = TestClient(app)
        resp = c.post("/reject-plan/T-003", follow_redirects=False)
        assert resp.status_code == 303

    def test_approve_plan_not_found(self, client: TestClient) -> None:
        resp = client.post("/approve-plan/NONEXISTENT")
        assert resp.status_code == 404

    def test_add_task(self, client: TestClient) -> None:
        resp = client.post(
            "/add-task",
            data={
                "task_id": "T-NEW",
                "plan": "test",
                "criteria": "cr",
                "stage": "",
                "budget": "5.0",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Verify task appears in API
        resp2 = client.get("/api/task/T-NEW")
        assert resp2.status_code == 200
        assert resp2.json()["task_id"] == "T-NEW"

    def test_add_stage(self, client: TestClient) -> None:
        resp = client.post(
            "/add-stage",
            data={"stage_id": "S-NEW", "name": "New Stage", "budget": "50.0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_add_duplicate_task(self, client: TestClient) -> None:
        resp = client.post(
            "/add-task",
            data={"task_id": "T-001", "stage": "", "budget": "5.0"},
        )
        assert resp.status_code == 400
