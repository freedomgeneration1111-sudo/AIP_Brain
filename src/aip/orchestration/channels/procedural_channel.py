"""Procedural guide retriever channel.

Surfaces how-to guides and step-by-step procedures from the
ArtifactStore, scored by query term overlap and procedural signals.
"""

from __future__ import annotations

import logging
from typing import Any

from aip.foundation.schemas.retrieval import RetrievalHit
from aip.orchestration.channels.types import ChannelFailure, safe_retriever

logger = logging.getLogger(__name__)

CHANNEL_NAME = "procedural"


def register(
    orchestrator: Any,
    stores: Any,
    config: dict | None = None,
) -> list[ChannelFailure]:
    """Register the procedural guide channel on the orchestrator.

    Requires ``stores.artifact_store`` to be non-None.

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

    if stores.artifact_store is None:
        failure = ChannelFailure(
            channel=CHANNEL_NAME,
            error_type="store_unavailable",
            message="Procedural channel skipped: artifact_store is None",
        )
        failures.append(failure)
        logger.debug(failure.message)
        return failures

    artifact_store = stores.artifact_store

    async def _procedural_retriever(query: str) -> list[RetrievalHit]:
        # Search for procedural artifacts
        procs = await artifact_store.list_artifacts_by_metadata(
            key="artifact_type",
            value="procedural_guide",
            limit=20,
        )
        # Also search compiled_knowledge which may contain procedural content
        compiled = await artifact_store.list_artifacts_by_metadata(
            key="artifact_type",
            value="compiled_knowledge",
            limit=20,
        )
        all_arts = procs + compiled

        if not all_arts:
            return []

        query_terms = set(query.lower().split())
        procedural_keywords = {
            "step",
            "steps",
            "how to",
            "procedure",
            "guide",
            "instructions",
            "process",
            "method",
            "tutorial",
        }
        hits: list[RetrievalHit] = []

        for art in all_arts:
            content = (art.get("content", "") or "").lower()
            meta = art.get("metadata", {}) or {}
            art_id = art.get("id", "")

            # Check if content has procedural signals
            has_procedural = any(kw in content for kw in procedural_keywords)
            if not has_procedural and meta.get("artifact_type") != "procedural_guide":
                continue

            # Score by query term overlap + procedural relevance
            overlap = sum(1 for t in query_terms if t in content)
            proc_boost = 0.3 if has_procedural else 0.0
            score = overlap * 0.2 + proc_boost

            if score > 0:
                hits.append(
                    RetrievalHit(
                        id=f"proc:{art_id}",
                        content=(art.get("content", "") or "")[:2000],
                        score=score,
                        source_channel=CHANNEL_NAME,
                        domain=meta.get("domain", ""),
                        metadata={
                            "type": "procedural_guide",
                            "artifact_id": art_id,
                            "domain": meta.get("domain", ""),
                        },
                    )
                )

        # Sort by score descending and assign ranks consistently
        hits.sort(key=lambda h: h.score, reverse=True)
        hits = hits[:10]
        for i, hit in enumerate(hits):
            hit.rank_in_channel = i + 1

        return hits

    orchestrator.register_channel(
        CHANNEL_NAME,
        safe_retriever(CHANNEL_NAME, _procedural_retriever, log_level="debug"),
    )
    return failures
