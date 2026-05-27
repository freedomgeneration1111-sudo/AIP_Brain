"""
Phase 0 foundation schemas only.
This file will be appended to (never rewritten) by CHUNK-1.0a.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class ContractTier(Enum):
    HARD = "HARD"
    SOFT = "SOFT"
    ASPIRATIONAL = "ASPIRATIONAL"


@dataclass
class ContractRule:
    """
    L1 environment contract rule. Per §1.8 (Harness Evolution Principle),
    any rule compensating for a model limitation must carry a non-null
    model_gen_assumption. Sexton audits these on model slot upgrades.
    """
    rule_id: str
    tier: ContractTier
    text: str
    domain: str | None
    model_gen_assumption: str | None  # Non-null = compensates for model limitation
    created: str
    deprecated: str | None


class EcsState(Enum):
    SPECIFIED = "SPECIFIED"
    GENERATED = "GENERATED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"


# Literal types used by Phase 0 contracts
ModelSlotName = Literal["synthesis", "evaluation", "sexton", "embedding"]

FailureType = Literal["A", "B", "C", "D", "E", "F"]

OutcomeType = Literal[
    "success", "failure", "timeout", "gate_blocked", "insufficient_memory"
]


# --- Phase 1 additions (append only) ---
# Added by CHUNK-1.0a per Rev 1.3 single source of truth.
# Do not modify or reorder anything above this line.
from dataclasses import dataclass, field


@dataclass
class Chunk:
    """Unified retrieval chunk. Replaces RetrievalHit.
    Used by VectorStore.retrieve() and retrieve_for_synthesis().
    One type for the entire retrieval pipeline.
    """
    id: str
    content: str | None = None
    score: float = 0.0
    metadata: dict = field(default_factory=dict)
    domain: str | None = None


@dataclass
class RetrievalResult:
    """Result of retrieve_for_synthesis with low-confidence gate status."""
    status: str  # "OK" | "INSUFFICIENT_MEMORY"
    hits: list[Chunk]
    max_confidence: float
    message: str | None = None


# --- Phase 2 / CHUNK-4.0a additions (append only) ---
# Per AIP_0_1_Phase2_BuildSpec_Rev1.2.md (remapped series)
# Do not modify or reorder anything above this line.
from dataclasses import dataclass, field
from typing import Literal


# Type alias for Appendix E failure type codes (matches FailureType enum values)
FailureTypeCode = Literal["A", "B", "C", "E"]


@dataclass
class ReviewVerdict:
    """Outcome of a review gate on a generated artifact.

    Per §9.3: REVIEWED state follows GENERATED.
    Per §1.7: DEFINER sovereignty for APPROVED state.
    failure_types use Appendix E taxonomy codes.
    """
    artifact_id: str
    verdict: Literal["APPROVED", "REJECTED", "NEEDS_REVISION"]
    reviewer: str  # "automated" | "definer"
    failure_types: list[FailureTypeCode] = field(default_factory=list)
    detail: str | None = None
    confidence: float = 1.0


@dataclass
class ReviewContext:
    """Assembled context for review decision.

    Contains everything a reviewer needs: the artifact content,
    its version history, recent trace events, and prior verdicts.
    """
    artifact_id: str
    artifact_content: str
    artifact_version: int
    trace_events: list[dict] = field(default_factory=list)
    prior_verdicts: list[ReviewVerdict] = field(default_factory=list)


@dataclass
class EcsTransition:
    """Record of a single ECS state transition.

    Per §1.5: every transition is recorded for provenance.
    Per §1.7: actor and reason are mandatory for sovereignty audit.
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
