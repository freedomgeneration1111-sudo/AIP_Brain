"""AIP GUI Status Summary Types — typed interface for /api/v1/status/summary.

UI Cycle 3: These TypedDict classes document the stable shape of the
consolidated status summary response. They are used for type-checking
and IDE autocompletion only — the GUI never validates against these
at runtime (the backend is the source of truth).

Import boundary: this module imports ONLY stdlib (typing).
It does NOT import from aip.* or any backend modules.
"""

from __future__ import annotations

from typing import Any, TypedDict


class BackendHealth(TypedDict, total=False):
    """Backend/API health sub-object from status summary."""

    status: str  # "ok" | "degraded" | "unhealthy"
    uptime_seconds: int
    db_writable: bool
    ci_mode: bool
    critical_available: bool
    optional_available: int
    optional_total: int


class ActorStatusEntry(TypedDict, total=False):
    """Per-actor status entry from status summary."""

    initialized: bool
    state: str  # "active" | "degraded" | "failed" | "not_configured" | "instantiated"
    last_cycle_time: int | str | None


class RetrievalChannelEntry(TypedDict, total=False):
    """Per-channel retrieval health entry from status summary."""

    state: str  # "available" | "not_configured" | "degraded" | "unavailable" | "active"
    registered: bool
    latency_ms: float | None


class CorpusSummary(TypedDict, total=False):
    """Corpus summary from status summary."""

    total_turns: int
    tagged: int
    untagged: int
    embedded: int
    unembedded: int


class EmbeddingBackfillSummary(TypedDict, total=False):
    """Embedding/backfill summary from status summary."""

    state: str  # "embedded" | "backfill_running" | "configured_idle" | etc.
    backfill_state: str
    percentage: float


class ReviewQueueSummary(TypedDict, total=False):
    """Review queue summary from status summary."""

    count: int
    state: str  # "active" | "empty"


class WikiSummary(TypedDict, total=False):
    """Wiki/CODEX summary from status summary."""

    total: int
    approved: int
    generated: int
    state: str


class ModelSlotEntry(TypedDict, total=False):
    """Per-model-slot entry from status summary."""

    slot_name: str
    model: str
    provider: str
    api_key: str  # "configured" | "missing" — never the actual key


class TextGenerationSlotEntry(TypedDict, total=False):
    """Per-model-slot entry for text-generation slots (excludes embedding).

    Used by the Model Council slot selector. Never exposes secrets.
    """

    slot_name: str
    provider: str
    model: str  # Display model name; sentinel like <slot_name> if unconfigured
    has_real_model: bool  # False if model is a sentinel placeholder


class StatusSummaryResponse(TypedDict, total=False):
    """Top-level response from GET /api/v1/status/summary.

    This is the stable schema that the Operator Console Dashboard
    and right rail consume. Missing subsystems are reported honestly
    as unavailable/not_wired. Secrets are never exposed.
    """

    dogfood_mode: str  # "FULL" | "DIAGNOSTIC" | "DEGRADED" | "BARE" | "DIRECT MODEL ONLY"
    backend_health: BackendHealth
    actor_status_summary: dict[str, ActorStatusEntry]
    retrieval_health_summary: dict[str, RetrievalChannelEntry]
    corpus_summary: CorpusSummary
    embedding_backfill_summary: EmbeddingBackfillSummary
    review_queue_summary: ReviewQueueSummary
    wiki_summary: WikiSummary
    model_slot_summary: list[ModelSlotEntry]
    warnings: list[str]
    recent_activity: list[dict[str, Any]]


# ── UI Cycle 4: Ask Workbench types ──────────────────────────────────


class SourceEntry(TypedDict, total=False):
    """Per-source entry from retrieval results."""

    source_id: str
    source_type: str  # "lexical" | "vector" | "corpus_turn" | "artifact" | etc.
    title: str
    score: float
    content_snippet: str
    domain: str
    metadata: dict[str, Any]


class RetrievalTraceEntry(TypedDict, total=False):
    """Per-trace entry from GET /api/v1/retrieval/traces/session/{session_id}."""

    session_id: str
    query: str
    channels_queried: list[str]
    channels_used: list[str]
    per_channel_elapsed_ms: dict[str, float]
    total_elapsed_ms: float
    hits_before_fusion: int
    hits_after_fusion: int
    hits_after_gate: int
    verdict: str  # "OK" | "NEEDS_MORE_CONTEXT" | "NO_RESULTS"
    channel_contributions: dict[str, int]
    lexical_only: bool
    vector_contributed: bool
    degradation_warnings: list[str]


