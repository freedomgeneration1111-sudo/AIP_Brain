"""Ollama Embedding Client — Phase 3 real embedding slot.

Implements EmbeddingProvider. Uses Ollama local embeddings.
Supports deterministic mock mode for CI (no real Ollama required for the gate).
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

from aip.foundation.protocols import EmbeddingProvider


def fake_embed_via_provider(text: str, dimensions: int = 768) -> list[float]:
    """Deterministic fake embedding for CI / adapter-layer use.

    Uses SHA-256 hash for determinism (same algorithm as
    orchestration.retrieval.fake_embed but without the import boundary
    violation — adapters must not import orchestration).
    """
    digest = hashlib.sha256(text.encode()).digest()
    vec = []
    for i in range(dimensions):
        byte_idx = (i * 4) % len(digest)
        val = int.from_bytes(digest[byte_idx : byte_idx + 4].ljust(4, b"\x00"), "big")
        vec.append(val / (2**32 - 1))
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec] if norm > 0 else vec


class OllamaEmbeddingClient(EmbeddingProvider):
    """Ollama-based embedding client.

    Embedding slot is local via Ollama.
    """

    def __init__(self, base_url: str, model: str, dimensions: int = 768) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30.0,
        )

    async def embed(self, text: str) -> list[float]:
        """Embed text using Ollama.

        On failure (Ollama not running, etc.), raises cleanly (no silent fake fallback).
        """
        try:
            resp = await self._client.post(
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
                "Is Ollama running? For CI use mock mode.",
            ) from e

    async def close(self) -> None:
        await self._client.aclose()


class MockOllamaEmbeddingClient(EmbeddingProvider):
    """Deterministic mock embedding client for CI (gate).

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
