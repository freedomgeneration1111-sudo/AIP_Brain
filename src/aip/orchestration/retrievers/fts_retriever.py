"""FTSRetriever — FTS5-based retrieval wrapping CorpusTurnStore + LexicalStore.

Implements the Retriever protocol. This is the Phase 5.1 baseline
retriever that replaces the inline FTS logic in ask_pipeline._search_sources.

Design decisions:
- CorpusTurnStore.search() is the primary path (rich metadata, Beast tags)
- LexicalStore.search() is the supplementary path (artifacts, indexed content)
- Results from both are merged and deduplicated
- Each hit gets retrieval_channel=RetrievalChannel.FTS
- Importance weighting is applied (Beast importance + position decay)
- Graceful degradation: if one store fails, return the other's results

Layer: orchestration. Imports from foundation (schemas, protocols) and
adapter (CorpusTurnStore, LexicalStore).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any

from aip.foundation.schemas.retrieval_trace import (
    EvidenceStatus,
    RetrievalBudget,
    RetrievalChannel,
    RetrievalHit,
    RetrievalQuery,
    RetrievalTrace,
    RetrieverTrace,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FTS5 query sanitization (shared with eval_retrieval.py)
# ---------------------------------------------------------------------------


def sanitize_fts_query(query: str) -> str:
    """Robust FTS5 sanitization — strips special chars, filters stop words, AND-joins.

    This is the canonical sanitization logic used by both FTSRetriever
    and eval_retrieval.py. Keeping it here as the single source of truth.
    """
    # Remove FTS5 special characters (including single quote for possessives like AIP's)
    cleaned = re.sub(r"""[?!.*+\-^(){}|~'"\\]""", " ", query)
    tokens = cleaned.split()
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "shall", "can",
        "of", "in", "to", "for", "with", "on", "at", "by", "from",
        "it", "its", "we", "our", "you", "your", "this", "that",
        "what", "which", "who", "whom", "how", "when", "where", "why",
        "about", "there", "here", "these", "those", "been", "some",
        "very", "also", "just", "than", "then", "so", "if", "or",
        "not", "no", "but", "and", "up", "out", "into", "over",
    }
    meaningful = [t for t in tokens if len(t) >= 2 and t.lower() not in stop_words]
    if not meaningful:
        meaningful = [t for t in tokens if len(t) >= 1 and t.lower() not in stop_words]
    if not meaningful:
        # Last resort: first few raw tokens
        meaningful = [t for t in tokens[:3] if t]
    if not meaningful:
        return query
    return " AND ".join(meaningful)


# ---------------------------------------------------------------------------
# CorpusTurn → RetrievalHit conversion
# ---------------------------------------------------------------------------


def _corpus_turn_to_hit(turn: Any, rank: int) -> RetrievalHit:
    """Convert a CorpusTurn search result to a RetrievalHit.

    Applies importance-weighted position scoring:
      score = position_base + importance_boost
      position_base = 1.0 - (rank / max_pool) * 0.5  (decays from 1.0 to 0.5)
      importance_boost = importance * 0.3              (Beast-tagged high-importance upweight)
    """
    # Build entities list from domains, tags, bridges
    entities: list[str] = []
    if hasattr(turn, "domains") and turn.domains:
        entities.extend(turn.domains)
    if hasattr(turn, "tags") and turn.tags:
        entities.extend(turn.tags)
    if hasattr(turn, "bridges") and turn.bridges:
        entities.extend(turn.bridges)

    # Build snippet (first 200 chars)
    snippet = ""
    if hasattr(turn, "searchable_text") and turn.searchable_text:
        snippet = turn.searchable_text[:200]

    # Parse timestamp
    recency_ts = None
    if hasattr(turn, "turn_timestamp") and turn.turn_timestamp:
        try:
            recency_ts = datetime.fromisoformat(turn.turn_timestamp)
        except (ValueError, TypeError):
            pass

    # Importance-weighted position scoring
    position_base = 1.0 - (rank / 100.0) * 0.5  # decays from 1.0 to ~0.5
    importance_boost = float(turn.importance or 0.0) * 0.3

    return RetrievalHit(
        id=turn.turn_id,
        source_type="corpus_turn",
        source_id=turn.turn_id,
        title=(
            f"{turn.conversation_name} [{turn.primary_domain}]"
            if hasattr(turn, "conversation_name")
            else None
        ),
        text=turn.searchable_text or "",
        snippet=snippet,
        rank=rank,
        score=position_base + importance_boost,
        confidence=float(turn.beast_confidence or 0.0),
        recency_ts=recency_ts,
        importance=float(turn.importance) if turn.importance else None,
        domain=turn.primary_domain or None,
        entities=entities,
        retrieval_channel=RetrievalChannel.FTS,
        evidence_status=EvidenceStatus.RAW,
        debug={
            "conversation_id": turn.conversation_id if hasattr(turn, "conversation_id") else "",
            "source_model": turn.source_model if hasattr(turn, "source_model") else "",
            "tagging_version": turn.tagging_version if hasattr(turn, "tagging_version") else 0,
            "position_base": round(position_base, 4),
            "importance_boost": round(importance_boost, 4),
        },
    )


