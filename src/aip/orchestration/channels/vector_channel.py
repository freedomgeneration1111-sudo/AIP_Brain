"""Vector (semantic) retriever channel.

Backed by VectorStore + EmbeddingProvider.  Requires both dependencies
to be available; returns a structured ChannelFailure when either is absent.
"""

from __future__ import annotations

import logging
from typing import Any

from aip.foundation.schemas.retrieval import RetrievalHit
from aip.orchestration.channels.types import ChannelFailure, safe_retriever

logger = logging.getLogger(__name__)

CHANNEL_NAME = "vector"


def register(
    orchestrator: Any,
    stores: Any,
    config: dict | None = None,
) -> list[ChannelFailure]:
    """Register the vector (semantic) channel on the orchestrator.

    Requires both ``stores.vector_store`` and ``stores.embedding_provider``
    to be non-None.  Returns a list of ChannelFailure objects if
    dependencies are missing (used for trace visibility).

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

    vec_store = stores.vector_store
    embed_prov = stores.embedding_provider

    if vec_store is None or embed_prov is None:
        missing = []
        if vec_store is None:
            missing.append("vector_store")
        if embed_prov is None:
            missing.append("embedding_provider")
        failure = ChannelFailure(
            channel=CHANNEL_NAME,
            error_type="store_unavailable",
            message=f"Vector channel skipped: missing {', '.join(missing)}",
        )
        failures.append(failure)
        logger.debug(failure.message)
        return failures

    async def _vector_retriever(query: str) -> list[RetrievalHit]:
        query_vec = await embed_prov.embed(query)
        if not query_vec or len(query_vec) == 0:
            return []
        chunks = await vec_store.retrieve(query_vec, domain=None, top_k=20)
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
        safe_retriever(CHANNEL_NAME, _vector_retriever, log_level="debug"),
    )
    return failures
