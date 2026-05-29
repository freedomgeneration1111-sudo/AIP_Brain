"""API collaborator management routes.

Adapter-layer. Requires DEFINER auth for create/update/remove.
Uses CollaboratorManager (injected via container).

Security: Password/secret material is NEVER accepted via query parameters.
All secrets must be transmitted in the request body to prevent leakage
into logs, browser history, proxies, and metrics.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from aip.adapter.api.dependencies import get_container, require_definer

router = APIRouter(prefix="/collaborators", tags=["collaborators"])


class CreateCollaboratorRequest(BaseModel):
    """Request body for creating a collaborator.

    Password is transmitted in the body, never as a query parameter.
    """

    identity: str
    role: str
    password: str = Field(..., description="Collaborator password — transmitted in body, never in URL")


class UpdateRoleRequest(BaseModel):
    """Request body for updating a collaborator's role."""

    new_role: str
    requested_by: str = "definer"


class RevokeRequest(BaseModel):
    """Request body for revoking a collaborator."""

    requested_by: str = "definer"


@router.get("")
async def list_collaborators(container=Depends(get_container)):
    cm = getattr(container, "collaborator_manager", None)
    if cm is None:
        return {"collaborators": []}
    return {"collaborators": await cm.list_collaborators()}


@router.post("")
async def create_collaborator(
    body: CreateCollaboratorRequest,
    container=Depends(get_container),
    _=Depends(require_definer),
):
    """Create a collaborator. Password must be in the request body, NOT query params."""
    cm = getattr(container, "collaborator_manager", None)
    if cm is None:
        raise HTTPException(503, "CollaboratorManager not available")
    result = await cm.create_collaborator(body.identity, body.role, body.password)
    if result.get("status") == "created":
        # Never reflect the password in the response
        return {k: v for k, v in result.items() if k != "password"}
    raise HTTPException(400, result.get("message", "Creation failed"))


@router.put("/{identity}")
async def update_role(
    identity: str,
    body: UpdateRoleRequest,
    container=Depends(get_container),
    _=Depends(require_definer),
):
    """Update a collaborator's role. Role must be in the request body."""
    cm = getattr(container, "collaborator_manager", None)
    if cm is None:
        raise HTTPException(503, "CollaboratorManager not available")
    result = await cm.update_role(identity, body.new_role, body.requested_by)
    if result.get("status") == "updated":
        return result
    raise HTTPException(400, result.get("message", "Update failed"))


@router.delete("/{identity}")
async def revoke_collaborator(
    identity: str,
    body: RevokeRequest | None = None,
    container=Depends(get_container),
    _=Depends(require_definer),
):
    """Revoke a collaborator. Request body is optional for delete."""
    requested_by = body.requested_by if body else "definer"
    cm = getattr(container, "collaborator_manager", None)
    if cm is None:
        raise HTTPException(503, "CollaboratorManager not available")
    result = await cm.revoke_collaborator(identity, requested_by)
    if result.get("status") == "revoked":
        return result
    raise HTTPException(400, result.get("message", "Revoke failed"))