class ChatResponseMetadata(TypedDict, total=False):
    """Metadata extracted from a WebSocket chat response for answer card rendering.

    These fields are added to the WS response in UI Cycle 4.
    """

    trace_available: bool
    lexical_only: bool
    vector_contributed: bool
    direct_model: bool  # True when response came from degraded/no-provider path


class SaveArtifactResponse(TypedDict, total=False):
    """Response from POST /api/v1/turns/save-artifact."""

    artifact_id: str
    ecs_state: str  # Always "GENERATED" — never auto-approved
    message: str


class SessionTraceResponse(TypedDict, total=False):
    """Response from GET /api/v1/retrieval/traces/session/{session_id}."""

    status: str  # "ok" | "not_found" | "error"
    trace: RetrievalTraceEntry | None
    error: str  # Only present when status is "error"


# ── UI Cycle 5: Beast Counsel types ──────────────────────────────────


class BeastCommentarySuggestedAction(TypedDict, total=False):
    """A single suggested action from Beast commentary."""

    action: str
    target: str
    advisory_only: bool  # Always True — Beast never executes actions
    requires_DEFINER_approval: bool  # Always True — DEFINER must approve


class BeastCommentaryResponse(TypedDict, total=False):
    """Response from Beast commentary endpoints.

    Status values:
      - "available": Commentary was found or generated successfully
      - "not_available": No commentary exists yet for this turn
      - "unavailable": Backend cannot produce/retrieve commentary (e.g. no artifact store)
      - "not_wired": Model provider not configured — cannot generate commentary
      - "error": Generation or retrieval failed

    Persistence values:
      - "available": Commentary persisted to artifact store
      - "not_available": Could not persist (e.g. no store)
    """

    id: str
    turn_id: str
    session_id: str
    mode: str  # continuity, critique, strategy, librarian, risk
    summary: str
    critique: str
    continuity_notes: str
    risk_notes: str
    suggested_actions: list[BeastCommentarySuggestedAction]
    suggested_wiki_links: list[str]
    suggested_artifacts: list[str]
    model_comparison: str
    retrieval_notes: str
    source_notes: str
    created_at: str
    status: str  # available, not_available, unavailable, not_wired, error
    persistence: str  # available, not_available
    error: str


# ── UI Cycle 6: Model Council types ──────────────────────────────────


class PerModelResult(TypedDict, total=False):
    """Per-model result within a Model Council comparison."""

    model_slot: str
    model_id: str
    provider: str
    status: str  # "pending" | "completed" | "failed" | "excluded"
    answer: str
    error: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


class ModelCouncilResponse(TypedDict, total=False):
    """Response from POST /api/v1/beast/compare-models.

    Status values:
      - "completed": All models responded successfully
      - "partial": Some models failed, report is degraded
      - "insufficient_models": Fewer than two text-generation slots configured
      - "unavailable": Backend cannot produce a report
      - "error": Comparison failed

    Reports are ADVISORY ONLY — never auto-approved.
    """

    id: str
    status: str  # completed, partial, insufficient_models, unavailable, error
    prompt: str
    turn_id: str
    session_id: str
    selected_models: list[PerModelResult]
    convergence: str
    disagreements: str
    unique_contributions: str
    risks: str
    beast_conclusion: str
    recommended_decision: str
    degraded_models: list[str]
    failed_models: list[str]
    artifact_id: str
    created_at: str
    advisory_only: bool  # Always True
    requires_DEFINER_approval: bool  # Always True
    error: str
    synthesis_status: str  # pending, completed, unavailable, failed


# ── UI Cycle 7: Wiki / CODEX types ────────────────────────────────────


