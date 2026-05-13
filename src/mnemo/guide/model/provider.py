"""Abstract base class for local model providers used by Mnemo Guide.

Concrete implementations (disabled, mock, and future real providers)
inherit from this class to keep the guide router decoupled from the
specific model backend.
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator


class LocalModelProvider(ABC):
    """Base class for model providers that power the Mnemo Guide."""

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the model is available and ready to generate."""
        ...

    @abstractmethod
    async def generate(self, prompt: str, max_tokens: int = 512) -> str:
        """Generate a response from the model.

        Args:
            prompt: The complete prompt string (system + user).
            max_tokens: Maximum tokens to generate.

        Returns:
            The model's response text.
        """
        ...

    async def stream(
        self, prompt: str, max_tokens: int = 512
    ) -> AsyncIterator[str]:
        """Stream a response token by token.

        Default implementation yields the full ``generate()`` result
        as a single chunk. Subclasses may override for true streaming.
        """
        result = await self.generate(prompt, max_tokens)
        yield result
