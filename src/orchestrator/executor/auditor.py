"""AuditorExecutor — independent code auditor on a different model."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    query,
)

from orchestrator.evidence import EvidencePack, parse_verdict
from orchestrator.executor.base import ExecutionResult
from orchestrator.executor.hooks import ToolLog, make_tool_logger, readonly_bash

if TYPE_CHECKING:
    from orchestrator.config import ProjectConfig

_AUDITOR_SYSTEM_PROMPT = """\
You are an adversarial code auditor. Your job is to find problems.
You receive: a diff, acceptance criteria, and test results.
You do NOT receive the developer's reasoning (INV-3).

Review the code against the checklist (§6.3):
A. Correctness — does it match the criteria?
B. Invariants — are business invariants preserved?
C. Quality — no anti-patterns from §4.2?
D. Tests — green, sufficient coverage, happy+edge+error?
E. Documentation — docstrings, ADR if needed?
F. Git hygiene — conventional commits, atomic?

Output your verdict in this exact format:
verdict: approve | request-changes | reject
<reasoning>
"""


class AuditorExecutor:
    """Runs the auditor agent (different model, read-only)."""

    def __init__(self, config: ProjectConfig) -> None:
        self._config = config
        self._tool_log = ToolLog()

    @property
    def tool_log(self) -> ToolLog:
        return self._tool_log

    def _build_options(self) -> ClaudeAgentOptions:
        logger_fn = make_tool_logger(self._tool_log)
        return ClaudeAgentOptions(
            model=self._config.models.auditor.value,
            system_prompt=_AUDITOR_SYSTEM_PROMPT,
            allowed_tools=["Read", "Glob", "Grep", "Bash"],
            permission_mode="acceptEdits",
            cwd=self._config.repo or ".",
            max_budget_usd=self._config.budgets.per_task_usd * 0.3,
            hooks={
                "PreToolUse": [
                    HookMatcher(matcher="Bash", hooks=[readonly_bash]),
                ],
                "PostToolUse": [
                    HookMatcher(hooks=[logger_fn]),
                ],
            },
        )

    async def execute(self, prompt: str, task_id: str) -> ExecutionResult:
        """Run the auditor agent. Prompt should come from EvidencePack.to_auditor_input()."""
        options = self._build_options()
        output_parts: list[str] = []
        cost_usd = 0.0
        input_tokens = 0
        output_tokens = 0
        model = self._config.models.auditor.value
        duration_ms = 0
        success = False

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        output_parts.append(block.text)
                usage: dict[str, Any] = message.usage or {}
                input_tokens += usage.get("input_tokens", 0)
                output_tokens += usage.get("output_tokens", 0)
            elif isinstance(message, ResultMessage):
                cost_usd = message.total_cost_usd or 0.0
                duration_ms = message.duration_ms
                success = message.subtype == "success"

        return ExecutionResult(
            task_id=task_id,
            success=success,
            output="\n".join(output_parts),
            cost_usd=cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            tool_calls=[dict(e) for e in self._tool_log.entries],
            duration_ms=duration_ms,
        )

    async def audit(self, evidence: EvidencePack) -> ExecutionResult:
        """Convenience: build prompt from evidence and execute.

        INV-3 enforced: uses to_auditor_input() which excludes developer reasoning.
        """
        prompt = evidence.to_auditor_input()
        return await self.execute(prompt=prompt, task_id=evidence.task_id)

    @staticmethod
    def parse_result(result: ExecutionResult) -> Any:
        """Parse the auditor's output into a structured AuditorVerdict."""
        return parse_verdict(result.output)
