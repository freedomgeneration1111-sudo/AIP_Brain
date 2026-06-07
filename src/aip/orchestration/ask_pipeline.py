"""Source-grounded ask pipeline — retrieve, assemble, dispatch, persist.

Orchestrates the ask work loop:
1. Resolve project by name
2. Search project memory using LexicalStore (FTS5) and VectorStore (when available)
3. Filter sources by type (ingested conversations, project artifacts, or all)
4. Assemble an inspectable context packet
5. Dispatch to model through the existing ModelProvider/ModelSlotResolver
6. Generate a source-grounded answer with provenance references
7. Optionally save the answer as a draft artifact with ECS lifecycle
8. Record the full session trace in EventStore

Uses existing AIP primitives — no parallel storage system.
The primary search backend is LexicalStore (persistent FTS5) to ensure
that content ingested via ``aip ingest`` survives process restarts.
VectorStore is used as a supplementary semantic search when available.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

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
from aip.foundation.schemas.retrieval import Chunk

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source filtering
# ---------------------------------------------------------------------------


def _source_type_matches(chunk: Chunk, source_filter: AskSource) -> bool:
    """Check if a chunk's source type matches the requested filter.

    Ingested conversation chunks have metadata.type == "conversation_chunk".
    Other indexed content (compiled knowledge, generated artifacts) are
    considered "artifacts" for the purpose of source filtering.
    """
    meta = chunk.metadata or {}
    chunk_type = meta.get("type", "")

    if source_filter == "all":
        return True
    elif source_filter == "ingested":
        return chunk_type == "conversation_chunk"
    elif source_filter == "artifacts":
        return chunk_type != "conversation_chunk"
    return True


def _chunk_to_source_ref(chunk: Chunk) -> SourceReference:
    """Convert a retrieval Chunk to a SourceReference with provenance."""
    meta = chunk.metadata or {}
    chunk_type = meta.get("type", "unknown")
    conv_id = meta.get("conversation_id", "")
    source_format = meta.get("source_format", "")
    domain = chunk.domain or meta.get("domain", "")

    # Build a human-readable title
    if chunk_type == "conversation_chunk" and conv_id:
        title = f"conversation:{conv_id}"
    else:
        title = chunk.id

    content = chunk.content or ""
    snippet = content[:200].replace("\n", " ") if content else ""

    return SourceReference(
        source_id=chunk.id,
        source_type=chunk_type,
        title=title,
        score=chunk.score,
        content_snippet=snippet,
        domain=domain,
        metadata={
            "conversation_id": conv_id,
            "source_format": source_format,
        },
    )


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def _assemble_context(sources: list[SourceReference], max_sources: int = 10) -> str:
    """Build a context string from source references for model input.

    Each source is formatted with its ID and content snippet so the model
    can reference specific sources in its answer. Sources are truncated
    and ordered by score (descending).
    """
    if not sources:
        return "No relevant sources found in project memory."

    # Sort by score descending, take top max_sources
    ranked = sorted(sources, key=lambda s: s.score, reverse=True)[:max_sources]

    parts: list[str] = []
    for i, src in enumerate(ranked, 1):
        parts.append(
            f"[Source {i}: {src.source_id} (score={src.score:.2f}, type={src.source_type})]\n"
            f"{src.content_snippet}"
        )

    return "\n\n".join(parts)


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


# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------


async def _resolve_project(
    project_name: str,
    project_store: ProjectStore,
) -> dict | None:
    """Resolve a project by name or ID from ProjectStore.

    Searches first by name (display name), then by project_id
    (internal identifier) as a fallback. Returns the project dict
    if found, None otherwise.
    """
    projects = await project_store.list_projects()
    # First try matching by name (display name)
    for p in projects:
        if p.get("name") == project_name:
            return p
    # Fallback: match by project_id (internal identifier)
    for p in projects:
        if p.get("project_id") == project_name:
            return p
    return None


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def _sanitize_fts_query(query: str) -> str:
    """Sanitize a user query for FTS5 MATCH syntax.

    Delegates to the canonical sanitize_fts_query in
    orchestration.retrievers.fts_retriever. The local copy
    is kept only as a fallback for the legacy path.
    """
    try:
        from aip.orchestration.retrievers.fts_retriever import sanitize_fts_query
        return sanitize_fts_query(query)
    except ImportError:
        pass

    # Local fallback (legacy path)
    import re

    cleaned = re.sub(r"""[?!.*+\-^(){}|~'"\\]""", " ", query)
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


async def _search_sources(
    query: str,
    project_domain: str | None,
    source_filter: AskSource,
    lexical_store: LexicalStore,
    vector_store: VectorStore | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    max_sources: int = 10,
    config: dict | None = None,
    corpus_turn_store: Any = None,
) -> list[SourceReference]:
    """Search for relevant sources using the unified RetrievalOrchestrator.

    Phase 5.1: Uses FTSRetriever (CorpusTurnStore + LexicalStore) through
    the RetrievalOrchestrator with RRF fusion and importance weighting.
    Falls back to legacy path if the orchestrator fails entirely.

    The orchestrator dispatches to FTSRetriever, applies RRF fusion,
    importance weighting, and budget curation. This replaces the previous
    inline retrieval logic while preserving full backward compatibility.

    Vector search (when available) is still handled via the legacy hybrid
    scoring path and will be migrated to a VectorRetriever in a future sprint.
    """
    # --- Try the unified retrieval architecture (Phase 5.1+) ---
    try:
        from aip.foundation.schemas.retrieval_trace import (
            RetrievalBudget,
            RetrievalQuery,
        )
        from aip.orchestration.retrievers import FTSRetriever, RetrievalOrchestrator

        # Build the orchestrator with FTSRetriever
        fts_retriever = FTSRetriever(
            corpus_turn_store=corpus_turn_store,
            lexical_store=lexical_store,
        )
        orchestrator = RetrievalOrchestrator()
        orchestrator.register_retriever(fts_retriever)

        # Build query and budget
        ret_query = RetrievalQuery(raw_query=query)
        budget = RetrievalBudget(max_sources=max(max_sources * 2, 25))

        # Execute retrieval
        hits, trace = await orchestrator.retrieve(ret_query, budget=budget)

        # Convert RetrievalHit → SourceReference (preserving the same format)
        sources: list[SourceReference] = []
        for hit in hits:
            # Apply source type filter
            hit_type = hit.source_type
            if source_filter == "ingested" and hit_type != "corpus_turn":
                continue
            elif source_filter == "artifacts" and hit_type == "corpus_turn":
                continue

            snippet = hit.snippet or (hit.text[:200].replace("\n", " ") if hit.text else "")
            sources.append(SourceReference(
                source_id=hit.source_id,
                source_type=hit_type,
                title=hit.title or hit.id,
                score=hit.score,
                content_snippet=snippet,
                domain=hit.domain or "",
                metadata={
                    "conversation_id": hit.debug.get("conversation_id", ""),
                    "source_format": hit.debug.get("source", hit.source_type),
                },
            ))

        # If the orchestrator returned results, we're done.
        # Vector hybrid scoring is still handled below for backward compat.
        if sources and vector_store is None:
            return sources[:max_sources]

        # If we have vector store, apply hybrid scoring to the orchestrator
        # results (same logic as before, but on the curated hit list).
        if sources and vector_store is not None and embedding_provider is not None:
            sources = await _apply_vector_hybrid(
                query, sources, vector_store, embedding_provider, config
            )

        if sources:
            return sources[:max_sources]

    except Exception as exc:
        logger.warning("RetrievalOrchestrator failed, falling back to legacy path: %s", exc)

    # --- Legacy fallback path (exact original logic, unchanged) ---
    return await _search_sources_legacy(
        query=query,
        project_domain=project_domain,
        source_filter=source_filter,
        lexical_store=lexical_store,
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        max_sources=max_sources,
        config=config,
        corpus_turn_store=corpus_turn_store,
    )


async def _apply_vector_hybrid(
    query: str,
    sources: list[SourceReference],
    vector_store: VectorStore,
    embedding_provider: EmbeddingProvider,
    config: dict | None = None,
) -> list[SourceReference]:
    """Apply vector hybrid scoring to orchestrator results.

    This preserves the existing vector hybrid scoring logic but operates
    on the curated source list from the RetrievalOrchestrator rather
    than raw chunks. Will be replaced by VectorRetriever in a future sprint.
    """
    try:
        query_vec = await embedding_provider.embed(query)
        if not query_vec or len(query_vec) == 0:
            return sources

        vec_hits = await vector_store.retrieve(query_vec, domain=None, top_k=len(sources) * 2)
        if not vec_hits:
            return sources

        # Build vector score map
        vec_scores: dict[str, float] = {}
        max_vec = max((h.score for h in vec_hits), default=1.0) or 1.0
        for h in vec_hits:
            vec_scores[h.id] = min(1.0, h.score / max_vec)

        # Get weights
        if config is None:
            config = {}
        ret_cfg = config.get("retrieval", {}) if isinstance(config, dict) else {}
        lex_w = float(ret_cfg.get("lexical_weight", 0.4))
        vec_w = float(ret_cfg.get("vector_weight", 0.6))

        # Normalize lexical scores
        max_lex = max((s.score for s in sources), default=1.0) or 1.0
        for src in sources:
            lex_norm = min(1.0, src.score / max_lex)
            vec_norm = vec_scores.get(src.source_id, 0.0)
            src.score = lex_w * lex_norm + vec_w * vec_norm

        # Re-sort
        sources.sort(key=lambda s: s.score, reverse=True)

    except Exception as exc:
        logger.debug("Vector hybrid scoring failed (non-fatal): %s", exc)

    return sources


async def _search_sources_legacy(
    query: str,
    project_domain: str | None,
    source_filter: AskSource,
    lexical_store: LexicalStore,
    vector_store: VectorStore | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    max_sources: int = 10,
    config: dict | None = None,
    corpus_turn_store: Any = None,
) -> list[SourceReference]:
    """Legacy retrieval path — exact original _search_sources logic.

    Used as fallback if the RetrievalOrchestrator fails entirely (AIP-G-02).
    This is the pre-Phase-5.1 code, preserved unchanged for safety.
    """
    all_chunks: list[Chunk] = []

    # Sanitize query for FTS5 MATCH syntax (remove special characters)
    fts_query = _sanitize_fts_query(query)

    # Corpus turn search (project-agnostic — no domain filter)
    # The corpus_turns table has no project_id column; corpus is global.
    # Results come back ordered by FTS5 BM25 rank (most relevant first).
    if corpus_turn_store is not None:
        try:
            corpus_turns = await corpus_turn_store.search(
                query=fts_query,
                primary_domain=None,  # no domain filter — search ALL turns
                limit=max_sources * 3,
            )
            for i, turn in enumerate(corpus_turns):
                # CorpusTurnStore.search() returns results ordered by FTS5 BM25 rank.
                # Use position-based score to preserve relevance ordering (1.0 → ~0.5)
                # plus an importance boost so Beast-tagged high-importance turns rank higher.
                position_score = 1.0 - (i / max(len(corpus_turns), 1)) * 0.5
                importance_boost = float(turn.importance or 0.0) * 0.3
                chunk = Chunk(
                    id=turn.turn_id,
                    content=turn.searchable_text or "",
                    score=position_score + importance_boost,
                    metadata={
                        "type": "conversation_chunk",
                        "conversation_id": turn.conversation_id,
                        "source_format": "corpus_turn",
                        "domain": turn.primary_domain or "",
                        "importance": float(turn.importance or 0.0),
                    },
                    domain=turn.primary_domain or "",
                )
                all_chunks.append(chunk)
        except Exception as exc:
            logger.warning("CorpusTurnStore search failed: %s", exc)

    # Lexical search (persistent, always available — no domain filter)
    # Corpus is project-agnostic, so we search all domains.
    lexical_results = []
    try:
        lexical_hits = await lexical_store.search(
            fts_query, domain=None, limit=max_sources * 3
        )
        all_chunks.extend(lexical_hits)
        lexical_results = [(h.id, h.score) for h in lexical_hits]
    except Exception as exc:
        logger.warning("Lexical search failed: %s", exc)

    # Vector search (supplementary, when available — no domain filter)
    vector_results = []
    if vector_store is not None and embedding_provider is not None:
        try:
            query_vec = await embedding_provider.embed(query)
            if query_vec and len(query_vec) > 0:
                vec_hits = await vector_store.retrieve(
                    query_vec, domain=None, top_k=max_sources * 2
                )
                all_chunks.extend(vec_hits)
                vector_results = [(h.id, h.score) for h in vec_hits]
        except Exception as exc:
            logger.debug("Vector search failed (non-fatal): %s", exc)

    # Hybrid scoring if we have both and overlap on ids (for corpus turns with turn_id keys)
    # Normalize and combine only for overlapping ids; legacy paths unchanged.
    if lexical_results and vector_results:
        # Get weights from config or default per spec
        if config is None:
            config = {}
        ret_cfg = config.get("retrieval", {}) if isinstance(config, dict) else {}
        lex_w = float(ret_cfg.get("lexical_weight", 0.4))
        vec_w = float(ret_cfg.get("vector_weight", 0.6))

        # Normalize scores per source to 0-1 (rough, using max)
        def _norm(pairs):
            if not pairs:
                return {}
            scores = [s for _, s in pairs]
            mx = max(scores) or 1.0
            return {iid: min(1.0, s / mx) for iid, s in pairs}

        lex_norm = _norm(lexical_results)
        vec_norm = _norm(vector_results)

        # Merge
        combined = {}
        for iid, ln in lex_norm.items():
            vn = vec_norm.get(iid, 0.0)
            combined[iid] = lex_w * ln + vec_w * vn
        for iid, vn in vec_norm.items():
            if iid not in combined:
                combined[iid] = vec_w * vn

        # Re-score the chunks we have
        for chunk in all_chunks:
            if chunk.id in combined:
                chunk.score = combined[chunk.id]

    # Filter by source type
    filtered = [c for c in all_chunks if _source_type_matches(c, source_filter)]

    # Deduplicate by chunk ID (or turn_id for corpus)
    seen_ids: set[str] = set()
    unique: list[Chunk] = []
    for chunk in filtered:
        if chunk.id not in seen_ids:
            seen_ids.add(chunk.id)
            unique.append(chunk)

    # Sort by score descending and limit
    unique.sort(key=lambda c: c.score, reverse=True)
    unique = unique[:max_sources]

    # Convert to SourceReference
    return [_chunk_to_source_ref(c) for c in unique]


# ---------------------------------------------------------------------------
# Store creation (same persistent stores as ingestion)
# ---------------------------------------------------------------------------


class AskStores:
    """Container for the stores needed by the ask pipeline.

    Uses the SAME persistent stores as the ingestion pipeline to ensure
    that ``aip ask`` reads from the same data that ``aip ingest`` wrote.
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

    Uses the SAME database paths as ``create_ingestion_stores()`` to ensure
    that ask reads from the same persistent stores that ingest writes to.

    LexicalStore (FTS5) is the primary search backend because it is
    persistent. VectorStore is in-memory by default but provides
    supplementary semantic search when an embedding provider is available.
    """
    import os

    from aip.adapter.artifact_store_versioned import VersionedArtifactStore
    from aip.adapter.ecs_store_persistent import PersistentEcsStore
    from aip.adapter.event_store_queryable import QueryableEventStore
    from aip.adapter.lexical.sqlite_fts5_store import SqliteFts5LexicalStore
    from aip.adapter.model_slot_resolver import ModelSlotResolver
    from aip.adapter.project.sqlite_project_store import SqliteProjectStore
    from aip.adapter.vector._in_memory import InMemoryVectorStore

    # Same paths as create_ingestion_stores()
    artifact_store = VersionedArtifactStore(db_path)
    await artifact_store.initialize()

    lexical_db = os.path.join(os.path.dirname(db_path), "lexical.db")
    lexical_store = SqliteFts5LexicalStore(lexical_db)
    await lexical_store.initialize()

    vector_store = InMemoryVectorStore()

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

    # Embedding provider — use centralized creation from [models.embedding] slot (or legacy [embedding])
    # This removes the bypass/stale direct creation, so CLI `aip ask` and tests respect UI-selected embedding model.
    embedding_provider = None
    try:
        config = _load_config()
        if config is not None:
            from aip.adapter.api.app import _create_embedding_provider
            embedding_provider = _create_embedding_provider(config)
    except Exception:
        pass  # graceful: no embedding provider — lexical-only search

    # CorpusTurnStore — the canonical corpus of ingested turns (project-agnostic)
    corpus_turn_store = None
    try:
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        corpus_turn_store = CorpusTurnStore(db_path)
        await corpus_turn_store.initialize()
    except Exception as exc:
        logger.info("CorpusTurnStore not available for ask pipeline: %s", exc)

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

    # Step 2: Search for relevant sources
    # Corpus is project-agnostic: we pass corpus_turn_store and search all
    # turns regardless of project domain. The project_domain param is kept
    # for future use but does not limit retrieval.
    try:
        sources = await _search_sources(
            query=question,
            project_domain=None,  # project-agnostic: search ALL corpus turns
            source_filter=source,
            lexical_store=stores.lexical_store,
            vector_store=stores.vector_store,
            embedding_provider=stores.embedding_provider,
            max_sources=max_sources,
            corpus_turn_store=stores.corpus_turn_store,
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

    # Step 4: Assemble context
    context = _assemble_context(sources, max_sources)

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

    # Step 9: Record successful trace
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
) -> None:
    """Record the full ask session trace in EventStore.

    This ensures that every ask query (successful or failed) leaves
    an audit trail: what was asked, what context was used, what answer
    was generated, and what happened.
    """
    if stores.event_store is None:
        return

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
