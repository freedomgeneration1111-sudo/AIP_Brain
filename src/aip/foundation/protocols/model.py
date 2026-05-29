"""Model and embedding provider Protocol definitions.

Abstractions for LLM model calls and text-to-vector embedding,
ensuring orchestration code never imports provider SDKs directly.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ModelProvider(Protocol):
    """Abstracts model API calls for a named slot.

    Orchestration code must never import openai/anthropic/ollama directly.
    """

    async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict:
        """Call the model for the given slot.

        Returns a dict with at minimum: content, model, usage, latency_ms.
        """
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Abstracts text-to-vector embedding.

    Used by retrieval and knowledge store components for
    generating embeddings from text content.
    """

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string and return the vector."""
        ...


__all__ = [
    "ModelProvider",
    "EmbeddingProvider",
]
