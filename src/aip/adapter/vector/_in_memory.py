"""In-memory VectorStore fallback for environments without sqlite_vss or pgvector.

Graceful degradation: when neither vector backend is available,
this provides a working (but non-persistent) store so the system can still
operate in CI and development environments.
"""

from __future__ import annotations

import math
from typing import Any

from aip.foundation.protocols import VectorStore
from aip.foundation.schemas import Chunk


class InMemoryVectorStore(VectorStore):
    """In-memory VectorStore fallback. Not suitable for production."""

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}
        self._vectors: dict[str, list[float]] = {}

    async def upsert(
        self,
        id: str,
        embedding: list[float],
        content: str,
        metadata: dict[str, Any] | None = None,
        domain: str | None = None,
    ) -> None:
        self._data[id] = {"content": content, "metadata": metadata or {}, "domain": domain}
        self._vectors[id] = embedding

    async def retrieve(
        self,
        query_vector: list[float],
        domain: str | None = None,
        top_k: int = 10,
    ) -> list[Chunk]:
        # Simple cosine similarity fallback

        results = []
        for id_, data in self._data.items():
            if domain and data.get("domain") != domain:
                continue
            vec = self._vectors.get(id_, [])
            if not vec:
                continue
            score = self._cosine_similarity(query_vector, vec)
            results.append(
                Chunk(
                    id=id_,
                    content=data["content"],
                    score=score,
                    metadata=data["metadata"],
                    domain=data.get("domain"),
                ),
            )
        results.sort(key=lambda c: c.score, reverse=True)
        return results[:top_k]

    async def delete(self, id: str) -> None:
        self._data.pop(id, None)
        self._vectors.pop(id, None)

    async def count(self, domain: str | None = None) -> int:
        if domain:
            return sum(1 for d in self._data.values() if d.get("domain") == domain)
        return len(self._data)

    async def health_check(self) -> dict:
        return {"connected": True, "backend_name": "in-memory", "count": len(self._data)}

    async def list_stale_vectors(
        self,
        threshold_days: int = 30,
        domain: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """In-memory store has no timestamps; return empty list (no staleness tracking)."""
        return []

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)
