"""FTS5 lexical retriever channel.

Primary retrieval channel backed by the persistent FTS5 LexicalStore.
Always available as long as the lexical store is provided.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from aip.foundation.schemas.retrieval import RetrievalHit
from aip.orchestration.channels.types import ChannelFailure, safe_retriever

logger = logging.getLogger(__name__)

# Channel name constant
CHANNEL_NAME = "fts"


def _sanitize_fts_query(query: str) -> str:
    """Sanitize a user query for FTS5 MATCH syntax.

    FTS5 has special syntax for operators like AND, OR, NOT, NEAR, *, ^, etc.
    Questions from users often contain ?, !, and other characters that
    are not valid in FTS5 MATCH expressions.  This function extracts
    clean word tokens and joins them with AND for FTS5 matching.
    """
    cleaned = re.sub(r'[?!.*+\-^(){}|~"\\]', " ", query)
    tokens = cleaned.split()
    stop_words = {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "of",
        "in",
        "to",
        "for",
        "with",
        "on",
        "at",
        "by",
        "from",
        "it",
        "its",
        "we",
        "our",
        "you",
        "your",
        "this",
        "that",
        "what",
        "which",
        "who",
        "whom",
        "how",
        "when",
        "where",
        "why",
        "about",
        "there",
        "here",
        "these",
        "those",
        "been",
        "some",
        "very",
        "also",
        "just",
        "than",
        "then",
        "so",
        "if",
        "or",
        "not",
        "no",
        "but",
        "and",
        "up",
        "out",
        "into",
        "over",
    }
    meaningful = [t for t in tokens if len(t) >= 2 and t.lower() not in stop_words]

    if not meaningful:
        meaningful = [t for t in tokens if len(t) >= 1 and t.lower() not in stop_words]

    if not meaningful:
        meaningful = [t for t in tokens[:3] if t]

    if not meaningful:
        return query

    return " AND ".join(meaningful)


def register(
    orchestrator: Any,
    stores: Any,
    config: dict | None = None,
) -> list[ChannelFailure]:
    """Register the FTS5 lexical channel on the orchestrator.

    This channel is always available when a LexicalStore is provided.
    It uses FTS5 full-text search to find matching chunks.

    Args:
        orchestrator: RetrievalOrchestrator instance to register on.
        stores: AskStores container with lexical_store attribute.
        config: Optional TOML config dict (unused by this channel).

    Returns:
        List of ChannelFailure for missing dependencies (empty on success).
    """
    failures: list[ChannelFailure] = []

    if orchestrator.is_registered(CHANNEL_NAME):
        return failures

    lexical_store = stores.lexical_store

    async def _fts_retriever(query: str) -> list[RetrievalHit]:
        fts_query = _sanitize_fts_query(query)
        chunks = await lexical_store.search(
            fts_query,
            domain=None,
            limit=30,
        )
        hits = []
        for i, chunk in enumerate(chunks):
            hits.append(
                RetrievalHit(
                    id=chunk.id,
                    content=chunk.content or "",
                    score=chunk.score,
                    source_channel=CHANNEL_NAME,
                    domain=chunk.domain or "",
                    metadata=chunk.metadata or {},
                    rank_in_channel=i + 1,
                )
            )
        return hits

    orchestrator.register_channel(
        CHANNEL_NAME,
        safe_retriever(CHANNEL_NAME, _fts_retriever, log_level="warning"),
    )
    return failures
