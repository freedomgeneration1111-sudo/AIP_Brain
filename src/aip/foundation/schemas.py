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


# --- Phase 5 additions (append only) ---
from typing import Literal


# Type alias for budget scoping
BudgetScope = Literal["session", "project", "daily"]


@dataclass
class SextonConfig:
    """Configuration for the Sexton failure classification actor.

    Per §16.1: Sexton reads trace_events and classifies failures A-F.
    Per §1.8: Sexton audits stale model assumptions on slot changes.
    """
    classification_batch_size: int = 50
    classification_interval_seconds: int = 300
    audit_on_slot_change: bool = True
    max_unclassified_before_alert: int = 10


@dataclass
class AcePlaybookEntry:
    """A single procedural intervention rule in the ACE Playbook.

    Per §8.1: procedural intervention rules, loaded at session start.
    Per §16.1: curated by Sexton.
    Per §1.8: every rule must carry model_gen_assumption.
    Per Appendix E Type B: "Add or strengthen playbook entry."
    """
    entry_id: str
    domain: str
    failure_type: str  # A-F per Appendix E
    intervention: str
    condition: str  # Jinja2 expression
    model_gen_assumption: str | None = None
    source_trace_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    created_at: str = ""
    deprecated_at: str | None = None
    deprecated_reason: str | None = None


@dataclass
class BudgetConfig:
    """Token budget configuration.

    Per §6: BudgetStore Protocol required.
    Per §11.1: parallel nodes inherit parent budget.
    Per §1.8: all limits toggleable via config.
    """
    session_token_limit: int = 500000
    project_token_limit: int = 5000000
    daily_token_limit: int = 10000000
    budget_warning_threshold: float = 0.80
    budget_hard_stop: bool = True


@dataclass
class RoutingWeight:
    """A single domain x model routing weight.

    Per §4.3: default routing uses highest-weight model for domain.
    Per §4.3: exploration_weight controls probability of non-optimal routing.
    Per §16.1: Sexton recommends exploration_weight adjustments per domain.
    """
    model_slot: str
    domain: str
    weight: float = 0.5
    exploration_weight: float = 0.10
    sample_count: int = 0
    updated_at: str = ""


@dataclass
class BeastCadenceConfig:
    """Configuration for the Beast maintenance actor.

    Per §3: Beast — cadence / corpus / entity maintenance.
    Per §5.10: state.db stores cadence_state.
    """
    corpus_reindex_interval_seconds: int = 3600
    entity_maintenance_interval_seconds: int = 1800
    health_check_interval_seconds: int = 60
    max_reindex_batch_size: int = 1000


@dataclass
class FailureClassification:
    """Sexton's classification output for a single trace event.

    Per §16.1: Sexton assigns appropriate Type A-F label.
    Per §5.9: writes back to trace_events.failure_type.
    Per §1.8: every classification carries model_gen_assumption.
    """
    trace_event_id: int
    failure_type: str  # A-F per Appendix E
    confidence: float = 0.0
    rationale: str = ""
    model_slot_used: str = "sexton"
    tokens_consumed: int = 0
    model_gen_assumption: str | None = None
    classified_at: str = ""  # REQUIRED — ISO 8601


# --- Phase 6 additions (append only) ---
from dataclasses import dataclass, field
from typing import Literal


# Type aliases for autonomy levels
AutonomyLevel = Literal["none", "read", "write", "admin"]
McpAutonomyLevel = Literal["read", "write", "admin"]


@dataclass
class SurfaceConfig:
    """Configuration for AIP surfaces (API, CLI, Chat, MCP).

    Per §1.8: all parameters toggleable via config.
    Per §2.1: surfaces must respect laptop-viable hardware profile.
    Per §7.2: surfaces are adapter-layer, composing Foundation and Orchestration.
    """
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])
    api_workers: int = 1
    chat_max_history_turns: int = 50
    review_page_size: int = 20
    artifact_page_size: int = 20


@dataclass
class ApiRoute:
    """A single REST API route definition.

    Per §1.7: autonomy_gate=True routes enforce DEFINER sovereignty.
    Per §7.2: all routes are adapter-layer compositions.
    """
    method: str
    path: str
    handler: str
    auth_required: bool = False
    autonomy_gate: bool = False


@dataclass
class McpToolDef:
    """A single MCP tool definition.

    Per §3: MCP/API surface.
    Per Appendix D: "MCP ≠ bypass", "MCP ≠ vector_store.retrieve() directly."
    Per §1.8: model_gen_assumption tags what model limitation this tool compensates for.
    """
    tool_name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    autonomy_level: McpAutonomyLevel = "read"
    model_gen_assumption: str | None = None


