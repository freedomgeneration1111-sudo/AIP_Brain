"""Ask API route — source-grounded knowledge queries.

Exposes the ask_pipeline via REST endpoint so the GUI can submit
knowledge-augmented queries without requiring CLI access.

The ask pipeline retrieves relevant sources from LexicalStore and
VectorStore, assembles context, dispatches to the configured model,
and returns a source-grounded answer with provenance references.
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

    # Build AskStores from container's already-wired components
    from aip.orchestration.ask_pipeline import AskStores

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

    # Import and call the ask pipeline
    from aip.orchestration.ask_pipeline import ask

    try:
        result = await ask(
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

    from aip.orchestration.ask_pipeline import _search_sources_with_trace, _sanitize_fts_query

    # Corpus is project-agnostic: do not filter by domain/project.
    # project_domain is kept for future use but does not limit retrieval.
    project_domain = None

    # Use the orchestrator pipeline for retrieval
    try:
        sources, _trace, _packed = await _search_sources_with_trace(
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
    }
