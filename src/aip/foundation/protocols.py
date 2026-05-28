"""
Phase 0 protocol definitions (stub form).

Per ANNEX D of AIP 0.1 BuildSpec Phase 0.

This file will be amended by addition only (never rewritten) starting in.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# Phase 0 VectorStore (the version that will amend)
@runtime_checkable
class VectorStore(Protocol):
    """Vector store abstraction for pgvector / sqlite-vss swap.
    Phase 1 amendment: added upsert, retrieve with query_vector, delete.
    Phase 0 store() method is deprecated but retained for backward compat.
    """

    # Phase 1 methods (added by per Rev 1.3)
    async def upsert(
        self,
        id: str,
        embedding: list[float],
        content: str,
        metadata: dict,
        domain: str | None = None,
    ) -> None:
        """Store or update a vector with metadata."""
        ...

    async def retrieve(
        self,
        query_vector: list[float],
        domain: str | None = None,
        top_k: int = 10,
    ) -> list["Chunk"]:
        """Retrieve chunks by vector similarity."""
        ...

    async def delete(self, id: str) -> None:
        """Delete a vector entry by id."""
        ...

    async def count(self, domain: str | None = None) -> int:
        """Count entries, optionally filtered by domain."""
        ...

    # Deprecated Phase 0 method — retained for backward compatibility
    async def store(self, chunk: "Chunk") -> str:
        """Deprecated: use upsert() instead. Returns chunk id."""
        ...

    # --- Phase 4 amendments (append method stubs only — do NOT redeclare existing Protocol classes) ---

    # VectorStore — add health_check and count method stubs to existing class
    # (upsert and retrieve already defined in Phase 1 )
    async def health_check(self) -> dict:
        """Check backend health and return status.

        Returns dict with: connected (bool), pool_size (int),
        latency_ms (int), backend_name (str).
        Used by aip status and production hardening.
        """
        ...

    # count() already defined above — Phase 4 amendment adds health_check only

    # --- Phase 3 amendment (append method stub only) ---
    async def list_stale_vectors(
        self, threshold_days: int = 30, domain: str | None = None, limit: int = 100
    ) -> list[dict]:
        """List vectors that have not been updated within threshold_days.

        Used by Beast corpus maintenance and Vigil to identify stale vectors
        that may need re-embedding after model slot changes or knowledge updates.

        Returns list of dicts with: id, domain, updated_at/created_at, metadata.
        """
        ...


# Other Phase 0 protocols as empty runtime_checkable stubs
@runtime_checkable
class LexicalStore(Protocol):
    """Full-text search abstraction.

    Abstracts SQLite FTS5 so that orchestration and adapter code
    never import sqlite3 directly for search operations.

    Supports domain-filtered retrieval.
    Laptop-viable, local-only.
    """

    async def search(
        self,
        query: str,
        domain: str | None = None,
        limit: int = 10,
    ) -> list["Chunk"]:
        """Full-text search for documents matching query.

        Returns Chunk results with score = FTS5 rank.
        Optionally filtered by domain.
        """
        ...

    async def index_document(
        self,
        doc_id: str,
        content: str,
        domain: str,
        metadata: dict,
    ) -> None:
        """Add or update a document in the FTS5 index.

        Idempotent — re-indexing the same doc_id updates content.
        """
        ...

    async def delete_document(self, doc_id: str) -> None:
        """Remove a document from the FTS5 index.

        Per Appendix D: "Supersession ≠ deletion" — but stale
        FTS5 entries should be cleaned up.
        """
        ...


@runtime_checkable
class CanonicalStore(Protocol):
    """DEFINER-approved durable artifact store."""

    # --- Phase 6 amendments (append method stubs only) ---
    async def read_canonical(self, artifact_id: str) -> dict | None:
        """Read a canonical artifact by ID.

        Returns None if no canonical version exists.
        Canonical artifacts are DEFINER-approved.
        """
        ...

    async def write_canonical(
        self, artifact_id: str, content: dict, approved_by: str
    ) -> None:
        """Write a canonical artifact.

        Only called after DEFINER approval (ECS APPROVED state).
        approved_by must be "definer" — enforced by AutonomyGate.
        """
        ...

    async def list_canonical(
        self, domain: str | None = None
    ) -> list[dict]:
        """List canonical artifacts, optionally filtered by domain.

        Returns list of dicts with artifact_id, domain, approved_by, created_at.
        """
        ...


@runtime_checkable
class ArtifactStore(Protocol):
    """Artifact store for reading and writing generated content.
    P1 fix: Phase 0 left this as `...` stub.
    Method signatures match call sites.
    """
    async def write(self, id: str, content: str, metadata: dict) -> None:
        """Write artifact content with metadata."""
        ...

    async def read(self, id: str) -> str:
        """Read artifact content by id."""
        ...

    # --- Phase 2 amendments (append method stubs only) ---
    async def read(self, id: str, version: int | None = None) -> str:
        """Read artifact content by id.

        version=None returns the latest version (Phase 1 backward compat).
        version=N returns the specific version (1-indexed).
        """
        ...

    async def list_versions(self, id: str) -> list[int]:
        """Return list of available version numbers for this artifact id."""
        ...


@runtime_checkable
class TraceStore(Protocol):
    """Trace store for logging node execution events.
    P1 fix: Phase 0 left this as `...` stub.
    Method signature matches call site.
    Method name is write_event (confirmed per Rev 1.3 R2').
    """
    async def write_event(
        self,
        session_id: str,
        node_type: str,
        failure_type: str,
        outcome: str,
        detail: str | None = None,
    ) -> None:
        """Write a trace event for node execution."""
        ...

    # --- Phase 3 amendment (append method stub only) ---
    async def query_events(
        self,
        session_id: str,
        node_type: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query trace events for a session (raw dicts).

        Used by L4 trajectory detectors (loop, anxiety, failure streak).
        """
        ...

    # L4 addition (amend by addition only — never rewrite existing methods)
    # Provides the query surface required by TrajectoryMonitor and future Sexton.
    # Implementations must return events in descending created_at order (most recent first).
    async def get_recent_events(
        self, session_id: str, limit: int = 100
    ) -> list[dict]:
        """Return recent trace events for a session (raw dicts matching trace_events schema).
        Used by L4 trajectory regulation for drift/loop detection. Zero tokens.
        """
        ...

    # Sexton / addition (amend by addition only)
    # Provides the cross-session query surface required by Sexton for
    # classifying unclassified failures. Must return events newest-first.
    async def get_unclassified_failures(self, limit: int = 100) -> list[dict]:
        """Return recent unclassified failures (failure_type IS NULL and outcome == 'failure').
        Used by Sexton for Appendix E classification. Zero tokens.
        """
        ...


@runtime_checkable
class EntityStore(Protocol):
    """Entity and operations store."""

    # --- Phase 6 amendments (append method stubs only) ---
    async def get_entity(self, entity_id: str) -> dict | None:
        """Get an entity by ID.

        Returns None if entity does not exist.
        """
        ...

    async def list_entities(
        self, entity_type: str | None = None
    ) -> list[dict]:
        """List entities, optionally filtered by type.

        Returns list of dicts with entity_id, entity_type, name, metadata.
        """
        ...

    async def update_entity(
        self, entity_id: str, updates: dict
    ) -> None:
        """Update entity fields.

        updates is a dict of field→value pairs to apply.
        """
        ...


@runtime_checkable
class EventStore(Protocol):
    """Event store for recording ECS state transitions and lifecycle events.
    P1 fix: Phase 0 left this as `...` stub.
    Method signature matches call site.
    """
    async def write_event(
        self,
        event_type: str,
        actor: str,
        artifact_id: str,
        from_state: str | None = None,
        to_state: str | None = None,
        **kwargs,
    ) -> None:
        """Write an event recording a state transition or lifecycle event."""
        ...

    # --- Phase 2 amendment (append method stub only) ---
    async def query(
        self,
        artifact_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list["Event"]:
        """Query events by artifact_id and/or event_type.

        Returns most recent events first (descending timestamp).
        Used by review node, DEFINER audit, and Sexton analysis.
        """
        ...


@runtime_checkable
class ProjectStore(Protocol):
    """Project and WorkUnit persistent state."""

    # --- Phase 5 amendment (append method stub only) ---
    async def list_projects(self, status: str | None = None) -> list[dict]:
        """List projects, optionally filtered by status.

        Used by Beast for corpus maintenance iteration.
        Returns list of dicts with project_id, name, status, etc.
        """
        ...


@runtime_checkable
class EcsStore(Protocol):
    """Artifact governance. EcsStore.transition() is the only legal path for state changes."""

    async def transition(
        self,
        artifact_id: str,
        from_state: str | None,
        to_state: str,
        actor: str,
        reason: str,
        superseded_by: str | None = None,
    ) -> None: ...

    # --- Phase 2 amendment (append method stub only) ---
    async def current_state(self, artifact_id: str) -> str | None:
        """Return the current ECS state of the artifact, or None if unknown."""
        ...


@runtime_checkable
class BudgetStore(Protocol):
    """Budget and autonomy tracking.
    method signatures added by amend-by-addition (matching the
    InMemoryBudgetStore implementation delivered in 3.11). Zero-token contract.

    Phase 5 : extended by addition with the canonical get_budget /
    record_usage / check_limit interface. Old methods
    retained for backward compatibility during 7.0b transition.
    """
    async def consume(self, amount: int, budget_id: str = "default") -> bool:
        """Consume amount from the named budget. Return True if successful."""
        ...

    async def remaining(self, budget_id: str = "default") -> int:
        """Return remaining budget units for the named budget."""
        ...

    async def reset(self, budget_id: str = "default", amount: int | None = None) -> None:
        """Reset or initialize the named budget."""
        ...

    # --- Phase 5 new methods (append only; extends existing Protocol) ---
    async def get_budget(self, scope: "BudgetScope", scope_id: str) -> dict:
        """Get current budget status.

        Args:
            scope: Budget scope (session/project/daily).
            scope_id: Scope identifier (session_id, project_id, or date string).

        Returns:
            dict with consumed, remaining, limit, warning_threshold.
        """
        ...

    async def record_usage(
        self,
        scope: "BudgetScope",
        scope_id: str,
        tokens_used: int,
        cost_usd: float,
        model_slot: str,
    ) -> None:
        """Record token consumption after a model call.

        Called by the workflow engine after each agent node completes.
        Writes to budget_ledger table in state.db.
        """
        ...

    async def check_limit(self, scope: "BudgetScope", scope_id: str) -> bool:
        """Check whether budget has remaining capacity.

        Returns True if budget is not exhausted, False if at/past limit.
        Used by workflow engine before dispatching model calls.
        When budget_hard_stop is True, returning False blocks the call.
        """
        ...


@runtime_checkable
class AutonomyGate(Protocol):
    """Two-phase autonomy gate.
    method signatures added by amend-by-addition (matching the
    SimpleAutonomyGate implementation delivered in 3.11). Per Architecture L6.
    Low levels (Phase 1) are local; higher levels require DEFINER/policy (Phase 2).
    """
    async def request_autonomy(self, level: int, context: dict[str, Any]) -> bool:
        """Request permission for the given autonomy level. Return True if granted."""
        ...

    async def record_autonomy_use(self, level: int, context: dict[str, Any]) -> None:
        """Record that the given autonomy level was used (for audit / Sexton)."""
        ...

    # --- Phase 6 amendments (append method stubs only — AutonomyGate partial from preserved per Rule #10) ---
    async def check(
        self,
        action_type: str,
        resource_id: str,
        requested_level: "AutonomyLevel",
        requested_by: str,
    ) -> "AutonomyEscalation":
        """Check whether an action is allowed at the current autonomy level.

        Returns an AutonomyEscalation record with granted=True/False.
        Does not block — use escalate() for blocking gate.
        """
        ...

    async def escalate(
        self,
        action_type: str,
        resource_id: str,
        requested_level: "AutonomyLevel",
        requested_by: str,
    ) -> "AutonomyEscalation":
        """Request autonomy escalation for an action.

        Blocks until DEFINER approves if escalation_requires_definer is True.
        Returns an AutonomyEscalation record with the resolution.
        """
        ...

    async def audit_log(self, limit: int = 100) -> list["AutonomyEscalation"]:
        """Return recent autonomy escalation records for audit.

        Used by admin console and DEFINER review.
        """
        ...


# --- Phase 3 new Protocols (not amendments to existing ones) ---

@runtime_checkable
class ModelProvider(Protocol):
    """Abstracts model API calls for a named slot.

    Phase 3 addition. Orchestration code must never import openai/anthropic/ollama directly.
    """

    async def call(self, slot_name: str, messages: list[dict], **kwargs) -> dict:
        """Call the model for the given slot.

        Returns a dict with at minimum: content, model, usage, latency_ms.
        """
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Abstracts text-to-vector embedding.

    Phase 3 addition. Used by retrieval and future L2 components.
    """

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string and return the vector."""
        ...


# --- Phase 7 additions (append only) ---


@runtime_checkable
class VigilStore(Protocol):
    """Protocol for Vigil actor storage needs (canonical health, entity consistency).

    Per Phase 7 : Vigil is read-only; it detects and reports, never modifies autonomously.
    Methods: get_canonical_health, list_stale_canonicals, record_vigil_check, get_last_vigil_check.
    """

    async def get_canonical_health(self, artifact_id: str) -> dict | None:
        """Get health metadata for a canonical artifact.

        Returns dict with: artifact_id, last_evaluated_at, model_slot_used,
        faithfulness_score, domain_coherence_score, status (VigilHealthStatus).
        Returns None if artifact not found.
        """
        ...

    async def list_stale_canonicals(self, threshold_days: int) -> list[dict]:
        """Return canonical artifacts that have not been re-evaluated within threshold_days."""
        ...

    async def record_vigil_check(
        self,
        canonical_count: int,
        stale_count: int,
        status: "VigilHealthStatus",
    ) -> None:
        """Record the result of a Vigil health check pass."""
        ...

    async def get_last_vigil_check(self) -> dict | None:
        """Return the most recent Vigil check result, or None if never run."""
        ...


@runtime_checkable
class AuthStore(Protocol):
    """Protocol for authentication/authorization storage.

    Phase 7 scope: single-DEFINER + API keys for non-interactive access.
    Per INTERFACES: session + API key lifecycle methods.
    """

    async def get_definer_identity(self) -> dict | None:
        """Return the single DEFINER identity (or None if not configured)."""
        ...

    async def create_session(self, identity: str, role: "AuthRole") -> str:
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

    async def create_api_key(self, identity: str, role: "AuthRole", key_name: str) -> str:
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

    async def create_user(self, identity: str, role: "CollaboratorRole", password_hash: str | None = None) -> bool:
        """Create a collaborator or readonly user.

        The 'definer' role cannot be created through this method —
        it is defined in the configuration file.
        Returns True if created, False if identity already exists.
        """
        ...

    async def update_user_role(self, identity: str, new_role: "CollaboratorRole") -> bool:
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


# CanonicalPipeline methods (amendments to be used by 9.2 orchestration component)
# These are added as loose method stubs for the pipeline to use existing stores via the container.
# In practice they will be called on the injected stores.


# --- Phase 8 new Protocols (not amendments — these are new classes) ---


@runtime_checkable
class KnowledgeStore(Protocol):
    """Abstraction for the Deferred Compiled Knowledge Layer.

    "Deferred Compiled Knowledge Layer" — persistence concern.
    Compiled knowledge must track provenance to source canonicals.
    Per Appendix D: compiled knowledge ≠ canonical artifact.
    Per Process Rule 12: CompilationState is distinct from ECS states.
    """

    async def store_compiled(
        self,
        knowledge_id: str,
        content: str,
        source_canonical_ids: list[str],
        domain: str,
        metadata: dict,
    ) -> None:
        """Store a compiled knowledge artifact with provenance.

        metadata includes: compilation_model_slot, evaluation_scores,
        compilation_timestamp, confidence.
        """
        ...

    async def get_compiled(self, knowledge_id: str) -> dict | None:
        """Get a compiled knowledge artifact by ID.

        Returns dict with: knowledge_id, content, source_canonical_ids,
        domain, state, metadata, created_at, updated_at.
        """
        ...

    async def list_compiled(
        self, domain: str | None = None, state: "CompilationState" | None = None
    ) -> list[dict]:
        """List compiled knowledge, optionally filtered."""
        ...

    async def update_state(self, knowledge_id: str, new_state: "CompilationState") -> None:
        """Transition the compilation state."""
        ...

    async def get_provenance(self, knowledge_id: str) -> list[dict]:
        """Return the list of source canonicals used to compile this knowledge."""
        ...

    async def search_compiled(
        self, query: str, domain: str | None = None, limit: int = 10
    ) -> list[dict]:
        """Search compiled knowledge by query and domain."""
        ...


@runtime_checkable
class PluginProvider(Protocol):
    """Abstraction for a plugin-provided model provider.

    Plugins extend model slots without hardcoding names.
    Per Phase 3 ModelProvider: this is the extensible variant.
    """

    async def call_model(self, prompt: str, config: dict) -> str:
        """Send prompt to the plugin's model and return the response text."""
        ...

    async def health_check(self) -> dict:
        """Verify the plugin's model is accessible. Returns status dict."""
        ...

    def get_slot_name(self) -> str:
        """Return the model slot name this plugin binds to."""
        ...

    def get_provider_name(self) -> str:
        """Return the concrete provider name (e.g. plugin YAML key)."""
        ...
