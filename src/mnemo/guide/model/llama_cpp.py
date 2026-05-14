"""LlamaCppModelProvider — talks to llama-server (llama.cpp HTTP API).

Uses the same HTTP pattern as EmbeddingService: synchronous requests via
``run_in_executor`` to avoid blocking the asyncio event loop. The provider
is a pure HTTP client — it does NOT start or manage the llama-server
process (that is the responsibility of the Tauri/Rust layer).

Required llama-server endpoints:

- ``GET /health`` → ``{"status": "ok"}``
- ``POST /completion``

  .. code:: json

     {
       "prompt": "<system prompt>\\n\\n<user question>",
       "n_predict": 512,
       "temperature": 0.7,
       "top_p": 0.9,
       "stop": ["\\n\\n"],
       "stream": false
     }

  Response: ``{"content": "<answer text>", "stop": true, ...}``
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import requests

from mnemo.config import MnemoConfig
from mnemo.guide.model.provider import LocalModelProvider

logger = logging.getLogger(__name__)

# llama-server default port.
DEFAULT_LLAMA_SERVER_URL = "http://127.0.0.1:8080"
# Timeout for single HTTP request (seconds).
_REQUEST_TIMEOUT_S = 30.0
# Timeout for health check (seconds).
_HEALTH_TIMEOUT_S = 3.0


class LlamaCppModelProvider(LocalModelProvider):
    """Talks to llama-server (llama.cpp) for guide LLM generation."""

    def __init__(self, config: MnemoConfig | None = None) -> None:
        self._config = config or MnemoConfig()
        gm = self._config.guide_model
        self._server_url: str = gm.get("llama_server_url", DEFAULT_LLAMA_SERVER_URL)
        self._model_path: str = gm.get("model_path", "")
        self._max_tokens: int = gm.get("max_tokens", 512)
        self._context_size: int = gm.get("context_size", 4096)

    async def is_available(self) -> bool:
        """Check if llama-server is reachable and healthy."""
        try:
            result = await asyncio.to_thread(
                requests.get,
                f"{self._server_url}/health",
                timeout=_HEALTH_TIMEOUT_S,
            )
            if result.status_code != 200:
                return False
            data = result.json()
            return data.get("status") in ("ok", "ok, no slot available")
        except Exception:
            return False

    async def generate(self, prompt: str, max_tokens: int | None = None) -> str:
        """Send a completion request to llama-server.

        Args:
            prompt: The full prompt string (system + user).
            max_tokens: Max tokens to generate; defaults to config value.

        Returns:
            Generated text, or empty string on failure.
        """
        n_predict = max_tokens if max_tokens is not None else self._max_tokens

        body = {
            "prompt": prompt,
            "n_predict": n_predict,
            "temperature": 0.7,
            "top_p": 0.9,
            "stop": ["\n\n\n"],
            "stream": False,
        }

        try:
            resp = await asyncio.to_thread(
                requests.post,
                f"{self._server_url}/completion",
                json=body,
                timeout=_REQUEST_TIMEOUT_S,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("content", "")
        except requests.Timeout:
            logger.warning("llama-server generate timed out after %.0fs", _REQUEST_TIMEOUT_S)
            return ""
        except requests.ConnectionError:
            logger.warning("llama-server unreachable at %s", self._server_url)
            return ""
        except Exception:
            logger.exception("Unexpected error calling llama-server")
            return ""
