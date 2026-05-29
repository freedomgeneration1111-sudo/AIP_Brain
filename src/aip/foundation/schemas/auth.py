"""Auth, autonomy, collaborator, and rate-limiting types.

Authentication configuration, autonomy level definitions and coercion
helpers, escalation tracking, collaborator access, and rate limiting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Literal type aliases
# ---------------------------------------------------------------------------

AutonomyLevel = Literal["none", "read", "write", "admin"]
McpAutonomyLevel = Literal["read", "write", "admin"]
AuthRole = Literal["definer", "readonly", "unauthenticated"]
CollaboratorRole = Literal["definer", "collaborator", "readonly"]


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def coerce_autonomy_level(level: str | AutonomyLevel) -> AutonomyLevel:
    """Convert a string autonomy level to the proper AutonomyLevel Literal type.

    This eliminates the need for ``# type: ignore[arg-type]`` at call sites
    by validating the value and narrowing the return type.
    """
    valid: tuple[str, ...] = ("none", "read", "write", "admin")
    if level not in valid:
        raise ValueError(f"Invalid autonomy level: {level!r}. Must be one of {valid}")
    return level  # type: ignore[return-value]


def coerce_mcp_autonomy_level(level: str | McpAutonomyLevel) -> McpAutonomyLevel:
    """Convert a string autonomy level to the proper McpAutonomyLevel Literal type.

    This eliminates the need for ``# type: ignore[arg-type]`` at call sites
    by validating the value and narrowing the return type.
    """
    valid: tuple[str, ...] = ("read", "write", "admin")
    if level not in valid:
        raise ValueError(f"Invalid MCP autonomy level: {level!r}. Must be one of {valid}")
    return level  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AutonomyEscalation:
    """A single autonomy escalation request and its resolution.

    "No UI, workflow, Beast cadence, MCP call, or queued task
    may bypass the DEFINER gates."
    model_gen_assumption tags what assumption this escalation encodes.
    """

    escalation_id: str
    action_type: str
    requested_by: str
    resource_id: str
    current_level: AutonomyLevel = "none"
    requested_level: AutonomyLevel = "read"
    granted: bool = False
    reason: str = ""
    model_gen_assumption: str | None = None
    created_at: str = ""  # REQUIRED — ISO 8601


@dataclass
class AuthConfig:
    """Configuration for the authentication system.

    Enforces DEFINER sovereignty at the identity level.
    Phase 7 scope: single-DEFINER with API key support for non-interactive access (CLI/MCP).
    Per INTERFACES: auth_enabled, session_timeout_seconds, api_key_enabled,
    bcrypt_rounds, definer_identity.
    """

    auth_enabled: bool = False
    session_timeout_seconds: int = 86400
    api_key_enabled: bool = True
    bcrypt_rounds: int = 12
    definer_identity: str = "definer"


@dataclass
class RateLimitConfig:
    """Token-bucket rate limiting configuration.

    Per Phase 7 scope: prevents any single surface (Beast cadence, MCP, chat) from starving others.
    Configurable.
    Per INTERFACES: requests_per_minute, burst_size, per_endpoint_overrides,
    model_budget_protection.
    """

    enabled: bool = True
    requests_per_minute: int = 60
    burst_size: int = 10
    per_endpoint_overrides: dict[str, int] = field(default_factory=dict)
    model_budget_protection: bool = True


@dataclass
class CollaboratorConfig:
    """Collaborator access configuration.

    Collaborators never bypass DEFINER sovereignty.
    enabled is toggleable.
    Per Process Rule 11: collaborator_can_approve defaults to False.
    """

    enabled: bool = False
    max_collaborators: int = 5
    collaborator_can_create_drafts: bool = True
    collaborator_can_submit_review: bool = True
    collaborator_can_approve: bool = False
    readonly_can_search: bool = True


__all__ = [
    "AutonomyLevel",
    "McpAutonomyLevel",
    "AuthRole",
    "CollaboratorRole",
    "coerce_autonomy_level",
    "coerce_mcp_autonomy_level",
    "AutonomyEscalation",
    "AuthConfig",
    "RateLimitConfig",
    "CollaboratorConfig",
]
