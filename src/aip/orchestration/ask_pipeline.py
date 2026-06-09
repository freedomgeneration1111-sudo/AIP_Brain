"""Source-grounded ask pipeline — retrieve, assemble, dispatch, persist.

Contract: resolve project → multi-channel retrieval (FTS + Vector + Graph +
Wiki + Procedural + Corpus via RetrievalOrchestrator with RRF fusion) →
SmartContextPacker for budget-aware context assembly → model dispatch →
source-grounded answer with provenance references → optional ECS artifact
save → session trace recording.

Search backends: LexicalStore (persistent FTS5, primary) + VectorStore
(semantic, supplementary). EntityExtractor supports noun-phrase + graph-fuzzy
+ optional LLM entity extraction. ChannelSelector auto-enables channels
based on query signals. Per-channel budgets via OrchestratorConfig.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from aip.foundation.protocols import (
    ArtifactStore,
    EcsStore,
    EmbeddingProvider,
    EventStore,
    LexicalStore,
    ModelProvider,
    ProjectStore,
    VectorStore,
)
from aip.foundation.schemas.ask import AskResult, AskSource, SourceReference
from aip.foundation.schemas.retrieval import RetrievalHit, RetrievalTrace
from aip.orchestration.retrieval_orchestrator import (
    OrchestratorCache,
    OrchestratorConfig,
    RetrievalOrchestrator,
    get_orchestrator_cache,
)
from aip.orchestration.smart_context_packer import (
    PackedContext,
    PackerConfig,
    SmartContextPacker,
)

logger = logging.getLogger(__name__)

_orchestrator_cache: OrchestratorCache = get_orchestrator_cache()


def _format_source_citations(sources: list[SourceReference]) -> list[str]:
    """Generate inline source citation strings for the answer.

    Format: [source: <conversation_title>/<chunk_id>]
    or the shorter [source: <source_id>] for non-conversation sources.
    """
    citations = []
    for src in sources:
        if src.source_type == "conversation_chunk" and src.metadata.get("conversation_id"):
            conv_id = src.metadata["conversation_id"]
            citations.append(f"[source: {conv_id}/{src.source_id}]")
        else:
            citations.append(f"[source: {src.source_id}]")
    return citations


def _hit_type_matches(hit: RetrievalHit, source_filter: AskSource) -> bool:
    """Check if a RetrievalHit's source type matches the requested filter.

    Ingested conversation chunks have metadata.type == "conversation_chunk".
    Other indexed content (compiled knowledge, generated artifacts, wiki
    articles, procedural guides) are considered "artifacts" for filtering.
    """
    meta = hit.metadata or {}
    hit_type = meta.get("type", "")

    if source_filter == "all":
        return True
    elif source_filter == "ingested":
        return hit_type == "conversation_chunk"
    elif source_filter == "artifacts":
        return hit_type != "conversation_chunk"
    return True


# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------


async def _resolve_project(
    project_name: str,
    project_store: ProjectStore,
) -> dict | None:
    """Resolve a project by name, then by project_id as fallback."""
    projects = await project_store.list_projects()
    for p in projects:
        if p.get("name") == project_name:
            return p
    for p in projects:
        if p.get("project_id") == project_name:
            return p
    return None


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def _sanitize_fts_query(query: str) -> str:
    """Sanitize a user query for FTS5 MATCH syntax.

    FTS5 has special syntax for operators like AND, OR, NOT, NEAR, *, ^, etc.
    Questions from users often contain ?, !, and other characters that
    are not valid in FTS5 MATCH expressions. This function extracts
    clean word tokens and joins them with AND for FTS5 matching.
    """
    import re

    cleaned = re.sub(r'[?!.*+\-^(){}|~"\\]', " ", query)
    tokens = cleaned.split()
    stop_words = {"a", "an", "the", "is", "are", "was", "were", "be", "been",
                  "being", "have", "has", "had", "do", "does", "did", "will",
                  "would", "could", "should", "may", "might", "shall", "can",
                  "of", "in", "to", "for", "with", "on", "at", "by", "from",
                  "it", "its", "we", "our", "you", "your", "this", "that",
                  "what", "which", "who", "whom", "how", "when", "where", "why",
                  "about", "there", "here", "these", "those", "been", "some",
                  "very", "also", "just", "than", "then", "so", "if", "or",
                  "not", "no", "but", "and", "up", "out", "into", "over"}
    meaningful = [t for t in tokens if len(t) >= 2 and t.lower() not in stop_words]

    if not meaningful:
        meaningful = [t for t in tokens if len(t) >= 1 and t.lower() not in stop_words]

    if not meaningful:
        meaningful = [t for t in tokens[:3] if t]

    if not meaningful:
        return query

    return " AND ".join(meaningful)


# ---------------------------------------------------------------------------
# Multi-channel retrieval via RetrievalOrchestrator
# ---------------------------------------------------------------------------


def _register_retriever_channels(
    orchestrator: RetrievalOrchestrator,
    stores: AskStores,
    config: dict | None = None,
) -> None:
    """Register all available retriever channels on an orchestrator.

    Only registers channels for stores that are actually available.
    Called once per orchestrator instance (via OrchestratorCache).
    Channels: fts, vector, corpus, graph, wiki, procedural.
    """
    # FTS channel — always available (LexicalStore)
    if not orchestrator.is_registered("fts"):
        async def _fts_retriever(query: str) -> list[RetrievalHit]:
            fts_query = _sanitize_fts_query(query)
            try:
                chunks = await stores.lexical_store.search(
                    fts_query, domain=None, limit=30,
                )
            except Exception as exc:
                logger.warning("FTS retriever failed: %s", exc)
                return []
            hits = []
            for i, chunk in enumerate(chunks):
                hits.append(RetrievalHit(
                    id=chunk.id,
                    content=chunk.content or "",
                    score=chunk.score,
                    source_channel="fts",
                    domain=chunk.domain or "",
                    metadata=chunk.metadata or {},
                    rank_in_channel=i + 1,
                ))
            return hits
        orchestrator.register_channel("fts", _fts_retriever)

    # Vector channel — when embedding provider + vector store available
    has_vector_deps = (
        stores.vector_store is not None and stores.embedding_provider is not None
    )
    if not orchestrator.is_registered("vector") and has_vector_deps:
        _vec_store = stores.vector_store
        _embed_prov = stores.embedding_provider

        async def _vector_retriever(query: str) -> list[RetrievalHit]:
            try:
                query_vec = await _embed_prov.embed(query)
                if not query_vec or len(query_vec) == 0:
                    return []
                chunks = await _vec_store.retrieve(query_vec, domain=None, top_k=20)
            except Exception as exc:
                logger.debug("Vector retriever failed (non-fatal): %s", exc)
                return []
            hits = []
            for i, chunk in enumerate(chunks):
                hits.append(RetrievalHit(
                    id=chunk.id,
                    content=chunk.content or "",
                    score=chunk.score,
                    source_channel="vector",
                    domain=chunk.domain or "",
                    metadata=chunk.metadata or {},
                    rank_in_channel=i + 1,
                ))
            return hits
        orchestrator.register_channel("vector", _vector_retriever)

    # Corpus (CorpusTurnStore) channel — when available
    if not orchestrator.is_registered("corpus") and stores.corpus_turn_store is not None:
        _cts = stores.corpus_turn_store

        async def _corpus_retriever(query: str) -> list[RetrievalHit]:
            fts_query = _sanitize_fts_query(query)
            try:
                corpus_turns = await _cts.search(
                    query=fts_query,
                    primary_domain=None,
                    limit=30,
                )
            except Exception as exc:
                logger.warning("Corpus retriever failed: %s", exc)
                return []
            hits = []
            for i, turn in enumerate(corpus_turns):
                position_score = 1.0 - (i / max(len(corpus_turns), 1)) * 0.5
                importance_boost = float(turn.importance or 0.0) * 0.3
                hits.append(RetrievalHit(
                    id=turn.turn_id,
                    content=turn.searchable_text or "",
                    score=position_score + importance_boost,
                    source_channel="corpus",
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
        orchestrator.register_channel("corpus", _corpus_retriever)

    # Graph channel — PPR-based graph retrieval with EntityExtractor
    if not orchestrator.is_registered("graph"):
        from aip.orchestration.entity_extractor import EntityExtractor, EntityExtractorConfig

        _entity_extractor_config = EntityExtractorConfig(
            strategy="hybrid",
            use_graph_fuzzy=True,
        )

        # Wire LLM entity extraction if ModelProvider available
        _llm_entity_fn = None
        if stores.model_provider is not None:
            try:
                from aip.orchestration.entity_extractor import create_llm_entity_fn
                _llm_entity_fn = create_llm_entity_fn(
                    model_provider=stores.model_provider,
                    slot_name=_entity_extractor_config.llm_entity_extraction_model,
                    fallback_slot="synthesis",
                )
                # Enable hybrid_llm mode when model provider is available
                _entity_extractor_config.entity_extraction_mode = "hybrid_llm"
            except Exception as exc:
                logger.debug("LLM entity extraction wiring failed (non-fatal): %s", exc)

        async def _graph_retriever(query: str) -> list[RetrievalHit]:
            """Graph retriever: extract entities from query, run PPR, surface related nodes.

            Tracks LLM entity extraction timing and status in
            hit metadata (``_llm_entity_extraction_ms``, ``_llm_entity_extraction_status``,
            ``_llm_entity_count``) so the orchestrator can transfer it to the
            RetrievalTrace for dashboard observability.
            """
            llm_ext_ms = 0.0
            llm_ext_status = "not_used"
            llm_ext_count = 0

            try:
                from aip.orchestration.graph_retrieval import GraphRetriever

                _graph_store = getattr(stores, "graph_store", None)
                if _graph_store is None:
                    # Try to create a GraphStore from the stores' db_path
                    _db_path = getattr(stores, "_db_path", None)
                    if _db_path is None:
                        # Attempt default path
                        import os
                        _db_path = os.environ.get("AIP_DB_PATH", "db/state.db")
                    from aip.adapter.graph_store import GraphStore
                    _graph_store = GraphStore(_db_path)
                    await _graph_store.initialize()

                retriever = GraphRetriever(_graph_store)
                extractor = EntityExtractor(
                    config=_entity_extractor_config,
                    graph_store=_graph_store,
                    llm_fn=_llm_entity_fn,
                )

                # Track LLM entity extraction timing
                import time as _time
                ext_start = _time.monotonic()

                # Use EntityExtractor for robust entity extraction
                # (noun-phrase + graph-fuzzy + optional LLM fallback)
                seed_entities = await extractor.extract_async(
                    query, graph_store=_graph_store,
                )

                ext_elapsed = (_time.monotonic() - ext_start) * 1000.0

                # Determine LLM usage status from config and results
                if _llm_entity_fn is not None and _entity_extractor_config.entity_extraction_mode != "local":
                    if ext_elapsed > 0 and len(seed_entities) > 0:
                        # If extraction took significant time and we have entities,
                        # LLM was likely used (or at least attempted)
                        llm_ext_status = "success"
                        llm_ext_count = len(seed_entities)
                    elif ext_elapsed > 5.0:
                        # Took time but no entities — LLM likely failed
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
            except Exception as exc:
                logger.debug("Graph retriever failed (non-fatal): %s", exc)
                llm_ext_status = "failed"
                llm_ext_ms = 0.0
                return []

            if not expanded:
                return []

            # Convert expanded graph entities into RetrievalHit instances.
            # The graph channel surfaces *entity names* and their graph context,
            # which augments the other channels rather than returning raw content.
            #
            # Include LLM entity extraction observability data
            # in the first hit's metadata so the orchestrator can transfer
            # it to the RetrievalTrace.
            hits: list[RetrievalHit] = []
            for i, entity_name in enumerate(expanded):
                meta = {
                    "type": "graph_entity",
                    "entity_name": entity_name,
                }
                # Only stamp the first hit with LLM observability data
                # to avoid duplication
                if i == 0:
                    meta["_llm_entity_extraction_ms"] = llm_ext_ms
                    meta["_llm_entity_extraction_status"] = llm_ext_status
                    meta["_llm_entity_count"] = llm_ext_count
                hits.append(RetrievalHit(
                    id=f"graph:{entity_name}",
                    content=f"Graph entity: {entity_name} — connected to query entities via knowledge graph.",
                    score=1.0 - (i / max(len(expanded), 1)) * 0.5,
                    source_channel="graph",
                    metadata=meta,
                    rank_in_channel=i + 1,
                ))
            return hits
        orchestrator.register_channel("graph", _graph_retriever)

    # Wiki channel — retrieve approved wiki articles
    if not orchestrator.is_registered("wiki"):
        async def _wiki_retriever(query: str) -> list[RetrievalHit]:
            """Wiki retriever: find approved wiki articles relevant to the query."""
            try:
                if stores.artifact_store is None or stores.ecs_store is None:
                    return []

                arts = await stores.artifact_store.list_artifacts_by_metadata(
                    key="artifact_type", value="beast_wiki", limit=50,
                )
            except Exception as exc:
                logger.debug("Wiki retriever failed (non-fatal): %s", exc)
                return []

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
                    state = await stores.ecs_store.current_state(art_id)
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
                    content=content[:2000],  # cap content length
                    score=score,
                    source_channel="wiki",
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
        orchestrator.register_channel("wiki", _wiki_retriever)

    # Procedural channel — retrieve how-to guides
    if not orchestrator.is_registered("procedural"):
        async def _procedural_retriever(query: str) -> list[RetrievalHit]:
            """Procedural retriever: find how-to guides and step-by-step procedures."""
            try:
                if stores.artifact_store is None:
                    return []

                # Search for procedural artifacts
                procs = await stores.artifact_store.list_artifacts_by_metadata(
                    key="artifact_type", value="procedural_guide", limit=20,
                )
                # Also search compiled_knowledge which may contain procedural content
                compiled = await stores.artifact_store.list_artifacts_by_metadata(
                    key="artifact_type", value="compiled_knowledge", limit=20,
                )
                all_arts = procs + compiled
            except Exception as exc:
                logger.debug("Procedural retriever failed (non-fatal): %s", exc)
                return []

            if not all_arts:
                return []

            query_terms = set(query.lower().split())
            procedural_keywords = {"step", "steps", "how to", "procedure", "guide",
                                   "instructions", "process", "method", "tutorial"}
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
                    hits.append(RetrievalHit(
                        id=f"proc:{art_id}",
                        content=(art.get("content", "") or "")[:2000],
                        score=score,
                        source_channel="procedural",
                        domain=meta.get("domain", ""),
                        metadata={
                            "type": "procedural_guide",
                            "artifact_id": art_id,
                            "domain": meta.get("domain", ""),
                        },
                        rank_in_channel=0,  # assigned later by orchestrator
                    ))

            # Sort and assign ranks
            hits.sort(key=lambda h: h.score, reverse=True)
            for i, hit in enumerate(hits[:10]):
                hit.rank_in_channel = i + 1

            return hits[:10]
        orchestrator.register_channel("procedural", _procedural_retriever)


def _retrieval_hit_to_source_ref(hit: RetrievalHit) -> SourceReference:
    """Convert a RetrievalHit to a SourceReference for backward compatibility.

    The ask() function still returns SourceReference in AskResult.sources
    for backward compatibility.  This conversion preserves provenance.
    """
    meta = hit.metadata or {}
    chunk_type = meta.get("type", "unknown")
    conv_id = meta.get("conversation_id", "")
    domain = hit.domain or meta.get("domain", "")

    if chunk_type == "conversation_chunk" and conv_id:
        title = f"conversation:{conv_id}"
    else:
        title = hit.id

    content = hit.content or ""
    snippet = content[:200].replace("\n", " ") if content else ""

    return SourceReference(
        source_id=hit.id,
        source_type=chunk_type,
        title=title,
        score=hit.rrf_score or hit.score,
        content_snippet=snippet,
        domain=domain,
        metadata={
            "conversation_id": conv_id,
            "source_format": meta.get("source_format", ""),
            "source_channel": hit.source_channel,
            "rrf_score": hit.rrf_score,
        },
    )


async def _search_sources_with_trace(
    query: str,
    stores: AskStores,
    source_filter: AskSource = "all",
    max_sources: int = 10,
    session_id: str = "",
    config: dict | None = None,
    enable_fts: bool = True,
    enable_vector: bool = True,
    enable_graph: bool = False,
    enable_wiki: bool = False,
    enable_procedural: bool = False,
    auto_channel_selection: bool = True,
) -> tuple[list[SourceReference], RetrievalTrace | None, PackedContext | None]:
    """Search for sources using the multi-channel RetrievalOrchestrator.

    Primary retrieval path: RetrievalOrchestrator with parallel dispatch
    and RRF fusion, followed by SmartContextPacker for budget-aware
    context assembly.  When ``auto_channel_selection`` is True (default),
    ChannelSelector auto-enables relevant channels based on query signals;
    explicit ``enable_*`` parameters always take precedence.

    The orchestrator instance is cached via OrchestratorCache so channels
    are registered only once.

    Returns:
        Tuple of (source_references, retrieval_trace, packed_context).
    """
    # Build a fingerprint of the store identities for cache keying
    store_key = id(stores.lexical_store) ^ id(stores.vector_store) ^ id(stores.corpus_turn_store)

    # Get or create a cached orchestrator
    orchestrator = _orchestrator_cache.get_or_create(
        store_key=store_key,
        register_fn=lambda orch: _register_retriever_channels(orch, stores, config),
    )

    # Build per-call config (supports per-call enable_* toggles)
    # Sprint 6.1: coverage-aware vector enablement and hybrid channel weights.
    vector_available = enable_vector and stores.vector_store is not None and stores.embedding_provider is not None

    # Check embedding coverage to decide whether to enable hybrid retrieval
    vector_enabled = vector_available
    if vector_available and stores.corpus_turn_store is not None:
        try:
            progress = await stores.corpus_turn_store.get_embedding_progress()
            coverage = progress.get("percentage", 0.0) / 100.0
            min_coverage = 0.10  # 10% minimum for hybrid mode
            if coverage < min_coverage:
                logger.debug(
                    "vector_disabled_low_coverage",
                    coverage_percent=progress.get("percentage", 0.0),
                    min_coverage_percent=min_coverage * 100,
                )
                vector_enabled = False
        except Exception as exc:
            logger.debug("embedding_progress_check_failed (non-fatal): %s", exc)

    orch_config = OrchestratorConfig(
        enable_fts=enable_fts,
        enable_vector=vector_enabled,
        enable_graph=enable_graph,
        enable_wiki=enable_wiki,
        enable_procedural=enable_procedural,
        max_hits=max_sources * 3,
    )

    # Adaptive channel selection: auto-enable channels based on query signals.
    # Only enables (never disables). Set auto_channel_selection=False for manual control.
    if auto_channel_selection:
        try:
            from aip.orchestration.channel_selector import ChannelSelector
            _channel_selector = ChannelSelector()
            orch_config = _channel_selector.apply_to_config(query, orch_config)
        except Exception as exc:
            logger.debug("Channel selector failed (non-fatal): %s", exc)

    try:
        hits, trace = await orchestrator.retrieve(
            query=query,
            config=orch_config,
            session_id=session_id,
        )
    except Exception as exc:
        logger.error("Orchestrator retrieval failed: %s", exc)
        return [], None, None

    # Filter by source type
    if source_filter != "all":
        hits = [h for h in hits if _hit_type_matches(h, source_filter)]

    # Limit to max_sources
    hits = hits[:max_sources]

    # Pack context via SmartContextPacker
    packer_config = PackerConfig(
        max_context_tokens=4000,
        max_hits=max_sources,
        include_metadata=True,
    )
    packer = SmartContextPacker(config=packer_config)
    packed = packer.pack(hits, query=query)

    # Convert to SourceReference for backward compatibility
    sources = [_retrieval_hit_to_source_ref(h) for h in hits]

    return sources, trace, packed


# ---------------------------------------------------------------------------
# Store creation
# ---------------------------------------------------------------------------


class AskStores:
    """Container for the stores needed by the ask pipeline.

    Shares the same persistent stores as the ingestion pipeline so that
    ``aip ask`` reads from the same data that ``aip ingest`` wrote.
    """

    def __init__(
        self,
        artifact_store: ArtifactStore,
        lexical_store: LexicalStore,
        vector_store: VectorStore | None,
        event_store: EventStore | None,
        project_store: ProjectStore,
        ecs_store: EcsStore | None = None,
        model_provider: ModelProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        corpus_turn_store: Any = None,
        graph_store: Any = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.lexical_store = lexical_store
        self.vector_store = vector_store
        self.event_store = event_store
        self.project_store = project_store
        self.ecs_store = ecs_store
        self.model_provider = model_provider
        self.embedding_provider = embedding_provider
        self.corpus_turn_store = corpus_turn_store
        self.graph_store = graph_store

    async def close(self) -> None:
        """Close all stores that have a close method."""
        for store in (
            self.artifact_store,
            self.lexical_store,
            self.event_store,
            self.project_store,
            self.ecs_store,
            self.model_provider,
            self.embedding_provider,
            self.corpus_turn_store,
        ):
            if store is not None and hasattr(store, "close"):
                try:
                    await store.close()
                except Exception:
                    pass


async def create_ask_stores(db_path: str) -> AskStores:
    """Factory: create and initialize all stores needed for the ask pipeline.

    Uses the same database paths as ``create_ingestion_stores()`` so that
    ask reads from the same persistent stores that ingest writes to.
    All stores (including VectorStore) are SQLite-backed and persistent.
    """
    import os

    from aip.adapter.artifact_store_versioned import VersionedArtifactStore
    from aip.adapter.ecs_store_persistent import PersistentEcsStore
    from aip.adapter.event_store_queryable import QueryableEventStore
    from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore
    from aip.adapter.model_slot_resolver import ModelSlotResolver
    from aip.adapter.project.sqlite_project_store import SqliteProjectStore
    from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore

    # Same paths as create_ingestion_stores()
    artifact_store = VersionedArtifactStore(db_path)
    await artifact_store.initialize()

    lexical_db = os.path.join(os.path.dirname(db_path), "lexical.db")
    lexical_store = SqliteFts5LexicalStore(lexical_db)
    await lexical_store.initialize()

    event_store = QueryableEventStore(db_path)
    await event_store.initialize()

    project_store = SqliteProjectStore(db_path)
    await project_store.initialize()

    ecs_store = PersistentEcsStore(db_path, event_store=event_store)
    await ecs_store.initialize()

    # Model provider — load from config if available
    model_provider = None
    try:
        config = _load_config()
        if config is not None:
            model_provider = ModelSlotResolver(config)
    except Exception as exc:
        logger.info("No model provider configured: %s", exc)

    # Embedding provider — created BEFORE vector store so it can be passed
    # for real embedding generation in store().
    embedding_provider = None
    try:
        config = _load_config()
        if config is not None:
            from aip.adapter.api.app import _create_embedding_provider
            embedding_provider = _create_embedding_provider(config)
    except Exception:
        pass  # graceful: no embedding provider — lexical-only search

    # Use persistent SqliteVssVectorStore instead of InMemoryVectorStore
    # so that vectors survive process restarts.  The VSS extension may not be
    # available, in which case the store degrades to brute-force search over
    # the embedding_json column — but data is still persistent.
    vector_db = os.path.join(os.path.dirname(db_path), "vectors.db")
    vector_store = SqliteVssVectorStore(
        db_path=vector_db,
        dimensions=768,
        embedding_provider=embedding_provider,
    )
    await vector_store.initialize()

    # CorpusTurnStore — the canonical corpus of ingested turns (project-agnostic)
    corpus_turn_store = None
    try:
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        corpus_turn_store = CorpusTurnStore(db_path)
        await corpus_turn_store.initialize()
    except Exception as exc:
        logger.info("CorpusTurnStore not available for ask pipeline: %s", exc)

    # GraphStore — SQLite-backed knowledge graph for graph retrieval channel
    graph_store = None
    try:
        from aip.adapter.graph_store import GraphStore
        graph_store = GraphStore(db_path)
        await graph_store.initialize()
    except Exception as exc:
        logger.info("GraphStore not available for ask pipeline: %s", exc)

    return AskStores(
        artifact_store=artifact_store,
        lexical_store=lexical_store,
        vector_store=vector_store,
        event_store=event_store,
        project_store=project_store,
        ecs_store=ecs_store,
        model_provider=model_provider,
        embedding_provider=embedding_provider,
        corpus_turn_store=corpus_turn_store,
        graph_store=graph_store,
    )


def _load_config() -> dict | None:
    """Load AIP config from default location."""
    import os

    config_path = os.environ.get("AIP_CONFIG_PATH", "config/aip.config.toml")
    if not os.path.exists(config_path):
        return None

    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return None

    with open(config_path, "rb") as f:
        return tomllib.load(f)


# ---------------------------------------------------------------------------
# Main ask function
# ---------------------------------------------------------------------------


async def ask(
    question: str,
    project_name: str,
    stores: AskStores,
    source: AskSource = "all",
    max_sources: int = 10,
    save_artifact: bool = False,
    model_slot: str = "synthesis",
    session_id: str | None = None,
    system_prompt_modifier: str = "",
) -> AskResult:
    """Execute a source-grounded ask query against the AIP knowledge substrate.

    This is the main entry point for the ask pipeline. It:
    1. Resolves the project by name
    2. Searches project memory for relevant sources
    3. Assembles context from found sources
    4. Dispatches to the configured model
    5. Generates a source-grounded answer
    6. Optionally saves the answer as a draft artifact
    7. Records the session trace

    Failure modes are explicit and never silently produce fake results.
    """
    # Generate session ID if not provided
    if session_id is None:
        session_id = f"ask:{uuid.uuid4()}"

    # Step 1: Resolve project (soft — corpus is project-agnostic, so a missing
    # project does NOT block the ask. We still search all corpus turns.)
    project = None
    project_id = project_name
    project_domain = project_name
    try:
        project = await _resolve_project(project_name, stores.project_store)
    except Exception as exc:
        logger.warning("Failed to resolve project '%s' (non-fatal): %s", project_name, exc)

    if project is not None:
        project_id = project.get("project_id", project_name)
        project_domain = project.get("domain") or project_name

    # Step 2: Search for relevant sources (primary path: orchestrator + packer)
    # _search_sources_with_trace() is the primary path.
    # It uses RetrievalOrchestrator with parallel dispatch and RRF fusion,
    # then SmartContextPacker for budget-aware context assembly.
    retrieval_trace: RetrievalTrace | None = None
    packed_context: PackedContext | None = None
    try:
        sources, retrieval_trace, packed_context = await _search_sources_with_trace(
            query=question,
            stores=stores,
            source_filter=source,
            max_sources=max_sources,
            session_id=session_id,
        )
    except Exception as exc:
        logger.error("Source search failed: %s", exc)
        return AskResult(
            status="NO_PROJECT_MEMORY",
            answer=f"Error searching project memory: {exc}",
            prompt=question,
            project_name=project_name,
            project_id=project_id,
            session_id=session_id,
            errors=[str(exc)],
        )

    if not sources:
        return AskResult(
            status="NO_PROJECT_MEMORY",
            answer=(
                f"No relevant sources found in project '{project_name}' "
                f"for query: '{question}'. "
                f"Try ingesting conversations first with: aip ingest file <path> --domain {project_domain}"
            ),
            prompt=question,
            project_name=project_name,
            project_id=project_id,
            session_id=session_id,
            sources=[],
        )

    # Step 3: Check model provider
    if stores.model_provider is None:
        # No model configured — return sources but no answer
        return AskResult(
            status="NEEDS_CONFIGURATION",
            answer=(
                "NEEDS_CONFIGURATION: No model provider is configured. "
                "Set AIP_SYNTHESIS_BASE_URL and AIP_SYNTHESIS_MODEL environment "
                "variables, or configure the synthesis slot in config/aip.config.toml. "
                "Below are the retrieved sources that would have been used:"
            ),
            sources=sources,
            prompt=question,
            project_name=project_name,
            project_id=project_id,
            session_id=session_id,
            model_slot=model_slot,
        )

    # Step 4: Assemble context via SmartContextPacker
    # SmartContextPacker is the only context assembly path.
    # packed_context is always set by _search_sources_with_trace().
    context = packed_context.context_text if packed_context else "No relevant sources found in project memory."

    # Step 5: Dispatch to model
    model_provider_name = ""
    model_name = ""
    answer_content = ""
    model_errors: list[str] = []

    try:
        base_system = (
            "You are AIP, a source-grounded knowledge assistant. "
            "Answer the user's question based ONLY on the provided sources. "
            "Cite sources using [source: <source_id>] notation. "
            "If the sources do not contain enough information, say so explicitly. "
            "Do not fabricate information not present in the sources."
        )
        # Prepend chat mode modifier if provided (per AIP_UNIFIED_CHAT_SPEC)
        system_content = (
            f"{system_prompt_modifier}\n\n---\n\n{base_system}"
            if system_prompt_modifier
            else base_system
        )
        messages = [
            {
                "role": "system",
                "content": system_content,
            },
            {
                "role": "user",
                "content": f"Project: {project_name}\n\nSources:\n{context}\n\nQuestion: {question}",
            },
        ]

        result = await stores.model_provider.call(model_slot, messages, temperature=0.7)

        if result.get("error"):
            model_errors.append(result.get("error_message", "Model call failed"))
            answer_content = ""
        else:
            answer_content = result.get("content", "")
            model_provider_name = result.get("model", "")
            model_name = model_provider_name

    except Exception as exc:
        logger.error("Model call failed: %s", exc)
        model_errors.append(str(exc))
        answer_content = ""

    # Step 6: Handle model failure
    if model_errors:
        # Record failure in event trace
        await _record_trace(
            stores=stores,
            session_id=session_id,
            project_id=project_id,
            question=question,
            sources=sources,
            model_slot=model_slot,
            model_provider=model_provider_name,
            answer="",
            artifact_id="",
            errors=model_errors,
            status="MODEL_FAILURE",
        )

        return AskResult(
            status="MODEL_FAILURE",
            answer=(
                f"Model call failed: {'; '.join(model_errors)}. "
                f"Retrieved sources are preserved below for inspection."
            ),
            sources=sources,
            prompt=question,
            project_name=project_name,
            project_id=project_id,
            session_id=session_id,
            model_slot=model_slot,
            model_provider=model_provider_name,
            errors=model_errors,
        )

    # Step 7: Append source citations if not already present
    citations = _format_source_citations(sources)
    if citations and "[source:" not in answer_content:
        answer_content += "\n\nSources:\n" + "\n".join(citations)

    # Step 8: Optionally save as artifact
    artifact_id = ""
    artifact_errors: list[str] = []

    if save_artifact:
        try:
            artifact_id = f"ask:{hashlib.sha256(f'{project_id}:{question}'.encode()).hexdigest()[:24]}"

            artifact_metadata = {
                "artifact_type": "ask_answer",
                "project_id": project_id,
                "project_name": project_name,
                "prompt": question,
                "model_slot": model_slot,
                "model_name": model_name,
                "source_ids": [s.source_id for s in sources],
                "source_types": [s.source_type for s in sources],
                "session_id": session_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

            # Write to ArtifactStore
            await stores.artifact_store.write(artifact_id, answer_content, artifact_metadata)

            # ECS transition: SPECIFIED → GENERATED (draft, pending review)
            # This follows the existing lifecycle — the artifact is NOT auto-approved
            if stores.ecs_store is not None:
                try:
                    await stores.ecs_store.transition(
                        artifact_id=artifact_id,
                        from_state=None,
                        to_state="GENERATED",
                        actor="ask_pipeline",
                        reason="Generated by ask pipeline — pending DEFINER review",
                    )
                except Exception as exc:
                    # ECS transition failed but artifact is saved
                    artifact_errors.append(f"ECS transition failed: {exc}")
                    logger.warning("ECS transition failed for artifact '%s': %s", artifact_id, exc)

            # Also index the saved artifact in LexicalStore so future
            # asks can find it via --source artifacts
            try:
                await stores.lexical_store.index_document(
                    doc_id=f"artifact:{artifact_id}",
                    content=answer_content[:2000],
                    domain=project_domain,
                    metadata={
                        "type": "ask_answer",
                        "artifact_id": artifact_id,
                        "project_id": project_id,
                        "model_slot": model_slot,
                    },
                )
            except Exception as exc:
                artifact_errors.append(f"Lexical indexing of saved artifact failed: {exc}")
                logger.debug("Failed to index saved artifact: %s", exc)

        except Exception as exc:
            logger.error("Artifact save failed: %s", exc)
            artifact_errors.append(f"Artifact save failed: {exc}")

            # Record the failure in event trace
            await _record_trace(
                stores=stores,
                session_id=session_id,
                project_id=project_id,
                question=question,
                sources=sources,
                model_slot=model_slot,
                model_provider=model_provider_name,
                answer=answer_content,
                artifact_id="",
                errors=artifact_errors,
                status="ARTIFACT_SAVE_FAILURE",
            )

            return AskResult(
                status="ARTIFACT_SAVE_FAILURE",
                answer=answer_content,
                sources=sources,
                model_slot=model_slot,
                model_provider=model_provider_name,
                session_id=session_id,
                project_id=project_id,
                project_name=project_name,
                prompt=question,
                errors=artifact_errors,
            )

    # Step 9: Record successful trace (includes retrieval trace data)
    await _record_trace(
        stores=stores,
        session_id=session_id,
        project_id=project_id,
        question=question,
        sources=sources,
        model_slot=model_slot,
        model_provider=model_provider_name,
        answer=answer_content,
        artifact_id=artifact_id,
        errors=artifact_errors,
        status="OK",
        retrieval_trace=retrieval_trace,
    )

    return AskResult(
        status="OK",
        answer=answer_content,
        sources=sources,
        model_slot=model_slot,
        model_provider=model_provider_name,
        artifact_id=artifact_id,
        session_id=session_id,
        project_id=project_id,
        project_name=project_name,
        prompt=question,
        errors=artifact_errors,
    )


# ---------------------------------------------------------------------------
# Trace recording
# ---------------------------------------------------------------------------


async def _record_trace(
    stores: AskStores,
    session_id: str,
    project_id: str,
    question: str,
    sources: list[SourceReference],
    model_slot: str,
    model_provider: str,
    answer: str,
    artifact_id: str,
    errors: list[str],
    status: str,
    retrieval_trace: RetrievalTrace | None = None,
) -> None:
    """Record the full ask session trace in EventStore.

    This ensures that every ask query (successful or failed) leaves
    an audit trail: what was asked, what context was used, what answer
    was generated, and what happened.

    Also records RetrievalTrace data (channel timing, RRF
    fusion stats, quality-gate verdict) when available.
    """
    if stores.event_store is None:
        return

    # Build retrieval trace metadata
    retrieval_meta: dict[str, Any] = {}
    if retrieval_trace is not None:
        retrieval_meta = {
            "retrieval_round": retrieval_trace.round_number,
            "retrieval_channels": json.dumps(retrieval_trace.channels_queried),
            "retrieval_total_ms": retrieval_trace.total_elapsed_ms,
            "retrieval_per_channel_ms": json.dumps(retrieval_trace.per_channel_elapsed_ms),
            "retrieval_hits_before_fusion": retrieval_trace.hits_before_fusion,
            "retrieval_hits_after_fusion": retrieval_trace.hits_after_fusion,
            "retrieval_hits_after_gate": retrieval_trace.hits_after_quality_gate,
            "retrieval_verdict": retrieval_trace.verdict,
            # Channel contributions and LLM entity extraction
            # observability data stored in trace for dashboard access
            "retrieval_channel_contributions": json.dumps(retrieval_trace.channel_contributions),
            "retrieval_llm_entity_extraction_ms": retrieval_trace.llm_entity_extraction_ms,
            "retrieval_llm_entity_extraction_status": retrieval_trace.llm_entity_extraction_status,
            "retrieval_llm_entity_count": retrieval_trace.llm_entity_count,
        }

    try:
        await stores.event_store.write_event(
            event_type="ask_query",
            actor="ask_pipeline",
            artifact_id=artifact_id or f"session:{session_id}",
            from_state=None,
            to_state=status,
            # Additional trace metadata via kwargs
            session_id=session_id,
            project_id=project_id,
            prompt=question[:500],
            source_count=len(sources),
            source_ids=json.dumps([s.source_id for s in sources[:20]]),
            model_slot=model_slot,
            model_provider=model_provider,
            answer_digest=hashlib.sha256(answer.encode()).hexdigest()[:16] if answer else "",
            artifact_saved=bool(artifact_id),
            error_count=len(errors),
            errors=json.dumps(errors[:5]) if errors else "[]",
            **retrieval_meta,
        )
    except Exception as exc:
        logger.debug("Failed to record ask trace: %s", exc)


# ---------------------------------------------------------------------------
# Context inspection
# ---------------------------------------------------------------------------


def format_context_display(sources: list[SourceReference], max_sources: int = 10) -> str:
    """Format the retrieved context for display via --show-context.

    Shows each source with its ID, type, score, domain, and a content
    snippet so the user can verify what AIP retrieved before generation.
    """
    if not sources:
        return "No sources retrieved."

    lines = ["=== Retrieved Context ===\n"]
    for i, src in enumerate(sorted(sources, key=lambda s: s.score, reverse=True)[:max_sources], 1):
        lines.append(f"Source {i}:")
        lines.append(f"  ID:    {src.source_id}")
        lines.append(f"  Type:  {src.source_type}")
        lines.append(f"  Score: {src.score:.4f}")
        lines.append(f"  Domain: {src.domain}")
        lines.append(f"  Title: {src.title}")
        lines.append(f"  Snippet: {src.content_snippet[:150]}...")
        lines.append("")

    lines.append(f"Total sources: {len(sources)} (showing top {min(len(sources), max_sources)})")
    return "\n".join(lines)
