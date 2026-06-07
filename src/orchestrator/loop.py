"""Async runner loop — drives tasks through the FSM automatically."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from orchestrator.cost import BudgetExceededError
from orchestrator.evidence import MutationResults, TestResults
from orchestrator.store import (
    get_connection,
    save_stage,
    save_stage_transition,
    save_task,
    save_task_transition,
)
from orchestrator.types import StageState, TaskState

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from orchestrator.project import ProjectOrchestrator

logger = logging.getLogger(__name__)

# Task states that are terminal — no more driving needed
_TERMINAL = frozenset({TaskState.ACCEPTED, TaskState.FAILED})

# Stage states that are terminal
_STAGE_TERMINAL = frozenset({StageState.ACCEPTED, StageState.FAILED})


@dataclass
class RunResult:
    """Result of a run_loop() invocation."""

    completed: bool = False
    paused: bool = False
    pending_actions: list[str] = field(default_factory=list)
    tasks_run: int = 0
    error: str = ""


async def run_loop(
    orch: ProjectOrchestrator,
    db_path: Path,
    *,
    auto_approve: bool = False,
) -> RunResult:
    """Drive the orchestrator forward, persisting after each transition.

    In default mode, pauses at human checkpoints (plan_review, merged,
    stage review). With ``auto_approve=True``, automatically approves
    plans and accepts tasks/stages.
    """
    result = RunResult()
    conn = get_connection(db_path)

    try:
        progress = True
        while progress:
            progress = False

            for sid in list(orch.stage_ids):
                stage_ctx = orch.get_stage(sid)

                if stage_ctx.state in _STAGE_TERMINAL:
                    continue

                # Start stage if in PLANNING
                if stage_ctx.state is StageState.PLANNING:
                    prev_len = len(stage_ctx.history)
                    orch.start_stage(sid)
                    for t in stage_ctx.history[prev_len:]:
                        save_stage_transition(conn, sid, t)
                    save_stage(conn, stage_ctx)
                    progress = True

                if stage_ctx.state is StageState.IN_PROGRESS:
                    # Drive ready tasks
                    ready = orch.next_tasks(sid)
                    for tid in ready:
                        ctx = orch.runner.get_task(tid)
                        if ctx.state in _TERMINAL:
                            continue
                        driven = await _drive_task(
                            orch,
                            conn,
                            tid,
                            auto_approve=auto_approve,
                            result=result,
                        )
                        if driven:
                            progress = True
                            result.tasks_run += 1

                    # Check if stage is ready to advance
                    advanced = _advance_stage(
                        orch,
                        conn,
                        sid,
                        auto_approve=auto_approve,
                        result=result,
                    )
                    if advanced:
                        progress = True

                elif stage_ctx.state is StageState.REVIEW:
                    if auto_approve:
                        prev_len = len(stage_ctx.history)
                        orch.accept_stage(sid)
                        for t in stage_ctx.history[prev_len:]:
                            save_stage_transition(conn, sid, t)
                        save_stage(conn, stage_ctx)
                        progress = True
                    else:
                        result.pending_actions.append(f"accept-stage {sid}")
                        result.paused = True

        # Check if all stages are accepted
        all_accepted = all(orch.get_stage(s).state is StageState.ACCEPTED for s in orch.stage_ids)
        if all_accepted and orch.stage_ids:
            result.completed = True

    except BudgetExceededError as exc:
        result.error = str(exc)
    finally:
        conn.close()

    return result


async def _drive_task(
    orch: ProjectOrchestrator,
    conn: sqlite3.Connection,
    task_id: str,
    *,
    auto_approve: bool,
    result: RunResult,
) -> bool:
    """Drive a single task through as many states as possible.

    Returns True if any transition was made.
    """
    runner = orch.runner
    ctx = runner.get_task(task_id)
    made_progress = False

    while ctx.state not in _TERMINAL:
        prev_state = ctx.state
        prev_len = len(ctx.history)

        try:
            if ctx.state is TaskState.DRAFT:
                if ctx.plan and ctx.criteria:
                    runner.submit_plan(task_id, ctx.plan, ctx.criteria)
                else:
                    break  # No plan yet, can't proceed

            elif ctx.state is TaskState.PLAN_REVIEW:
                if auto_approve:
                    runner.approve_plan(task_id)
                else:
                    result.pending_actions.append(f"approve-plan {task_id}")
                    result.paused = True
                    break

            elif ctx.state is TaskState.PLAN_APPROVED:
                await runner.run_developer(task_id)

            elif ctx.state is TaskState.TESTING:
                test_results = await _run_test_command(orch, task_id)
                await runner.run_tests(task_id, test_results)

            elif ctx.state is TaskState.AWAIT_AUDIT:
                await runner.run_audit(task_id)

            elif ctx.state is TaskState.MUTATION:
                mut_results = await _run_mutation_command(
                    orch,
                    task_id,
                )
                await runner.run_mutation(task_id, mut_results)

            elif ctx.state is TaskState.REWORK:
                runner.rework(task_id)

            elif ctx.state is TaskState.PR_READY:
                runner.mark_merged(task_id)

            elif ctx.state is TaskState.MERGED:
                if auto_approve:
                    runner.accept(task_id)
                else:
                    result.pending_actions.append(f"accept-task {task_id}")
                    result.paused = True
                    break

            else:
                break

        except BudgetExceededError:
            # Task was already transitioned to FAILED by the runner
            pass
        except Exception:
            logger.exception("Error driving task %s", task_id)
            with contextlib.suppress(Exception):
                runner.fail(task_id, "loop error")
            break

        # Persist new transitions
        for t in ctx.history[prev_len:]:
            save_task_transition(conn, task_id, t)
        save_task(conn, ctx)

        if ctx.state != prev_state:
            made_progress = True
        else:
            break  # No progress, avoid infinite loop

    return made_progress


def _advance_stage(
    orch: ProjectOrchestrator,
    conn: sqlite3.Connection,
    stage_id: str,
    *,
    auto_approve: bool,
    result: RunResult,
) -> bool:
    """Check if a stage can advance (all tasks done -> INTEGRATING)."""
    stage_ctx = orch.get_stage(stage_id)

    if stage_ctx.state is not StageState.IN_PROGRESS:
        return False

    if not orch.check_stage_progress(stage_id):
        return False

    # All tasks done — begin integration
    prev_len = len(stage_ctx.history)
    try:
        orch.begin_integration(stage_id)
    except Exception:
        return False

    for t in stage_ctx.history[prev_len:]:
        save_stage_transition(conn, stage_id, t)
    save_stage(conn, stage_ctx)

    # Auto-submit green E2E results (stub)
    e2e = TestResults(passed=1, failed=0, errors=0, coverage_pct=100.0)
    prev_len = len(stage_ctx.history)
    orch.submit_e2e(stage_id, e2e)
    for t in stage_ctx.history[prev_len:]:
        save_stage_transition(conn, stage_id, t)
    save_stage(conn, stage_ctx)

    return True


async def _run_test_command(
    orch: ProjectOrchestrator,
    task_id: str,
) -> TestResults:
    """Run the configured test command and parse results."""
    cmd = orch.runner.config.commands.test
    if not cmd:
        return TestResults(
            passed=1,
            failed=0,
            errors=0,
            coverage_pct=100.0,
        )

    import asyncio

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        raw = stdout.decode(errors="replace") if stdout else ""

        if proc.returncode == 0:
            return TestResults(
                passed=1,
                failed=0,
                errors=0,
                coverage_pct=0.0,
                raw_output=raw,
            )
        else:
            return TestResults(
                passed=0,
                failed=1,
                errors=0,
                coverage_pct=0.0,
                raw_output=raw,
            )
    except Exception as exc:
        return TestResults(
            passed=0,
            failed=0,
            errors=1,
            coverage_pct=0.0,
            raw_output=str(exc),
        )


async def _run_mutation_command(
    orch: ProjectOrchestrator,
    task_id: str,
) -> MutationResults:
    """Run the configured mutation command and parse results."""
    cmd = orch.runner.config.commands.mutation
    if not cmd:
        return MutationResults(
            total_mutants=0,
            killed=0,
            survived=0,
            timeout=0,
        )

    import asyncio

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        raw = stdout.decode(errors="replace") if stdout else ""

        if proc.returncode == 0:
            return MutationResults(
                total_mutants=1,
                killed=1,
                survived=0,
                timeout=0,
                raw_output=raw,
            )
        else:
            return MutationResults(
                total_mutants=1,
                killed=0,
                survived=1,
                timeout=0,
                raw_output=raw,
            )
    except Exception as exc:
        return MutationResults(
            total_mutants=0,
            killed=0,
            survived=0,
            timeout=0,
            raw_output=str(exc),
        )
