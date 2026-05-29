"""Authentication, authorization, and autonomy gate Protocol definitions.

Protocols for auth storage (sessions, API keys, user management)
and the two-phase autonomy gate that enforces DEFINER sovereignty.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from aip.foundation.schemas import AuthRole, AutonomyEscalation, AutonomyLevel, CollaboratorRole


@runtime_checkable
class AutonomyGate(Protocol):
    """Two-phase autonomy gate.

    Low levels are local; higher levels require DEFINER/policy approval.
    Per Architecture L6.
    """

    async def request_autonomy(self, level: int, context: dict[str, Any]) -> bool:
        """Request permission for the given autonomy level. Return True if granted."""
        ...

    async def record_autonomy_use(self, level: int, context: dict[str, Any]) -> None:
        """Record that the given autonomy level was used (for audit / Sexton)."""
        ...

    async def check(
        self,
        action_type: str,
        resource_id: str,
        requested_level: AutonomyLevel,
        requested_by: str,
    ) -> AutonomyEscalation:
        """Check whether an action is allowed at the current autonomy level.

        Returns an AutonomyEscalation record with granted=True/False.
        Does not block — use escalate() for blocking gate.
        """
        ...

    async def escalate(
        self,
        action_type: str,
        resource_id: str,
        requested_level: AutonomyLevel,
        requested_by: str,
    ) -> AutonomyEscalation:
        """Request autonomy escalation for an action.

        Blocks until DEFINER approves if escalation_requires_definer is True.
        Returns an AutonomyEscalation record with the resolution.
        """
        ...

    async def audit_log(self, limit: int = 100) -> list[AutonomyEscalation]:
        """Return recent autonomy escalation records for audit.

        Used by admin console and DEFINER review.
        """
        ...


@runtime_checkable
class AuthStore(Protocol):
    """Protocol for authentication/authorization storage.

    Single-DEFINER + API keys for non-interactive access.
    Session and API key lifecycle methods.
    """

    async def get_definer_identity(self) -> dict | None:
        """Return the single DEFINER identity (or None if not configured)."""
        ...

    async def create_session(self, identity: str, role: AuthRole) -> str:
        """Create a session for the given identity and role. Returns session token."""
        ...

    async def validate_session(self, session_token: str) -> dict | None:
        """Validate a session token. Returns identity dict or None if expired/invalid."""
        ...

    async def revoke_session(self, session_token: str) -> None:
        """Revoke a session token."""
        ...

    async def validate_api_key(self, key: str) -> dict | None:
        """Validate an API key and return associated identity info."""
        ...

    async def create_api_key(self, identity: str, role: AuthRole, key_name: str) -> str:
        """Create an API key for non-interactive access. Returns the key string."""
        ...

    async def revoke_api_key(self, key_name: str) -> None:
        """Revoke an API key by name."""
        ...

    async def list_api_keys(self) -> list[dict]:
        """List all API keys (key_name, identity, role, created_at)."""
        ...

    async def list_users(self) -> list[dict]:
        """List all user identities.

        Returns list of dicts with: identity, role, created_at, last_active_at.
        """
        ...

    async def create_user(self, identity: str, role: CollaboratorRole, password_hash: str | None = None) -> bool:
        """Create a collaborator or readonly user.

        The 'definer' role cannot be created through this method —
        it is defined in the configuration file.
        Returns True if created, False if identity already exists.
        """
        ...

    async def update_user_role(self, identity: str, new_role: CollaboratorRole) -> bool:
        """Update a user's role.

        Cannot change the DEFINER's role.
        Returns True if updated, False if user not found.
        """
        ...

    async def revoke_user(self, identity: str) -> bool:
        """Remove a user. Cannot revoke the DEFINER.

        Revokes all sessions and API keys for the user.
        Returns True if revoked, False if user not found or is DEFINER.
        """
        ...


__all__ = [
    "AutonomyGate",
    "AuthStore",
]
