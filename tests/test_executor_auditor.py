"""Tests for AuditorExecutor (mocked Agent SDK)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from orchestrator.config import ProjectConfig
from orchestrator.evidence import EvidencePack, TestResults
from orchestrator.executor.auditor import AuditorExecutor
from orchestrator.types import AuditVerdict

_P = "orchestrator.executor.auditor"
_TB = lambda: type("TB", (), {})  # noqa: E731
_AM = lambda: type("AM", (), {})  # noqa: E731
_RM = lambda: type("RM", (), {})  # noqa: E731


def _config() -> ProjectConfig:
    return ProjectConfig.from_dict({"repo": "/tmp/test", "phase": "mvp"})


class TestAuditorExecutor:
    @patch(f"{_P}.query")
    @patch(f"{_P}.TextBlock", new_callable=_TB)
    @patch(f"{_P}.AssistantMessage", new_callable=_AM)
    @patch(f"{_P}.ResultMessage", new_callable=_RM)
    async def test_execute_returns_verdict_text(
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

        auditor = AuditorExecutor(_config())
        result = await auditor.execute("Review this diff", "T-001")

        assert result.success is True
        assert "approve" in result.output
        assert result.model == "claude-opus-4-6"

    @patch(f"{_P}.query")
    @patch(f"{_P}.TextBlock", new_callable=_TB)
    @patch(f"{_P}.AssistantMessage", new_callable=_AM)
    @patch(f"{_P}.ResultMessage", new_callable=_RM)
    async def test_audit_uses_evidence_input(
        self,
        mock_rm_cls: type,
        mock_am_cls: type,
        mock_tb_cls: type,
        mock_query: Any,
    ) -> None:
        """INV-3: audit() excludes developer reasoning."""
        captured_prompts: list[str] = []

        text_block = MagicMock(spec=mock_tb_cls)
        text_block.text = "verdict: approve"

        assistant = MagicMock(spec=mock_am_cls)
        assistant.content = [text_block]
        assistant.usage = {}

        result_msg = MagicMock(spec=mock_rm_cls)
        result_msg.subtype = "success"
        result_msg.total_cost_usd = 0.0
        result_msg.duration_ms = 0

        async def gen(**kwargs: Any) -> Any:
            captured_prompts.append(kwargs["prompt"])
            yield assistant
            yield result_msg

        mock_query.side_effect = gen

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

    @patch(f"{_P}.query")
    @patch(f"{_P}.TextBlock", new_callable=_TB)
    @patch(f"{_P}.AssistantMessage", new_callable=_AM)
    @patch(f"{_P}.ResultMessage", new_callable=_RM)
    async def test_parse_result_approve(
        self,
        mock_rm_cls: type,
        mock_am_cls: type,
        mock_tb_cls: type,
        mock_query: Any,
    ) -> None:
        from orchestrator.executor.base import ExecutionResult

        result = ExecutionResult(
            task_id="T-001",
            success=True,
            output="verdict: approve\nEverything looks great.",
        )
        verdict = AuditorExecutor.parse_result(result)
        assert verdict.verdict is AuditVerdict.APPROVE

    @patch(f"{_P}.query")
    @patch(f"{_P}.TextBlock", new_callable=_TB)
    @patch(f"{_P}.AssistantMessage", new_callable=_AM)
    @patch(f"{_P}.ResultMessage", new_callable=_RM)
    async def test_parse_result_fallback(
        self,
        mock_rm_cls: type,
        mock_am_cls: type,
        mock_tb_cls: type,
        mock_query: Any,
    ) -> None:
        from orchestrator.executor.base import ExecutionResult

        result = ExecutionResult(
            task_id="T-001",
            success=True,
            output="Not sure what to say here.",
        )
        verdict = AuditorExecutor.parse_result(result)
        assert verdict.verdict is AuditVerdict.REQUEST_CHANGES

    @patch(f"{_P}.query")
    @patch(f"{_P}.TextBlock", new_callable=_TB)
    @patch(f"{_P}.AssistantMessage", new_callable=_AM)
    @patch(f"{_P}.ResultMessage", new_callable=_RM)
    async def test_conforms_to_protocol(
        self,
        mock_rm_cls: type,
        mock_am_cls: type,
        mock_tb_cls: type,
        mock_query: Any,
    ) -> None:
        from orchestrator.executor.base import ExecutorAdapter

        auditor = AuditorExecutor(_config())
        assert isinstance(auditor, ExecutorAdapter)

    @patch(f"{_P}.query")
    @patch(f"{_P}.TextBlock", new_callable=_TB)
    @patch(f"{_P}.AssistantMessage", new_callable=_AM)
    @patch(f"{_P}.ResultMessage", new_callable=_RM)
    async def test_uses_auditor_model(
        self,
        mock_rm_cls: type,
        mock_am_cls: type,
        mock_tb_cls: type,
        mock_query: Any,
    ) -> None:
        async def gen(**kwargs: Any) -> Any:
            opts = kwargs.get("options")
            assert opts is not None
            assert opts.model == "claude-opus-4-6"
            result_msg = MagicMock(spec=mock_rm_cls)
            result_msg.subtype = "success"
            result_msg.total_cost_usd = 0.0
            result_msg.duration_ms = 0
            yield result_msg

        mock_query.side_effect = gen

        auditor = AuditorExecutor(_config())
        await auditor.execute("test", "T-010")
