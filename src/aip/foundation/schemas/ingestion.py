"""Ingestion-related types.

Schemas for conversation import: source formats, parsed turns,
imported conversations, and ingestion results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SourceFormat = Literal["chatgpt_json", "markdown", "plaintext"]


@dataclass
class ConversationTurn:
    """A single turn in a parsed conversation.

    Captures role (who spoke), content (what was said), and an
    optional timestamp for temporal ordering and recency scoring.
    """

    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    timestamp: str = ""  # ISO 8601 or empty


@dataclass
class ImportedConversation:
    """A fully parsed conversation ready for ingestion.

    Provenance metadata tracks where this conversation came from,
    enabling audit trails and re-import detection.
    """

    conversation_id: str
    title: str
    turns: list[ConversationTurn]
    source_format: SourceFormat
    source_file: str
    imported_at: str = ""  # ISO 8601 — set by pipeline
    metadata: dict = field(default_factory=dict)


@dataclass
class IngestionResult:
    """Outcome of ingesting a single conversation.

    Reports what was stored, how many chunks were created, and
    whether vector indexing succeeded.
    """

    conversation_id: str
    artifact_id: str
    turn_count: int
    chunk_count: int
    vector_indexed: bool
    lexical_indexed: bool
    source_format: SourceFormat
    source_file: str
    errors: list[str] = field(default_factory=list)


__all__ = [
    "SourceFormat",
    "ConversationTurn",
    "ImportedConversation",
    "IngestionResult",
]