@dataclass
class AutonomyEscalation:
    """A single autonomy escalation request and its resolution.

    Per §1.7: "No UI, workflow, Beast cadence, MCP call, or queued task
    may bypass the DEFINER gates."
    Per §1.8: model_gen_assumption tags what assumption this escalation encodes.
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
class ChatMessage:
    """A single chat message in the DEFINER conversation surface.

    Per §3: Chat surface is the primary DEFINER interaction point.
    Per §1.3: context is assembled from explicit stores, not long chat history.
    """
    message_id: str
    session_id: str
    role: str  # user / assistant / system
    content: str = ""
    artifacts_referenced: list[str] = field(default_factory=list)
    tokens_used: int = 0
    created_at: str = ""  # REQUIRED — ISO 8601


@dataclass
class ReviewQueueEntry:
    """A single entry in the review queue surface.

    Per §3: Review Queue surface.
    Per §9.3: ECS transitions REVIEWED→APPROVED or REVIEWED→FAILED.
    Per §1.7: canonical promotion requires DEFINER approval.
    """
    artifact_id: str
    artifact_version: int = 1
    ecs_state: str = "GENERATED"
    domain: str = ""
    project_id: str = ""
    review_type: str = "definer"  # definer / adversarial
    evaluation_scores: list[dict] = field(default_factory=list)
    created_at: str = ""  # REQUIRED — ISO 8601


# --- Phase 7 additions (append only) ---
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class VigilConfig:
    """Configuration for the Vigil actor (compiled knowledge maintenance).

    Per §3: Vigil — last missing orchestration actor.
    Per §16.1 / §1.8: monitors canonical corpus health and triggers re-evaluation on model slot changes.
    """
    enabled: bool = True
    stale_canonical_check_interval_seconds: int = 3600
    model_slot_change_audit: bool = True
    entity_consistency_check: bool = True


@dataclass
class AuthConfig:
    """Configuration for the authentication system.

    Per §1.7: enforces DEFINER sovereignty at the identity level.
    Phase 7 scope: single-DEFINER with API key support for non-interactive access (CLI/MCP).
    """
    session_secret_key: str = "change-me-in-production"
    api_key_enabled: bool = True
    session_timeout_minutes: int = 60
    rate_limit_per_ip: int = 100


@dataclass
class RateLimitConfig:
    """Token-bucket rate limiting configuration.

    Per Phase 7 scope: prevents any single surface (Beast cadence, MCP, chat) from starving others.
    Configurable per §1.8.
    """
    enabled: bool = True
    requests_per_minute: int = 60
    burst_size: int = 10
    per_definer: bool = True
    per_ip: bool = True


@dataclass
class CanonicalPromotionConfig:
    """Configuration for the canonical promotion pipeline.

    Per §1.6 / §9.3: drives REVIEWED→APPROVED→CANONICAL lifecycle with multi-stage verification.
    """
    auto_promote_on_approval: bool = False  # requires explicit DEFINER gate in 9.2
    require_vigil_health_check: bool = True
    indexing_enabled: bool = True  # LexicalStore + VectorStore sync


@dataclass
class WorkflowTemplate:
    """Extended workflow template definition (beyond Workflow 0.1)."""
    name: str
    version: str = "1.0"
    description: str = ""
    path: str = ""  # relative to workflows/


@dataclass
class DeploymentProfile:
    """Deployment profile (laptop-viable vs production)."""
    name: str  # "laptop" | "production"
    vector_backend: str
    model_providers: dict = field(default_factory=dict)
    docker_compose_profile: str = ""


# --- Phase 8 additions (append only) ---
from dataclasses import dataclass, field
from typing import Literal


# Type aliases for compiled knowledge lifecycle
CompilationState = Literal["SPECIFIED", "COMPILED", "REVIEWED", "APPROVED", "FAILED"]

# Type aliases for plugin status
PluginStatus = Literal["loaded", "error", "disabled"]

# Type aliases for collaborator roles (extends AuthRole from Phase 7)
CollaboratorRole = Literal["definer", "collaborator", "readonly"]


@dataclass
class KnowledgeCompilationConfig:
    """Configuration for the knowledge compilation system.

    Per §3: Deferred Compiled Knowledge Layer — finally implemented.
    Per §1.8: model_gen_assumption tags what the compilation criteria assume.
    Per Appendix D: compiled knowledge ≠ canonical artifact.
    """
    compilation_model_slot: str = "synthesis"
    evaluation_model_slot: str = "evaluation"
    max_source_canonicals: int = 10
    compilation_confidence_threshold: float = 0.60
    auto_index_on_approval: bool = True
    model_gen_assumption: str | None = None


@dataclass
class PluginConfig:
    """Plugin system configuration.

    Per §4.1: no hardcoded model names — plugins provide extensibility.
    Per §1.8: enabled and sandbox_mode are toggleable.
    Per §1.8: model_gen_assumption tags what model limitations plugins compensate for.
    """
    plugins_dir: str = "plugins"
    enabled: bool = True
    auto_discover: bool = True
    sandbox_mode: bool = True
    model_gen_assumption: str | None = None


@dataclass
class CollaboratorConfig:
    """Collaborator access configuration.

    Per §1.7: collaborators never bypass DEFINER sovereignty.
    Per §1.8: enabled is toggleable.
    Per Process Rule 11: collaborator_can_approve defaults to False.
    """
    enabled: bool = False
    max_collaborators: int = 5
    collaborator_can_create_drafts: bool = True
    collaborator_can_submit_review: bool = True
    collaborator_can_approve: bool = False
    readonly_can_search: bool = True


@dataclass
class PerformanceConfig:
    """Performance tuning configuration.

    Per §2.1: laptop-viable — must work on 4-6 GB RAM.
    Per §1.8: profiling_enabled is toggleable.
    """
    profiling_enabled: bool = False
    max_memory_mb: int = 4096
    retrieval_timeout_seconds: float = 30.0
    batch_embed_size: int = 32
    sqlite_wal_mode: bool = True
    sqlite_busy_timeout_ms: int = 5000
    vector_query_limit: int = 50
    fts5_query_limit: int = 50


@dataclass
class ReleaseMetadata:
    """AIP 0.1 release metadata.

    Written by release verification (CHUNK-10.7) when all §22 gates pass.
    Serves as the definitive release manifest.
    """
    release_version: str = "0.1.0"
    release_date: str = ""  # REQUIRED — ISO 8601
    release_status: str = "alpha"
    architecture_revision: str = "5.2"
    acceptance_gates_passed: list[str] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)
    breaking_changes: list[str] = field(default_factory=list)
