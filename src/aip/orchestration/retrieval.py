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
    """Deterministic fake embedding for Phase 1 CI (no real model calls).

    Uses SHA-256 hash for determinism (same input always produces same output,
    regardless of Python version or platform). Per spec CHUNK-1.1.
    """
    import hashlib
    digest = hashlib.sha256(text.encode()).digest()
    vec = []
    for i in range(dimensions):
        byte_idx = (i * 4) % len(digest)
        val = int.from_bytes(digest[byte_idx:byte_idx+4].ljust(4, b'\x00'), 'big')
        vec.append(val / (2**32 - 1))
    norm = sum(v*v for v in vec) ** 0.5
    return [v/norm for v in vec] if norm > 0 else vec


def _compute_recency(created_at: str | None) -> float:
    """Half-life decay with 30-day half-life per spec."""
    if not created_at:
        return 0.0
    try:
        from datetime import datetime, timezone
        created_dt = datetime.fromisoformat(created_at)
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created_dt).total_seconds() / 86400.0
        half_life = 30.0
        return 0.5 ** (age_days / half_life)
    except Exception:
        return 0.0


def _compute_authority(authority: str) -> float:
    """Authority score per spec: approved=1.0, reviewed=0.75, provisional=0.5, raw=0.25."""
    mapping = {"approved": 1.0, "reviewed": 0.75, "provisional": 0.5, "raw": 0.25}
    return mapping.get(authority, 0.25)


def _compute_frequency(access_count: int | None) -> float:
    """Frequency score per spec: min(1.0, count/10.0) with default 0.5."""
    if access_count is None:
        return 0.5
    return min(1.0, access_count / 10.0)


def rerank(hits: list[Chunk], domain: str, weights: RerankWeights) -> list[Chunk]:
    """Apply four-factor reranking to hits per spec."""
    if not hits:
        return hits

    # Compute boosted scores
    scored = []
    for h in hits:
        meta = h.metadata or {}
        authority = meta.get("authority", "raw")
        created = meta.get("created_at")
        access = meta.get("access_count")

        # Base semantic score from the Chunk (already a similarity score)
        base = h.score

        # Spec four-factor computation
        recency = _compute_recency(created)
        auth_score = _compute_authority(authority)
        freq_score = _compute_frequency(access)

        boosted = (
            base * weights.semantic
            + recency * weights.recency
            + auth_score * weights.authority
            + freq_score * weights.frequency
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
    top_k: int = 10,
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

    # Apply top_k limit after reranking
    reranked = reranked[:top_k]

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
            session_id="retrieval",
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
            session_id="retrieval",
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
