"""Evaluation, failure classification, and compilation types.

Evaluation scores (faithfulness, domain coherence), Sexton configuration
and classification output, ACE playbook entries, and knowledge compilation
configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Type alias for compiled knowledge lifecycle
CompilationState = Literal["SPECIFIED", "COMPILED", "REVIEWED", "APPROVED", "FAILED"]


@dataclass
class EvaluationScore:
    """A single evaluation dimension score.

    model_gen_assumption tags what model limitation this evaluation
    compensates for. Sexton audits these when model slots change.
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

    Faithfulness evaluation checks synthesis output against retrieved
    context. Hallucination flags identify claims not grounded in the
    retrieved context package.

    When ``ci_fixture`` is True, the scores come from CI fixture defaults,
    not from a real model evaluation. Callers MUST check this flag to
    avoid treating fixture scores as genuine quality measurements.
    """

    artifact_id: str
    faithfulness_score: float = 0.0
    context_coverage: float = 0.0
    hallucination_flags: list[str] = field(default_factory=list)
    evaluation_scores: list[EvaluationScore] = field(default_factory=list)
    ci_fixture: bool = True  # True when scores are CI fixtures, not real evaluation


@dataclass
class DomainCoherenceResult:
    """Domain coherence evaluation output (L3a Stage 3).

    Domain coherence evaluation checks domain-specific quality.
    Violations list domain-specific coherence issues found.

    When ``ci_fixture`` is True, the scores come from CI fixture defaults,
    not from a real model evaluation. Callers MUST check this flag to
    avoid treating fixture scores as genuine quality measurements.
    """

    artifact_id: str
    coherence_score: float = 0.0
    domain: str = ""
    violations: list[str] = field(default_factory=list)
    evaluation_scores: list[EvaluationScore] = field(default_factory=list)
    ci_fixture: bool = True  # True when scores are CI fixtures, not real evaluation


@dataclass
class SextonConfig:
    """Configuration for the Sexton failure classification actor.

    Sexton reads trace_events and classifies failures A-F.
    Sexton audits stale model assumptions on slot changes.
    """

    classification_batch_size: int = 50
    classification_interval_seconds: int = 300
    audit_on_slot_change: bool = True
    max_unclassified_before_alert: int = 10

    # Graph extraction batching (off by default — conservative)
    graph_extraction_batch_enabled: bool = False
    graph_extraction_batch_size: int = 1  # 1 = per-turn (current behavior)


@dataclass
class AcePlaybookEntry:
    """A single procedural intervention rule in the ACE Playbook.

    Procedural intervention rules, loaded at session start.
    Curated by Sexton.
    Every rule must carry model_gen_assumption.
    Type B remedy: add or strengthen playbook entry.
    """

    entry_id: str
    domain: str
    failure_type: str  # A-F failure type codes
    intervention: str
    condition: str  # Jinja2 expression
    model_gen_assumption: str | None = None
    source_trace_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    created_at: str = ""
    deprecated_at: str | None = None
    deprecated_reason: str | None = None


@dataclass
class FailureClassification:
    """Sexton's classification output for a single trace event.

    Sexton assigns appropriate Type A-F label.
    Writes back to trace_events.failure_type.
    Every classification carries model_gen_assumption.
    """

    trace_event_id: int
    failure_type: str  # A-F failure type codes
    confidence: float = 0.0
    rationale: str = ""
    model_slot_used: str = "sexton"
    tokens_consumed: int = 0
    model_gen_assumption: str | None = None
    classified_at: str = ""  # REQUIRED — ISO 8601


@dataclass
class KnowledgeCompilationConfig:
    """Configuration for the knowledge compilation system.

    Deferred Compiled Knowledge Layer — finally implemented.
    model_gen_assumption tags what the compilation criteria assume.
    Compiled knowledge and canonical artifacts are distinct (no collapse).
    """

    compilation_model_slot: str = "synthesis"
    evaluation_model_slot: str = "evaluation"
    max_source_canonicals: int = 10
    compilation_confidence_threshold: float = 0.60
    auto_index_on_approval: bool = True
    model_gen_assumption: str | None = None


__all__ = [
    "CompilationState",
    "EvaluationScore",
    "FaithfulnessResult",
    "DomainCoherenceResult",
    "SextonConfig",
    "AcePlaybookEntry",
    "FailureClassification",
    "KnowledgeCompilationConfig",
]
