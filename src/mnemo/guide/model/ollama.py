"""OllamaModelProvider — talks to Ollama for guide LLM generation.

Uses Ollama's HTTP API (``/api/generate``). Falls back to the configured
Ollama endpoint (``MNEMO_OLLAMA_URL``, default ``http://localhost:11434``).
The model must be pre-pulled by the user (e.g. ``ollama pull qwen2.5:1.5b``).

Required Ollama endpoints:

- ``GET /api/tags`` → ``{"models": [{"name": "..."}, ...]}``
- ``POST /api/generate``

  .. code:: json

     {
       "model": "qwen2.5:1.5b",
       "prompt": "...",
       "stream": false,
       "options": {"num_predict": 512, "temperature": 0.7}
     }

  Response: ``{"response": "...", "done": true, ...}``
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import requests

from mnemo.config import MnemoConfig
from mnemo.guide.model.provider import LocalModelProvider

logger = logging.getLogger(__name__)

# Default Ollama endpoint (overridable via MNEMO_OLLAMA_URL).
DEFAULT_OLLAMA_URL = "http://localhost:11434"
_REQUEST_TIMEOUT_S = 30.0
_HEALTH_TIMEOUT_S = 3.0


class OllamaModelProvider(LocalModelProvider):
    """Talks to Ollama for guide LLM generation."""

    def __init__(self, config: MnemoConfig | None = None) -> None:
        self._config = config or MnemoConfig()
        gm = self._config.guide_model
        self._ollama_url: str = self._config.ollama_url or DEFAULT_OLLAMA_URL
        self._model_name: str = gm.get("model_name", "qwen2.5:1.5b")
        self._max_tokens: int = gm.get("max_tokens", 512)
        self._cached_available: bool | None = None

    async def is_available(self) -> bool:
        """Check if Ollama is reachable and the configured model is pulled."""
        try:
            result = await asyncio.to_thread(
                requests.get,
                f"{self._ollama_url}/api/tags",
                timeout=_HEALTH_TIMEOUT_S,
            )
            result.raise_for_status()
            data = result.json()
            models = data.get("models", [])
            # Match by model name prefix (e.g. "qwen2.5:1.5b" matches any variant)
            available = any(
                m.get("name", "").startswith(self._model_name.split(":")[0])
                for m in models
            )
            self._cached_available = available
            return available
        except Exception:
            self._cached_available = False
            return False

    async def generate(self, prompt: str, max_tokens: int | None = None) -> str:
        """Send a generate request to Ollama.

        Args:
            prompt: The full prompt string.
            max_tokens: Max tokens to generate; defaults to config value.

        Returns:
            Generated text, or empty string on failure.
        """
        n_predict = max_tokens if max_tokens is not None else self._max_tokens

        body = {
            "model": self._model_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": n_predict,
                "temperature": 0.7,
            },
        }

        try:
            resp = await asyncio.to_thread(
                requests.post,
                f"{self._ollama_url}/api/generate",
                json=body,
                timeout=_REQUEST_TIMEOUT_S,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "")
        except requests.Timeout:
            logger.warning("Ollama generate timed out after %.0fs", _REQUEST_TIMEOUT_S)
            return ""
        except requests.ConnectionError:
            logger.warning("Ollama unreachable at %s", self._ollama_url)
            return ""
        except Exception:
            logger.exception("Unexpected error calling Ollama")
            return ""
