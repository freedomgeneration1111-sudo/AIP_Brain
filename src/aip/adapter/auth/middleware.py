"""AuthMiddleware for FastAPI.

Integrates with 8.1 app factory.
When auth_enabled=False (laptop profile): all requests treated as DEFINER.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from aip.adapter.auth.session_store import SqliteSessionStore
from aip.foundation.schemas import AuthConfig


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, auth_store: SqliteSessionStore, config: AuthConfig) -> None:
        super().__init__(app)
        self.auth_store = auth_store
        self.config = config

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.config.auth_enabled:
            # Laptop profile: everything is DEFINER
            request.state.auth_identity = self.config.definer_identity
            request.state.auth_role = "definer"
            return await call_next(request)

        # Try Bearer session token
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            identity = await self.auth_store.validate_session(token)
            if identity:
                request.state.auth_identity = identity["identity"]
                request.state.auth_role = identity["role"]
                return await call_next(request)

        # Try X-API-Key
        api_key = request.headers.get("X-API-Key")
        if api_key:
            identity = await self.auth_store.validate_api_key(api_key)
            if identity:
                request.state.auth_identity = identity["identity"]
                request.state.auth_role = identity["role"]
                return await call_next(request)

        # Unauthenticated
        request.state.auth_identity = None
        request.state.auth_role = None
        return await call_next(request)
