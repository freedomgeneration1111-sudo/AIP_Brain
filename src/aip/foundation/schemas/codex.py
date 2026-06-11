"""CODEX / Librarian schemas — structured internal map of the corpus.

The CODEX (Corpus Organization, Discovery, and EXploration) system gives AIP
an internal librarian capable of organizing project knowledge instead of merely
retrieving chunks. These schemas define the data structures for:

- Source registry: tracking every document that has been ingested
- Document classification: mapping documents to domains and topics
- Canonical topic map: the structured knowledge topology
- Staleness tracking: detecting outdated content
- Duplicate detection: identifying redundant or overlapping content
- Contradiction detection: flagging conflicting claims across sources
- "What do I know about X?" summaries: concise topic overviews
- Topic pages / wiki nodes: structured knowledge entries
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Source status lifecycle
CodexSourceStatus = Literal[
    "active",  # Currently valid and up-to-date
    "stale",  # Content may be outdated; needs review
    "superseded",  # Replaced by a newer version
    "quarantined",  # Flagged for issues; excluded from retrieval
]

# Contradiction severity
ContradictionSeverity = Literal[
    "critical",  # Direct factual conflict (e.g., "X is true" vs "X is false")
    "major",  # Substantive disagreement on a claim
    "minor",  # Minor inconsistency (wording, emphasis)
    "apparent",  # Looks contradictory but may be context-dependent
]

# Contradiction resolution status
ContradictionStatus = Literal[
    "open",  # Unresolved contradiction
    "investigating",  # DEFINER is reviewing
    "resolved_correct",  # One source confirmed correct; other updated
    "resolved_both",  # Both sources had partial truth; merged
    "resolved_outdated",  # One source was simply outdated
    "dismissed",  # Not actually contradictory after review
]


@dataclass
class CodexSource:
    """A registered source document in the CODEX.

    Every document ingested into AIP gets a CodexSource entry that tracks
    its identity, classification, status, and relationships. This is the
    foundation of the internal map — the librarian knows what it has.

    Fields:
        source_id: Unique identifier (typically the content_hash or a generated ID)
        title: Human-readable title (from file name, conversation name, or metadata)
        source_type: Origin kind (conversation, document, wiki, manual)
        source_path: File path or conversation identifier
        domain: Primary domain classification
        topics: List of topic tags associated with this source
        status: Current lifecycle status
        content_hash: SHA-256 hash for dedup and change detection
        word_count: Total word count of the source content
        turn_count: Number of corpus turns from this source
        first_ingested_at: When the source was first ingested
        last_updated_at: When the source content was last modified
        last_reviewed_at: When a DEFINER last reviewed this source
        superseded_by: If superseded, the source_id of the replacement
        metadata: Additional key-value metadata
    """

    source_id: str = ""
    title: str = ""
    source_type: str = "document"  # conversation | document | wiki | manual
    source_path: str = ""
    domain: str = ""
    topics: list[str] = field(default_factory=list)
    status: CodexSourceStatus | str = "active"
    content_hash: str = ""
    word_count: int = 0
    turn_count: int = 0
    first_ingested_at: str = ""
    last_updated_at: str = ""
    last_reviewed_at: str = ""
    superseded_by: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to dict for storage."""
        return {
            "source_id": self.source_id,
            "title": self.title,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "domain": self.domain,
            "topics": self.topics,
            "status": self.status,
            "content_hash": self.content_hash,
            "word_count": self.word_count,
            "turn_count": self.turn_count,
            "first_ingested_at": self.first_ingested_at,
            "last_updated_at": self.last_updated_at,
            "last_reviewed_at": self.last_reviewed_at,
            "superseded_by": self.superseded_by,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CodexSource:
        """Deserialize from dict."""
        return cls(
            source_id=data.get("source_id", ""),
            title=data.get("title", ""),
            source_type=data.get("source_type", "document"),
            source_path=data.get("source_path", ""),
            domain=data.get("domain", ""),
            topics=data.get("topics", []),
            status=data.get("status", "active"),
            content_hash=data.get("content_hash", ""),
            word_count=data.get("word_count", 0),
            turn_count=data.get("turn_count", 0),
            first_ingested_at=data.get("first_ingested_at", ""),
            last_updated_at=data.get("last_updated_at", ""),
            last_reviewed_at=data.get("last_reviewed_at", ""),
            superseded_by=data.get("superseded_by", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class CodexTopic:
    """A topic node in the CODEX knowledge map.

    Topics are the canonical units of the internal map. Each topic represents
    a distinct concept, area, or subject that the system knows about. Topics
    are linked to sources, can contain contradictions, and track staleness.

    The topic map is the librarian's primary organizational structure — it
    answers "what do I know about X?" by traversing the topic graph.
    """

    topic_id: str = ""  # snake_case identifier
    title: str = ""  # Human-readable title
    domain: str = ""  # Parent domain
    description: str = ""  # Brief summary of what this topic covers
    source_ids: list[str] = field(default_factory=list)  # Sources that discuss this topic
    related_topics: list[str] = field(default_factory=list)  # Connected topic_ids
    contradiction_count: int = 0  # Number of open contradictions
    staleness_score: float = 0.0  # 0.0 = fresh, 1.0 = very stale
    last_activity_at: str = ""  # Most recent source update
    is_wiki_page: bool = False  # Whether a wiki article exists for this topic
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to dict for storage."""
        return {
            "topic_id": self.topic_id,
            "title": self.title,
            "domain": self.domain,
            "description": self.description,
            "source_ids": self.source_ids,
            "related_topics": self.related_topics,
            "contradiction_count": self.contradiction_count,
            "staleness_score": self.staleness_score,
            "last_activity_at": self.last_activity_at,
            "is_wiki_page": self.is_wiki_page,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CodexTopic:
        """Deserialize from dict."""
        return cls(
            topic_id=data.get("topic_id", ""),
            title=data.get("title", ""),
            domain=data.get("domain", ""),
            description=data.get("description", ""),
            source_ids=data.get("source_ids", []),
            related_topics=data.get("related_topics", []),
            contradiction_count=data.get("contradiction_count", 0),
            staleness_score=data.get("staleness_score", 0.0),
            last_activity_at=data.get("last_activity_at", ""),
            is_wiki_page=data.get("is_wiki_page", False),
            metadata=data.get("metadata", {}),
        )


@dataclass
class CodexContradiction:
    """A detected contradiction between sources.

    The CODEX librarian flags contradictions when different sources make
    conflicting claims about the same topic. Each contradiction carries:
    - The specific claims from each source
    - Severity assessment
    - Resolution status
    - DEFINER notes

    Contradictions are never auto-resolved — the DEFINER must review and
    decide which source (if either) is correct.
    """

    contradiction_id: str = ""
    topic_id: str = ""
    claim_a: str = ""  # The claim from source A
    source_a_id: str = ""  # Source that makes claim A
    source_a_title: str = ""  # Title of source A (for display)
    claim_b: str = ""  # The conflicting claim from source B
    source_b_id: str = ""  # Source that makes claim B
    source_b_title: str = ""  # Title of source B (for display)
    severity: ContradictionSeverity | str = "major"
    status: ContradictionStatus | str = "open"
    context: str = ""  # Additional context about the contradiction
    resolution_notes: str = ""  # DEFINER's resolution notes
    resolved_by: str = ""  # Actor who resolved (typically "definer")
    resolved_at: str = ""  # When resolved
    detected_at: str = ""  # When first detected
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to dict for storage."""
        return {
            "contradiction_id": self.contradiction_id,
            "topic_id": self.topic_id,
            "claim_a": self.claim_a,
            "source_a_id": self.source_a_id,
            "source_a_title": self.source_a_title,
            "claim_b": self.claim_b,
            "source_b_id": self.source_b_id,
            "source_b_title": self.source_b_title,
            "severity": self.severity,
            "status": self.status,
            "context": self.context,
            "resolution_notes": self.resolution_notes,
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at,
            "detected_at": self.detected_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CodexContradiction:
        """Deserialize from dict."""
        return cls(
            contradiction_id=data.get("contradiction_id", ""),
            topic_id=data.get("topic_id", ""),
            claim_a=data.get("claim_a", ""),
            source_a_id=data.get("source_a_id", ""),
            source_a_title=data.get("source_a_title", ""),
            claim_b=data.get("claim_b", ""),
            source_b_id=data.get("source_b_id", ""),
            source_b_title=data.get("source_b_title", ""),
            severity=data.get("severity", "major"),
            status=data.get("status", "open"),
            context=data.get("context", ""),
            resolution_notes=data.get("resolution_notes", ""),
            resolved_by=data.get("resolved_by", ""),
            resolved_at=data.get("resolved_at", ""),
            detected_at=data.get("detected_at", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class CodexDashboard:
    """Dashboard summary for the CODEX system.

    Provides a snapshot of the CODEX state for the `aip codex dashboard`
    command. Includes topic graph stats, recently changed concepts,
    stale documents, contradictions, and unclassified documents.
    """

    total_sources: int = 0
    active_sources: int = 0
    stale_sources: int = 0
    superseded_sources: int = 0
    quarantined_sources: int = 0

    total_topics: int = 0
    topics_with_contradictions: int = 0
    topics_with_wiki: int = 0

    open_contradictions: int = 0
    critical_contradictions: int = 0
    major_contradictions: int = 0
    minor_contradictions: int = 0

    unclassified_sources: int = 0
    unclassified_turns: int = 0

    recently_changed: list[dict] = field(default_factory=list)  # Last 10 updated topics
    stale_documents: list[dict] = field(default_factory=list)  # Top stale sources
    open_contradiction_list: list[dict] = field(default_factory=list)  # Open contradictions
    topic_graph: dict = field(default_factory=dict)  # Domain -> topic_count

    @property
    def health_score(self) -> float:
        """Overall CODEX health score (0.0 - 1.0).

        Penalized by: stale sources, open contradictions, unclassified docs.
        """
        if self.total_sources == 0:
            return 1.0
        stale_penalty = self.stale_sources / max(self.total_sources, 1) * 0.3
        contradiction_penalty = min(self.open_contradictions / max(self.total_topics, 1), 1.0) * 0.4
        unclassified_penalty = self.unclassified_sources / max(self.total_sources, 1) * 0.3
        return max(0.0, 1.0 - stale_penalty - contradiction_penalty - unclassified_penalty)


@dataclass
class CodexConfig:
    """Configuration for the CODEX / Librarian system.

    Controls the maintenance cycle behavior, staleness thresholds,
    and contradiction detection settings.
    """

    # Staleness thresholds (days since last update)
    stale_threshold_days: int = 90  # Mark as stale after 90 days
    very_stale_threshold_days: int = 180  # Mark as very stale after 180 days

    # Maintenance cycle settings
    cycle_limit_sources: int = 50  # Max sources to process per cycle
    cycle_limit_contradictions: int = 20  # Max contradiction checks per cycle

    # Duplicate detection
    similarity_threshold: float = 0.85  # Content similarity above this = potential duplicate
    min_content_length_for_dedup: int = 50  # Don't dedup very short content

    # Topic map settings
    auto_create_topics: bool = True  # Auto-create topics from domain/tags
    topic_merge_threshold: float = 0.90  # Topic similarity above this = merge candidate

    # "What do I know about X?" settings
    summary_max_sources: int = 10  # Max sources to include in a summary
    summary_max_words: int = 500  # Target word count for summaries

    # Model slot for LLM-assisted classification/contradiction detection
    librarian_model_slot: str = "sexton"  # Use the sexton slot (free-tier)


__all__ = [
    "CodexSourceStatus",
    "ContradictionSeverity",
    "ContradictionStatus",
    "CodexSource",
    "CodexTopic",
    "CodexContradiction",
    "CodexDashboard",
    "CodexConfig",
]
