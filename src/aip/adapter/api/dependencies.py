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
        # VigilStore not in protocols yet — typed as Any
        self.vigil_store: Any = None
        # artifact_store already declared above as ArtifactStore | None
        # Orchestration components — typed as Any to avoid adapter→orchestration import
        self.session_manager: Any = None
        self.budget_manager: Any = None
        self.adaptive_router: Any = None
        self.sexton: Any = None
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
        # Backfill status for async backfill tracking (simple in-memory for now)
        self.backfill_status: dict = {"running": False, "last_result": None, "progress": {}}

    def set_embedding_provider(self, provider: "EmbeddingProvider | None") -> None:
        """Safely replace the embedding provider.

        Updates the container reference and pokes private attributes on
        dependent components (vector_store, beast, knowledge_store) so that
        runtime changes (e.g. from PATCH /models/slots/embedding/model) take
        effect without requiring a full restart.

        This centralizes the previously duplicated fragile poking logic.
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
