"""Tests for the bootstrap feature (AI spec decomposition)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from orchestrator.store import (
    delete_bootstrap_draft,
    get_connection,
    init_db,
    load_bootstrap_draft,
    save_bootstrap_draft,
)
from orchestrator.web.app import create_app
from orchestrator.web.bootstrap import (
    BootstrapResult,
    BootstrapStage,
    BootstrapTask,
    parse_result,
    validate_result,
)

if TYPE_CHECKING:
    from pathlib import Path


# ── Sample data ──

_SAMPLE_DATA: dict[str, Any] = {
    "project_name": "Test Project",
    "summary": "A test project for testing",
    "stages": [
        {"stage_id": "S1", "name": "Setup", "budget_usd": 30.0},
        {"stage_id": "S2", "name": "Features", "budget_usd": 50.0},
    ],
    "tasks": [
        {
            "task_id": "T-001",
            "plan": "Set up repo structure",
            "criteria": "pytest passes",
            "stage_id": "S1",
            "budget_usd": 5.0,
            "touches_critical": False,
            "dependencies": [],
        },
        {
            "task_id": "T-002",
            "plan": "Add config module",
            "criteria": "Config loads from YAML",
            "stage_id": "S1",
            "budget_usd": 5.0,
            "touches_critical": False,
            "dependencies": ["T-001"],
        },
        {
            "task_id": "T-003",
            "plan": "Implement auth",
            "criteria": "Login endpoint returns JWT",
            "stage_id": "S2",
            "budget_usd": 8.0,
            "touches_critical": True,
            "dependencies": ["T-002"],
        },
    ],
}


def _mock_decompose_return() -> tuple[dict[str, Any], float, str]:
    """Return value for mocked decompose_spec."""
    return (_SAMPLE_DATA, 0.05, "anthropic/claude-sonnet-4-6")


# ── parse_result tests ──


class TestParseResult:
    def test_parses_stages(self) -> None:
        result = parse_result(_SAMPLE_DATA)
        assert len(result.stages) == 2
        assert result.stages[0].stage_id == "S1"
        assert result.stages[1].name == "Features"

    def test_parses_tasks(self) -> None:
        result = parse_result(_SAMPLE_DATA)
        assert len(result.tasks) == 3
        assert result.tasks[0].task_id == "T-001"
        assert result.tasks[2].touches_critical is True
        assert result.tasks[1].dependencies == ["T-001"]

    def test_parses_metadata(self) -> None:
        result = parse_result(_SAMPLE_DATA)
        assert result.project_name == "Test Project"
        assert result.summary == "A test project for testing"

    def test_defaults(self) -> None:
        result = parse_result({"stages": [], "tasks": []})
        assert result.project_name == "Untitled"
        assert result.summary == ""

    def test_missing_optional_fields(self) -> None:
        data = {
            "stages": [{"stage_id": "S1", "name": "Only"}],
            "tasks": [
                {
                    "task_id": "T-001",
                    "plan": "p",
                    "criteria": "c",
                    "stage_id": "S1",
                }
            ],
        }
        result = parse_result(data)
        assert result.tasks[0].budget_usd == 5.0
        assert result.tasks[0].touches_critical is False
        assert result.tasks[0].dependencies == []


# ── validate_result tests ──


class TestValidateResult:
    def test_valid(self) -> None:
        result = parse_result(_SAMPLE_DATA)
        issues = validate_result(result)
        assert issues == []

    def test_no_stages(self) -> None:
        result = BootstrapResult(stages=[], tasks=[], project_name="", summary="")
        issues = validate_result(result)
        assert any("No stages" in i for i in issues)

    def test_no_tasks(self) -> None:
        result = BootstrapResult(
            stages=[BootstrapStage("S1", "X", 10)], tasks=[], project_name="", summary=""
        )
        issues = validate_result(result)
        assert any("No tasks" in i for i in issues)

    def test_unknown_stage_ref(self) -> None:
        result = BootstrapResult(
            stages=[BootstrapStage("S1", "X", 10)],
            tasks=[BootstrapTask("T-001", "p", "c", "S99", 5, False, [])],
            project_name="",
            summary="",
        )
        issues = validate_result(result)
        assert any("unknown stage S99" in i for i in issues)

    def test_unknown_dependency(self) -> None:
        result = BootstrapResult(
            stages=[BootstrapStage("S1", "X", 10)],
            tasks=[BootstrapTask("T-001", "p", "c", "S1", 5, False, ["T-999"])],
            project_name="",
            summary="",
        )
        issues = validate_result(result)
        assert any("unknown task T-999" in i for i in issues)

    def test_cycle_detection(self) -> None:
        result = BootstrapResult(
            stages=[BootstrapStage("S1", "X", 10)],
            tasks=[
                BootstrapTask("T-001", "p", "c", "S1", 5, False, ["T-002"]),
                BootstrapTask("T-002", "p", "c", "S1", 5, False, ["T-001"]),
            ],
            project_name="",
            summary="",
        )
        issues = validate_result(result)
        assert any("cycle" in i.lower() for i in issues)

    def test_empty_stage(self) -> None:
        result = BootstrapResult(
            stages=[
                BootstrapStage("S1", "X", 10),
                BootstrapStage("S2", "Y", 10),
            ],
            tasks=[BootstrapTask("T-001", "p", "c", "S1", 5, False, [])],
            project_name="",
            summary="",
        )
        issues = validate_result(result)
        assert any("S2 has no tasks" in i for i in issues)


# ── Draft persistence tests ──


class TestDraftPersistence:
    @pytest.fixture()
    def db_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "test.db"
        init_db(p)
        return p

    def test_save_load_delete(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        save_bootstrap_draft(conn, "d1", "spec", '{"a":1}', "model-x", 0.12)
        draft = load_bootstrap_draft(conn, "d1")
        assert draft is not None
        assert draft["spec_text"] == "spec"
        assert draft["result_json"] == '{"a":1}'
        assert draft["model"] == "model-x"
        assert draft["cost_usd"] == pytest.approx(0.12)

        delete_bootstrap_draft(conn, "d1")
        assert load_bootstrap_draft(conn, "d1") is None
        conn.close()

    def test_load_missing(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        assert load_bootstrap_draft(conn, "nonexistent") is None
        conn.close()

    def test_upsert(self, db_path: Path) -> None:
        conn = get_connection(db_path)
        save_bootstrap_draft(conn, "d1", "v1", "{}", "m", 0.1)
        save_bootstrap_draft(conn, "d1", "v2", '{"new":true}', "m2", 0.2)
        draft = load_bootstrap_draft(conn, "d1")
        assert draft is not None
        assert draft["spec_text"] == "v2"
        assert draft["model"] == "m2"
        conn.close()


# ── Route tests ──

# Patch target: decompose_spec is called from routes.py which imports it from
# orchestrator.web.bootstrap. We mock the function itself, not OpenRouterClient
# (which is lazily imported inside decompose_spec).
_DECOMPOSE_PATCH = "orchestrator.web.bootstrap.decompose_spec"


@pytest.fixture()
def bootstrap_client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "state.db"
    init_db(db_path)
    app = create_app(db_path)
    return TestClient(app)


@pytest.fixture()
def bootstrap_client_no_db(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "nonexistent.db"
    app = create_app(db_path)
    return TestClient(app)


class TestBootstrapRoutes:
    def test_get_bootstrap(self, bootstrap_client: TestClient) -> None:
        resp = bootstrap_client.get("/bootstrap")
        assert resp.status_code == 200
        assert "Bootstrap" in resp.text
        assert "textarea" in resp.text

    def test_post_bootstrap_success(self, bootstrap_client: TestClient) -> None:
        mock_fn = AsyncMock(return_value=_mock_decompose_return())
        with patch(_DECOMPOSE_PATCH, mock_fn):
            resp = bootstrap_client.post(
                "/bootstrap",
                data={"spec_text": "Build a todo app"},
                follow_redirects=False,
            )

        assert resp.status_code == 200
        assert "Test Project" in resp.text
        assert "T-001" in resp.text
        assert "Confirm" in resp.text

    def test_post_bootstrap_api_error(self, bootstrap_client: TestClient) -> None:
        mock_fn = AsyncMock(side_effect=RuntimeError("API down"))
        with patch(_DECOMPOSE_PATCH, mock_fn):
            resp = bootstrap_client.post(
                "/bootstrap",
                data={"spec_text": "Anything"},
            )

        assert resp.status_code == 200
        assert "API down" in resp.text
        assert "textarea" in resp.text

    def test_post_bootstrap_invalid_json(self, bootstrap_client: TestClient) -> None:
        mock_fn = AsyncMock(side_effect=RuntimeError("AI returned invalid JSON: bla"))
        with patch(_DECOMPOSE_PATCH, mock_fn):
            resp = bootstrap_client.post(
                "/bootstrap",
                data={"spec_text": "Anything"},
            )

        assert resp.status_code == 200
        assert "invalid JSON" in resp.text

    def test_confirm_flow(self, bootstrap_client: TestClient) -> None:
        """Full flow: submit -> confirm -> entities created."""
        mock_fn = AsyncMock(return_value=_mock_decompose_return())
        with patch(_DECOMPOSE_PATCH, mock_fn):
            resp = bootstrap_client.post(
                "/bootstrap",
                data={"spec_text": "Build a todo app"},
            )

        # Extract draft_id from hidden field
        match = re.search(r'name="draft_id"\s+value="([^"]+)"', resp.text)
        assert match, "draft_id not found in preview page"
        draft_id = match.group(1)

        # Confirm
        resp2 = bootstrap_client.post(
            "/bootstrap/confirm",
            data={"draft_id": draft_id},
            follow_redirects=False,
        )
        assert resp2.status_code == 303
        assert resp2.headers["location"] == "/"

        # Verify entities exist via API
        resp3 = bootstrap_client.get("/api/task/T-001")
        assert resp3.status_code == 200
        assert resp3.json()["task_id"] == "T-001"

    def test_confirm_missing_draft(self, bootstrap_client: TestClient) -> None:
        resp = bootstrap_client.post(
            "/bootstrap/confirm",
            data={"draft_id": "nonexistent"},
        )
        assert resp.status_code == 404

    def test_discard_flow(self, bootstrap_client: TestClient) -> None:
        mock_fn = AsyncMock(return_value=_mock_decompose_return())
        with patch(_DECOMPOSE_PATCH, mock_fn):
            resp = bootstrap_client.post(
                "/bootstrap",
                data={"spec_text": "Build something"},
            )

        match = re.search(r'name="draft_id"\s+value="([^"]+)"', resp.text)
        assert match
        draft_id = match.group(1)

        resp2 = bootstrap_client.post(
            "/bootstrap/discard",
            data={"draft_id": draft_id},
            follow_redirects=False,
        )
        assert resp2.status_code == 303
        assert resp2.headers["location"] == "/bootstrap"

    def test_auto_init_db(self, bootstrap_client_no_db: TestClient) -> None:
        """POST /bootstrap should auto-init DB if it doesn't exist."""
        mock_fn = AsyncMock(return_value=_mock_decompose_return())
        with patch(_DECOMPOSE_PATCH, mock_fn):
            resp = bootstrap_client_no_db.post(
                "/bootstrap",
                data={"spec_text": "Build something"},
            )

        assert resp.status_code == 200
        assert "Test Project" in resp.text
