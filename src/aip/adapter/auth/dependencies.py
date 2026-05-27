"""FastAPI dependencies for auth (CHUNK-9.0b)."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from aip.foundation.schemas import AuthConfig


async def get_current_identity(request: Request) -> dict:
    """Returns the authenticated identity (or {'identity': 'definer', 'role': 'definer'} if auth disabled)."""
    identity = getattr(request.state, "auth_identity", None)
    role = getattr(request.state, "auth_role", None)
    if identity is None:
        # Laptop profile fallback
        return {"identity": "definer", "role": "definer"}
    return {"identity": identity, "role": role}


async def require_definer(identity: dict = Depends(get_current_identity)) -> dict:
    """Raises 403 if not DEFINER role."""
    if identity.get("role") != "definer":
        raise HTTPException(status_code=403, detail="DEFINER role required")
    return identity
