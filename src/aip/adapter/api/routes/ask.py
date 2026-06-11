"""Ask API route — source-grounded knowledge queries.

Exposes the ask_pipeline via REST endpoint so the GUI can submit
knowledge-augmented queries without requiring CLI access.

Layer discipline: This module imports ONLY from adapter and foundation.
Orchestration functions (ask, AskStores, _search_sources_with_trace,
_sanitize_fts_query) are accessed through the container, not imported
directly from orchestration.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container
from aip.foundation.schemas.ask import AskSource

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/ask")
async def ask_query(payload: dict, container: AipContainer = Depends(get_container)):
    """Execute a source-grounded ask query against the AIP knowledge substrate.

    Accepts:
      - question (str, required): The query text
      - project_name (str, required): Project to search within
      - source (str, optional): "ingested" | "artifacts" | "all" (default: "all")
      - max_sources (int, optional): Max sources to retrieve (default: 10)
      - save_artifact (bool, optional): Save answer as draft artifact (default: false)
      - model_slot (str, optional): Model slot to use (default: "synthesis")
      - system_prompt_modifier (str, optional): Chat mode modifier text
        prepended to the synthesis system prompt (per AIP_UNIFIED_CHAT_SPEC)

    Returns AskResult dict with status, answer, sources, and metadata.
    """
    question = payload.get("question", "").strip()
    project_name = payload.get("project_name", "").strip()

    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    if not project_name:
        raise HTTPException(status_code=400, detail="project_name is required")

    source: AskSource = payload.get("source", "all")  # type: ignore[assignment]
    if source not in ("ingested", "artifacts", "all"):
        source = "all"

    max_sources = payload.get("max_sources", 10)
    save_artifact = payload.get("save_artifact", False)
    model_slot = payload.get("model_slot", "synthesis")
    system_prompt_modifier = payload.get("system_prompt_modifier", "")

    # Validate required stores
    if container.lexical_store is None:
        raise HTTPException(
            status_code=503,
            detail="Lexical store not available — cannot perform knowledge queries. "
                   "Ensure the AIP backend is configured with FTS5 support.",
        )

    if container.artifact_store is None:
        raise HTTPException(
            status_code=503,
            detail="Artifact store not available — cannot perform knowledge queries.",
        )

    # Project store is optional — corpus is project-agnostic and search
    # proceeds even when no project exists in the database.

    # Build AskStores from container's already-wired components.
    # Access AskStores class through the container (layer discipline:
    # routes do not import from orchestration directly).
    AskStores = container._ask_stores_class
    if AskStores is None:
        raise HTTPException(status_code=503, detail="Ask pipeline not available")

    stores = AskStores(
        artifact_store=container.artifact_store,
        lexical_store=container.lexical_store,
        vector_store=container.vector_store,
        event_store=container.event_store,
        project_store=container.project_store,
        ecs_store=container.ecs_store,
        model_provider=container.model_provider,
        embedding_provider=container.embedding_provider,
        corpus_turn_store=container.corpus_turn_store,
        graph_store=getattr(container, "graph_store", None),
    )

    # Call the ask pipeline through the container (layer discipline).
    ask_fn = container._ask_fn
    if ask_fn is None:
        raise HTTPException(status_code=503, detail="Ask pipeline not available")

    try:
        result = await ask_fn(
            question=question,
            project_name=project_name,
            stores=stores,
            source=source,
            max_sources=max_sources,
            save_artifact=save_artifact,
            model_slot=model_slot,
            system_prompt_modifier=system_prompt_modifier,
        )
    except Exception as exc:
        logger.error("Ask pipeline failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ask pipeline error: {exc}") from exc

    # Convert dataclass to dict for JSON response
    return {
        "status": result.status,
        "answer": result.answer,
        "sources": [
            {
                "source_id": s.source_id,
                "source_type": s.source_type,
                "title": s.title,
                "score": s.score,
                "content_snippet": s.content_snippet,
                "domain": s.domain,
                "metadata": s.metadata,
            }
            for s in result.sources
        ],
        "model_slot": result.model_slot,
        "model_provider": result.model_provider,
        "artifact_id": result.artifact_id,
        "session_id": result.session_id,
        "project_id": result.project_id,
        "project_name": result.project_name,
        "prompt": result.prompt,
        "errors": result.errors,
        "trace_available": bool(result.sources),
        "lexical_only": result.retrieval_degradation.get("lexical_only", False) if result.retrieval_degradation else False,
        "vector_contributed": result.retrieval_degradation.get("vector_contributed", False) if result.retrieval_degradation else False,
    }


@router.post("/ask/retrieve")
async def ask_retrieve_only(payload: dict, container: AipContainer = Depends(get_container)):
    """Retrieve sources for a query without generating an answer.

    Lightweight endpoint for the Vector search panel: returns matching
    sources from LexicalStore + VectorStore without dispatching to a model.

    Accepts:
      - question (str, required): The query text
      - project_name (str, optional): Project domain to filter by
      - domain (str, optional): Domain to filter by (alternative to project_name)
      - source (str, optional): "ingested" | "artifacts" | "all" (default: "all")
      - max_sources (int, optional): Max sources to retrieve (default: 20)
    """
    question = payload.get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    domain = payload.get("domain") or payload.get("project_name")
    source: AskSource = payload.get("source", "all")  # type: ignore[assignment]
    if source not in ("ingested", "artifacts", "all"):
        source = "all"
    max_sources = payload.get("max_sources", 20)

    if container.lexical_store is None:
        raise HTTPException(status_code=503, detail="Lexical store not available")

    # Access orchestration functions through the container (layer discipline).
    search_sources_fn = container._search_sources_fn
    AskStores = container._ask_stores_class
    if search_sources_fn is None or AskStores is None:
        raise HTTPException(status_code=503, detail="Retrieval pipeline not available")

    # Corpus is project-agnostic: do not filter by domain/project.
    # project_domain is kept for future use but does not limit retrieval.
    project_domain = None

    # Use the orchestrator pipeline for retrieval
    trace = None
    try:
        sources, trace, _packed = await search_sources_fn(
            query=question,
            stores=AskStores(
                artifact_store=container.artifact_store,
                lexical_store=container.lexical_store,
                vector_store=container.vector_store,
                event_store=container.event_store,
                project_store=container.project_store,
                ecs_store=container.ecs_store,
                embedding_provider=container.embedding_provider,
                corpus_turn_store=container.corpus_turn_store,
                graph_store=getattr(container, "graph_store", None),
            ),
            source_filter=source,
            max_sources=max_sources,
        )
    except Exception as exc:
        logger.error("Source retrieval failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Retrieval error: {exc}") from exc

    return {
        "question": question,
        "domain": project_domain,
        "sources": [
            {
                "source_id": s.source_id,
                "source_type": s.source_type,
                "title": s.title,
                "score": s.score,
                "content_snippet": s.content_snippet,
                "domain": s.domain,
                "metadata": s.metadata,
            }
            for s in sources
        ],
        "total": len(sources),
        "trace_available": trace is not None and bool(trace),
        "lexical_only": getattr(trace, "lexical_only", False) if trace is not None else False,
        "vector_contributed": getattr(trace, "vector_contributed", False) if trace is not None else False,
    }
