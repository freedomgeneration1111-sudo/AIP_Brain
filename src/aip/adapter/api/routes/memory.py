"""Memory Inspector routes (CHUNK-8.6) — all read-only, no AutonomyGate."""

from __future__ import annotations

try:
    from fastapi import APIRouter, Depends
except ImportError:
    APIRouter = None  # type: ignore
    Depends = None  # type: ignore

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter() if APIRouter is not None else None


@router.get("/memory/trace/{session_id}")
async def get_trace(session_id: str, container: AipContainer = Depends(get_container)):
    # From TraceStore (Phase 3/5)
    return {"session_id": session_id, "events": []}


@router.get("/memory/events/{project_id}")
async def get_events(project_id: str, container: AipContainer = Depends(get_container)):
    # From EventStore (4.0b)
    return {"project_id": project_id, "timeline": []}


@router.get("/memory/search")
async def memory_search(q: str, container: AipContainer = Depends(get_container)):
    # Hybrid via Lexical (8.0b) + Vector (8.0b)
    return {"results": []}


@router.get("/memory/entities")
async def list_entities(container: AipContainer = Depends(get_container)):
    # From EntityStore (8.0b)
    return {"entities": []}


@router.get("/memory/canonical")
async def list_canonical(container: AipContainer = Depends(get_container)):
    # From CanonicalStore (8.0b)
    return {"canonicals": []}
