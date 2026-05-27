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


# --- Phase 3 / CHUNK-5.0a additions (append only) ---
from dataclasses import dataclass, field
from typing import Literal


# Type alias for trajectory signal types
TrajectorySignalType = Literal["loop", "anxiety", "failure_streak"]


@dataclass
class TrajectorySignal:
    """A single detection from an L4 trajectory detector.

    Per §10.1: loop detection → D, anxiety → F, failure streak → E.
    Per §1.8: every L4 trigger must carry model_gen_assumption.
    Per Appendix E: D/E/F are the L4 failure type codes.
    """
    signal_type: TrajectorySignalType
    session_id: str
    artifact_id: str | None = None
    failure_type: Literal["D", "E", "F"] = "D"
    confidence: float = 0.0
    detail: str = ""
    detected_at: str = ""  # REQUIRED — ISO 8601
    model_gen_assumption: str | None = None


@dataclass
class SessionContext:
    """State of a multi-turn session for L4 and context management.

    Tracks turn count, context window usage, artifacts produced,
    and when the last reset occurred.
    """
    session_id: str
    project_id: str
    turn_count: int = 0
    context_tokens_estimate: int = 0
    context_window_limit: int = 128000
    artifacts_produced: list[str] = field(default_factory=list)
    last_reset_at: str | None = None


@dataclass
class ModelSlotConfig:
    """Resolved configuration for a named model slot (per §4.1)."""
    slot_name: str
    provider: str
    model: str
    base_url: str | None = None
    fallback_provider: str | None = None
    fallback_model: str | None = None
    dimensions: int | None = None


# --- Phase 4 additions (append only) ---
from dataclasses import dataclass, field
from typing import Literal


# Type alias for vector backend selection per §2.2
VectorBackendType = Literal["pgvector", "sqlite_vss"]


@dataclass
class PgvectorConfig:
    """Configuration for the pgvector VectorStore adapter.

    Per §2.2: PostgreSQL 16 + pgvector is the required production path.
    Per §1.8: all parameters toggleable via config, not hardcoded.
    HNSW parameters tune index quality vs. build time.
    """
    connection_string: str
    pool_min_size: int = 2
    pool_max_size: int = 10
    pool_timeout_seconds: float = 30.0
    statement_timeout_ms: int = 5000
    hnsw_m: int = 16
    hnsw_ef_construction: int = 64
    hnsw_ef_search: int = 40


@dataclass
class MigrationStatus:
    """Tracks the state of a sqlite_vss → pgvector migration.

    Per Phase Scope Definition: migration must be idempotent and resumable.
    checkpoint_id enables resuming from last successful vector.
    """
    source_backend: str
    target_backend: str
    total_vectors: int = 0
    migrated_vectors: int = 0
    failed_vectors: int = 0
    started_at: str = ""
    completed_at: str | None = None
    checkpoint_id: str | None = None


@dataclass
class MigrationCheckpoint:
    """A resumable migration point.

    If migration is interrupted, resume from last_migrated_id + 1.
    """
    checkpoint_id: str
    source_backend: str
    target_backend: str
    last_migrated_id: int = 0
    total_migrated: int = 0
    created_at: str = ""


@dataclass
class EvaluationScore:
    """A single evaluation dimension score.

    Per §1.8: model_gen_assumption tags what model limitation this
    evaluation compensates for. Sexton audits these when model slots change.
    """
    dimension: str
    score: float = 0.0
    rationale: str | None = None
    model_slot_used: str = ""
    tokens_consumed: int = 0
    model_gen_assumption: str | None = None


@dataclass
class FaithfulnessResult:
    """Faithfulness evaluation output (L3a Stage 2).

    Per §9.1: faithfulness evaluation checks synthesis output against
    retrieved context. Hallucination flags identify claims not grounded
    in the retrieved context package.
    """
    artifact_id: str
    faithfulness_score: float = 0.0
    context_coverage: float = 0.0
    hallucination_flags: list[str] = field(default_factory=list)
    evaluation_scores: list[EvaluationScore] = field(default_factory=list)


@dataclass
class DomainCoherenceResult:
    """Domain coherence evaluation output (L3a Stage 3).

    Per §9.1: domain coherence evaluation checks domain-specific quality.
    Violations list domain-specific coherence issues found.
    """
    artifact_id: str
    coherence_score: float = 0.0
    domain: str = ""
    violations: list[str] = field(default_factory=list)
    evaluation_scores: list[EvaluationScore] = field(default_factory=list)
