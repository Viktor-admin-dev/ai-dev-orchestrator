"""ExecutorAdapter protocol and ExecutionResult."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ExecutionResult:
    """Result of an executor run."""

    task_id: str
    success: bool
    output: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    tool_calls: list[dict[str, str]] = field(default_factory=list)
    duration_ms: int = 0


@runtime_checkable
class ExecutorAdapter(Protocol):
    """Protocol for executor adapters.

    Any class implementing ``async execute(prompt, task_id) -> ExecutionResult``
    is a valid adapter. This allows swapping Claude for OpenAI Codex later
    without changing the orchestrator interface.
    """

    async def execute(self, prompt: str, task_id: str) -> ExecutionResult: ...
