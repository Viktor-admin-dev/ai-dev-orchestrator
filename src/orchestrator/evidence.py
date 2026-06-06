"""Evidence pack — artifacts collected during a task cycle."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from orchestrator.types import AuditVerdict

_VERDICT_PATTERN = re.compile(
    r"(?:verdict|decision)\s*[:=]\s*(approve|request[_-]changes|reject)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TestResults:
    """Results of running the project test suite."""

    passed: int
    failed: int
    errors: int
    coverage_pct: float
    raw_output: str = ""

    @property
    def all_green(self) -> bool:
        return self.failed == 0 and self.errors == 0


@dataclass(frozen=True)
class AuditorVerdict:
    """Structured auditor response."""

    verdict: AuditVerdict
    reasoning: str
    checklist: dict[str, bool] = field(default_factory=dict)
    raw_response: str = ""


@dataclass(frozen=True)
class EvidencePack:
    """Complete evidence for a task cycle (INV-1, INV-3)."""

    task_id: str
    diff: str
    criteria: str
    test_results: TestResults | None = None
    auditor_verdict: AuditorVerdict | None = None
    developer_log: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def is_ready_for_pr(self) -> bool:
        """INV-1: green tests AND auditor approve required for PR_READY."""
        if self.test_results is None or not self.test_results.all_green:
            return False
        if self.auditor_verdict is None:
            return False
        return self.auditor_verdict.verdict is AuditVerdict.APPROVE

    def to_auditor_input(self) -> str:
        """Build auditor prompt input — excludes developer reasoning (INV-3)."""
        parts = [
            "## Diff\n```\n" + self.diff + "\n```",
            "## Acceptance Criteria\n" + self.criteria,
        ]
        if self.test_results is not None:
            parts.append(
                f"## Test Results\nPassed: {self.test_results.passed}, "
                f"Failed: {self.test_results.failed}, Errors: {self.test_results.errors}, "
                f"Coverage: {self.test_results.coverage_pct}%"
            )
        return "\n\n".join(parts)

    def to_json(self) -> str:
        """Serialize to JSON for storage."""
        data: dict[str, object] = {
            "task_id": self.task_id,
            "diff": self.diff,
            "criteria": self.criteria,
            "timestamp": self.timestamp,
        }
        if self.test_results is not None:
            data["test_results"] = {
                "passed": self.test_results.passed,
                "failed": self.test_results.failed,
                "errors": self.test_results.errors,
                "coverage_pct": self.test_results.coverage_pct,
            }
        if self.auditor_verdict is not None:
            data["auditor_verdict"] = {
                "verdict": self.auditor_verdict.verdict.value,
                "reasoning": self.auditor_verdict.reasoning,
                "checklist": self.auditor_verdict.checklist,
            }
        return json.dumps(data, ensure_ascii=False, indent=2)


def parse_verdict(raw: str) -> AuditorVerdict:
    """Parse raw auditor text into a structured verdict.

    Falls back to REQUEST_CHANGES if parsing fails — safe default.
    """
    match = _VERDICT_PATTERN.search(raw)
    if match:
        value = match.group(1).lower().replace("_", "-")
        try:
            verdict = AuditVerdict(value)
        except ValueError:
            verdict = AuditVerdict.REQUEST_CHANGES
    else:
        verdict = AuditVerdict.REQUEST_CHANGES

    return AuditorVerdict(
        verdict=verdict,
        reasoning=raw,
        raw_response=raw,
    )
