"""Tests for EvidencePack, TestResults, AuditorVerdict, parse_verdict."""

from __future__ import annotations

import json

from orchestrator.evidence import (
    AuditorVerdict,
    EvidencePack,
    TestResults,
    parse_verdict,
)
from orchestrator.types import AuditVerdict

# ── TestResults ──


class TestTestResults:
    def test_all_green(self) -> None:
        tr = TestResults(passed=10, failed=0, errors=0, coverage_pct=85.0)
        assert tr.all_green is True

    def test_not_green_failed(self) -> None:
        tr = TestResults(passed=9, failed=1, errors=0, coverage_pct=85.0)
        assert tr.all_green is False

    def test_not_green_errors(self) -> None:
        tr = TestResults(passed=10, failed=0, errors=1, coverage_pct=85.0)
        assert tr.all_green is False


# ── EvidencePack ──


def _green_tests() -> TestResults:
    return TestResults(passed=10, failed=0, errors=0, coverage_pct=90.0)


def _approve_verdict() -> AuditorVerdict:
    return AuditorVerdict(verdict=AuditVerdict.APPROVE, reasoning="All good")


def _reject_verdict() -> AuditorVerdict:
    return AuditorVerdict(verdict=AuditVerdict.REQUEST_CHANGES, reasoning="Fix tests")


class TestEvidencePack:
    def test_ready_for_pr_happy_path(self) -> None:
        ep = EvidencePack(
            task_id="T-001",
            diff="+ line",
            criteria="Must pass",
            test_results=_green_tests(),
            auditor_verdict=_approve_verdict(),
        )
        assert ep.is_ready_for_pr() is True

    def test_not_ready_no_tests(self) -> None:
        ep = EvidencePack(
            task_id="T-001",
            diff="+ line",
            criteria="Must pass",
            auditor_verdict=_approve_verdict(),
        )
        assert ep.is_ready_for_pr() is False

    def test_not_ready_failed_tests(self) -> None:
        tr = TestResults(passed=9, failed=1, errors=0, coverage_pct=80.0)
        ep = EvidencePack(
            task_id="T-001",
            diff="+ line",
            criteria="Must pass",
            test_results=tr,
            auditor_verdict=_approve_verdict(),
        )
        assert ep.is_ready_for_pr() is False

    def test_not_ready_no_verdict(self) -> None:
        ep = EvidencePack(
            task_id="T-001",
            diff="+ line",
            criteria="Must pass",
            test_results=_green_tests(),
        )
        assert ep.is_ready_for_pr() is False

    def test_not_ready_request_changes(self) -> None:
        ep = EvidencePack(
            task_id="T-001",
            diff="+ line",
            criteria="Must pass",
            test_results=_green_tests(),
            auditor_verdict=_reject_verdict(),
        )
        assert ep.is_ready_for_pr() is False

    def test_auditor_input_excludes_developer_log(self) -> None:
        """INV-3: auditor must not see developer reasoning."""
        ep = EvidencePack(
            task_id="T-001",
            diff="+ new code",
            criteria="Criteria here",
            test_results=_green_tests(),
            developer_log=[
                "thinking about approach X",
                "decided on Y",
            ],
        )
        auditor_input = ep.to_auditor_input()
        assert "new code" in auditor_input
        assert "Criteria here" in auditor_input
        assert "thinking about approach" not in auditor_input
        assert "decided on Y" not in auditor_input

    def test_auditor_input_includes_test_results(self) -> None:
        ep = EvidencePack(
            task_id="T-001",
            diff="diff",
            criteria="crit",
            test_results=_green_tests(),
        )
        out = ep.to_auditor_input()
        assert "Passed: 10" in out
        assert "Coverage: 90.0%" in out

    def test_to_json_roundtrip(self) -> None:
        ep = EvidencePack(
            task_id="T-001",
            diff="+ line",
            criteria="Must pass",
            test_results=_green_tests(),
            auditor_verdict=_approve_verdict(),
        )
        data = json.loads(ep.to_json())
        assert data["task_id"] == "T-001"
        assert data["test_results"]["passed"] == 10
        assert data["auditor_verdict"]["verdict"] == "approve"

    def test_to_json_minimal(self) -> None:
        ep = EvidencePack(task_id="T-001", diff="d", criteria="c")
        data = json.loads(ep.to_json())
        assert "test_results" not in data
        assert "auditor_verdict" not in data


# ── parse_verdict ──


class TestParseVerdict:
    def test_approve(self) -> None:
        v = parse_verdict("verdict: approve\nLooks good overall.")
        assert v.verdict is AuditVerdict.APPROVE

    def test_request_changes(self) -> None:
        v = parse_verdict("decision: request-changes\nFix the naming.")
        assert v.verdict is AuditVerdict.REQUEST_CHANGES

    def test_reject(self) -> None:
        v = parse_verdict("Verdict = reject\nFundamental flaw.")
        assert v.verdict is AuditVerdict.REJECT

    def test_case_insensitive(self) -> None:
        v = parse_verdict("VERDICT: APPROVE")
        assert v.verdict is AuditVerdict.APPROVE

    def test_underscore_variant(self) -> None:
        v = parse_verdict("verdict: request_changes")
        assert v.verdict is AuditVerdict.REQUEST_CHANGES

    def test_fallback_on_garbage(self) -> None:
        """Unknown input falls back to REQUEST_CHANGES (safe default)."""
        v = parse_verdict("I'm not sure what to say")
        assert v.verdict is AuditVerdict.REQUEST_CHANGES

    def test_raw_response_preserved(self) -> None:
        raw = "verdict: approve\nAll checks passed."
        v = parse_verdict(raw)
        assert v.raw_response == raw
        assert v.reasoning == raw
