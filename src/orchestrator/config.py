"""Project configuration — frozen dataclasses loaded from YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from orchestrator.types import Phase, vendor_from_model


@dataclass(frozen=True)
class DocsConfig:
    """Paths to project documentation files."""

    spec: str = "docs/SPEC.md"
    rules: str = "docs/DEVELOPMENT_RULES.md"
    agent: str = "AGENT.md"
    tasks: str = "docs/TASKS.md"


@dataclass(frozen=True)
class CommandsConfig:
    """Shell commands for the target project."""

    install: str = ""
    test: str = ""
    lint: str = ""
    types: str = ""
    mutation: str = ""


@dataclass(frozen=True)
class BudgetsConfig:
    """Cost limits in USD."""

    per_task_usd: float = 5.0
    per_project_usd: float = 100.0


@dataclass(frozen=True)
class ModelsConfig:
    """Model assignments for developer and auditor roles."""

    developer: str = "claude-sonnet-4-6"
    developer_critical: str = "claude-opus-4-8"
    auditor: str = "google/gemini-3.1-pro-preview"
    auditor_gateway: str = "openrouter"


@dataclass(frozen=True)
class ProjectConfig:
    """Top-level project configuration (immutable after load)."""

    repo: str = ""
    main_branch: str = "main"
    phase: Phase = Phase.PROTOTYPE
    docs: DocsConfig = field(default_factory=DocsConfig)
    commands: CommandsConfig = field(default_factory=CommandsConfig)
    budgets: BudgetsConfig = field(default_factory=BudgetsConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    critical_modules: tuple[str, ...] = ()

    @classmethod
    def from_yaml(cls, path: str | Path) -> ProjectConfig:
        """Load config from a YAML file."""
        raw = Path(path).read_text(encoding="utf-8")
        return cls.from_dict(yaml.safe_load(raw) or {})

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectConfig:
        """Build config from a raw dictionary."""
        tp = data.get("target_project", data)

        docs = DocsConfig(**tp["docs"]) if "docs" in tp else DocsConfig()
        commands = CommandsConfig(**tp["commands"]) if "commands" in tp else CommandsConfig()
        budgets = BudgetsConfig(**tp["budgets"]) if "budgets" in tp else BudgetsConfig()

        models_raw = tp.get("models", {})
        models = ModelsConfig(
            developer=models_raw.get("developer", ModelsConfig.developer),
            developer_critical=models_raw.get(
                "developer_critical", ModelsConfig.developer_critical
            ),
            auditor=models_raw.get("auditor", ModelsConfig.auditor),
            auditor_gateway=models_raw.get("auditor_gateway", ModelsConfig.auditor_gateway),
        )

        critical = tp.get("critical_modules", [])

        return cls(
            repo=tp.get("repo", ""),
            main_branch=tp.get("main_branch", "main"),
            phase=Phase(tp["phase"]) if "phase" in tp else Phase.PROTOTYPE,
            docs=docs,
            commands=commands,
            budgets=budgets,
            models=models,
            critical_modules=tuple(critical),
        )

    def check_vendor_independence(self) -> list[str]:
        """Check that developer and auditor use different vendors.

        Returns a list of warning messages (empty = OK).
        """
        dev_vendor = vendor_from_model(self.models.developer)
        aud_vendor = vendor_from_model(self.models.auditor)
        if dev_vendor == aud_vendor:
            return [
                f"Audit independence violated: developer ({dev_vendor}) "
                f"and auditor ({aud_vendor}) are the same vendor"
            ]
        return []
