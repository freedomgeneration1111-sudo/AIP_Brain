"""Knowledge API routes — compiled knowledge browsing and search.

Exposes the KnowledgeStore via REST endpoints so the GUI can browse,
inspect, and search compiled knowledge artifacts.

The KnowledgeStore manages the Deferred Compiled Knowledge Layer:
knowledge items track provenance to source canonicals and follow a
compilation state machine: SPECIFIED → COMPILED → REVIEWED → APPROVED.

Phase 4: Knowledge Exploration Features.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/knowledge")
async def list_knowledge(
    domain: str | None = None,
    state: str | None = None,
    container: AipContainer = Depends(get_container),
):
    """List compiled knowledge items, optionally filtered by domain and state.

    Returns a list of knowledge items with metadata. Each item includes:
    knowledge_id, content, source_canonical_ids, domain, state, metadata,
    created_at, updated_at.
    """
    if container.knowledge_store is None:
        raise HTTPException(
            status_code=503,
            detail="Knowledge store not available. Ensure vector_store and lexical_store are configured.",
        )

    try:
        items = await container.knowledge_store.list_compiled(
            domain=domain,
            state=state,  # type: ignore[arg-type]
        )
        return {"items": items, "total": len(items)}
    except Exception as exc:
        logger.error("Failed to list knowledge: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/knowledge/search")
async def search_knowledge(
    q: str,
    domain: str | None = None,
    limit: int = 10,
    container: AipContainer = Depends(get_container),
):
    """Search compiled knowledge by query and domain.

    Performs hybrid search: lexical (FTS5) when available, plus vector
    semantic search when embedding provider is configured. Results are
    merged and deduplicated by knowledge_id.

    NOTE: This route MUST be registered before /knowledge/{knowledge_id}
    to avoid the path parameter capturing "search" as a knowledge_id.
    """
    if container.knowledge_store is None:
        raise HTTPException(status_code=503, detail="Knowledge store not available")

    if not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")

    try:
        results = await container.knowledge_store.search_compiled(
            query=q,
            domain=domain,
            limit=limit,
        )
        return {"results": results, "total": len(results), "query": q}
    except Exception as exc:
        logger.error("Knowledge search failed for query '%s': %s", q[:50], exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/knowledge/{knowledge_id}")
async def get_knowledge(
    knowledge_id: str,
    container: AipContainer = Depends(get_container),
):
    """Get a specific compiled knowledge item by ID.

    Returns the knowledge item with full content and provenance chain.
    Provenance shows which source canonicals were used to compile this knowledge.
    """
    if container.knowledge_store is None:
        raise HTTPException(status_code=503, detail="Knowledge store not available")

    try:
        item = await container.knowledge_store.get_compiled(knowledge_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Knowledge item '{knowledge_id}' not found")

        # Also fetch provenance
        provenance = await container.knowledge_store.get_provenance(knowledge_id)

        return {**item, "provenance": provenance}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get knowledge '%s': %s", knowledge_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
