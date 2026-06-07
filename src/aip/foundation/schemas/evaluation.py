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

    # Graph extraction batching — enabled by default (Sprint 5.21 graduation).
    # Batch mode reduces LLM API calls by N/batch_size, saving cost.
    # With batch_size=2, each LLM call processes 2 turns, halving API calls.
    # Falls back to per-turn processing if batch response parsing fails.
    # See test_sexton_graph_batch.py for E2E coverage.
    graph_extraction_batch_enabled: bool = True
    graph_extraction_batch_size: int = 2  # Conservative default; increase to 4–6 for larger corpora

    # Sprint 5.23: Batch size auto-tuning (graduated to default-on Sprint 5.24)
    # When enabled, the graph extraction batch_size adjusts automatically
    # based on parse failure rate.  If failures are high, batch_size
    # decreases (more conservative).  If consistently successful,
    # batch_size can increase within safe bounds.
    graph_extraction_batch_auto_tune_enabled: bool = True  # Default-on since Sprint 5.24
    graph_extraction_batch_size_min: int = 1    # Never go below 1
    graph_extraction_batch_size_max: int = 8    # Conservative upper bound (validated Sprint 5.23)
    graph_extraction_auto_tune_window: int = 5   # Number of batches to consider
    graph_extraction_auto_tune_decrease_threshold: float = 0.3  # Failure rate above this → decrease
    graph_extraction_auto_tune_increase_threshold: float = 0.1  # Failure rate below this → increase


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
