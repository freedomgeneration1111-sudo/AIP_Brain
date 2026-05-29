"""Memory Inspector routes  — all read-only, no AutonomyGate.

Phase 3: added logging for silent exception handling.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from aip.adapter.api.dependencies import AipContainer, get_container

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/memory/trace/{session_id}")
async def get_trace(session_id: str, container: AipContainer = Depends(get_container)):
    # From TraceStore (Phase 3/5)
    if container.trace_store:
        try:
            events = await container.trace_store.query_events(session_id=session_id)
            return {"session_id": session_id, "events": events}
        except Exception:
            logger.warning("Trace query failed for session %s", session_id, exc_info=True)
    return {"session_id": session_id, "events": []}


@router.get("/memory/events/{project_id}")
async def get_events(project_id: str, container: AipContainer = Depends(get_container)):
    # From EventStore (4.0b)
    if container.event_store:
        try:
            events = await container.event_store.query(artifact_id=None, limit=100)
            return {"project_id": project_id, "timeline": [dict(e) if hasattr(e, "__dict__") else e for e in events]}
        except Exception:
            logger.warning("Event query failed for project %s", project_id, exc_info=True)
    return {"project_id": project_id, "timeline": []}


@router.get("/memory/search")
async def memory_search(q: str, container: AipContainer = Depends(get_container)):
    # Hybrid via Lexical (8.0b) + Vector (8.0b)
    results = []
    if container.lexical_store:
        try:
            lexical_results = await container.lexical_store.search(q, limit=20)
            results.extend(
                [{"id": r.id, "content": r.content, "score": r.score, "source": "lexical"} for r in lexical_results],
            )
        except Exception:
            logger.warning("Lexical search failed for query '%s'", q[:50], exc_info=True)
    if container.vector_store and container.embedding_provider:
        try:
            query_vector = await container.embedding_provider.embed(q)
            vector_results = await container.vector_store.retrieve(query_vector, top_k=20)
            results.extend(
                [{"id": r.id, "content": r.content, "score": r.score, "source": "vector"} for r in vector_results],
            )
        except Exception:
            logger.warning("Vector search failed for query '%s'", q[:50], exc_info=True)
    return {"results": results}


@router.get("/memory/entities")
async def list_entities(container: AipContainer = Depends(get_container)):
    # From EntityStore (8.0b)
    if container.entity_store:
        try:
            entities = await container.entity_store.list_entities()
            return {"entities": entities}
        except Exception:
            logger.warning("Entity list failed", exc_info=True)
    return {"entities": []}


@router.get("/memory/canonical")
async def list_canonical(container: AipContainer = Depends(get_container)):
    # From CanonicalStore (8.0b)
    if container.canonical_store:
        try:
            canonicals = await container.canonical_store.list_canonical()
            return {"canonicals": canonicals}
        except Exception:
            logger.warning("Canonical list failed", exc_info=True)
    return {"canonicals": []}
