#!/usr/bin/env python3
"""Smoke test for executor adapters (requires ANTHROPIC_API_KEY).

Usage:
    python scripts/smoke_executor.py

This is NOT a CI test — it calls the real API and costs money.
"""

from __future__ import annotations

import asyncio
import sys

from orchestrator.config import ProjectConfig
from orchestrator.executor.developer import DeveloperExecutor


async def main() -> None:
    config = ProjectConfig.from_dict(
        {
            "repo": ".",
            "phase": "prototype",
            "budgets": {"per_task_usd": 0.50, "per_project_usd": 5.0},
        }
    )

    dev = DeveloperExecutor(config)
    result = await dev.execute(
        prompt="List the files in the current directory using ls. Then print 'smoke test passed'.",
        task_id="SMOKE-001",
    )

    print(f"Success: {result.success}")
    print(f"Cost: ${result.cost_usd:.4f}")
    print(f"Duration: {result.duration_ms}ms")
    print(f"Output:\n{result.output[:500]}")
    print(f"Tool calls: {len(result.tool_calls)}")

    if not result.success:
        print("SMOKE TEST FAILED", file=sys.stderr)
        sys.exit(1)

    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main())
