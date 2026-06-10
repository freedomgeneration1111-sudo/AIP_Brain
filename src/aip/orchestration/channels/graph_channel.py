"""Graph retriever channel.

PPR-based graph retrieval with EntityExtractor (noun-phrase + graph-fuzzy +
optional LLM).  Requires a GraphStore; if absent, creates one from the
stores' db_path as a fallback.
"""

from __future__ import annotations

import logging
import os
import time as _time
from typing import Any

from aip.foundation.schemas.retrieval import RetrievalHit
from aip.orchestration.channels.types import ChannelFailure, safe_retriever

logger = logging.getLogger(__name__)

CHANNEL_NAME = "graph"


def register(
    orchestrator: Any,
    stores: Any,
    config: dict | None = None,
) -> list[ChannelFailure]:
    """Register the graph channel on the orchestrator.

    Wires EntityExtractor with optional LLM entity extraction when
    a ModelProvider is available.  The graph channel uses PPR
    (Personalized PageRank) to expand query entities via the
    knowledge graph.

    Args:
        orchestrator: RetrievalOrchestrator instance to register on.
        stores: AskStores container.
        config: Optional TOML config dict.

    Returns:
        List of ChannelFailure for missing dependencies or init errors.
    """
    failures: list[ChannelFailure] = []

    if orchestrator.is_registered(CHANNEL_NAME):
        return failures

    # Import EntityExtractor at registration time (not top-level, to avoid
    # circular imports with the orchestration package)
    from aip.orchestration.entity_extractor import EntityExtractor, EntityExtractorConfig

    entity_extractor_config = EntityExtractorConfig(
        strategy="hybrid",
        use_graph_fuzzy=True,
    )

    # Wire LLM entity extraction if ModelProvider available
    llm_entity_fn = None
    if stores.model_provider is not None:
        try:
            from aip.orchestration.entity_extractor import create_llm_entity_fn
            llm_entity_fn = create_llm_entity_fn(
                model_provider=stores.model_provider,
                slot_name=entity_extractor_config.llm_entity_extraction_model,
                fallback_slot="synthesis",
            )
            # Enable hybrid_llm mode when model provider is available
            entity_extractor_config.entity_extraction_mode = "hybrid_llm"
        except Exception as exc:
            logger.debug("LLM entity extraction wiring failed (non-fatal): %s", exc)
            failures.append(ChannelFailure(
                channel=CHANNEL_NAME,
                error_type="initialization",
                message=f"LLM entity extraction wiring failed: {exc}",
                exception_type=type(exc).__qualname__,
            ))

    async def _graph_retriever(query: str) -> list[RetrievalHit]:
        """Graph retriever: extract entities from query, run PPR, surface related nodes.

        Tracks LLM entity extraction timing and status in hit metadata
        so the orchestrator can transfer it to the RetrievalTrace for
        dashboard observability.
        """
        llm_ext_ms = 0.0
        llm_ext_status = "not_used"
        llm_ext_count = 0

        from aip.orchestration.graph_retrieval import GraphRetriever

        _graph_store = getattr(stores, "graph_store", None)
        if _graph_store is None:
            # Try to create a GraphStore from the stores' db_path
            _db_path = getattr(stores, "_db_path", None)
            if _db_path is None:
                _db_path = os.environ.get("AIP_DB_PATH", "db/state.db")
            from aip.adapter.graph_store import GraphStore
            _graph_store = GraphStore(_db_path)
            await _graph_store.initialize()

        retriever = GraphRetriever(_graph_store)
        extractor = EntityExtractor(
            config=entity_extractor_config,
            graph_store=_graph_store,
            llm_fn=llm_entity_fn,
        )

        # Track LLM entity extraction timing
        ext_start = _time.monotonic()

        # Use EntityExtractor for robust entity extraction
        # (noun-phrase + graph-fuzzy + optional LLM fallback)
        seed_entities = await extractor.extract_async(
            query, graph_store=_graph_store,
        )

        ext_elapsed = (_time.monotonic() - ext_start) * 1000.0

        # Determine LLM usage status from config and results
        if llm_entity_fn is not None and entity_extractor_config.entity_extraction_mode != "local":
            if ext_elapsed > 0 and len(seed_entities) > 0:
                llm_ext_status = "success"
                llm_ext_count = len(seed_entities)
            elif ext_elapsed > 5.0:
                llm_ext_status = "failed"
            else:
                llm_ext_status = "not_used"
            llm_ext_ms = ext_elapsed

        if not seed_entities:
            return []

        expanded = await retriever.expand_query_via_graph(
            seed_entities=seed_entities,
            max_hops=2,
            top_k=10,
            min_confidence=0.4,
        )

        if not expanded:
            return []

        # Convert expanded graph entities into RetrievalHit instances.
        # Include LLM entity extraction observability data in the first
        # hit's metadata so the orchestrator can transfer it to the
        # RetrievalTrace.
        hits: list[RetrievalHit] = []
        for i, entity_name in enumerate(expanded):
            meta: dict[str, Any] = {
                "type": "graph_entity",
                "entity_name": entity_name,
            }
            if i == 0:
                meta["_llm_entity_extraction_ms"] = llm_ext_ms
                meta["_llm_entity_extraction_status"] = llm_ext_status
                meta["_llm_entity_count"] = llm_ext_count
            hits.append(RetrievalHit(
                id=f"graph:{entity_name}",
                content=f"Graph entity: {entity_name} — connected to query entities via knowledge graph.",
                score=1.0 - (i / max(len(expanded), 1)) * 0.5,
                source_channel=CHANNEL_NAME,
                metadata=meta,
                rank_in_channel=i + 1,
            ))
        return hits

    orchestrator.register_channel(
        CHANNEL_NAME,
        safe_retriever(CHANNEL_NAME, _graph_retriever, log_level="debug"),
    )
    return failures
