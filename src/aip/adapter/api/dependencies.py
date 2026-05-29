"""Dependency injection for the AIP FastAPI surfaces.

Per spec: single AipContainer as the source of truth. Routes never import concrete adapters.

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
        # VigilStore and artifact store not in protocols yet — typed as Any
        self.vigil_store: Any = None
        self.artifact_store: Any = None
        # Orchestration components — typed as Any to avoid adapter→orchestration import
        self.session_manager: Any = None
        self.budget_manager: Any = None
        self.adaptive_router: Any = None
        self.sexton: Any = None
        self.beast: Any = None
        self.ace_playbook: Any = None


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
