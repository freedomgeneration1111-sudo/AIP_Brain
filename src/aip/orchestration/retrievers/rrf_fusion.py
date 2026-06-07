"""Reciprocal Rank Fusion (RRF) for merging multi-channel retrieval results.

RRF is a simple, effective fusion method that converts per-retriever
rankings into a single ranked list. It is robust to score scale
differences between retrievers (FTS5 BM25 vs cosine similarity vs
PPR score) because it operates on ranks, not raw scores.

Algorithm:
    rrf_score(hit) = sum over channels of 1 / (k + rank_in_channel)

Where k is a smoothing constant (default 60, per Cormack et al. 2009).
Higher k dampens the effect of individual rank positions.

Phase 5.1: Fuse FTS results from CorpusTurnStore + LexicalStore.
Future: Add vector, graph, wiki channels with zero changes here.

Layer: orchestration (pure function, no store dependencies).
"""

from __future__ import annotations

from aip.foundation.schemas.retrieval_trace import (
    RetrievalChannel,
    RetrievalHit,
)


def rrf_fuse(
    hits_by_channel: dict[RetrievalChannel, list[RetrievalHit]],
    k: int = 60,
) -> list[RetrievalHit]:
    """Reciprocal Rank Fusion across retrieval channels.

    Takes a dict of {channel: sorted_hits} and returns a single
    deduplicated, RRF-scored, sorted list of RetrievalHit objects.

    Args:
        hits_by_channel: Per-channel hit lists. Each list should be
            sorted by relevance (best first). Ranks are assigned
            from position (1-based).
        k: Smoothing constant. Default 60 per Cormack et al. (2009).
            Higher k reduces the advantage of top ranks.

    Returns:
        Deduplicated list of RetrievalHit, sorted by descending RRF
        score. Each hit's score field is overwritten with the RRF
        score and rank is reassigned.

    Notes:
        - Deduplication: if a hit appears in multiple channels (same id),
          its RRF score accumulates across channels (the whole point of
          fusion — multi-source agreement is a strong signal).
        - Single-source hits still contribute (1/(k+rank)).
        - The hit object from the *first* channel that provides it is
          preserved; subsequent channels only contribute their rank
          to the score, not their metadata.
    """
    if not hits_by_channel:
        return []

    # Accumulate RRF scores and preserve first-seen hit object
    rrf_scores: dict[str, float] = {}
    hit_objects: dict[str, RetrievalHit] = {}
    channel_contributions: dict[str, list[str]] = {}  # hit_id -> [channel names]

    for channel, hits in hits_by_channel.items():
        if not hits:
            continue
        for rank_pos, hit in enumerate(hits, start=1):
            contribution = 1.0 / (k + rank_pos)
            hid = hit.id

            if hid not in rrf_scores:
                rrf_scores[hid] = 0.0
                hit_objects[hid] = hit
                channel_contributions[hid] = []

            rrf_scores[hid] += contribution
            channel_contributions[hid].append(channel.value)

    if not rrf_scores:
        return []

    # Sort by RRF score descending
    sorted_ids = sorted(rrf_scores.keys(), key=lambda hid: rrf_scores[hid], reverse=True)

    # Build result list with updated scores and ranks
    results: list[RetrievalHit] = []
    for rank, hid in enumerate(sorted_ids, start=1):
        hit = hit_objects[hid]
        # Create a new hit with updated fusion score and rank
        fused_hit = RetrievalHit(
            id=hit.id,
            source_type=hit.source_type,
            source_id=hit.source_id,
            title=hit.title,
            text=hit.text,
            snippet=hit.snippet,
            rank=rank,
            score=round(rrf_scores[hid], 6),
            confidence=hit.confidence,
            recency_ts=hit.recency_ts,
            importance=hit.importance,
            domain=hit.domain,
            entities=hit.entities,
            retrieval_channel=hit.retrieval_channel,
            evidence_status=hit.evidence_status,
            debug={
                **hit.debug,
                "rrf_score": round(rrf_scores[hid], 6),
                "rrf_channels": channel_contributions[hid],
            },
        )
        results.append(fused_hit)

    return results


__all__ = ["rrf_fuse"]