class WikiArticle(TypedDict, total=False):
    """Stable WikiArticle schema from GET /api/v1/wiki/articles and
    GET /api/v1/wiki/articles/{id}.

    Fields not yet backed by the crosslink system return empty arrays
    honestly. No fake data. No secrets exposed.

    Status values match ECS states:
      - "GENERATED": Created but not yet reviewed
      - "REVIEWED": Reviewed but not yet approved
      - "APPROVED": Approved by DEFINER — canonical
      - "REJECTED": Rejected by DEFINER
      - "SUPERSEDED": Replaced by a newer version
      - "UNKNOWN": No ECS state entry found
    """

    id: str
    title: str
    summary: str
    body: str
    status: str  # ECS state: GENERATED, REVIEWED, APPROVED, REJECTED, SUPERSEDED, UNKNOWN
    tags: list[str]
    aliases: list[str]
    linked_articles: list[str]
    backlinks: list[dict[str, Any]]
    source_documents: list[str]
    related_artifacts: list[str]
    related_turns: list[str]
    related_beast_commentaries: list[str]
    open_questions: list[str]
    contradictions: list[dict[str, Any]]
    revision_history: list[dict[str, Any]]
    created_at: str
    updated_at: str
    approved_at: str | None
    domain: str
    artifact_type: str  # "wiki" | "proposal"
    version: int
    word_count: int
    metadata: dict[str, Any]
    storage_backend: str  # "artifact_store" | "sqlite_compat" | "unavailable"


class WikiArticleListResponse(TypedDict, total=False):
    """Response from GET /api/v1/wiki/articles."""

    items: list[WikiArticle]
    total: int
    page: int
    page_size: int
    storage_backend: str  # "artifact_store" | "sqlite_compat" | "unavailable"


class WikiArticleCreateResponse(TypedDict, total=False):
    """Response from POST /api/v1/wiki/articles.

    State is always GENERATED — never auto-approved.
    """

    id: str
    title: str
    domain: str
    state: str  # Always "GENERATED"
    message: str
    created_at: str
    storage_backend: str  # "artifact_store" | "sqlite_compat"


class WikiArticleUpdateResponse(TypedDict, total=False):
    """Response from PATCH /api/v1/wiki/articles/{id}.

    ECS state is unchanged by editing — separate review/approve required.
    """

    id: str
    title: str
    version: int
    state: str  # Unchanged from current
    message: str
    updated_at: str
    storage_backend: str  # "artifact_store" | "sqlite_compat"


class WikiBacklinkEntry(TypedDict, total=False):
    """A single backlink from the backlinks endpoint."""

    source_id: str
    source_type: str
    relation_type: str
    confidence: float | None


class WikiBacklinksResponse(TypedDict, total=False):
    """Response from GET /api/v1/wiki/backlinks/{id}."""

    article_id: str
    backlinks: list[WikiBacklinkEntry]
    total: int
    available: bool  # False if graph_edges table not present
    storage_backend: str  # "artifact_store" | "sqlite_compat" | "unavailable"


class WikiContradictionEntry(TypedDict, total=False):
    """A single contradiction from the contradictions endpoint.

    Contradictions are never auto-resolved — DEFINER must review.
    """

    contradiction_id: str
    topic_id: str
    claim_a: str
    source_a_id: str
    source_a_title: str
    claim_b: str
    source_b_id: str
    source_b_title: str
    severity: str  # critical, major, minor, apparent
    status: str  # open, investigating, resolved_correct, etc.
    context: str
    detected_at: str


class WikiContradictionsResponse(TypedDict, total=False):
    """Response from GET /api/v1/wiki/contradictions."""

    items: list[WikiContradictionEntry]
    total: int
    available: bool  # False if codex_contradictions table not present
    storage_backend: str  # "artifact_store" | "sqlite_compat" | "unavailable"


class WikiStaleEntry(TypedDict, total=False):
    """A single stale topic from the stale endpoint."""

    topic_id: str
    title: str
    domain: str
    staleness_score: float
    last_activity_at: str
    has_wiki_page: bool


class WikiStaleResponse(TypedDict, total=False):
    """Response from GET /api/v1/wiki/stale."""

    items: list[WikiStaleEntry]
    total: int
    available: bool  # False if codex_topics table not present
    storage_backend: str  # "artifact_store" | "sqlite_compat" | "unavailable"


# ── UI Cycle 8: Crosslink System types ─────────────────────────────────


