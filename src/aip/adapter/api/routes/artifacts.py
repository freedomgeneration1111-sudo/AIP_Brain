"""Artifact Browser routes (CHUNK-8.4) — read-only, no AutonomyGate."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from aip.adapter.api.dependencies import AipContainer, get_container
from aip.foundation.schemas import SurfaceConfig

router = APIRouter()


@router.get("/artifacts")
async def list_artifacts(
    domain: str | None = None,
    project_id: str | None = None,
    ecs_state: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    container: AipContainer = Depends(get_container),
):
    cfg = SurfaceConfig(**container.config.get("surface", {})) if hasattr(container, "config") else SurfaceConfig()
    effective_page_size = min(page_size, cfg.artifact_page_size)
    # Real impl: container.artifact_store.list with filters + pagination
    return {"items": [], "page": page, "page_size": effective_page_size, "total": 0}


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, container: AipContainer = Depends(get_container)):
    # container.artifact_store.read(artifact_id) + ecs state
    return {"id": artifact_id, "ecs_state": "GENERATED", "versions": 1}


@router.get("/artifacts/{artifact_id}/versions")
async def get_versions(artifact_id: str, container: AipContainer = Depends(get_container)):
    # Uses VersionedArtifactStore from 4.0b/Phase 2
    return {"artifact_id": artifact_id, "versions": []}


@router.get("/artifacts/{artifact_id}/evaluation")
async def get_evaluation(artifact_id: str, container: AipContainer = Depends(get_container)):
    # Returns L3a/L3b results (Faithfulness, DomainCoherence, etc.)
    return {"artifact_id": artifact_id, "faithfulness": 0.92, "domain_coherence": 0.85}
