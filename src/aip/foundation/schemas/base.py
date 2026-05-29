"""Core enums, base types, and event-sourcing primitives.

Foundation types: contracts, ECS lifecycle, failure taxonomy,
and event-store read models. These are the bedrock types that other
domain modules may reference but never depend on higher-level schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ContractTier(Enum):
    HARD = "HARD"
    SOFT = "SOFT"
    ASPIRATIONAL = "ASPIRATIONAL"


class EcsState(Enum):
    SPECIFIED = "SPECIFIED"
    GENERATED = "GENERATED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Literal type aliases
# ---------------------------------------------------------------------------

# Standard model slot names
ModelSlotName = Literal["synthesis", "evaluation", "sexton", "embedding"]

# Failure type codes (see architecture Appendix E)
FailureType = Literal["A", "B", "C", "D", "E", "F"]

# Standard outcome types
OutcomeType = Literal["success", "failure", "timeout", "gate_blocked", "insufficient_memory"]

# Alias for backward compatibility (same values as FailureType)
FailureTypeCode = Literal["A", "B", "C", "D", "E", "F"]

# Vigil health status
VigilHealthStatus = Literal["healthy", "stale", "degraded", "unknown"]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ContractRule:
    """L1 environment contract rule. Any rule compensating for a model
    limitation must carry a non-null model_gen_assumption. Sexton audits
    these on model slot upgrades.
    """

    rule_id: str
    tier: ContractTier
    text: str
    domain: str | None
    model_gen_assumption: str | None  # Non-null = compensates for model limitation
    created: str
    deprecated: str | None


@dataclass
class EcsTransition:
    """Record of a single ECS state transition.

    Every transition is recorded for provenance.
    Actor and reason are mandatory for sovereignty audit.
    """

    artifact_id: str
    from_state: str
    to_state: str
    actor: str
    reason: str
    timestamp: str


@dataclass
class Event:
    """Read-model returned by EventStore.query().

    Used for timeline reconstruction, DEFINER audit,
    Sexton failure analysis, and review decisions.
    """

    id: int
    event_type: str
    actor: str
    artifact_id: str
    timestamp: str  # REQUIRED — ISO 8601
    from_state: str | None = None
    to_state: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ReleaseMetadata:
    """AIP 0.1 release metadata.

    Written by release verification when all gates pass.
    Serves as the definitive release manifest.
    """

    release_version: str = "0.1.0"
    release_date: str = ""  # REQUIRED — ISO 8601
    release_status: str = "alpha"
    architecture_revision: str = "5.2"
    acceptance_gates_passed: list[str] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)
    breaking_changes: list[str] = field(default_factory=list)


__all__ = [
    "ContractTier",
    "ContractRule",
    "EcsState",
    "ModelSlotName",
    "FailureType",
    "OutcomeType",
    "FailureTypeCode",
    "VigilHealthStatus",
    "EcsTransition",
    "Event",
    "ReleaseMetadata",
]