class KnowledgeLink(TypedDict, total=False):
    """A single knowledge link from the Crosslink System.

    Links are first-class objects connecting knowledge entities.
    Default status is 'suggested' — requires DEFINER approval to become 'approved'.
    No linked objects are mutated by link creation.
    No artifacts are approved/exported by link creation.
    """

    id: str  # Stable link ID: link:{hash}:{timestamp}
    source_type: str  # e.g. "wiki_article", "artifact", "conversation_turn"
    source_id: str
    target_type: str
    target_id: str
    relation_type: str  # e.g. "supports", "contradicts", "mentions"
    confidence: float  # 0.0 - 1.0
    created_by: str  # "definer" | "beast" | "system"
    created_at: str
    updated_at: str
    approved_by_definer: bool  # Always False for new links — explicit approval required
    approved_at: str | None
    status: str  # "suggested" | "approved" | "rejected" | "deleted"
    provenance: str
    notes: str
    storage_backend: str  # "knowledge_link_store" | "unavailable"


class KnowledgeLinkListResponse(TypedDict, total=False):
    """Response from GET /api/v1/links."""

    items: list[KnowledgeLink]
    total: int
    limit: int
    offset: int
    storage_backend: str  # "knowledge_link_store" | "unavailable"


class KnowledgeLinkCreateResponse(TypedDict, total=False):
    """Response from POST /api/v1/links.

    Default status is 'suggested', approved_by_definer is False.
    No linked objects are mutated.
    """

    id: str
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    relation_type: str
    confidence: float
    created_by: str
    created_at: str
    updated_at: str
    approved_by_definer: bool  # Default False
    approved_at: str | None
    status: str  # Default "suggested"
    provenance: str
    notes: str
    storage_backend: str


class KnowledgeLinkUpdateResponse(TypedDict, total=False):
    """Response from PATCH /api/v1/links/{link_id}.

    Approval requires explicit approved_by_definer=True.
    """

    id: str
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    relation_type: str
    confidence: float
    created_by: str
    created_at: str
    updated_at: str
    approved_by_definer: bool
    approved_at: str | None
    status: str
    provenance: str
    notes: str
    storage_backend: str


class KnowledgeLinkBacklinksResponse(TypedDict, total=False):
    """Response from GET /api/v1/links/backlinks/{target_type}/{target_id}.

    Returns honest empty list if no backlinks exist or storage unavailable.
    """

    target_type: str
    target_id: str
    backlinks: list[KnowledgeLink]
    total: int
    available: bool  # False if storage unavailable
    storage_backend: str


class KnowledgeLinkForwardLinksResponse(TypedDict, total=False):
    """Response from GET /api/v1/links/forward/{source_type}/{source_id}.

    Returns honest empty list if no forward links exist or storage unavailable.
    """

    source_type: str
    source_id: str
    forward_links: list[KnowledgeLink]
    total: int
    available: bool  # False if storage unavailable
    storage_backend: str


# ── UI Cycle 9: Artifact Workbench types ────────────────────────────────


class ArtifactListItem(TypedDict, total=False):
    """A single artifact item in the artifact list response."""

    artifact_id: str
    title: str
    ecs_state: str  # GENERATED, REVIEWED, APPROVED, REJECTED, SUPERSEDED, FAILED
    has_needs_revision: bool  # True if artifact has a NEEDS_REVISION verdict event
    has_export: bool  # True if artifact has an export event
    artifact_type: str  # ask_answer, beast_wiki, beast_domain_proposal, etc.
    domain: str
    project: str
    model_slot: str
    model_name: str
    source_count: int
    created_at: str
    updated_at: str


class ArtifactListResponse(TypedDict, total=False):
    """Response from GET /api/v1/artifacts."""

    items: list[ArtifactListItem]
    page: int
    page_size: int
    total: int


class ArtifactDetailResponse(TypedDict, total=False):
    """Response from GET /api/v1/artifacts/{artifact_id}.

    No fake data. Empty arrays if reviews/sources unavailable.
    No secrets exposed.
    """

    artifact_id: str
    title: str
    ecs_state: str
    has_needs_revision: bool
    has_export: bool
    artifact_type: str
    content: str
    metadata: dict[str, Any]
    domain: str
    project: str
    prompt: str
    model_slot: str
    model_name: str
    model_provider: str
    generated_at: str
    session_id: str
    source_ids: list[str]
    source_types: list[str]
    source_count: int
    review_notes: list[dict[str, Any]]
    transition_history: list[dict[str, Any]]
    export_events: list[dict[str, Any]]
    force_export_events: list[dict[str, Any]]
    export_eligible: bool  # Only True if ecs_state == APPROVED
    export_requires_force: bool  # True if not APPROVED
    versions: list[dict[str, Any]]
    created_at: str
    updated_at: str


