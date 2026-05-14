"""Tests for guide model providers.

- Unit tests: mock HTTP responses for LlamaCppModelProvider and
  OllamaModelProvider.
- Existing providers: DisabledModelProvider and MockModelProvider are
  tested implicitly through the guide integration.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mnemo.config import MnemoConfig
from mnemo.guide.model.llama_cpp import LlamaCppModelProvider
from mnemo.guide.model.ollama import OllamaModelProvider
from mnemo.guide.model.disabled import DisabledModelProvider
from mnemo.guide.model.mock import MockModelProvider


# =========================================================================
# DisabledModelProvider
# =========================================================================


class TestDisabledModelProvider:
    async def test_is_available_returns_false(self) -> None:
        provider = DisabledModelProvider()
        assert await provider.is_available() is False

    async def test_generate_returns_empty(self) -> None:
        provider = DisabledModelProvider()
        result = await provider.generate("hello")
        assert result == ""


# =========================================================================
# MockModelProvider
# =========================================================================


class TestMockModelProvider:
    async def test_is_available_returns_true(self) -> None:
        provider = MockModelProvider()
        assert await provider.is_available() is True

    async def test_generate_install_keyword(self) -> None:
        provider = MockModelProvider()
        result = await provider.generate("怎么安装 Mnemo")
        assert "curl" in result or "irm" in result

    async def test_generate_setup_keyword(self) -> None:
        provider = MockModelProvider()
        result = await provider.generate("如何配置 Claude Code 接入 Mnemo")
        assert "mnemo setup" in result

    async def test_generate_what_is_keyword(self) -> None:
        provider = MockModelProvider()
        result = await provider.generate("Mnemo 是什么")
        assert "AI 记忆层" in result

    async def test_generate_unknown_keyword(self) -> None:
        provider = MockModelProvider()
        result = await provider.generate("random gibberish xxx yyy")
        assert "Mnemo 公共说明书" in result

    async def test_generate_custom_responses(self) -> None:
        provider = MockModelProvider(
            responses={"custom_key": "custom answer"}
        )
        result = await provider.generate("this has custom_key in it")
        assert result == "custom answer"

    async def test_generate_call_count(self) -> None:
        provider = MockModelProvider()
        await provider.generate("a")
        await provider.generate("b")
        assert provider.call_count == 2


# =========================================================================
# LlamaCppModelProvider
# =========================================================================


class TestLlamaCppModelProvider:
    def _make_config(self, **overrides) -> MnemoConfig:
        """Create a config dict with defaults and overrides."""
        defaults = {
            "guide_model": {
                "enabled": True,
                "provider": "llama_cpp",
                "model_name": "Qwen2.5-1.5B-Instruct-GGUF",
                "model_path": "",
                "runtime": "llama_cpp",
                "context_size": 4096,
                "max_tokens": 512,
            }
        }
        for k, v in overrides.items():
            defaults[k] = v
        return MnemoConfig(**defaults)

    @patch("mnemo.guide.model.llama_cpp.requests.get")
    async def test_is_available_returns_true_when_healthy(
        self, mock_get: MagicMock,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        mock_get.return_value = mock_response

        provider = LlamaCppModelProvider(self._make_config())
        assert await provider.is_available() is True

    @patch("mnemo.guide.model.llama_cpp.requests.get")
    async def test_is_available_returns_true_for_no_slot(
        self, mock_get: MagicMock,
    ) -> None:
        # "ok, no slot available" means server is running but busy
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok, no slot available"}
        mock_get.return_value = mock_response

        provider = LlamaCppModelProvider(self._make_config())
        assert await provider.is_available() is True

    @patch("mnemo.guide.model.llama_cpp.requests.get")
    async def test_is_available_returns_false_on_error(
        self, mock_get: MagicMock,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        provider = LlamaCppModelProvider(self._make_config())
        assert await provider.is_available() is False

    @patch("mnemo.guide.model.llama_cpp.requests.get")
    async def test_is_available_returns_false_on_connection_error(
        self, mock_get: MagicMock,
    ) -> None:
        import requests as req
        mock_get.side_effect = req.ConnectionError("refused")

        provider = LlamaCppModelProvider(self._make_config())
        assert await provider.is_available() is False

    @patch("mnemo.guide.model.llama_cpp.requests.post")
    async def test_generate_returns_content(
        self, mock_post: MagicMock,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": "Mnemo 是一款本地 AI 记忆层工具。",
            "stop": True,
        }
        mock_post.return_value = mock_response

        provider = LlamaCppModelProvider(self._make_config())
        result = await provider.generate("Mnemo 是什么？")
        assert "AI 记忆层" in result

    @patch("mnemo.guide.model.llama_cpp.requests.post")
    async def test_generate_returns_empty_on_error(
        self, mock_post: MagicMock,
    ) -> None:
        import requests as req
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = req.HTTPError("500 Server Error")
        mock_post.return_value = mock_response

        provider = LlamaCppModelProvider(self._make_config())
        result = await provider.generate("Mnemo 是什么？")
        assert result == ""

    @patch("mnemo.guide.model.llama_cpp.requests.post")
    async def test_generate_returns_empty_on_timeout(
        self, mock_post: MagicMock,
    ) -> None:
        import requests as req
        mock_post.side_effect = req.Timeout()

        provider = LlamaCppModelProvider(self._make_config())
        result = await provider.generate("Mnemo 是什么？")
        assert result == ""

    @patch("mnemo.guide.model.llama_cpp.requests.post")
    async def test_generate_uses_custom_max_tokens(
        self, mock_post: MagicMock,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"content": "ok", "stop": True}
        mock_post.return_value = mock_response

        provider = LlamaCppModelProvider(self._make_config())
        await provider.generate("test", max_tokens=100)

        # Verify n_predict was 100 in the request body
        call_args = mock_post.call_args
        body = call_args[1]["json"]
        assert body["n_predict"] == 100


# =========================================================================
# OllamaModelProvider
# =========================================================================


class TestOllamaModelProvider:
    def _make_config(self, **overrides) -> MnemoConfig:
        defaults = {
            "ollama_url": "http://localhost:11434",
            "guide_model": {
                "enabled": True,
                "provider": "ollama",
                "model_name": "qwen2.5:1.5b",
                "model_path": "",
                "runtime": "ollama",
                "context_size": 4096,
                "max_tokens": 512,
            },
        }
        for k, v in overrides.items():
            defaults[k] = v
        return MnemoConfig(**defaults)

    @patch("mnemo.guide.model.ollama.requests.get")
    async def test_is_available_returns_true_when_model_exists(
        self, mock_get: MagicMock,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "qwen2.5:1.5b"},
                {"name": "other-model:latest"},
            ]
        }
        mock_get.return_value = mock_response

        provider = OllamaModelProvider(self._make_config())
        assert await provider.is_available() is True

    @patch("mnemo.guide.model.ollama.requests.get")
    async def test_is_available_returns_false_when_model_missing(
        self, mock_get: MagicMock,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "llama3:latest"},
            ]
        }
        mock_get.return_value = mock_response

        provider = OllamaModelProvider(self._make_config())
        assert await provider.is_available() is False

    @patch("mnemo.guide.model.ollama.requests.get")
    async def test_is_available_returns_false_on_connection_error(
        self, mock_get: MagicMock,
    ) -> None:
        import requests as req
        mock_get.side_effect = req.ConnectionError("refused")

        provider = OllamaModelProvider(self._make_config())
        assert await provider.is_available() is False

    @patch("mnemo.guide.model.ollama.requests.post")
    async def test_generate_returns_response(
        self, mock_post: MagicMock,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": "Mnemo 是一款本地 AI 记忆层工具。",
            "done": True,
        }
        mock_post.return_value = mock_response

        provider = OllamaModelProvider(self._make_config())
        result = await provider.generate("Mnemo 是什么？")
        assert "AI 记忆层" in result

    @patch("mnemo.guide.model.ollama.requests.post")
    async def test_generate_returns_empty_on_error(
        self, mock_post: MagicMock,
    ) -> None:
        import requests as req
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = req.HTTPError("500 Server Error")
        mock_post.return_value = mock_response

        provider = OllamaModelProvider(self._make_config())
        result = await provider.generate("Mnemo 是什么？")
        assert result == ""

    @patch("mnemo.guide.model.ollama.requests.post")
    async def test_generate_uses_model_name(
        self, mock_post: MagicMock,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "ok", "done": True}
        mock_post.return_value = mock_response

        provider = OllamaModelProvider(self._make_config())
        await provider.generate("test")

        call_args = mock_post.call_args
        body = call_args[1]["json"]
        assert body["model"] == "qwen2.5:1.5b"
        assert body["stream"] is False


# =========================================================================
# Integration tests (require real llama-server or Ollama)
# =========================================================================


@pytest.mark.integration
class TestLlamaCppIntegration:
    """Integration tests that require a running llama-server.

    Skip with: ``pytest -m "not integration"``
    """

    @pytest.mark.asyncio
    async def test_is_available(self) -> None:
        provider = LlamaCppModelProvider()
        # This will return False if llama-server is not running.
        # It's a smoke test — we just want to verify it doesn't crash.
        result = await provider.is_available()
        assert result is True or result is False  # noqa: safe, bool type check

    @pytest.mark.asyncio
    async def test_generate_smoke(self) -> None:
        provider = LlamaCppModelProvider()
        if not await provider.is_available():
            pytest.skip("llama-server not available")

        result = await provider.generate("说一句你好", max_tokens=50)
        # Even minimal output is fine — we just verify it doesn't crash.
        assert isinstance(result, str)


@pytest.mark.integration
class TestOllamaIntegration:
    """Integration tests that require a running Ollama.

    Skip with: ``pytest -m "not integration"``
    """

    @pytest.mark.asyncio
    async def test_is_available(self) -> None:
        provider = OllamaModelProvider()
        result = await provider.is_available()
        assert result is True or result is False

    @pytest.mark.asyncio
    async def test_generate_smoke(self) -> None:
        provider = OllamaModelProvider()
        if not await provider.is_available():
            pytest.skip("Ollama not available")

        result = await provider.generate("说一句你好", max_tokens=50)
        assert isinstance(result, str)
