"""Tests for the CLI bootstrap command."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app
from orchestrator.web.bootstrap import BootstrapResult, BootstrapStage, BootstrapTask

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()

# Reusable fixture data

_SAMPLE_DATA: dict = {
    "project_name": "TestApp",
    "summary": "A test application",
    "stages": [{"stage_id": "S1", "name": "Core", "budget_usd": 50.0}],
    "tasks": [
        {
            "task_id": "T-001",
            "plan": "Implement feature A",
            "criteria": "Tests pass",
            "stage_id": "S1",
            "budget_usd": 5.0,
            "touches_critical": False,
            "dependencies": [],
        },
        {
            "task_id": "T-002",
            "plan": "Implement feature B",
            "criteria": "Tests pass",
            "stage_id": "S1",
            "budget_usd": 5.0,
            "touches_critical": False,
            "dependencies": ["T-001"],
        },
    ],
}

_SAMPLE_RESULT = BootstrapResult(
    stages=[BootstrapStage(stage_id="S1", name="Core", budget_usd=50.0)],
    tasks=[
        BootstrapTask(
            task_id="T-001",
            plan="Implement feature A",
            criteria="Tests pass",
            stage_id="S1",
            budget_usd=5.0,
            touches_critical=False,
            dependencies=[],
        ),
        BootstrapTask(
            task_id="T-002",
            plan="Implement feature B",
            criteria="Tests pass",
            stage_id="S1",
            budget_usd=5.0,
            touches_critical=False,
            dependencies=["T-001"],
        ),
    ],
    project_name="TestApp",
    summary="A test application",
)


@pytest.fixture()
def spec_dir(tmp_path: Path) -> Path:
    """Create a temp directory with sample .md spec files."""
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "overview.md").write_text("# Overview\nThis is the project overview.")
    (specs / "features.md").write_text("# Features\n- Feature A\n- Feature B")
    return specs


@pytest.fixture()
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set working directory to tmp_path (DB will be created here)."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _mock_decompose() -> AsyncMock:
    """Return an AsyncMock for decompose_spec returning sample data."""
    mock = AsyncMock(return_value=(_SAMPLE_DATA, 0.0123, "anthropic/claude-sonnet-4-6"))
    return mock


# ── Tests ──


class TestBootstrapReadsFolder:
    def test_reads_and_concatenates_md_files(self, spec_dir: Path, project_dir: Path) -> None:
        """All .md files are read and concatenated before calling decompose_spec."""
        captured_text: list[str] = []

        async def fake_decompose(text: str) -> tuple[dict, float, str]:
            captured_text.append(text)
            return _SAMPLE_DATA, 0.01, "test-model"

        with patch("orchestrator.web.bootstrap.decompose_spec", side_effect=fake_decompose):
            result = runner.invoke(app, ["bootstrap", str(spec_dir), "--yes"])

        assert result.exit_code == 0, result.output
        assert len(captured_text) == 1
        spec = captured_text[0]
        assert "--- features.md ---" in spec
        assert "--- overview.md ---" in spec
        assert "Feature A" in spec
        assert "project overview" in spec


class TestBootstrapPreviewAndConfirm:
    def test_shows_preview_and_asks_confirmation(self, spec_dir: Path, project_dir: Path) -> None:
        """Without --yes, the command shows a preview and asks for confirmation."""
        with patch("orchestrator.web.bootstrap.decompose_spec", new_callable=_mock_decompose):
            # Simulate user saying "n" to abort
            result = runner.invoke(app, ["bootstrap", str(spec_dir)], input="n\n")

        assert result.exit_code == 1  # Aborted
        # Preview should still be shown
        assert "TestApp" in result.output
        assert "S1" in result.output
        assert "T-001" in result.output


class TestBootstrapYesSkipsConfirm:
    def test_yes_flag_skips_confirmation(self, spec_dir: Path, project_dir: Path) -> None:
        """With --yes, stages and tasks are created without asking."""
        with patch("orchestrator.web.bootstrap.decompose_spec", new_callable=_mock_decompose):
            result = runner.invoke(app, ["bootstrap", str(spec_dir), "--yes"])

        assert result.exit_code == 0, result.output
        assert "Created 1 stages, 2 tasks" in result.output


class TestBootstrapEmptyFolderErrors:
    def test_empty_folder_exits_with_error(self, tmp_path: Path, project_dir: Path) -> None:
        """A folder with no .md files produces exit code 1."""
        empty = tmp_path / "empty_specs"
        empty.mkdir()

        result = runner.invoke(app, ["bootstrap", str(empty)])

        assert result.exit_code == 1
        assert "No .md files" in result.output


class TestBootstrapInvalidPathErrors:
    def test_nonexistent_path_exits_with_error(self, project_dir: Path) -> None:
        """A nonexistent path produces exit code 1."""
        result = runner.invoke(app, ["bootstrap", "/tmp/does_not_exist_xyz"])

        assert result.exit_code == 1
        assert "Path not found" in result.output

    def test_file_instead_of_dir_exits_with_error(self, tmp_path: Path, project_dir: Path) -> None:
        """Passing a file instead of directory produces exit code 1."""
        f = tmp_path / "spec.md"
        f.write_text("# Spec")

        result = runner.invoke(app, ["bootstrap", str(f)])

        assert result.exit_code == 1
        assert "Not a directory" in result.output


class TestBootstrapValidationIssuesAborts:
    def test_validation_issues_abort(self, spec_dir: Path, project_dir: Path) -> None:
        """If validate_result returns issues, bootstrap aborts."""
        bad_data: dict = {
            "project_name": "Bad",
            "summary": "Bad project",
            "stages": [{"stage_id": "S1", "name": "Core", "budget_usd": 50.0}],
            "tasks": [
                {
                    "task_id": "T-001",
                    "plan": "Do stuff",
                    "criteria": "Works",
                    "stage_id": "S99",  # unknown stage
                    "budget_usd": 5.0,
                    "dependencies": [],
                }
            ],
        }

        async def fake_decompose(text: str) -> tuple[dict, float, str]:
            return bad_data, 0.01, "test-model"

        with patch("orchestrator.web.bootstrap.decompose_spec", side_effect=fake_decompose):
            result = runner.invoke(app, ["bootstrap", str(spec_dir), "--yes"])

        assert result.exit_code == 1
        assert "validation issues" in result.output.lower() or "Issues" in result.output
