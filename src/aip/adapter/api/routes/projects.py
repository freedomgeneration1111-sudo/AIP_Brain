"""Project CRUD routes (POST goes through AutonomyGate).

Routes for creating and listing projects. Projects persist in the
SQLite-backed ProjectStore (db/state.db) and survive server restarts.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container
from aip.foundation.schemas import coerce_autonomy_level

router = APIRouter()


@router.get("/projects")
async def list_projects(container: AipContainer = Depends(get_container)):
    """List all projects from the persistent ProjectStore.

    Returns a list of project dicts with project_id, name, status,
    domain, created_at, updated_at. Data is read from SQLite so it
    survives server restarts.
    """
    if not container.project_store:
        raise HTTPException(503, "ProjectStore not wired")
    try:
        projects = await container.project_store.list_projects()
        return {"projects": projects}
    except Exception as exc:
        raise HTTPException(500, f"Failed to list projects: {exc}") from exc


@router.post("/projects")
async def create_project(payload: dict, container: AipContainer = Depends(get_container)):
    """Create a new project via the persistent ProjectStore.

    Accepts:
      - name (str, required): Project name
      - domain (str, optional): Domain for indexing (defaults to name)
      - project_id (str, optional): Custom ID (auto-generated if omitted)

    The project is persisted to SQLite and survives server restarts.
    Write operations go through AutonomyGate.
    """
    if not container.autonomy_gate:
        raise HTTPException(503, "AutonomyGate not wired")
    # Gate check (write level)
    esc = await container.autonomy_gate.escalate(
        action_type="create_project",
        resource_id=payload.get("name", "unknown"),
        requested_level=coerce_autonomy_level("write"),
        requested_by="api",
    )
    if not esc.granted:
        raise HTTPException(403, f"Autonomy gate blocked: {esc.reason}")

    if not container.project_store:
        raise HTTPException(503, "ProjectStore not wired")

    name = payload.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name is required")

    project_id = payload.get("project_id") or f"proj-{uuid.uuid4().hex[:12]}"
    domain = payload.get("domain", name)

    try:
        result = await container.project_store.create_project(
            project_id=project_id,
            name=name,
            domain=domain,
        )
        return result
    except Exception as exc:
        raise HTTPException(500, f"Failed to create project: {exc}") from exc


@router.get("/projects/{project_id}")
async def get_project(project_id: str, container: AipContainer = Depends(get_container)):
    """Get a single project by ID from the persistent store."""
    if not container.project_store:
        raise HTTPException(503, "ProjectStore not wired")
    try:
        projects = await container.project_store.list_projects()
        for p in projects:
            if p.get("project_id") == project_id:
                return p
        raise HTTPException(404, f"Project '{project_id}' not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Failed to get project: {exc}") from exc


@router.get("/projects/{project_id}/work_units")
async def list_work_units(project_id: str, container: AipContainer = Depends(get_container)):
    """List work units for a project (placeholder)."""
    return {"project_id": project_id, "work_units": []}
