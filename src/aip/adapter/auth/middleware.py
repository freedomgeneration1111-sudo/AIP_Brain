"""AuthMiddleware for FastAPI.

Integrates with 8.1 app factory.

Behavior by profile:
- **laptop** (auth_enabled=False): All requests are treated as DEFINER.
  A warning is logged on every request to make this explicitly visible.
  This is acceptable for local development but MUST NOT be used in production.
- **production** (auth_enabled=True): Requests must authenticate via Bearer
  session token or X-API-Key header. Unauthenticated requests proceed
  without identity (auth_identity=None), and downstream authorization
  checks determine access.

Security warning:
  When auth_enabled=False, every request has DEFINER privileges — full admin
  access including artifact promotion, autonomy escalation, and collaborator
  management. This mode is only safe on a local machine with no network
  exposure.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from aip.adapter.auth.session_store import SqliteSessionStore
from aip.foundation.schemas import AuthConfig

logger = logging.getLogger(__name__)

# Track whether we've already logged the startup warning to avoid log spam
# on every single request. The per-request warning is throttled.
_auth_disabled_startup_warned = False


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, auth_store: SqliteSessionStore, config: AuthConfig) -> None:
        super().__init__(app)
        self.auth_store = auth_store
        self.config = config

        if not config.auth_enabled:
            logger.warning(
                "AUTH DISABLED: All requests will be treated as DEFINER. "
                "This is only acceptable for local/laptop development. "
                "Set auth.auth_enabled=true in production deployments. "
                "Every request will have full admin access including artifact "
                "promotion, autonomy escalation, and collaborator management.",
            )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.config.auth_enabled:
            # Laptop profile: everything is DEFINER
            request.state.auth_identity = self.config.definer_identity
            request.state.auth_role = "definer"

            # Log a warning on every request (throttled to once per 100 requests)
            # to make the security posture explicitly visible.
            if not hasattr(self, "_auth_disabled_request_count"):
                self._auth_disabled_request_count = 0
            self._auth_disabled_request_count += 1
            if self._auth_disabled_request_count <= 5 or self._auth_disabled_request_count % 100 == 0:
                logger.warning(
                    "AUTH DISABLED: Request to %s %s treated as DEFINER. Enable auth in production!",
                    request.method,
                    request.url.path,
                )

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
