"""Wiki article retriever channel.

Surfaces approved wiki articles from the ArtifactStore, scored by
query term overlap and domain match, filtered by ECS state.
"""

from __future__ import annotations

import logging
from typing import Any

from aip.foundation.schemas.retrieval import RetrievalHit
from aip.orchestration.channels.types import ChannelFailure, safe_retriever

logger = logging.getLogger(__name__)

CHANNEL_NAME = "wiki"


def register(
    orchestrator: Any,
    stores: Any,
    config: dict | None = None,
) -> list[ChannelFailure]:
    """Register the wiki article channel on the orchestrator.

    Requires both ``stores.artifact_store`` and ``stores.ecs_store``.

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

    if stores.artifact_store is None or stores.ecs_store is None:
        missing = []
        if stores.artifact_store is None:
            missing.append("artifact_store")
        if stores.ecs_store is None:
            missing.append("ecs_store")
        failure = ChannelFailure(
            channel=CHANNEL_NAME,
            error_type="store_unavailable",
            message=f"Wiki channel skipped: missing {', '.join(missing)}",
        )
        failures.append(failure)
        logger.debug(failure.message)
        return failures

    artifact_store = stores.artifact_store
    ecs_store = stores.ecs_store

    async def _wiki_retriever(query: str) -> list[RetrievalHit]:
        arts = await artifact_store.list_artifacts_by_metadata(
            key="artifact_type", value="beast_wiki", limit=50,
        )

        if not arts:
            return []

        # Score articles by query term overlap with content/metadata
        query_terms = set(query.lower().split())
        scored_arts: list[tuple[float, dict]] = []
        for art in arts:
            art_id = art.get("id", "")
            if not art_id:
                continue
            # Check ECS state — prefer APPROVED, accept GENERATED
            try:
                state = await ecs_store.current_state(art_id)
            except Exception:
                state = None
            if state not in ("APPROVED", "GENERATED"):
                continue

            # Score by term overlap
            content = (art.get("content", "") or "").lower()
            meta = art.get("metadata", {}) or {}
            domain = meta.get("domain", "")
            overview = meta.get("overview_text", "").lower()

            overlap = sum(1 for t in query_terms if t in content or t in overview)
            domain_match = 1.0 if any(t in domain.lower() for t in query_terms) else 0.0
            score = overlap * 0.3 + domain_match * 0.7
            state_bonus = 0.1 if state == "APPROVED" else 0.0

            if score + state_bonus > 0:
                scored_arts.append((score + state_bonus, art))

        scored_arts.sort(key=lambda x: x[0], reverse=True)

        hits: list[RetrievalHit] = []
        for i, (score, art) in enumerate(scored_arts[:10]):
            art_id = art.get("id", "")
            content = art.get("content", "") or ""
            meta = art.get("metadata", {}) or {}
            hits.append(RetrievalHit(
                id=f"wiki:{art_id}",
                content=content[:2000],
                score=score,
                source_channel=CHANNEL_NAME,
                domain=meta.get("domain", ""),
                metadata={
                    "type": "wiki_article",
                    "artifact_id": art_id,
                    "domain": meta.get("domain", ""),
                    "overview_text": meta.get("overview_text", "")[:500],
                },
                rank_in_channel=i + 1,
            ))
        return hits

    orchestrator.register_channel(
        CHANNEL_NAME,
        safe_retriever(CHANNEL_NAME, _wiki_retriever, log_level="debug"),
    )
    return failures
