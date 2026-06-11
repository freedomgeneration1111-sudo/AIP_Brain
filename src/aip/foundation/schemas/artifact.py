"""Artifact lifecycle types.

Schemas for the artifact creation, metadata, and lifecycle management
layer that makes AIP useful for producing real work without bypassing
sovereignty gates.

Every artifact carries:
- Metadata (title, description, tags, project, artifact_type)
- Sources (provenance back to retrieved content)
- State (ECS lifecycle: GENERATED → REVIEWED → APPROVED → EXPORTED)
- Review history (verdicts, notes, revision instructions)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# Standard artifact types
ArtifactType = Literal[
    "ask_answer",
    "beast_wiki",
    "beast_domain_proposal",
    "compiled_knowledge",
    "procedural_guide",
    "manual_document",
    "custom",
]


@dataclass
class ArtifactMetadata:
    """Rich metadata for an artifact.

    Stored in VersionedArtifactStore as the metadata_json field.
    Every artifact has this metadata, whether created from ask,
    from Beast synthesis, or manually by the DEFINER.

    The metadata captures:
    - Identity: title, artifact_type, project
    - Provenance: originating prompt, session, model info
    - Classification: tags, domain
    - Sources: source_ids and source_types for traceability
    - Lifecycle: generated_at timestamp
    """

    artifact_type: ArtifactType | str = "ask_answer"
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    project_id: str = ""
    project_name: str = ""
    domain: str = ""
    prompt: str = ""
    session_id: str = ""
    model_slot: str = ""
    model_name: str = ""
    model_provider: str = ""
    source_ids: list[str] = field(default_factory=list)
    source_types: list[str] = field(default_factory=list)
    generated_at: str = ""
    custom_fields: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to dict for storage in VersionedArtifactStore."""
        d = {
            "artifact_type": self.artifact_type,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "domain": self.domain,
            "prompt": self.prompt,
            "session_id": self.session_id,
            "model_slot": self.model_slot,
            "model_name": self.model_name,
            "model_provider": self.model_provider,
            "source_ids": self.source_ids,
            "source_types": self.source_types,
            "generated_at": self.generated_at,
        }
        if self.custom_fields:
            d["custom_fields"] = self.custom_fields
        return d

    @classmethod
    def from_dict(cls, data: dict) -> ArtifactMetadata:
        """Deserialize from dict (e.g., from VersionedArtifactStore metadata)."""
        return cls(
            artifact_type=data.get("artifact_type", "ask_answer"),
            title=data.get("title", ""),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            project_id=data.get("project_id", ""),
            project_name=data.get("project_name", ""),
            domain=data.get("domain", ""),
            prompt=data.get("prompt", ""),
            session_id=data.get("session_id", ""),
            model_slot=data.get("model_slot", ""),
            model_name=data.get("model_name", ""),
            model_provider=data.get("model_provider", ""),
            source_ids=data.get("source_ids", []),
            source_types=data.get("source_types", []),
            generated_at=data.get("generated_at", ""),
            custom_fields=data.get("custom_fields", {}),
        )


@dataclass
class ArtifactLedgerEntry:
    """A single entry in the artifact ledger.

    Represents one event in the artifact's lifecycle:
    - ECS state transition (GENERATED → REVIEWED → APPROVED)
    - Review verdict (APPROVED, REJECTED, NEEDS_REVISION)
    - Reviewer note (added without state change)
    - Export event (normal or force-export)
    - Any other event in the EventStore
    """

    event_type: str  # "ecs_transition", "review_verdict", "reviewer_note", "artifact_exported", "force_export"
    actor: str  # "ask_pipeline", "definer", "beast", "sexton", "export_pipeline"
    artifact_id: str
    from_state: str | None = None
    to_state: str | None = None
    timestamp: str = ""
    detail: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class ReviewQueueSummary:
    """Summary of the review queue for the dashboard.

    Provides a snapshot of how many artifacts are in each
    lifecycle state, plus recent activity and force-export
    exceptions that need attention.
    """

    generated_count: int = 0
    reviewed_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    superseded_count: int = 0
    failed_count: int = 0

    # Artifacts with NEEDS_REVISION verdict (still in GENERATED state)
    needs_revision_count: int = 0

    # Recent force-exports (sovereign override exceptions)
    force_export_count: int = 0
    force_export_events: list[dict] = field(default_factory=list)

    # Recent activity (last N events)
    recent_events: list[dict] = field(default_factory=list)

    @property
    def total_active(self) -> int:
        """Total artifacts in active states (not terminal)."""
        return self.generated_count + self.reviewed_count + self.approved_count

    @property
    def total_pending_review(self) -> int:
        """Artifacts pending DEFINER review."""
        return self.generated_count + self.needs_revision_count


__all__ = [
    "ArtifactType",
    "ArtifactMetadata",
    "ArtifactLedgerEntry",
    "ReviewQueueSummary",
]
