"""
L2 retrieval node: retrieve_for_synthesis with low-confidence gate and four-factor reranking.
Implemented exactly per Rev 1.3 CHUNK-1.1.

Config-driven (Delta 5), explicit embed_fn (Delta 4), TraceStore logging on failure (R2/F2).
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from aip.foundation.protocols import TraceStore, VectorStore
from aip.foundation.schemas import Chunk, RetrievalResult


@dataclass
class RerankWeights:
    semantic: float = 0.60
    recency: float = 0.15
    authority: float = 0.15
    frequency: float = 0.10

    @classmethod
    def from_config(cls, config: dict | Any | None = None) -> "RerankWeights":
        """Load weights from config dict or object with model_dump() (F3 support)."""
        if config is None:
            return cls()

        if hasattr(config, "model_dump"):
            cfg = config.model_dump()
        elif isinstance(config, dict):
            cfg = config
        else:
            cfg = {}

        retrieval = cfg.get("retrieval", {}) if isinstance(cfg, dict) else {}
        return cls(
            semantic=retrieval.get("weight_semantic", cls.semantic),
            recency=retrieval.get("weight_recency", cls.recency),
            authority=retrieval.get("weight_authority", cls.authority),
            frequency=retrieval.get("weight_frequency", cls.frequency),
        )


def fake_embed(text: str, dimensions: int = 768) -> list[float]:
    """Deterministic fake embedding for Phase 1 CI (no real model calls)."""
    # Simple but deterministic hash-based embedding
    seed = abs(hash(text)) % (2**32)
    vec = []
    for i in range(dimensions):
        val = math.sin(seed + i) * math.cos(seed * 0.1 + i * 0.3)
        vec.append(val)
    # Normalize to unit vector
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def rerank(hits: list[Chunk], domain: str, weights: RerankWeights) -> list[Chunk]:
    """Apply four-factor reranking to hits."""
    if not hits:
        return hits

    # Compute boosted scores
    scored = []
    for h in hits:
        meta = h.metadata or {}
        authority = meta.get("authority", "raw")
        created = meta.get("created_at")
        access = meta.get("access_count", 0)

        # Base semantic score from the Chunk (already a similarity score)
        base = h.score

        # Recency boost (simplified: prefer non-null created_at)
        recency = 0.1 if created else 0.0

        # Authority boost
        auth_boost = 0.15 if authority == "approved" else 0.0

        # Frequency boost (simple log scaling)
        freq_boost = min(0.1, math.log1p(access) * 0.05)

        boosted = (
            base * weights.semantic
            + recency * weights.recency
            + auth_boost * weights.authority
            + freq_boost * weights.frequency
        )
        scored.append((boosted, h))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [h for _, h in scored]


async def retrieve_for_synthesis(
    query: str,
    domain: str,
    vector_store: VectorStore,
    embed_fn: Callable[[str], list[float]],
    trace_store: TraceStore,
    config: dict | Any | None = None,
    ace_rules: list[dict] | None = None,
) -> RetrievalResult:
    """
    L2 retrieval + low-confidence gate + reranking.
    CHUNK-3.8: Optional ace_rules from Sexton (derived playbook) are applied
    in a minimal deterministic way (e.g., boost for procedural matches).
    Returns RetrievalResult with status OK or INSUFFICIENT_MEMORY.
    """
    weights = RerankWeights.from_config(config)
    threshold = 0.30
    if config is not None:
        if hasattr(config, "model_dump"):
            cfg = config.model_dump()
        elif isinstance(config, dict):
            cfg = config
        else:
            cfg = {}
        retrieval = cfg.get("retrieval", {}) if isinstance(cfg, dict) else {}
        threshold = retrieval.get("confidence_threshold", 0.30)

    # Embed (Delta 4: explicit, no NotImplemented in VectorStore)
    _vec = embed_fn(query)
    if asyncio.iscoroutine(_vec):
        query_vector = await _vec
    else:
        query_vector = _vec

    # Retrieve (now using the amended protocol with query_vector)
    raw_hits = await vector_store.retrieve(query_vector, domain=domain, top_k=20)

    # Rerank
    reranked = rerank(raw_hits, domain, weights)

    # CHUNK-3.8: Minimal application of ACE rules from Sexton (deterministic boost for procedural matches)
    if ace_rules:
        for rule in ace_rules:
            if rule.get("failure_type") == "B" or "procedural" in str(rule.get("recommended_action", "")).lower():
                keyword = str(rule.get("node_type_pattern", "") or "").lower()
                for hit in reranked:
                    content = str(hit.content or "").lower()
                    if keyword and keyword in content:
                        hit.score = min(1.0, hit.score + 0.15)
        reranked.sort(key=lambda h: h.score, reverse=True)

    if not reranked:
        # Log trace event (R2 / F2 fix: failure_type = "A" for Missing Context)
        await trace_store.write_event(
            session_id="current",
            node_type="L2",
            failure_type="A",
            outcome="insufficient_memory",
            detail="No retrieval hits",
        )
        return RetrievalResult(
            status="INSUFFICIENT_MEMORY",
            hits=[],
            max_confidence=0.0,
            message="No context retrieved.",
        )

    max_conf = max(h.score for h in reranked)

    if max_conf < threshold:
        await trace_store.write_event(
            session_id="current",
            node_type="L2",
            failure_type="A",
            outcome="insufficient_memory",
            detail=f"Max confidence {max_conf:.2f} below threshold {threshold}",
        )
        return RetrievalResult(
            status="INSUFFICIENT_MEMORY",
            hits=reranked[:5],  # still return some for transparency
            max_confidence=max_conf,
            message=f"Best match confidence {max_conf:.2f} below threshold.",
        )

    return RetrievalResult(
        status="OK",
        hits=reranked,
        max_confidence=max_conf,
        message=None,
    )
