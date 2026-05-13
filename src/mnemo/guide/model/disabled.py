"""Disabled model provider — always returns unavailable.

Used when the guide is running in V0 (no-model) mode or when the
local model has been explicitly disabled via configuration.
"""

from mnemo.guide.model.provider import LocalModelProvider


class DisabledModelProvider(LocalModelProvider):
    """Model provider that is never available.

    All generate calls return empty strings.
    """

    async def is_available(self) -> bool:
        return False

    async def generate(self, prompt: str, max_tokens: int = 512) -> str:
        return ""
