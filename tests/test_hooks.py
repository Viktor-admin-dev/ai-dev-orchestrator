"""Tests for executor hooks."""

from __future__ import annotations

from typing import Any

from orchestrator.executor.hooks import (
    ToolLog,
    branch_guard,
    make_tool_logger,
    readonly_bash,
)

_CTX: Any = {"signal": None}


def _bash_input(command: str) -> Any:
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "hook_event_name": "PreToolUse",
        "session_id": "test",
        "cwd": "/tmp",
    }


def _non_bash_input() -> Any:
    return {
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/x.py"},
        "hook_event_name": "PreToolUse",
        "session_id": "test",
        "cwd": "/tmp",
    }


def _is_denied(result: dict[str, Any]) -> bool:
    hook_out = result.get("hookSpecificOutput")
    if not isinstance(hook_out, dict):
        return False
    return hook_out.get("permissionDecision") == "deny"


# ── branch_guard ──


class TestBranchGuard:
    async def test_blocks_push_main(self) -> None:
        r = await branch_guard(_bash_input("git push origin main"), None, _CTX)
        assert _is_denied(r)

    async def test_blocks_push_master(self) -> None:
        r = await branch_guard(_bash_input("git push origin master"), None, _CTX)
        assert _is_denied(r)

    async def test_allows_push_feature(self) -> None:
        r = await branch_guard(_bash_input("git push origin feat/cool"), None, _CTX)
        assert not _is_denied(r)

    async def test_allows_git_log_main(self) -> None:
        r = await branch_guard(_bash_input("git log main"), None, _CTX)
        assert not _is_denied(r)

    async def test_allows_git_diff_main(self) -> None:
        r = await branch_guard(_bash_input("git diff main...HEAD"), None, _CTX)
        assert not _is_denied(r)

    async def test_ignores_non_bash(self) -> None:
        r = await branch_guard(_non_bash_input(), None, _CTX)
        assert not _is_denied(r)

    async def test_blocks_checkout_main(self) -> None:
        r = await branch_guard(_bash_input("git checkout main"), None, _CTX)
        assert _is_denied(r)

    async def test_blocks_switch_main(self) -> None:
        r = await branch_guard(_bash_input("git switch main"), None, _CTX)
        assert _is_denied(r)

    async def test_allows_checkout_branch(self) -> None:
        r = await branch_guard(_bash_input("git checkout -b feat/new"), None, _CTX)
        assert not _is_denied(r)


# ── readonly_bash ──


class TestReadonlyBash:
    async def test_blocks_rm_rf(self) -> None:
        r = await readonly_bash(_bash_input("rm -rf /tmp/foo"), None, _CTX)
        assert _is_denied(r)

    async def test_blocks_git_push(self) -> None:
        r = await readonly_bash(_bash_input("git push origin feat"), None, _CTX)
        assert _is_denied(r)

    async def test_blocks_git_commit(self) -> None:
        r = await readonly_bash(_bash_input("git commit -m 'msg'"), None, _CTX)
        assert _is_denied(r)

    async def test_blocks_sudo(self) -> None:
        r = await readonly_bash(_bash_input("sudo rm file"), None, _CTX)
        assert _is_denied(r)

    async def test_allows_cat(self) -> None:
        r = await readonly_bash(_bash_input("cat /tmp/file.py"), None, _CTX)
        assert not _is_denied(r)

    async def test_allows_ls(self) -> None:
        r = await readonly_bash(_bash_input("ls -la"), None, _CTX)
        assert not _is_denied(r)

    async def test_allows_pytest(self) -> None:
        r = await readonly_bash(_bash_input("pytest --cov"), None, _CTX)
        assert not _is_denied(r)

    async def test_allows_grep(self) -> None:
        r = await readonly_bash(_bash_input("grep -r 'pattern' src/"), None, _CTX)
        assert not _is_denied(r)

    async def test_ignores_non_bash(self) -> None:
        r = await readonly_bash(_non_bash_input(), None, _CTX)
        assert not _is_denied(r)


# ── tool_logger ──


def _tool_input(name: str) -> Any:
    return {
        "tool_name": name,
        "tool_input": {},
        "session_id": "t",
        "cwd": "/",
    }


class TestToolLogger:
    async def test_logs_tool_calls(self) -> None:
        log = ToolLog()
        logger_fn = make_tool_logger(log)
        await logger_fn(_tool_input("Read"), "id-1", _CTX)
        assert len(log.entries) == 1
        assert log.entries[0]["tool"] == "Read"

    async def test_multiple_calls(self) -> None:
        log = ToolLog()
        logger_fn = make_tool_logger(log)
        await logger_fn(_tool_input("Read"), None, _CTX)
        await logger_fn(_tool_input("Edit"), None, _CTX)
        assert len(log.entries) == 2

    async def test_returns_allow(self) -> None:
        log = ToolLog()
        logger_fn = make_tool_logger(log)
        result = await logger_fn(_tool_input("Bash"), None, _CTX)
        assert not _is_denied(result)
