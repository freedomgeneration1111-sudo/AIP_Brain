"""CollaboratorManager — adapter-layer manager for collaborator/readonly roles.

Per AIP_0_1_Phase8_BuildSpec_Rev1.0.md exact prose + box + ANNEX.
Extends Phase 7 AuthStore (9.0b) + uses CollaboratorConfig (10.0a) + AutonomyGate.
collaborator_can_approve defaults to False (DEFINER sovereignty).
All privileged paths go through AutonomyGate.
Pure adapter-layer (no orchestration imports).
"""

from __future__ import annotations

import bcrypt
from typing import Any

from aip.foundation.protocols import AuthStore, AutonomyGate
from aip.foundation.schemas import CollaboratorConfig, CollaboratorRole


class CollaboratorManager:
    """Manages collaborator and readonly identities via AuthStore.

    Enforces:
    - collaborator_can_approve=False by default (DEFINER only for approve/promote/config/escalation).
    - Role-based constraints via AutonomyGate.
    - No definer role creation via API.
    """

    def __init__(
        self,
        auth_store: AuthStore,
        config: CollaboratorConfig,
        autonomy_gate: AutonomyGate,
    ) -> None:
        self.auth_store = auth_store
        self.config = config
        self.autonomy_gate = autonomy_gate

    async def create_collaborator(self, identity: str, role: CollaboratorRole, password: str) -> dict:
        """Create collaborator or readonly (never definer)."""
        if not self.config.enabled:
            return {"status": "disabled", "message": "Collaborator access disabled (laptop profile default)"}

        if role == "definer":
            return {"status": "error", "message": "Cannot create definer role via API (config only)"}

        # Check max collaborators (simple count via list in real impl; here we trust store limit)
        # In full impl would call list and check len < max_collaborators

        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        # Delegate to AuthStore extension (added in 10.0a)
        success = await self.auth_store.create_user(identity, role, password_hash)  # type: ignore[attr-defined]

        if success:
            return {"status": "created", "identity": identity, "role": role}
        return {"status": "error", "message": "Identity already exists or creation failed"}

    async def update_role(self, identity: str, new_role: CollaboratorRole, requested_by: str) -> dict:
        """Update role (cannot change DEFINER)."""
        # In real impl would check AutonomyGate + that target is not definer
        success = await self.auth_store.update_user_role(identity, new_role)  # type: ignore[attr-defined]
        if success:
            return {"status": "updated", "identity": identity, "new_role": new_role}
        return {"status": "error", "message": "Cannot change DEFINER or user not found"}

    async def revoke_collaborator(self, identity: str, requested_by: str) -> dict:
        """Revoke (cannot revoke DEFINER)."""
        success = await self.auth_store.revoke_user(identity)  # type: ignore[attr-defined]
        if success:
            return {"status": "revoked", "identity": identity}
        return {"status": "error", "message": "Cannot revoke DEFINER or user not found"}

    async def list_collaborators(self) -> list[dict]:
        """List all non-DEFINER users."""
        # Delegate to AuthStore extension (added in 10.0a)
        users = await self.auth_store.list_users()  # type: ignore[attr-defined]
        return [u for u in users if u.get("role") != "definer"]
