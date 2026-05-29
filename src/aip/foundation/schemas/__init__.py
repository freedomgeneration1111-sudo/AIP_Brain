"""Compatibility barrel file — re-exports all schema types.

This module preserves backward compatibility so that existing imports like::

    from aip.foundation.schemas import Chunk, EcsState, ReviewVerdict

continue to work unchanged.

The actual definitions live in domain-specific sub-modules:
    base, retrieval, review, evaluation, trajectory,
    workflow, vector, auth, budget, surface, config
"""
from __future__ import annotations

# -- base --
from .base import (
    ContractRule,
    ContractTier,
    EcsState,
    EcsTransition,
    Event,
    FailureType,
    FailureTypeCode,
    ModelSlotName,
    OutcomeType,
    ReleaseMetadata,
    VigilHealthStatus,
)

# -- retrieval --
from .retrieval import (
    Chunk,
    RetrievalResult,
)

# -- review --
from .review import (
    CanonicalPromotionConfig,
    ReviewContext,
    ReviewQueueEntry,
    ReviewVerdict,
    VigilConfig,
)

# -- evaluation --
from .evaluation import (
    AcePlaybookEntry,
    CompilationState,
    DomainCoherenceResult,
    EvaluationScore,
    FaithfulnessResult,
    FailureClassification,
    KnowledgeCompilationConfig,
    SextonConfig,
)

# -- trajectory --
from .trajectory import (
    SessionContext,
    TrajectorySignal,
    TrajectorySignalType,
)

# -- workflow --
from .workflow import (
    BeastCadenceConfig,
    DeploymentProfile,
    ModelSlotConfig,
    RoutingWeight,
    WorkflowTemplate,
)

# -- vector --
from .vector import (
    MigrationCheckpoint,
    MigrationStatus,
    PgvectorConfig,
    VectorBackendType,
)

# -- auth --
from .auth import (
    AuthConfig,
    AuthRole,
    AutonomyEscalation,
    AutonomyLevel,
    CollaboratorConfig,
    CollaboratorRole,
    McpAutonomyLevel,
    RateLimitConfig,
    coerce_autonomy_level,
    coerce_mcp_autonomy_level,
)

# -- budget --
from .budget import (
    BudgetConfig,
    BudgetScope,
)

# -- surface --
from .surface import (
    ApiRoute,
    ChatMessage,
    McpToolDef,
    SurfaceConfig,
)

# -- config --
from .config import (
    PerformanceConfig,
    PluginConfig,
    PluginStatus,
)

__all__ = [
    # base
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
    # retrieval
    "Chunk",
    "RetrievalResult",
    # review
    "ReviewVerdict",
    "ReviewContext",
    "ReviewQueueEntry",
    "VigilConfig",
    "CanonicalPromotionConfig",
    # evaluation
    "CompilationState",
    "EvaluationScore",
    "FaithfulnessResult",
    "DomainCoherenceResult",
    "SextonConfig",
    "AcePlaybookEntry",
    "FailureClassification",
    "KnowledgeCompilationConfig",
    # trajectory
    "TrajectorySignalType",
    "TrajectorySignal",
    "SessionContext",
    # workflow
    "ModelSlotConfig",
    "RoutingWeight",
    "BeastCadenceConfig",
    "WorkflowTemplate",
    "DeploymentProfile",
    # vector
    "VectorBackendType",
    "PgvectorConfig",
    "MigrationStatus",
    "MigrationCheckpoint",
    # auth
    "AutonomyLevel",
    "McpAutonomyLevel",
    "AuthRole",
    "CollaboratorRole",
    "coerce_autonomy_level",
    "coerce_mcp_autonomy_level",
    "AutonomyEscalation",
    "AuthConfig",
    "RateLimitConfig",
    "CollaboratorConfig",
    # budget
    "BudgetScope",
    "BudgetConfig",
    # surface
    "SurfaceConfig",
    "ApiRoute",
    "McpToolDef",
    "ChatMessage",
    # config
    "PluginStatus",
    "PluginConfig",
    "PerformanceConfig",
]
