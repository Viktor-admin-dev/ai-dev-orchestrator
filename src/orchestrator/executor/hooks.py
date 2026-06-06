"""Agent SDK hooks for enforcement.

- branch_guard: blocks git push/commit to main (INV-2)
- tool_logger: records all tool invocations
- readonly_bash: blocks destructive bash commands (for auditor)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk.types import (
    HookContext,
    HookInput,
    HookJSONOutput,
    PreToolUseHookSpecificOutput,
    SyncHookJSONOutput,
)

logger = logging.getLogger(__name__)

# Patterns for branch_guard: git push targeting main
_GIT_PUSH_MAIN = re.compile(
    r"\bgit\s+push\b.*\b(main|master)\b",
    re.IGNORECASE,
)
_GIT_CHECKOUT_MAIN = re.compile(
    r"\bgit\s+(checkout|switch)\s+(main|master)\b",
    re.IGNORECASE,
)

# Destructive commands the auditor must never run
_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\brm\s+(-[a-zA-Z]*f|-[a-zA-Z]*r)\b"),
    re.compile(
        r"\bgit\s+(push|commit|merge|rebase|reset|checkout\s+--)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(mv|cp)\s+.*--force\b"),
    re.compile(r"\bchmod\b"),
    re.compile(r"\bchown\b"),
    re.compile(r"\bkill\b"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bdd\s+"),
]


def _deny(reason: str) -> SyncHookJSONOutput:
    """Build a deny response for a PreToolUse hook."""
    return SyncHookJSONOutput(
        hookSpecificOutput=PreToolUseHookSpecificOutput(
            hookEventName="PreToolUse",
            permissionDecision="deny",
            permissionDecisionReason=reason,
        ),
    )


def _allow() -> SyncHookJSONOutput:
    return SyncHookJSONOutput()


def _get_command(input_data: HookInput) -> str:
    """Extract bash command from hook input, returning empty if not Bash."""
    if input_data.get("tool_name") != "Bash":
        return ""
    raw = input_data.get("tool_input", {})
    if isinstance(raw, dict):
        cmd = raw.get("command", "")
        return str(cmd)
    return ""


@dataclass
class ToolLog:
    """Accumulated tool call log."""

    entries: list[dict[str, str]] = field(default_factory=list)


async def branch_guard(
    input_data: HookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> HookJSONOutput:
    """PreToolUse hook: block git push/commit to main (INV-2).

    Allows read-only git commands like git log main, git diff main.
    """
    command = _get_command(input_data)
    if not command:
        return _allow()

    if _GIT_PUSH_MAIN.search(command):
        logger.warning("branch_guard blocked: %s", command)
        return _deny("INV-2: push to main/master is forbidden")

    if _GIT_CHECKOUT_MAIN.search(command):
        logger.warning("branch_guard blocked checkout to main: %s", command)
        return _deny(
            "INV-2: checkout to main/master is forbidden during development",
        )

    return _allow()


async def readonly_bash(
    input_data: HookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> HookJSONOutput:
    """PreToolUse hook: block destructive bash commands (for auditor)."""
    command = _get_command(input_data)
    if not command:
        return _allow()

    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            logger.warning("readonly_bash blocked: %s", command)
            return _deny(
                "Auditor read-only mode: destructive command blocked",
            )

    return _allow()


def make_tool_logger(log: ToolLog | None = None) -> Any:
    """Create a PostToolUse hook that records all tool invocations."""
    if log is None:
        log = ToolLog()

    async def tool_logger(
        input_data: HookInput,
        tool_use_id: str | None,
        context: HookContext,
    ) -> HookJSONOutput:
        tool_name = str(input_data.get("tool_name", "unknown"))
        tool_input = str(input_data.get("tool_input", {}))
        entry: dict[str, str] = {
            "tool": tool_name,
            "input": tool_input,
        }
        assert log is not None
        log.entries.append(entry)
        logger.debug("tool_logger: %s", entry)
        return _allow()

    tool_logger._log = log  # type: ignore[attr-defined]
    return tool_logger
