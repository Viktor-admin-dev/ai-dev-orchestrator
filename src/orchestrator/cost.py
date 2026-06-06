"""Cost tracking with budget enforcement (INV-5)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_WARNING_THRESHOLD = 0.8  # 80%


class BudgetExceededError(RuntimeError):
    """Raised when cumulative cost reaches the budget limit."""

    def __init__(self, spent: float, budget: float) -> None:
        self.spent = spent
        self.budget = budget
        super().__init__(f"Budget exceeded: ${spent:.4f} / ${budget:.2f}")


@dataclass
class CostEntry:
    """Single cost record."""

    task_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class CostTracker:
    """Accumulates costs and enforces budget limits.

    Raises ``BudgetExceededError`` when the budget is hit.
    Logs a warning at 80%.
    """

    budget_usd: float
    _entries: list[CostEntry] = field(default_factory=list, repr=False)
    _total_usd: float = field(default=0.0, repr=False)
    _warning_fired: bool = field(default=False, repr=False)

    @property
    def total_usd(self) -> float:
        return self._total_usd

    @property
    def entries(self) -> list[CostEntry]:
        return list(self._entries)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.budget_usd - self._total_usd)

    def record(self, entry: CostEntry) -> None:
        """Record a cost entry, enforcing budget."""
        self._entries.append(entry)
        self._total_usd += entry.cost_usd

        if self._total_usd >= self.budget_usd:
            raise BudgetExceededError(self._total_usd, self.budget_usd)

        if not self._warning_fired and self._total_usd >= self.budget_usd * _WARNING_THRESHOLD:
            self._warning_fired = True
            logger.warning(
                "Budget warning: $%.4f / $%.2f (%.0f%%)",
                self._total_usd,
                self.budget_usd,
                (self._total_usd / self.budget_usd) * 100,
            )

    def summary(self) -> dict[str, float]:
        """Return per-model cost breakdown."""
        by_model: dict[str, float] = {}
        for e in self._entries:
            by_model[e.model] = by_model.get(e.model, 0.0) + e.cost_usd
        return by_model
