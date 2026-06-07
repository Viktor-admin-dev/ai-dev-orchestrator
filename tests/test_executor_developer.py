"""Tests for DeveloperExecutor (mocked Agent SDK)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.config import ProjectConfig
from orchestrator.executor.developer import DeveloperExecutor

_P = "orchestrator.executor.developer"
_TB = lambda: type("TB", (), {})  # noqa: E731
_AM = lambda: type("AM", (), {})  # noqa: E731
_RM = lambda: type("RM", (), {})  # noqa: E731


class TestDeveloperExecutor:
    @patch(f"{_P}.query")
    @patch(f"{_P}.TextBlock", new_callable=_TB)
    @patch(f"{_P}.AssistantMessage", new_callable=_AM)
    @patch(f"{_P}.ResultMessage", new_callable=_RM)
    async def test_execute_success(
        self,
        mock_rm_cls: type,
        mock_am_cls: type,
        mock_tb_cls: type,
        mock_query: Any,
    ) -> None:
        text_block = MagicMock(spec=mock_tb_cls)
        text_block.text = "Implementation done."

        assistant_msg = MagicMock(spec=mock_am_cls)
        assistant_msg.content = [text_block]
        assistant_msg.usage = {"input_tokens": 200, "output_tokens": 100}

        result_msg = MagicMock(spec=mock_rm_cls)
        result_msg.subtype = "success"
        result_msg.total_cost_usd = 0.35
        result_msg.duration_ms = 2000

        async def gen(**kwargs: Any) -> Any:
            yield assistant_msg
            yield result_msg

        mock_query.side_effect = gen

        config = ProjectConfig.from_dict({"repo": "/tmp/test", "phase": "mvp"})
        dev = DeveloperExecutor(config)
        result = await dev.execute("Implement feature X", "T-001")

        assert result.task_id == "T-001"
        assert result.success is True
        assert result.cost_usd == pytest.approx(0.35)
        assert result.duration_ms == 2000
        assert result.model == "claude-sonnet-4-6"
        mock_query.assert_called_once()

    @patch(f"{_P}.query")
    @patch(f"{_P}.TextBlock", new_callable=_TB)
    @patch(f"{_P}.AssistantMessage", new_callable=_AM)
    @patch(f"{_P}.ResultMessage", new_callable=_RM)
    async def test_execute_failure(
        self,
        mock_rm_cls: type,
        mock_am_cls: type,
        mock_tb_cls: type,
        mock_query: Any,
    ) -> None:
        result_msg = MagicMock(spec=mock_rm_cls)
        result_msg.subtype = "error_during_execution"
        result_msg.total_cost_usd = 0.1
        result_msg.duration_ms = 500

        async def gen(**kwargs: Any) -> Any:
            yield result_msg

        mock_query.side_effect = gen

        config = ProjectConfig.from_dict({"repo": "/tmp/test"})
        dev = DeveloperExecutor(config)
        result = await dev.execute("Broken task", "T-002")

        assert result.success is False
        assert result.output == ""

    @patch(f"{_P}.query")
    @patch(f"{_P}.TextBlock", new_callable=_TB)
    @patch(f"{_P}.AssistantMessage", new_callable=_AM)
    @patch(f"{_P}.ResultMessage", new_callable=_RM)
    async def test_options_use_developer_model(
        self,
        mock_rm_cls: type,
        mock_am_cls: type,
        mock_tb_cls: type,
        mock_query: Any,
    ) -> None:
        async def gen(**kwargs: Any) -> Any:
            opts = kwargs.get("options")
            assert opts is not None
            assert opts.model == "claude-sonnet-4-6"
            result_msg = MagicMock(spec=mock_rm_cls)
            result_msg.subtype = "success"
            result_msg.total_cost_usd = 0.0
            result_msg.duration_ms = 0
            yield result_msg

        mock_query.side_effect = gen

        config = ProjectConfig.from_dict({"repo": "/tmp/t"})
        dev = DeveloperExecutor(config)
        await dev.execute("test", "T-003")

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

        config = ProjectConfig.from_dict({})
        dev = DeveloperExecutor(config)
        assert isinstance(dev, ExecutorAdapter)


class TestCriticalEscalation:
    def test_set_critical_default_false(self) -> None:
        config = ProjectConfig.from_dict({})
        dev = DeveloperExecutor(config)
        assert dev._critical is False  # noqa: SLF001

    def test_set_critical_true(self) -> None:
        config = ProjectConfig.from_dict({})
        dev = DeveloperExecutor(config)
        dev.set_critical(True)
        assert dev._active_model == "claude-opus-4-8"  # noqa: SLF001

    def test_set_critical_false(self) -> None:
        config = ProjectConfig.from_dict({})
        dev = DeveloperExecutor(config)
        dev.set_critical(False)
        assert dev._active_model == "claude-sonnet-4-6"  # noqa: SLF001

    @patch(f"{_P}.query")
    @patch(f"{_P}.TextBlock", new_callable=_TB)
    @patch(f"{_P}.AssistantMessage", new_callable=_AM)
    @patch(f"{_P}.ResultMessage", new_callable=_RM)
    async def test_critical_model_used_in_options(
        self,
        mock_rm_cls: type,
        mock_am_cls: type,
        mock_tb_cls: type,
        mock_query: Any,
    ) -> None:
        async def gen(**kwargs: Any) -> Any:
            opts = kwargs.get("options")
            assert opts is not None
            assert opts.model == "claude-opus-4-8"
            result_msg = MagicMock(spec=mock_rm_cls)
            result_msg.subtype = "success"
            result_msg.total_cost_usd = 0.0
            result_msg.duration_ms = 0
            yield result_msg

        mock_query.side_effect = gen

        config = ProjectConfig.from_dict({})
        dev = DeveloperExecutor(config)
        dev.set_critical(True)
        await dev.execute("critical task", "T-100")

    @patch(f"{_P}.query")
    @patch(f"{_P}.TextBlock", new_callable=_TB)
    @patch(f"{_P}.AssistantMessage", new_callable=_AM)
    @patch(f"{_P}.ResultMessage", new_callable=_RM)
    async def test_critical_flag_resets_after_execute(
        self,
        mock_rm_cls: type,
        mock_am_cls: type,
        mock_tb_cls: type,
        mock_query: Any,
    ) -> None:
        async def gen(**kwargs: Any) -> Any:
            result_msg = MagicMock(spec=mock_rm_cls)
            result_msg.subtype = "success"
            result_msg.total_cost_usd = 0.0
            result_msg.duration_ms = 0
            yield result_msg

        mock_query.side_effect = gen

        config = ProjectConfig.from_dict({})
        dev = DeveloperExecutor(config)
        dev.set_critical(True)
        await dev.execute("task", "T-101")
        assert dev._critical is False  # noqa: SLF001
