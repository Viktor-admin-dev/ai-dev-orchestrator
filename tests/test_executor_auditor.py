"""Tests for AuditorExecutor (mocked OpenRouter + mocked Agent SDK)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from orchestrator.config import ProjectConfig
from orchestrator.evidence import EvidencePack, TestResults
from orchestrator.executor.auditor import AuditorExecutor
from orchestrator.executor.base import ExecutionResult
from orchestrator.types import AuditVerdict

_P = "orchestrator.executor.auditor"
_OR = "orchestrator.executor.openrouter"
_TB = lambda: type("TB", (), {})  # noqa: E731
_AM = lambda: type("AM", (), {})  # noqa: E731
_RM = lambda: type("RM", (), {})  # noqa: E731


def _config(*, gateway: str = "openrouter") -> ProjectConfig:
    return ProjectConfig.from_dict(
        {
            "repo": "/tmp/test",
            "phase": "mvp",
            "models": {
                "auditor": "google/gemini-3.1-pro-preview",
                "auditor_gateway": gateway,
            },
        }
    )


def _sdk_config() -> ProjectConfig:
    """Config that uses Agent SDK (legacy) path."""
    return _config(gateway="agent-sdk")


class TestAuditorOpenRouter:
    """Tests for AuditorExecutor using OpenRouter gateway."""

    async def test_execute_via_openrouter(self) -> None:
        mock_result = ExecutionResult(
            task_id="T-001",
            success=True,
            output="verdict: approve\nAll checks pass.",
            cost_usd=0.05,
            input_tokens=300,
            output_tokens=200,
            model="google/gemini-3.1-pro-preview",
            duration_ms=3000,
        )

        with patch(f"{_OR}.OpenRouterClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat = AsyncMock(return_value=mock_result)
            mock_cls.return_value = mock_client

            auditor = AuditorExecutor(_config())
            result = await auditor.execute("Review this diff", "T-001")

        assert result.success is True
        assert "approve" in result.output
        assert result.model == "google/gemini-3.1-pro-preview"

    async def test_audit_uses_evidence_input_inv3(self) -> None:
        """INV-3: audit() excludes developer reasoning via OpenRouter."""
        captured_prompts: list[str] = []

        async def fake_chat(*, prompt: str, task_id: str) -> ExecutionResult:
            captured_prompts.append(prompt)
            return ExecutionResult(
                task_id=task_id,
                success=True,
                output="verdict: approve",
                model="google/gemini-3.1-pro-preview",
            )

        with patch(f"{_OR}.OpenRouterClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat = fake_chat
            mock_cls.return_value = mock_client

            evidence = EvidencePack(
                task_id="T-005",
                diff="+ new_feature()",
                criteria="Must implement new_feature",
                test_results=TestResults(passed=5, failed=0, errors=0, coverage_pct=95.0),
                developer_log=[
                    "I thought about approach A",
                    "Decided B is better",
                ],
            )

            auditor = AuditorExecutor(_config())
            result = await auditor.audit(evidence)

        assert result.task_id == "T-005"
        prompt = captured_prompts[0]
        assert "new_feature" in prompt
        assert "thought about approach A" not in prompt
        assert "Decided B is better" not in prompt

    def test_parse_result_approve(self) -> None:
        result = ExecutionResult(
            task_id="T-001",
            success=True,
            output="verdict: approve\nEverything looks great.",
        )
        verdict = AuditorExecutor.parse_result(result)
        assert verdict.verdict is AuditVerdict.APPROVE

    def test_parse_result_fallback(self) -> None:
        result = ExecutionResult(
            task_id="T-001",
            success=True,
            output="Not sure what to say here.",
        )
        verdict = AuditorExecutor.parse_result(result)
        assert verdict.verdict is AuditVerdict.REQUEST_CHANGES

    def test_conforms_to_protocol(self) -> None:
        from orchestrator.executor.base import ExecutorAdapter

        auditor = AuditorExecutor(_config())
        assert isinstance(auditor, ExecutorAdapter)

    def test_model_property(self) -> None:
        auditor = AuditorExecutor(_config())
        assert auditor.model == "google/gemini-3.1-pro-preview"


class TestAuditorAgentSDK:
    """Tests for AuditorExecutor using Agent SDK (legacy path)."""

    @patch("claude_agent_sdk.query")
    @patch("claude_agent_sdk.TextBlock", new_callable=_TB)
    @patch("claude_agent_sdk.AssistantMessage", new_callable=_AM)
    @patch("claude_agent_sdk.ResultMessage", new_callable=_RM)
    async def test_execute_via_agent_sdk(
        self,
        mock_rm_cls: type,
        mock_am_cls: type,
        mock_tb_cls: type,
        mock_query: Any,
    ) -> None:
        text_block = MagicMock(spec=mock_tb_cls)
        text_block.text = "verdict: approve\nAll checks pass."

        assistant = MagicMock(spec=mock_am_cls)
        assistant.content = [text_block]
        assistant.usage = {"input_tokens": 300, "output_tokens": 200}

        result_msg = MagicMock(spec=mock_rm_cls)
        result_msg.subtype = "success"
        result_msg.total_cost_usd = 0.8
        result_msg.duration_ms = 3000

        async def gen(**kwargs: Any) -> Any:
            yield assistant
            yield result_msg

        mock_query.side_effect = gen

        auditor = AuditorExecutor(_sdk_config())
        result = await auditor.execute("Review this diff", "T-001")

        assert result.success is True
        assert "approve" in result.output
        assert result.model == "google/gemini-3.1-pro-preview"
