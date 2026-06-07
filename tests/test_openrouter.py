"""Tests for OpenRouterClient — mocked httpx, no real API calls."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.executor.openrouter import OpenRouterClient, estimate_cost

_P = "orchestrator.executor.openrouter"


# ── estimate_cost ──


class TestEstimateCost:
    def test_gemini_small_tier(self) -> None:
        # 1000 input + 500 output = 1500 total (≤200K → small tier)
        # $2/M * 1000 + $12/M * 500 = 0.002 + 0.006 = 0.008
        cost = estimate_cost("google/gemini-3.1-pro-preview", 1000, 500)
        assert cost == pytest.approx(0.008)

    def test_gemini_large_tier(self) -> None:
        # 150K input + 60K output = 210K total (>200K → large tier)
        # $4/M * 150000 + $18/M * 60000 = 0.6 + 1.08 = 1.68
        cost = estimate_cost("google/gemini-3.1-pro-preview", 150_000, 60_000)
        assert cost == pytest.approx(1.68)

    def test_sonnet_pricing(self) -> None:
        # $3/M * 1000 + $15/M * 500 = 0.003 + 0.0075 = 0.0105
        cost = estimate_cost("anthropic/claude-sonnet-4.6", 1000, 500)
        assert cost == pytest.approx(0.0105)

    def test_unknown_model_returns_zero(self) -> None:
        cost = estimate_cost("unknown/model", 1000, 500)
        assert cost == 0.0


# ── OpenRouterClient ──


class TestOpenRouterClientAuth:
    def test_missing_key_raises(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"),
        ):
            OpenRouterClient(model="google/gemini-3.1-pro-preview")

    def test_empty_key_raises(self) -> None:
        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}),
            pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"),
        ):
            OpenRouterClient(model="google/gemini-3.1-pro-preview")

    def test_valid_key_accepted(self) -> None:
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-test-key"}):
            client = OpenRouterClient(model="google/gemini-3.1-pro-preview")
            assert client._model == "google/gemini-3.1-pro-preview"  # noqa: SLF001


class TestOpenRouterClientChat:
    async def test_chat_success(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "verdict: approve\nLGTM"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
        mock_response.headers = {"X-OpenRouter-Cost": "0.0035"}
        mock_response.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-test"}):
            client = OpenRouterClient(
                model="google/gemini-3.1-pro-preview",
                system_prompt="You are an auditor.",
            )

        with patch(f"{_P}.httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            result = await client.chat(prompt="Review this", task_id="T-001")

        assert result.success is True
        assert result.output == "verdict: approve\nLGTM"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.cost_usd == pytest.approx(0.0035)
        assert result.model == "google/gemini-3.1-pro-preview"
        assert result.task_id == "T-001"

    async def test_chat_cost_from_header(self) -> None:
        """X-OpenRouter-Cost header is used as primary cost source."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
        }
        mock_response.headers = {"X-OpenRouter-Cost": "0.042"}
        mock_response.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-test"}):
            client = OpenRouterClient(model="google/gemini-3.1-pro-preview")

        with patch(f"{_P}.httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            result = await client.chat(prompt="test", task_id="T-002")

        # Header value, not calculated from pricing table
        assert result.cost_usd == pytest.approx(0.042)

    async def test_chat_cost_fallback_to_table(self) -> None:
        """Without X-OpenRouter-Cost header, falls back to pricing table."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
        }
        mock_response.headers = {}  # No cost header
        mock_response.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-test"}):
            client = OpenRouterClient(model="google/gemini-3.1-pro-preview")

        with patch(f"{_P}.httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            result = await client.chat(prompt="test", task_id="T-003")

        expected = estimate_cost("google/gemini-3.1-pro-preview", 1000, 500)
        assert result.cost_usd == pytest.approx(expected)

    async def test_key_not_in_result(self) -> None:
        """API key must never appear in ExecutionResult fields."""
        api_key = "sk-or-v1-secret-key-value"
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "verdict: approve"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_response.headers = {}
        mock_response.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": api_key}):
            client = OpenRouterClient(model="google/gemini-3.1-pro-preview")

        with patch(f"{_P}.httpx.AsyncClient") as mock_httpx:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_response)
            mock_httpx.return_value = mock_ctx

            result = await client.chat(prompt="test", task_id="T-004")

        # Key must not leak into any field
        assert api_key not in result.output
        assert api_key not in result.model
        assert api_key not in str(result.tool_calls)


class TestOpenRouterCostWithBudget:
    """OpenRouter cost entries work correctly with CostTracker (INV-5)."""

    def test_cost_entry_from_openrouter(self) -> None:
        from orchestrator.cost import BudgetExceededError, CostEntry, CostTracker

        ct = CostTracker(budget_usd=1.0)
        entry = CostEntry(
            task_id="T-001",
            model="google/gemini-3.1-pro-preview",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.5,
        )
        ct.record(entry)
        assert ct.total_usd == pytest.approx(0.5)

        with pytest.raises(BudgetExceededError):
            ct.record(CostEntry("T-001", "google/gemini-3.1-pro-preview", 1000, 500, 0.6))
