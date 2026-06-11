"""Corpus turn retriever channel.

Backed by CorpusTurnStore.  Provides FTS5 search over ingested
conversation turns with importance-based scoring.
"""

from __future__ import annotations

import logging
from typing import Any

from aip.foundation.schemas.retrieval import RetrievalHit
from aip.orchestration.channels.lexical_channel import _sanitize_fts_query
from aip.orchestration.channels.types import ChannelFailure, safe_retriever

logger = logging.getLogger(__name__)

CHANNEL_NAME = "corpus"


def register(
    orchestrator: Any,
    stores: Any,
    config: dict | None = None,
) -> list[ChannelFailure]:
    """Register the corpus turn channel on the orchestrator.

    Requires ``stores.corpus_turn_store`` to be non-None.

    Args:
        orchestrator: RetrievalOrchestrator instance to register on.
        stores: AskStores container.
        config: Optional TOML config dict.

    Returns:
        List of ChannelFailure for missing dependencies (empty on success).
    """
    failures: list[ChannelFailure] = []

    if orchestrator.is_registered(CHANNEL_NAME):
        return failures

    cts = stores.corpus_turn_store

    if cts is None:
        failure = ChannelFailure(
            channel=CHANNEL_NAME,
            error_type="store_unavailable",
            message="Corpus channel skipped: corpus_turn_store is None",
        )
        failures.append(failure)
        logger.debug(failure.message)
        return failures

    async def _corpus_retriever(query: str) -> list[RetrievalHit]:
        fts_query = _sanitize_fts_query(query)
        corpus_turns = await cts.search(
            query=fts_query,
            primary_domain=None,
            limit=30,
        )
        hits = []
        for i, turn in enumerate(corpus_turns):
            position_score = 1.0 - (i / max(len(corpus_turns), 1)) * 0.5
            importance_boost = float(turn.importance or 0.0) * 0.3
            hits.append(RetrievalHit(
                id=turn.turn_id,
                content=turn.searchable_text or "",
                score=position_score + importance_boost,
                source_channel=CHANNEL_NAME,
                domain=turn.primary_domain or "",
                metadata={
                    "type": "conversation_chunk",
                    "conversation_id": turn.conversation_id,
                    "source_format": "corpus_turn",
                    "domain": turn.primary_domain or "",
                    "importance": float(turn.importance or 0.0),
                },
                rank_in_channel=i + 1,
            ))
        return hits

    orchestrator.register_channel(
        CHANNEL_NAME,
        safe_retriever(CHANNEL_NAME, _corpus_retriever, log_level="warning"),
    )
    return failures