class ArtifactSourcesResponse(TypedDict, total=False):
    """Response from GET /api/v1/artifacts/{artifact_id}/sources.

    Returns honest empty list if sources unavailable.
    """

    artifact_id: str
    source_count: int
    sources: list[dict[str, Any]]


class ArtifactReviewsResponse(TypedDict, total=False):
    """Response from GET /api/v1/artifacts/{artifact_id}/reviews.

    Returns honest empty list if reviews unavailable.
    """

    artifact_id: str
    ledger: list[dict[str, Any]]
    transition_count: int
    review_count: int
    note_count: int
    export_count: int
    force_export_count: int


class ArtifactActionResponse(TypedDict, total=False):
    """Response from approve/reject/needs-revision/export/force-export endpoints.

    No auto-approve. No auto-export. All actions are explicit DEFINER actions.
    """

    artifact_id: str
    previous_state: str
    new_state: str
    actor: str  # Always "definer"
    canonical_written: bool  # Only for approve
    artifact_preserved: bool  # Always True — artifacts are never deleted by review
    note: str  # For reject
    instruction: str  # For needs-revision
    exported: bool  # For export/force-export
    exported_at: str
    force_bypass: bool  # True only for force-export
    force_bypass_state: str  # The state that was bypassed
    force_reason: str  # The reason given for override
    audit_recorded: bool  # Always True for force-export


class ArtifactDashboardResponse(TypedDict, total=False):
    """Response from GET /api/v1/artifacts/dashboard.

    Honest zeros if stores unavailable.
    """

    counts: dict[str, int]  # ECS state → count
    needs_revision_count: int
    force_export_count: int
    total_active: int
    total_pending_review: int
    recent_events: list[dict[str, Any]]


# ── UI Cycle 10: Corpus Workbench types ─────────────────────────────────


class CorpusStatusResponse(TypedDict, total=False):
    """Response from GET /api/v1/corpus/status.

    Honest zeros if store unavailable.
    """

    total_turns: int
    embedded: int
    tagged: int
    untagged: int
    embed_failures: int
    needs_reembed: int
    documents: int
    conversations: int
    embed_coverage: float
    tag_coverage: float
    error: str


class CorpusEmbeddingProgressResponse(TypedDict, total=False):
    """Response from GET /api/v1/corpus/embedding-progress.

    Honest zeros if store unavailable.
    """

    total: int
    embedded: int
    unembedded: int
    needs_reembed: int
    percentage: float
    last_embed_at: str | None
    embedding_models: dict[str, int]
    sexton_pass: dict[str, Any] | None
    error: str


class CorpusDocumentItem(TypedDict, total=False):
    """A single document item in the corpus document list response."""

    source_path: str
    source_model: str
    turn_count: int
    embedded_count: int
    unembedded_count: int
    embed_fail_count: int
    needs_reembed_count: int
    primary_domains: list[str]
    last_updated: str
    conversation_count: int


class CorpusDocumentListResponse(TypedDict, total=False):
    """Response from GET /api/v1/corpus/documents."""

    items: list[CorpusDocumentItem]
    total: int
    limit: int
    offset: int
    error: str


class CorpusDocumentDetailResponse(TypedDict, total=False):
    """Response from GET /api/v1/corpus/documents/{source_path}.

    Returns not_found=True honestly if document doesn't exist.
    No fake data. No secrets exposed.
    """

    not_found: bool
    source_path: str
    source_model: str
    source_account: str
    turn_count: int
    embedded_count: int
    unembedded_count: int
    embed_fail_count: int
    needs_reembed_count: int
    embed_coverage: float
    primary_domains: list[str]
    embedding_models: list[str]
    first_turn_at: str
    last_updated: str
    conversation_count: int
    total_word_count: int
    errors: list[dict[str, Any]]
    sample_turns: list[dict[str, Any]]
    error: str


class CorpusFailedJob(TypedDict, total=False):
    """A single failed ingest/embed job from corpus problems."""

    turn_id: str
    source_path: str
    fail_count: int
    last_error: str
    source_model: str
    primary_domain: str


