"""FastAPI dependencies for auth (CHUNK-9.0b)."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from aip.foundation.schemas import AuthConfig


async def get_current_identity(request: Request) -> dict:
    """Returns the authenticated identity (or {'identity': 'definer', 'role': 'definer'} if auth disabled).

    Checks in order:
    1. Session token in Authorization header
    2. API key in X-API-Key header
    3. Pre-set identity on request.state (from middleware)
    4. DEFINER fallback (laptop profile / auth disabled)
    """
    # 1. Check for session token in headers
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        # Try to validate against the auth_store on the app container
        container = getattr(request.app.state, "container", None)
        if container is not None:
            auth_store = getattr(container, "auth_store", None)
            if auth_store is not None:
                try:
                    result = await auth_store.validate_session(token)
                    if result is not None:
                        return result
                except Exception:
                    pass

    # 2. Check for API key in headers
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        container = getattr(request.app.state, "container", None)
        if container is not None:
            auth_store = getattr(container, "auth_store", None)
            if auth_store is not None:
                try:
                    result = await auth_store.validate_api_key(api_key)
                    if result is not None:
                        return result
                except Exception:
                    pass

    # 3. Check for pre-set identity on request.state (from middleware)
    identity = getattr(request.state, "auth_identity", None)
    role = getattr(request.state, "auth_role", None)
    if identity is not None:
        return {"identity": identity, "role": role}

    # 4. Laptop profile fallback (auth disabled)
    return {"identity": "definer", "role": "definer"}


async def require_definer(identity: dict = Depends(get_current_identity)) -> dict:
    """Raises 403 if not DEFINER role."""
    if identity.get("role") != "definer":
        raise HTTPException(status_code=403, detail="DEFINER role required")
    return identity
