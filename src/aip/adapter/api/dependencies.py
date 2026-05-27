"""Dependency injection for the AIP FastAPI surfaces (CHUNK-8.1).

Per spec: single AipContainer as the source of truth. Routes never import concrete adapters.
"""

from __future__ import annotations

from typing import Any

try:
    from fastapi import Depends, Request
except ImportError:
    Depends = None  # type: ignore
    Request = None  # type: ignore

from aip.foundation.protocols import (
    VectorStore,
    EcsStore,
    ArtifactStore,
    EventStore,
    TraceStore,
    BudgetStore,
    ProjectStore,
    EntityStore,
    LexicalStore,
    CanonicalStore,
    AutonomyGate,
    ModelProvider,
    EmbeddingProvider,
)
from aip.orchestration.session import SessionManager
from aip.orchestration.budget import BudgetManager
from aip.orchestration.router import AdaptiveRouter
from aip.orchestration.sexton.sexton import Sexton
from aip.orchestration.actors.beast import Beast
from aip.orchestration.ace_playbook import AcePlaybook


class AipContainer:
    """Central DI container for all Phase 6 surfaces.

    Populated in lifespan startup from config + the various adapter/foundation
    implementations delivered in 8.0a/8.0b + Phase 5 orchestration layer.
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
        self.session_manager: SessionManager | None = None
        self.budget_manager: BudgetManager | None = None
        self.adaptive_router: AdaptiveRouter | None = None
        self.sexton: Sexton | None = None
        self.beast: Beast | None = None
        self.ace_playbook: AcePlaybook | None = None


def get_container(request: "Request") -> AipContainer:
    """FastAPI dependency that returns the app's container (populated in lifespan)."""
    if Depends is None:
        raise RuntimeError("fastapi not available (Phase 6 surface dependency)")
    container = request.app.state.container
    if container is None:
        raise RuntimeError("AipContainer not initialized (lifespan not run?)")
    return container