def _chunk_to_hit(chunk: Any, rank: int) -> RetrievalHit:
    """Convert a LexicalStore Chunk to a RetrievalHit."""
    meta = chunk.metadata or {}
    chunk_type = meta.get("type", "indexed_content")

    snippet = ""
    if hasattr(chunk, "content") and chunk.content:
        snippet = chunk.content[:200]

    return RetrievalHit(
        id=chunk.id,
        source_type=chunk_type,
        source_id=chunk.id,
        title=chunk.id,
        text=chunk.content or "",
        snippet=snippet,
        rank=rank,
        score=float(chunk.score or 0.0),
        confidence=0.0,
        domain=chunk.domain or meta.get("domain"),
        entities=meta.get("tags", []),
        retrieval_channel=RetrievalChannel.FTS,
        evidence_status=EvidenceStatus.RAW,
        debug={
            "source": "lexical_store",
            "type": chunk_type,
        },
    )


# ---------------------------------------------------------------------------
# FTSRetriever
# ---------------------------------------------------------------------------


class FTSRetriever:
    """FTS5-based retriever wrapping CorpusTurnStore + LexicalStore.

    Implements the Retriever protocol from foundation.protocols.retrieval.

    Primary search: CorpusTurnStore (rich Beast-tagged corpus turns).
    Supplementary: LexicalStore (artifacts, indexed content).
    Both are searched without domain filter (corpus is project-agnostic).

    Graceful degradation (AIP-G-02):
    - If CorpusTurnStore fails, return LexicalStore results only.
    - If LexicalStore fails, return CorpusTurnStore results only.
    - If both fail, return [] (never raise).
    """

    def __init__(
        self,
        corpus_turn_store: Any = None,
        lexical_store: Any = None,
    ) -> None:
        self._corpus_store = corpus_turn_store
        self._lexical_store = lexical_store

    @property
    def name(self) -> str:
        return "FTSRetriever"

    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        budget: RetrievalBudget,
        trace: RetrievalTrace,
    ) -> list[RetrievalHit]:
        """Execute FTS5 retrieval against corpus turns and lexical index.

        Returns deduplicated, importance-weighted RetrievalHit list.
        On failure of any store, gracefully degrades to remaining store.
        """
        started = time.monotonic()
        fts_query = sanitize_fts_query(query.raw_query)

        hits: list[RetrievalHit] = []
        errors: list[str] = []
        domains_seen: list[str] = []
        corpus_count = 0
        lexical_count = 0

        # --- CorpusTurnStore search (primary) ---
        if self._corpus_store is not None:
            try:
                corpus_results = await self._corpus_store.search(
                    query=fts_query,
                    primary_domain=None,  # project-agnostic: search ALL
                    limit=budget.max_sources * 3,  # overfetch for dedup + filtering
                )
                for i, turn in enumerate(corpus_results):
                    hit = _corpus_turn_to_hit(turn, rank=i + 1)
                    hits.append(hit)
                    if hit.domain and hit.domain not in domains_seen:
                        domains_seen.append(hit.domain)
                corpus_count = len(corpus_results)
            except Exception as exc:
                msg = f"CorpusTurnStore search failed: {exc}"
                logger.warning(msg)
                errors.append(msg)

        # --- LexicalStore search (supplementary) ---
        if self._lexical_store is not None:
            try:
                lexical_results = await self._lexical_store.search(
                    fts_query,
                    domain=None,  # project-agnostic
                    limit=budget.max_sources * 3,
                )
                for i, chunk in enumerate(lexical_results):
                    hit = _chunk_to_hit(chunk, rank=i + 1)
                    hits.append(hit)
                    dom = hit.domain
                    if dom and dom not in domains_seen:
                        domains_seen.append(dom)
                lexical_count = len(lexical_results)
            except Exception as exc:
                msg = f"LexicalStore search failed: {exc}"
                logger.warning(msg)
                errors.append(msg)

        # --- Deduplicate by hit ID ---
        seen_ids: set[str] = set()
        unique_hits: list[RetrievalHit] = []
        for hit in hits:
            if hit.id not in seen_ids:
                seen_ids.add(hit.id)
                unique_hits.append(hit)

        # --- Sort by score descending and apply budget cap ---
        unique_hits.sort(key=lambda h: h.score, reverse=True)
        unique_hits = unique_hits[: budget.max_sources]

        # --- Re-rank after sort ---
        for i, hit in enumerate(unique_hits):
            hit.rank = i + 1

        # --- Record retriever trace ---
        elapsed_ms = (time.monotonic() - started) * 1000.0
        degraded = len(errors) > 0 and len(hits) > 0
        error_msg = "; ".join(errors) if errors else None

        retriever_trace = RetrieverTrace(
            retriever_name=self.name,
            enabled=True,
            degraded=degraded,
            error=error_msg,
            started_at=datetime.now(),
            finished_at=datetime.now(),
            elapsed_ms=round(elapsed_ms, 2),
            hit_count=len(unique_hits),
            top_score=unique_hits[0].score if unique_hits else 0.0,
            top_hit_ids=[h.id for h in unique_hits[:10]],
            scores=[round(h.score, 4) for h in unique_hits],
            debug={
                "channel": "fts5",
                "corpus_count": corpus_count,
                "lexical_count": lexical_count,
                "fts_query": fts_query,
                "domains": domains_seen[:20],
            },
        )
        trace.retriever_traces.append(retriever_trace)

        if errors and not hits:
            # Total failure — record as fallback
            trace.fallbacks_triggered.append("fts_retriever_total_failure")

        return unique_hits


__all__ = ["FTSRetriever", "sanitize_fts_query"]
