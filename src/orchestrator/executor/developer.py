"""DeveloperExecutor — Claude Code as the developer agent."""

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

from orchestrator.executor.base import ExecutionResult
from orchestrator.executor.hooks import ToolLog, branch_guard, make_tool_logger

if TYPE_CHECKING:
    from orchestrator.config import ProjectConfig

_DEVELOPER_SYSTEM_PROMPT = """\
You are a senior Python developer working on a task.
Follow the project's DEVELOPMENT_RULES strictly.
Work only in your feature branch — never push to main.
Write tests alongside code. Run lint/types/tests before finishing.
"""


class DeveloperExecutor:
    """Runs Claude Code as the developer agent via Agent SDK query()."""

    def __init__(self, config: ProjectConfig) -> None:
        self._config = config
        self._tool_log = ToolLog()
        self._critical = False

    def set_critical(self, flag: bool) -> None:
        """Select the model tier for the next execute() call.

        When *flag* is True the executor uses ``models.developer_critical``
        (e.g. Opus); otherwise the default ``models.developer`` (Sonnet).
        The flag is automatically reset to False after execute().
        """
        self._critical = flag

    @property
    def tool_log(self) -> ToolLog:
        return self._tool_log

    @property
    def _active_model(self) -> str:
        if self._critical:
            return self._config.models.developer_critical
        return self._config.models.developer

    def _build_options(self) -> ClaudeAgentOptions:
        logger_fn = make_tool_logger(self._tool_log)
        return ClaudeAgentOptions(
            model=self._active_model,
            system_prompt=_DEVELOPER_SYSTEM_PROMPT,
            allowed_tools=["Read", "Edit", "Write", "Bash", "Glob", "Grep"],
            permission_mode="acceptEdits",
            cwd=self._config.repo or ".",
            max_budget_usd=self._config.budgets.per_task_usd,
            hooks={
                "PreToolUse": [
                    HookMatcher(matcher="Bash", hooks=[branch_guard]),
                ],
                "PostToolUse": [
                    HookMatcher(hooks=[logger_fn]),
                ],
            },
        )

    async def execute(self, prompt: str, task_id: str) -> ExecutionResult:
        """Run the developer agent and collect results."""
        options = self._build_options()
        model = self._active_model
        self._critical = False  # reset after building options

        output_parts: list[str] = []
        cost_usd = 0.0
        input_tokens = 0
        output_tokens = 0
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
