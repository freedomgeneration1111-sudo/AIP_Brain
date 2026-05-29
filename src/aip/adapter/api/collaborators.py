"""API collaborator management routes.

Adapter-layer. Requires DEFINER auth for create/update/remove.
Uses CollaboratorManager (injected via container).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import get_container, require_definer

router = APIRouter(prefix="/collaborators", tags=["collaborators"])


@router.get("")
async def list_collaborators(container=Depends(get_container)):
    cm = getattr(container, "collaborator_manager", None)
    if cm is None:
        return {"collaborators": []}
    return {"collaborators": await cm.list_collaborators()}


@router.post("")
async def create_collaborator(
    identity: str,
    role: str,
    password: str,
    container=Depends(get_container),
    _=Depends(require_definer),
):
    cm = getattr(container, "collaborator_manager", None)
    if cm is None:
        raise HTTPException(503, "CollaboratorManager not available")
    result = await cm.create_collaborator(identity, role, password)
    if result.get("status") == "created":
        return result
    raise HTTPException(400, result.get("message", "Creation failed"))


@router.put("/{identity}")
async def update_role(
    identity: str,
    new_role: str,
    container=Depends(get_container),
    requested_by: str = "definer",
    _=Depends(require_definer),
):
    cm = getattr(container, "collaborator_manager", None)
    if cm is None:
        raise HTTPException(503, "CollaboratorManager not available")
    result = await cm.update_role(identity, new_role, requested_by)
    if result.get("status") == "updated":
        return result
    raise HTTPException(400, result.get("message", "Update failed"))


@router.delete("/{identity}")
async def revoke_collaborator(
    identity: str,
    container=Depends(get_container),
    requested_by: str = "definer",
    _=Depends(require_definer),
):
    cm = getattr(container, "collaborator_manager", None)
    if cm is None:
        raise HTTPException(503, "CollaboratorManager not available")
    result = await cm.revoke_collaborator(identity, requested_by)
    if result.get("status") == "revoked":
        return result
    raise HTTPException(400, result.get("message", "Revoke failed"))
