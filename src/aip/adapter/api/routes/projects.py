"""Project CRUD routes (POST goes through AutonomyGate per spec)."""

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container
from aip.foundation.schemas import coerce_autonomy_level

router = APIRouter()


@router.get("/projects")
async def list_projects(container: AipContainer = Depends(get_container)):
    if not container.project_store:
        raise HTTPException(503, "ProjectStore not wired")
    # In real 8.1 the container.project_store would be the real one from Phase 3/4
    return {"projects": []}  # scaffold shape


@router.post("/projects")
async def create_project(payload: dict, container: AipContainer = Depends(get_container)):
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
    # Would call real create here
    return {"id": "proj-new", "name": payload.get("name"), "domain": payload.get("domain")}


@router.get("/projects/{project_id}")
async def get_project(project_id: str, container: AipContainer = Depends(get_container)):
    return {"id": project_id, "work_units": []}


@router.get("/projects/{project_id}/work_units")
async def list_work_units(project_id: str, container: AipContainer = Depends(get_container)):
    return {"project_id": project_id, "work_units": []}
