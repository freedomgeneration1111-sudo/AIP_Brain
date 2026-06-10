"""FastAPI dependencies for auth."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request

logger = logging.getLogger(__name__)

# Sentinel value to distinguish "middleware ran but found no identity"
# from "middleware didn't set anything at all" (shouldn't happen in
# normal operation but provides a safe fallback).
_AUTHENTICATED_NONE = object()


async def get_current_identity(request: Request) -> dict:
    """Returns the authenticated identity.

    Checks in order:
    1. Session token in Authorization header
    2. API key in X-API-Key header
    3. Pre-set identity on request.state (from middleware)
    4. DEFINER fallback — ONLY when auth is disabled (laptop mode)

    When auth is enabled and no valid credential is provided, returns
    an anonymous identity with no role, which will be rejected by
    require_definer.
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
                except Exception as exc:
                    logger.debug("Session token validation failed: %s", exc)

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
                except Exception as exc:
                    logger.debug("API key validation failed: %s", exc)

    # 3. Check for pre-set identity on request.state (from middleware)
    identity = getattr(request.state, "auth_identity", _AUTHENTICATED_NONE)

    if identity is _AUTHENTICATED_NONE:
        # Middleware didn't run — this shouldn't happen in normal operation.
        # Return anonymous identity so require_definer will reject.
        return {"identity": None, "role": None}

    if identity is not None:
        role = getattr(request.state, "auth_role", None)
        return {"identity": identity, "role": role}

    # identity is None — middleware ran but found no valid credentials.
    # This means auth is enabled and the request is unauthenticated.
    # Return anonymous identity so require_definer will reject.
    return {"identity": None, "role": None}


async def require_definer(identity: dict = Depends(get_current_identity)) -> dict:
    """Raises 403 if not DEFINER role.

    In laptop mode (auth disabled), the AuthMiddleware sets
    auth_identity=definer on every request, so require_definer passes.

    When auth is enabled, unauthenticated requests get identity=None,
    role=None, and are rejected with 403.
    """
    if identity.get("role") != "definer":
        raise HTTPException(status_code=403, detail="DEFINER role required")
    return identity


async def require_collaborator_or_above(identity: dict = Depends(get_current_identity)) -> dict:
    """Raises 403 if role is 'readonly'. Allows 'definer' and 'collaborator'."""
    if identity.get("role") == "readonly":
        raise HTTPException(status_code=403, detail="Collaborator or above required")
    return identity
