"""Tests for CostTracker and budget enforcement."""

from __future__ import annotations

import logging

import pytest

from orchestrator.cost import BudgetExceededError, CostEntry, CostTracker


def _entry(task: str = "T-001", model: str = "sonnet", cost: float = 0.1) -> CostEntry:
    return CostEntry(task_id=task, model=model, input_tokens=1000, output_tokens=500, cost_usd=cost)


class TestCostTracker:
    def test_initial_state(self) -> None:
        ct = CostTracker(budget_usd=10.0)
        assert ct.total_usd == 0.0
        assert ct.remaining_usd == 10.0
        assert ct.entries == []

    def test_record_accumulates(self) -> None:
        ct = CostTracker(budget_usd=10.0)
        ct.record(_entry(cost=1.0))
        ct.record(_entry(cost=2.0))
        assert ct.total_usd == pytest.approx(3.0)
        assert ct.remaining_usd == pytest.approx(7.0)
        assert len(ct.entries) == 2

    def test_budget_exceeded_raises(self) -> None:
        ct = CostTracker(budget_usd=1.0)
        with pytest.raises(BudgetExceededError) as exc_info:
            ct.record(_entry(cost=1.5))
        assert exc_info.value.spent == pytest.approx(1.5)
        assert exc_info.value.budget == 1.0

    def test_budget_exact_raises(self) -> None:
        ct = CostTracker(budget_usd=1.0)
        with pytest.raises(BudgetExceededError):
            ct.record(_entry(cost=1.0))

    def test_warning_at_80_percent(self, caplog: pytest.LogCaptureFixture) -> None:
        ct = CostTracker(budget_usd=10.0)
        with caplog.at_level(logging.WARNING):
            ct.record(_entry(cost=8.0))
        assert "Budget warning" in caplog.text
        assert "80%" in caplog.text

    def test_warning_fires_once(self, caplog: pytest.LogCaptureFixture) -> None:
        ct = CostTracker(budget_usd=10.0)
        with caplog.at_level(logging.WARNING):
            ct.record(_entry(cost=8.0))
            ct.record(_entry(cost=1.0))
        assert caplog.text.count("Budget warning") == 1

    def test_no_warning_below_threshold(self, caplog: pytest.LogCaptureFixture) -> None:
        ct = CostTracker(budget_usd=10.0)
        with caplog.at_level(logging.WARNING):
            ct.record(_entry(cost=7.9))
        assert "Budget warning" not in caplog.text

    def test_summary_by_model(self) -> None:
        ct = CostTracker(budget_usd=100.0)
        ct.record(_entry(model="sonnet", cost=1.0))
        ct.record(_entry(model="opus", cost=3.0))
        ct.record(_entry(model="sonnet", cost=2.0))
        s = ct.summary()
        assert s["sonnet"] == pytest.approx(3.0)
        assert s["opus"] == pytest.approx(3.0)

    def test_entries_returns_copy(self) -> None:
        ct = CostTracker(budget_usd=100.0)
        ct.record(_entry(cost=1.0))
        entries = ct.entries
        entries.clear()
        assert len(ct.entries) == 1


class TestBudgetExceededError:
    def test_message(self) -> None:
        err = BudgetExceededError(spent=5.1234, budget=5.0)
        assert "$5.1234" in str(err)
        assert "$5.00" in str(err)
