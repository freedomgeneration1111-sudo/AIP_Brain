"""Workflow, model routing, cadence, and deployment types.

Model slot configuration, domain routing weights, Beast cadence
configuration, workflow template definitions, and deployment profiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .vector import VectorBackendType


@dataclass
class ModelSlotConfig:
    """Resolved configuration for a named model slot."""

    slot_name: str
    provider: str
    model: str
    base_url: str | None = None
    fallback_provider: str | None = None
    fallback_model: str | None = None
    dimensions: int | None = None


@dataclass
class RoutingWeight:
    """A single domain x model routing weight.

    Default routing uses highest-weight model for domain.
    exploration_weight controls probability of non-optimal routing.
    Sexton recommends exploration_weight adjustments per domain.
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

    Beast — cadence / corpus / entity maintenance.
    state.db stores cadence_state.
    """

    corpus_reindex_interval_seconds: int = 3600
    entity_maintenance_interval_seconds: int = 1800
    health_check_interval_seconds: int = 60
    max_reindex_batch_size: int = 1000


@dataclass
class WorkflowTemplate:
    """Extended workflow template definition (beyond Workflow 0.1).

    Per INTERFACES: template_id, name, description, yaml_path, trigger, domains,
    model_gen_assumption.
    """

    template_id: str
    name: str = ""
    description: str = ""
    yaml_path: str = ""  # relative to workflows/
    trigger: str = "manual"  # manual | on_artifact_approved | on_schedule
    domains: list[str] = field(default_factory=list)
    model_gen_assumption: str | None = None


@dataclass
class DeploymentProfile:
    """Deployment profile (laptop-viable vs production).

    Per INTERFACES: profile_name, vector_backend (VectorBackendType),
    model_provider, auth_enabled, workers, memory_limit_mb.
    """

    profile_name: str  # "laptop" | "production"
    vector_backend: VectorBackendType = "sqlite_vss"
    model_provider: str = "ollama"
    auth_enabled: bool = False
    workers: int = 1
    memory_limit_mb: int = 4096


__all__ = [
    "ModelSlotConfig",
    "RoutingWeight",
    "BeastCadenceConfig",
    "WorkflowTemplate",
    "DeploymentProfile",
]