class CorpusStaleDoc(TypedDict, total=False):
    """A single stale document from corpus problems."""

    source_path: str
    last_updated: str
    turn_count: int


class CorpusDuplicateHash(TypedDict, total=False):
    """A single duplicate content hash from corpus problems."""

    content_hash: str
    count: int


class CorpusProblemsResponse(TypedDict, total=False):
    """Response from GET /api/v1/corpus/problems.

    Returns honest empty lists when no problems exist.
    Never fakes healthy state.
    """

    failed_ingest_jobs: list[CorpusFailedJob]
    unembedded_count: int
    needs_reembed_count: int
    duplicate_hashes: list[CorpusDuplicateHash]
    stale_docs: list[CorpusStaleDoc]
    available: bool
    error: str


class CorpusUnembeddedResponse(TypedDict, total=False):
    """Response from GET /api/v1/corpus/unembedded.

    Honest empty list when all chunks are embedded.
    """

    items: list[dict[str, Any]]
    count: int
    available: bool
    error: str


class CorpusBackfillResponse(TypedDict, total=False):
    """Response from POST /api/v1/corpus/backfill.

    Explicit DEFINER action. Status values:
      - accepted: Backfill started
      - not_wired: Embedding provider not configured
      - already_running: Backfill in progress
      - error: Failed to start
    """

    status: str  # accepted, not_wired, already_running, error
    message: str
    limit: int
    batch_size: int
    dry_run: bool


class CorpusRetryFailedResponse(TypedDict, total=False):
    """Response from POST /api/v1/corpus/retry-failed.

    Explicit DEFINER action. Status values:
      - accepted: Failures cleared, will retry in next cycle
      - no_failed: No failed embed jobs found
      - not_wired: CorpusTurnStore or embedding provider not wired
      - error: Failed
    """

    status: str  # accepted, no_failed, not_wired, error
    message: str
    retried_count: int


class CorpusIngestResponse(TypedDict, total=False):
    """Response from POST /api/v1/corpus/ingest.

    Explicit DEFINER action. Reports honestly: never fakes success.
    """

    type: str  # file, directory, error
    source_path: str
    source_type: str
    turns_ingested: int
    turns_skipped: int
    turns_updated: int
    turns_failed: int
    warnings: list[str]
    errors: list[str]
    error: str


# ═══════════════════════════════════════════════════════════════════════
# UI Cycle 11 — Retrieval Lab
# ═══════════════════════════════════════════════════════════════════════


class RetrievalTestItem(TypedDict, total=False):
    """A single result item from a retrieval test channel."""

    id: str
    title: str
    snippet: str
    score: float
    source_type: str
    source_id: str
    domain: str
    metadata: dict


class RetrievalChannelResult(TypedDict, total=False):
    """Per-channel result from a retrieval test."""

    channel: str
    state: str  # active, degraded, failed, disabled, unavailable, not_configured, empty
    result_count: int
    latency_ms: float
    items: list[RetrievalTestItem]
    warning: str
    error: str
    backend_type: str
    vss_available: bool | None
    embedding_provider_configured: bool | None


class RetrievalTestScores(TypedDict, total=False):
    """Scores summary from a retrieval test."""

    top_rrf_scores: list[dict]
    hits_before_fusion: int
    hits_after_fusion: int
    hits_after_quality_gate: int
    verdict: str


class RetrievalTestResponse(TypedDict, total=False):
    """Response from POST /api/v1/retrieval/test.

    Full retrieval diagnostic without answer synthesis.
    """

    status: str
    message: str
    query: str
    selected_channels: list[str]
    channel_results: dict[str, RetrievalChannelResult]
    channel_health: dict[str, str]
    latency_ms: float
    per_channel_latency_ms: dict[str, float]
    scores: RetrievalTestScores
    fusion_results: list[dict]
    selected_context: list[dict]
    degraded_channels: list[str]
    failed_channels: list[str]
    warnings: list[str]
    trace: dict | None
    lexical_only: bool
    vector_contributed: bool


class RetrievalHealthChannel(TypedDict, total=False):
    """Per-channel health from GET /api/v1/retrieval/health."""

    channel: str
    state: str  # active, degraded, unavailable, not_configured
    backend_type: str
    available: bool
    degraded: bool
    degradation_reason: str
    embedding_provider_configured: bool | None
    vss_available: bool | None
    vector_count: int | None


