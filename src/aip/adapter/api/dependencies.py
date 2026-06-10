"""Dependency injection for the AIP FastAPI surfaces.

Single AipContainer as the source of truth. Routes never import concrete adapters.

Removed direct orchestration imports from adapter layer.
Orchestration components (SessionManager, BudgetManager, etc.) are typed
as Any and injected at runtime via lifespan wiring. This preserves the
three-layer discipline: adapter may only import foundation.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from aip.foundation.protocols import (
    ArtifactStore,
    AutonomyGate,
    BudgetStore,
    CanonicalStore,
    EcsStore,
    EmbeddingProvider,
    EntityStore,
    EventStore,
    GraphStore,
    KnowledgeStore,
    LexicalStore,
    ModelProvider,
    ProjectStore,
    TraceStore,
    VectorStore,
)


class AipContainer:
    """Central DI container for all API surfaces.

    Populated in lifespan startup from config + adapter and orchestration
    implementations.

    Orchestration components are typed as Any (injected at runtime) to avoid
    direct adapter→orchestration import dependency.
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        # Will be populated by lifespan / factory
        self.vector_store: VectorStore | None = None
        self.ecs_store: EcsStore | None = None
        self.artifact_store: ArtifactStore | None = None
        self.event_store: EventStore | None = None
        self.trace_store: TraceStore | None = None
        self.budget_store: BudgetStore | None = None
        self.project_store: ProjectStore | None = None
        self.entity_store: EntityStore | None = None
        self.lexical_store: LexicalStore | None = None
        self.canonical_store: CanonicalStore | None = None
        self.autonomy_gate: AutonomyGate | None = None
        self.model_provider: ModelProvider | None = None
        self.embedding_provider: EmbeddingProvider | None = None
        self.knowledge_store: KnowledgeStore | None = None
        # DefinerProfile — optional profile for augmented chat injection (degrades to no injection)
        self.definer_profile: Any = None
        # VigilStore not in protocols yet — typed as Any
        self.vigil_store: Any = None
        # artifact_store already declared above as ArtifactStore | None
        # Orchestration components — typed as Any to avoid adapter→orchestration import
        self.session_manager: Any = None
        self.budget_manager: Any = None
        self.adaptive_router: Any = None
        self.sexton_actor: Any = None  # ADR-011 full maintenance worker (actors.sexton.Sexton)
        self.beast: Any = None
        self.vigil: Any = None
        self.ace_playbook: Any = None
        # PerformanceProfiler — None when not configured (API returns BACKEND_UNAVAILABLE)
        self.performance_profiler: Any = None
        # CollaboratorManager — None when auth not fully wired
        self.collaborator_manager: Any = None
        # ReviewQueueStore — None when not initialized
        self.review_queue_store: Any = None
        # SessionStore — None when not initialized (degrades to in-memory)
        self.session_store: Any = None
        # CorpusTurnStore — None when not initialized (degrades to no corpus search in augmented chat)
        self.corpus_turn_store: Any = None
        # GraphStore — knowledge graph nodes and edges (degrades to no graph retrieval)
        self.graph_store: GraphStore | None = None
        # Sprint 5.27: Operational components wired into the running application
        self._vigil_quality_store: Any = None  # VigilQualityStore for persistent quality history
        self._alert_manager: Any = None  # AlertManager for operator notifications
        self._config_watcher: Any = None  # ConfigWatcher for hot-reload
        self._read_pool_auto_sizer: Any = None  # ReadPoolAutoSizer for auto pool sizing
        self._auto_tuning_policy: Any = None  # AutoTuningPolicy for configurable thresholds
        # Sprint 5.29: Persistent alert history store
        self._alert_history_store: Any = None  # AlertHistoryStore for SQLite-backed alert history
        # SyncAlertHistoryBridge for AlertManager compatibility (wraps async store)
        self._alert_history_bridge: Any = None
        # Backfill status for async backfill tracking (simple in-memory for now)
        self.backfill_status: dict = {"running": False, "last_result": None, "progress": {}}
        # Startup background tasks — stored on container so shutdown can cancel them
        self._sexton_startup_task: Any = None
        self._vigil_startup_task: Any = None
        # Orchestration function references — populated in lifespan.
        # Routes access these through the container instead of importing
        # orchestration directly, preserving layer discipline (adapter → foundation only).
        self._ask_fn: Any = None  # ask_pipeline.ask
        self._ask_stores_class: Any = None  # ask_pipeline.AskStores
        self._search_sources_fn: Any = None  # ask_pipeline._search_sources_with_trace
        self._sanitize_fts_query_fn: Any = None  # ask_pipeline._sanitize_fts_query
        self._ingest_conversation_fn: Any = None  # ingestion.pipeline.ingest_conversation
        self._ingest_file_fn: Any = None  # ingestion.pipeline.ingest_file
        # Store registry — maps store_name → db_path for datastore truth.
        # Populated during lifespan startup as each store is initialized.
        # Used by startup validation, backup, and the /health/datastore endpoint.
        self._store_registry: dict[str, str] = {}

    def register_store(self, name: str, db_path: str) -> None:
        """Register a store's database path in the datastore registry.

        Called during lifespan startup for each initialized store so that
        ``datastore_summary()`` can report exactly where every store lives.
        """
        self._store_registry[name] = db_path

    def datastore_summary(self) -> dict[str, Any]:
        """Return a summary of all registered stores and their locations.

        This is the honest datastore truth: which files exist, which are
        shared, and what the backup story is for each.

        Product decision: AIP_Brain uses Option B — honest multi-file
        local datastore. This was chosen because:

        1. The data has fundamentally different access patterns (state.db
           is transactional, lexical.db is FTS5 read-heavy, vectors.db
           may use VSS virtual tables, quality/alert DBs are append-mostly).
        2. SQLite performance degrades with many concurrent connections to
           a single file; separate files allow independent WAL mode and
           connection pooling.
        3. Backup granularity: each .db can be backed up independently
           via VACUUM INTO without locking the others.
        4. Disaster recovery: a corrupt FTS index doesn't take down the
           entity store.

        The 7 DB files are:
          - state.db:     Core entity/canonical/event/artifact/budget/project/
                         ECS/review/graph/corpus/session/autonomy data
          - lexical.db:   FTS5 full-text search index
          - vectors.db:   Vector embeddings (VSS or brute-force)
          - vigil_quality.db: Vigil quality cycle history
          - alert_history.db: Alert/delivery/experiment/mute rule persistence
          - trace.db:     Trace events and routing outcomes
          - ace_playbook.db: ACE procedural intervention rules
        """
        from pathlib import Path

        stores: dict[str, Any] = {}
        shared_dbs: dict[str, list[str]] = {}

        for name, db_path in sorted(self._store_registry.items()):
            p = Path(db_path)
            exists = p.exists()
            size_mb = round(p.stat().st_size / (1024 * 1024), 2) if exists else 0
            stores[name] = {
                "db_path": db_path,
                "exists": exists,
                "size_mb": size_mb,
            }
            # Track which stores share a DB file
            shared_dbs.setdefault(db_path, []).append(name)

        # Identify shared databases
        shared_info = {
            db_path: names
            for db_path, names in shared_dbs.items()
            if len(names) > 1
        }

        return {
            "architecture": "multi-file local datastore (Option B)",
            "stores": stores,
            "shared_databases": shared_info,
            "backup_story": (
                "Each .db file can be backed up via 'aip backup' (uses VACUUM INTO "
                "for consistent snapshots) or file-level tar (deploy/backup.sh). "
                "WAL mode ensures read consistency during backup."
            ),
            "total_stores": len(stores),
            "total_db_files": len(set(self._store_registry.values())),
        }

    def set_embedding_provider(self, provider: "EmbeddingProvider | None") -> None:
        """Safely replace the embedding provider.

        Updates the container reference and pokes private attributes on
        dependent components (vector_store, beast, knowledge_store, sexton_actor)
        so that runtime changes (e.g. from PATCH /models/slots/embedding/model)
        take effect without requiring a full restart.

        Sprint 6.1: Also triggers re-embedding of all corpus turns whose
        embedding_model differs from the new provider's model, so that
        the Sexton actor will re-embed them on its next cycle.
        """
        old_provider = self.embedding_provider
        if old_provider is not None and hasattr(old_provider, "close"):
            try:
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(old_provider.close())
                except RuntimeError:
                    pass
            except Exception:
                pass

        self.embedding_provider = provider

        # Update dependents (fragile private attrs, but now in one place)
        if self.vector_store is not None and hasattr(self.vector_store, "_embedding_provider"):
            self.vector_store._embedding_provider = provider
        if self.beast is not None and hasattr(self.beast, "_embed"):
            self.beast._embed = provider
        if self.knowledge_store is not None and hasattr(self.knowledge_store, "_embedding_provider"):
            self.knowledge_store._embedding_provider = provider

        # Sprint 6.1: Update Sexton's embedding provider reference
        if self.sexton_actor is not None and hasattr(self.sexton_actor, "update_embedding_provider"):
            self.sexton_actor.update_embedding_provider(provider)
        elif self.sexton_actor is not None and hasattr(self.sexton_actor, "_embed"):
            # Fallback for actors that don't have the update method yet
            self.sexton_actor._embed = provider

        # Sprint 6.1: Trigger re-embedding when the embedding model changes
        if provider is not None and self.corpus_turn_store is not None:
            try:
                # Determine new model name
                new_model = ""
                for attr in ("model", "_model", "model_name", "_model_name"):
                    val = getattr(provider, attr, None)
                    if val and isinstance(val, str):
                        new_model = val
                        break
                if not new_model:
                    new_model = provider.__class__.__name__

                # Mark turns with different model for re-embedding
                if hasattr(self.corpus_turn_store, "mark_all_for_reembed"):
                    import asyncio
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self._trigger_reembed(new_model))
                    except RuntimeError:
                        # No running loop — create one
                        try:
                            asyncio.run(self._trigger_reembed(new_model))
                        except Exception:
                            pass
            except Exception:
                pass  # Non-critical: re-embedding will happen on next Sexton cycle

    async def _trigger_reembed(self, new_model: str) -> None:
        """Mark corpus turns for re-embedding and log the trigger."""
        try:
            count = await self.corpus_turn_store.mark_all_for_reembed(except_model=new_model)
            import logging
            logging.getLogger(__name__).info(
                "reembed_triggered",
                new_model=new_model,
                turns_marked=count,
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("reembed_trigger_failed", error=str(exc))


def get_container(request: "Request") -> AipContainer:
    """FastAPI dependency that returns the app's container (populated in lifespan)."""
    container = getattr(request.app.state, "container", None)
    if container is None:
        # In test mode without lifespan, create a fresh container from any available config
        config = getattr(request.app.state, "raw_config", {}) or {}
        container = AipContainer(config)
        request.app.state.container = container
    return container


# Re-export auth dependencies so route modules can import from this single location
from aip.adapter.auth.dependencies import (  # noqa: E402
    get_current_identity,  # noqa: F401
    require_collaborator_or_above,  # noqa: F401
    require_definer,  # noqa: F401
)
