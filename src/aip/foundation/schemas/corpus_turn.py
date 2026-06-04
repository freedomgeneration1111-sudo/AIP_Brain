"""CorpusTurn schema — the atomic unit of the AIP knowledge corpus.

A CorpusTurn represents one complete user-assistant exchange (a "turn")
in a conversation. This is the foundational object for all retrieval,
Beast tagging, vector indexing, and knowledge graph work.

Unlike fixed-size text chunks (which split thoughts mid-sentence),
a turn preserves semantic coherence: the full user question + full
assistant response as a single atomic record.

All Beast metadata (domains, tags, importance, bridges) is stored
directly on the turn for efficient querying and re-evaluation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class CorpusTurn:
    """A single complete turn in a conversation — the atomic corpus unit.

    Identity fields (all required, no defaults):
      turn_id, conversation_id, conversation_name, turn_index

    Source provenance (all required):
      source_model, source_account, export_date

    Content (both sides preserved, required):
      user_text, assistant_text, turn_timestamp (empty str if unknown)

    Beast-assigned metadata (optional, default empty/unscored/untagged):
      domains, primary_domain, tags, importance, bridges,
      beast_confidence, tagging_version

    Computed (populated at construction if not provided):
      searchable_text = user_text + "\n\n" + assistant_text
      word_count = len(searchable_text.split())

    Layer: foundation only. No adapter/orchestration imports.
    """

    # Identity — all required, no defaults
    turn_id: str  # deterministic: sha256(conv_id + str(turn_index))[:16]
    conversation_id: str  # from source export UUID
    conversation_name: str  # human-readable conversation title
    turn_index: int  # 0-based position within conversation

    # Source provenance — required
    source_model: str  # "claude" | "gpt" | "deepseek" | "glm" | "gemini" | "grok"
    source_account: str  # identifier for which export file (e.g. "claude_export_2026")
    export_date: str  # ISO date string of when export was made

    # Content — both sides preserved, required
    user_text: str  # the human's message (may be long)
    assistant_text: str  # the AI's response (may be very long)
    turn_timestamp: str  # ISO timestamp of when turn occurred, empty string if unknown
    thinking_text: str = ""
    # Claude extended thinking blocks, preserved separately from assistant_text.
    # Populated by claude_parser when thinking content blocks are present.
    # Beast can retrieve and surface this as distinct reasoning context.
    # Empty string when no thinking blocks present or source model does not
    # support extended thinking.

    # Beast-assigned metadata — all optional, populated after ingestion
    domains: list[str] = field(default_factory=list)
    # Multi-domain allowed: ["nbcm", "theology"]
    # Valid domains: aip_loom, nbcm, theology, freedom_gen, freelance, misc
    primary_domain: str = ""
    # Beast's single best assignment, empty until Beast tags
    tags: list[str] = field(default_factory=list)
    # Freeform topic tags, Beast-assigned
    importance: float = 0.0
    # 0.0-1.0, Beast-scored. 0.0 = unscored, not "unimportant"
    bridges: list[str] = field(default_factory=list)
    # Cross-domain connection markers: ["nbcm→theology", "aip→freelance"]
    # Only assigned when Beast detects genuine domain bridging in the turn
    beast_confidence: float = 0.0
    # Beast's confidence in its tagging, 0.0-1.0
    tagging_version: int = 0
    # Increments each time Beast re-evaluates this turn
    # 0 = never tagged, 1 = first tagging, 2+ = re-evaluated

    embedded: int = 0
    # 0 = not embedded, 1 = has vector in vector store (for embedding pipeline)

    # Computed fields — populated at ingestion time
    searchable_text: str = ""
    # user_text + "\n\n" + assistant_text + (thinking if present)
    # This is what FTS5 and vector store index
    word_count: int = 0
    # len(searchable_text.split()) — for importance scoring

    def __post_init__(self):
        if not self.searchable_text:
            parts = [self.user_text, self.assistant_text]
            if self.thinking_text:
                parts.append(self.thinking_text)
            self.searchable_text = "\n\n".join(
                p for p in parts if p.strip()
            ).strip()
        if not self.word_count:
            self.word_count = len(self.searchable_text.split())


def make_turn_id(conversation_id: str, turn_index: int) -> str:
    """Generate deterministic 16-char hex turn_id from conv_id + index.

    Uses SHA256 for collision resistance while keeping ids short.
    """
    key = f"{conversation_id}:{turn_index}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