class RetrievalEmbeddingCoverage(TypedDict, total=False):
    """Embedding coverage from retrieval health."""

    status: str
    coverage_percent: float
    total_turns: int
    embedded_turns: int


class RetrievalHealthSummary(TypedDict, total=False):
    """Summary counts from retrieval health."""

    total_channels: int
    active: int
    degraded: int
    unavailable: int


class RetrievalHealthResponse(TypedDict, total=False):
    """Response from GET /api/v1/retrieval/health."""

    status: str
    channels: dict[str, RetrievalHealthChannel]
    embedding_coverage: RetrievalEmbeddingCoverage
    vector_fallback_chain: list[str]
    summary: RetrievalHealthSummary


# ═══════════════════════════════════════════════════════════════════════
# UI Cycle 12 — Maintenance Center
# ═══════════════════════════════════════════════════════════════════════


class MaintenanceActorEntry(TypedDict, total=False):
    """Per-actor status entry from GET /api/v1/maintenance/status."""

    name: str  # beast, vigil, sexton
    initialized: bool
    scheduled: bool
    running: bool
    enabled: bool
    state: str  # active, degraded, failed, not_configured, unknown, instantiated
    last_run_at: str | int | None
    next_run_at: str | int | None
    last_result: str | None
    last_error: str | None
    degraded_reason: str | None
    run_now_supported: bool
    cycle_count: int
    recent_errors: list[str]
    dependencies: dict[str, bool]
    missing_core_dependencies: list[str]
    interval_seconds: int
    role: str
    health_error: str


class MaintenanceBackfillStatus(TypedDict, total=False):
    """Backfill status from GET /api/v1/maintenance/status."""

    state: str  # not_configured, configured_idle, backfill_running, etc.
    running: bool
    progress: dict[str, Any]
    last_result: dict[str, Any] | None


class MaintenanceCapabilityEntry(TypedDict, total=False):
    """A single maintenance capability from GET /api/v1/maintenance/status."""

    available: bool
    status: str  # available, not_wired, scheduled_only
    message: str


class MaintenanceStatusResponse(TypedDict, total=False):
    """Response from GET /api/v1/maintenance/status.

    Aggregated maintenance overview. Honest about unavailable states.
    Never fakes actor health or capability availability.
    """

    actors: dict[str, MaintenanceActorEntry]
    backfill: MaintenanceBackfillStatus
    capabilities: dict[str, MaintenanceCapabilityEntry]
    warnings: list[str]


class ActorRunEvent(TypedDict, total=False):
    """A single actor run event from GET /api/v1/actors/{actor}/runs."""

    event_type: str
    actor: str
    artifact_id: str
    from_state: str | None
    to_state: str | None
    timestamp: str
    metadata: dict[str, Any]


class ActorRunsResponse(TypedDict, total=False):
    """Response from GET /api/v1/actors/{actor}/runs.

    Honest empty list if event store unavailable or no events exist.
    Never fakes run history.
    """

    actor: str
    runs: list[ActorRunEvent]
    available: bool
    count: int
    message: str


class MaintenanceLogEntry(TypedDict, total=False):
    """A single maintenance log entry from GET /api/v1/maintenance/logs."""

    event_type: str
    actor: str
    artifact_id: str
    from_state: str | None
    to_state: str | None
    timestamp: str
    metadata: dict[str, Any]


class MaintenanceLogsResponse(TypedDict, total=False):
    """Response from GET /api/v1/maintenance/logs.

    Honest empty list if event store unavailable. Never fakes logs.
    """

    logs: list[MaintenanceLogEntry]
    available: bool
    count: int
    message: str


class MaintenanceJobResponse(TypedDict, total=False):
    """Response from POST /api/v1/maintenance/* job endpoints.

    All maintenance jobs are explicit DEFINER actions.
    Status values:
      - accepted: Job started
      - completed: Job finished (synchronous)
      - not_wired: Capability not available
      - scheduled_only: Only available via scheduled actor cycle
      - already_running: Job already in progress
      - error: Failed
    """

    status: str  # accepted, completed, not_wired, scheduled_only, already_running, error
    message: str
    alternative: str  # Suggested alternative action
    limit: int
    batch_size: int
    dry_run: bool
    stale_count: int
    stale_docs: list[dict[str, Any]]
