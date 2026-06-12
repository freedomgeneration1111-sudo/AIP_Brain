"""FTS5 lexical retriever channel.

Primary retrieval channel backed by the persistent FTS5 LexicalStore.
Always available as long as the lexical store is provided.
"""

from __future__ import annotations

import logging
from typing import Any

from aip.foundation.sanitize_fts import sanitize_fts_query
from aip.foundation.schemas.retrieval import RetrievalHit
from aip.orchestration.channels.types import ChannelFailure, safe_retriever

logger = logging.getLogger(__name__)

# Channel name constant
CHANNEL_NAME = "fts"

# Backward-compatible alias — orchestration consumers may still reference the
# private name via the module attribute (e.g. container wiring).
_sanitize_fts_query = sanitize_fts_query


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
