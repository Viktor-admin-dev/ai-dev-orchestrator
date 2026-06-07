"""Tests for types and config modules."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from orchestrator.config import (
    BudgetsConfig,
    CommandsConfig,
    DocsConfig,
    ModelsConfig,
    ProjectConfig,
)
from orchestrator.types import AuditVerdict, ModelId, Phase, vendor_from_model

if TYPE_CHECKING:
    from pathlib import Path


# ── types ──


class TestPhase:
    def test_values(self) -> None:
        assert Phase.PROTOTYPE.value == "prototype"
        assert Phase.MVP.value == "mvp"
        assert Phase.PRODUCTION.value == "production"

    def test_from_string(self) -> None:
        assert Phase("mvp") is Phase.MVP


class TestAuditVerdict:
    def test_values(self) -> None:
        assert AuditVerdict.APPROVE.value == "approve"
        assert AuditVerdict.REQUEST_CHANGES.value == "request-changes"
        assert AuditVerdict.REJECT.value == "reject"


class TestModelId:
    def test_sonnet(self) -> None:
        assert "sonnet" in ModelId.SONNET.value

    def test_opus(self) -> None:
        assert "opus" in ModelId.OPUS.value


# ── config defaults ──


class TestDocsConfig:
    def test_defaults(self) -> None:
        d = DocsConfig()
        assert d.spec == "docs/SPEC.md"
        assert d.agent == "AGENT.md"

    def test_frozen(self) -> None:
        d = DocsConfig()
        with pytest.raises(AttributeError):
            d.spec = "other"  # type: ignore[misc]


class TestCommandsConfig:
    def test_defaults_empty(self) -> None:
        c = CommandsConfig()
        assert c.test == ""


class TestBudgetsConfig:
    def test_defaults(self) -> None:
        b = BudgetsConfig()
        assert b.per_task_usd == 5.0
        assert b.per_project_usd == 100.0


class TestModelsConfig:
    def test_defaults(self) -> None:
        m = ModelsConfig()
        assert m.developer == "claude-sonnet-4-6"
        assert m.developer_critical == "claude-opus-4-8"
        assert m.auditor == "google/gemini-3.1-pro-preview"
        assert m.auditor_gateway == "openrouter"


# ── ProjectConfig ──


class TestProjectConfig:
    def test_defaults(self) -> None:
        cfg = ProjectConfig()
        assert cfg.main_branch == "main"
        assert cfg.phase is Phase.PROTOTYPE
        assert cfg.critical_modules == ()

    def test_frozen(self) -> None:
        cfg = ProjectConfig()
        with pytest.raises(AttributeError):
            cfg.repo = "x"  # type: ignore[misc]

    def test_from_dict_minimal(self) -> None:
        cfg = ProjectConfig.from_dict({})
        assert cfg.repo == ""
        assert cfg.phase is Phase.PROTOTYPE

    def test_from_dict_full(self) -> None:
        data = {
            "target_project": {
                "repo": "/tmp/my-project",
                "main_branch": "develop",
                "phase": "mvp",
                "docs": {
                    "spec": "SPEC.md",
                    "rules": "RULES.md",
                    "agent": "AI.md",
                    "tasks": "TASKS.md",
                },
                "commands": {
                    "install": "pip install -e .",
                    "test": "pytest",
                    "lint": "ruff check .",
                    "types": "mypy src/",
                    "mutation": "mutmut run",
                },
                "budgets": {
                    "per_task_usd": 2.0,
                    "per_project_usd": 50.0,
                },
                "models": {
                    "developer": "claude-sonnet-4-6",
                    "developer_critical": "claude-opus-4-8",
                    "auditor": "google/gemini-3.1-pro-preview",
                    "auditor_gateway": "openrouter",
                },
                "critical_modules": ["auth", "billing"],
            }
        }
        cfg = ProjectConfig.from_dict(data)
        assert cfg.repo == "/tmp/my-project"
        assert cfg.main_branch == "develop"
        assert cfg.phase is Phase.MVP
        assert cfg.docs.spec == "SPEC.md"
        assert cfg.commands.test == "pytest"
        assert cfg.budgets.per_task_usd == 2.0
        assert cfg.models.developer == "claude-sonnet-4-6"
        assert cfg.models.auditor == "google/gemini-3.1-pro-preview"
        assert cfg.models.auditor_gateway == "openrouter"
        assert cfg.critical_modules == ("auth", "billing")

    def test_from_yaml(self, tmp_path: Path) -> None:
        yaml_content = textwrap.dedent("""\
            target_project:
              repo: /tmp/test
              phase: production
              budgets:
                per_task_usd: 10.0
                per_project_usd: 200.0
        """)
        p = tmp_path / "config.yaml"
        p.write_text(yaml_content)
        cfg = ProjectConfig.from_yaml(p)
        assert cfg.repo == "/tmp/test"
        assert cfg.phase is Phase.PRODUCTION
        assert cfg.budgets.per_task_usd == 10.0

    def test_from_yaml_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text("")
        cfg = ProjectConfig.from_yaml(p)
        assert cfg.repo == ""

    def test_from_dict_without_target_project_wrapper(self) -> None:
        """Config dict without the target_project key should also work."""
        data = {"repo": "/direct", "phase": "mvp"}
        cfg = ProjectConfig.from_dict(data)
        assert cfg.repo == "/direct"
        assert cfg.phase is Phase.MVP

    def test_from_dict_backward_compat_no_gateway(self) -> None:
        """Old-style config without gateway fields still works."""
        data = {
            "models": {
                "developer": "claude-sonnet-4-5",
                "auditor": "claude-opus-4-6",
            }
        }
        cfg = ProjectConfig.from_dict(data)
        assert cfg.models.developer == "claude-sonnet-4-5"
        assert cfg.models.auditor == "claude-opus-4-6"
        assert cfg.models.auditor_gateway == "openrouter"  # default

    def test_from_yaml_new_format(self, tmp_path: Path) -> None:
        yaml_content = textwrap.dedent("""\
            target_project:
              repo: /tmp/test
              models:
                developer: claude-sonnet-4-6
                developer_critical: claude-opus-4-8
                auditor: google/gemini-3.1-pro-preview
                auditor_gateway: openrouter
        """)
        p = tmp_path / "new_config.yaml"
        p.write_text(yaml_content)
        cfg = ProjectConfig.from_yaml(p)
        assert cfg.models.developer == "claude-sonnet-4-6"
        assert cfg.models.developer_critical == "claude-opus-4-8"
        assert cfg.models.auditor == "google/gemini-3.1-pro-preview"
        assert cfg.models.auditor_gateway == "openrouter"


# ── vendor_from_model ──


class TestVendorFromModel:
    def test_openrouter_format(self) -> None:
        assert vendor_from_model("google/gemini-3.1-pro-preview") == "google"

    def test_anthropic_openrouter(self) -> None:
        assert vendor_from_model("anthropic/claude-sonnet-4.6") == "anthropic"

    def test_bare_claude_id(self) -> None:
        assert vendor_from_model("claude-sonnet-4-6") == "anthropic"

    def test_unknown(self) -> None:
        assert vendor_from_model("llama-3") == "unknown"


# ── check_vendor_independence ──


class TestVendorIndependence:
    def test_different_vendors_ok(self) -> None:
        cfg = ProjectConfig.from_dict(
            {
                "models": {
                    "developer": "claude-sonnet-4-6",
                    "auditor": "google/gemini-3.1-pro-preview",
                }
            }
        )
        assert cfg.check_vendor_independence() == []

    def test_same_vendor_warns(self) -> None:
        cfg = ProjectConfig.from_dict(
            {
                "models": {
                    "developer": "claude-sonnet-4-6",
                    "auditor": "claude-opus-4-6",
                }
            }
        )
        warnings = cfg.check_vendor_independence()
        assert len(warnings) == 1
        assert "anthropic" in warnings[0]

    def test_same_vendor_openrouter_format(self) -> None:
        cfg = ProjectConfig.from_dict(
            {
                "models": {
                    "developer": "anthropic/claude-sonnet-4.6",
                    "auditor": "anthropic/claude-opus-4.8",
                }
            }
        )
        warnings = cfg.check_vendor_independence()
        assert len(warnings) == 1
