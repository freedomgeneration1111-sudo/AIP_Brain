"""Ollama Embedding Client — Phase 3 real embedding slot (CHUNK-5.1).

Implements EmbeddingProvider. Uses Ollama local embeddings.
Supports deterministic mock mode for CI (no real Ollama required for the gate).
"""
from __future__ import annotations

import hashlib
from typing import Any

from aip.foundation.protocols import EmbeddingProvider

# httpx is only needed for the real client; we import it lazily inside the class
# so that the module (and the mock) can be imported in environments without httpx.


class OllamaEmbeddingClient(EmbeddingProvider):
    """Ollama-based embedding client.

    Per §4.1 and §8.1: embedding slot is local via Ollama.
    """

    def __init__(self, base_url: str, model: str, dimensions: int = 768) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions

    async def embed(self, text: str) -> list[float]:
        """Embed text using Ollama.

        On failure (Ollama not running, etc.), raises cleanly (no silent fake fallback).
        """
        try:
            import httpx  # lazy import
            async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as client:
                resp = await client.post(
                    "/api/embeddings",
                    json={"model": self.model, "prompt": text},
                )
                resp.raise_for_status()
                data = resp.json()
                vec = data.get("embedding", [])
                return vec
        except Exception as e:
            raise ConnectionError(
                f"Failed to embed via Ollama at {self.base_url} (model={self.model}). "
                "Is Ollama running? For CI use mock mode."
            ) from e

    async def close(self) -> None:
        await self._client.aclose()


class MockOllamaEmbeddingClient(EmbeddingProvider):
    """Deterministic mock embedding client for CI (CHUNK-5.1 gate).

    Returns a 768-dim vector derived from the input text hash (same algorithm
    spirit as the old fake_embed, but through the EmbeddingProvider interface).
    """

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions

    async def embed(self, text: str) -> list[float]:
        # Deterministic vector from text hash
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # Expand/truncate to desired dimensions
        vec = []
        i = 0
        while len(vec) < self.dimensions:
            val = (h[i % len(h)] / 255.0) - 0.5
            vec.append(val)
            i += 1
        return vec[: self.dimensions]
