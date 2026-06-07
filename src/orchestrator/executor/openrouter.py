"""OpenRouter client — OpenAI-compatible gateway for the auditor agent."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from orchestrator.executor.base import ExecutionResult

logger = logging.getLogger(__name__)

_BASE_URL = "https://openrouter.ai/api/v1"

# Tiered pricing per 1M tokens (USD).
# Source: OpenRouter pricing page, June 2026.
_PRICING: dict[str, dict[str, tuple[float, float]]] = {
    # model: { tier_key: (input_per_1M, output_per_1M) }
    "google/gemini-3.1-pro-preview": {
        "small": (2.0, 12.0),  # ≤200K context tokens
        "large": (4.0, 18.0),  # >200K context tokens
    },
    "anthropic/claude-sonnet-4.6": {
        "small": (3.0, 15.0),
        "large": (3.0, 15.0),
    },
    "anthropic/claude-opus-4.8": {
        "small": (15.0, 75.0),
        "large": (15.0, 75.0),
    },
}

_TIER_BOUNDARY = 200_000  # tokens


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Estimate cost from the pricing table (fallback when API doesn't return cost).

    Uses tiered pricing: ≤200K tokens → small tier, >200K → large tier.
    Returns 0.0 for unknown models.
    """
    tiers = _PRICING.get(model)
    if tiers is None:
        return 0.0
    tier_key = "small" if (input_tokens + output_tokens) <= _TIER_BOUNDARY else "large"
    input_price, output_price = tiers[tier_key]
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


class OpenRouterClient:
    """Async client for OpenRouter chat completions API.

    Reads ``OPENROUTER_API_KEY`` from the environment.  The key is never
    logged, stored in config, or included in ``ExecutionResult``.
    """

    def __init__(self, model: str, system_prompt: str = "") -> None:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY not set. "
                "Add it to your .env or export it before running the orchestrator."
            )
        self._model = model
        self._system_prompt = system_prompt
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ai-dev-orchestrator",
        }

    async def chat(self, prompt: str, task_id: str) -> ExecutionResult:
        """Send a chat completion request and return an ExecutionResult."""
        messages: list[dict[str, str]] = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }

        start_ms = time.monotonic_ns() // 1_000_000

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{_BASE_URL}/chat/completions",
                headers=self._headers,
                json=payload,
            )
            resp.raise_for_status()

        elapsed_ms = (time.monotonic_ns() // 1_000_000) - start_ms
        data: dict[str, Any] = resp.json()

        # Parse response
        choices = data.get("choices", [])
        output = ""
        if choices:
            message = choices[0].get("message", {})
            output = message.get("content", "")

        # Parse usage
        usage = data.get("usage", {})
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))

        # Cost: prefer X-OpenRouter-Cost header, fallback to pricing table
        cost_header = resp.headers.get("X-OpenRouter-Cost")
        if cost_header:
            try:
                cost_usd = float(cost_header)
            except ValueError:
                cost_usd = estimate_cost(self._model, input_tokens, output_tokens)
        else:
            cost_usd = estimate_cost(self._model, input_tokens, output_tokens)

        return ExecutionResult(
            task_id=task_id,
            success=True,
            output=output,
            cost_usd=cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self._model,
            duration_ms=elapsed_ms,
        )
