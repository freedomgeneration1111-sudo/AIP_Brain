"""Smart Context Packer — budget-aware, structured context assembly.

Replaces the naive _assemble_context() in ask_pipeline.py with a
budget-aware packer that:

1. Separates hits by channel (evidence, wiki, procedural, graph/debug)
2. Respects RetrievalBudget allocations (token limits per section)
3. Truncates long hits intelligently, preserving section structure
4. Adds clear section headers and provenance markers
5. Ensures the final prompt stays within token limits
6. Provides a structured context object with section metadata

Phase 5.5 deliverable: Smart Context Packer.
Phase 5.6 enhancement: Extractive summarization for long evidence hits.

Layer: orchestration. Imports from foundation (schemas).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from aip.foundation.schemas.retrieval_trace import (
    RetrievalBudget,
    RetrievalChannel,
    RetrievalHit,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context Packet — structured output of the packer
# ---------------------------------------------------------------------------


@dataclass
class ContextSection:
    """A single section in the assembled context packet."""

    label: str               # e.g. "EVIDENCE", "WIKI BACKGROUND"
    header: str              # Section header line for the model
    entries: list[str]       # Formatted entry strings
    char_count: int = 0      # Total characters in this section
    est_tokens: int = 0      # Estimated tokens (~4 chars/token)
    truncated_count: int = 0  # How many entries were truncated
    compressed_count: int = 0  # How many entries were extractively summarized


@dataclass
class ContextPacket:
    """Structured context packet produced by the Smart Context Packer.

    Contains clearly separated sections with provenance markers,
    token budgets respected, and truncation tracking.
    """

    sections: list[ContextSection] = field(default_factory=list)
    total_chars: int = 0
    total_est_tokens: int = 0
    budget: RetrievalBudget | None = None
    hit_count: int = 0
    truncated_hits: int = 0
    compressed_hits: int = 0  # Phase 5.6: extractively summarized hits
    elapsed_ms: float = 0.0

    def to_prompt_string(self) -> str:
        """Render the packet as a single string for model input.

        Each section gets a clear header and provenance markers,
        making it easy for the model to distinguish evidence from
        wiki background from procedural instructions.
        """
        if not self.sections:
            return "No relevant sources found in project memory."

        parts: list[str] = []
        for section in self.sections:
            if not section.entries:
                continue
            parts.append(section.header)
            parts.append("")  # blank line after header
            for entry in section.entries:
                parts.append(entry)
            parts.append("")  # blank line between sections

        return "\n".join(parts).rstrip()


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    """Rough token estimation: ~4 chars per token for English text.

    This is a conservative estimate — actual tokenization varies
    by model, but 4 chars/token is a reasonable planning number.
    """
    return len(text) // 4


def _truncate_text(text: str, max_tokens: int, suffix: str = "...") -> str:
    """Truncate text to fit within a token budget.

    Preserves the start of the text (where the most important
    information typically is) and adds a truncation indicator.
    Also attempts to break at sentence or paragraph boundaries
    for readability.
    """
    if not text:
        return text

    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]

    # Try to break at the last sentence boundary (., !, ?)
    # Look within the last 20% of the allowed space for a clean break
    search_start = int(max_chars * 0.8)
    for i in range(len(truncated) - 1, search_start, -1):
        if truncated[i] in '.!?':
            truncated = truncated[:i + 1]
            break

    return truncated + suffix


# ---------------------------------------------------------------------------
# Extractive Summarization (Phase 5.6)
# ---------------------------------------------------------------------------


# Sentence-splitting pattern: split on sentence-ending punctuation
# followed by whitespace and an uppercase letter or end of text.
_SENTENCE_PATTERN = re.compile(
    r'(?<=[.!?])\s+(?=[A-Z\u00C0-\u024F\u0400-\u04FF])'
)


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using simple heuristics.

    Uses sentence-ending punctuation (., !, ?) followed by whitespace
    and an uppercase letter as the primary boundary. Falls back to
    splitting on sentence-ending punctuation alone for texts without
    clear capitalization.

    This is intentionally lightweight — no NLP dependencies needed.
    It doesn't need to be perfect; it just needs to produce reasonable
    sentence-level chunks for relevance scoring.
    """
    if not text or not text.strip():
        return []

    # Primary split: sentence-ending punctuation + whitespace + uppercase
    sentences = _SENTENCE_PATTERN.split(text.strip())

    # If we got very few splits (maybe the text lacks clear sentence
    # boundaries), try a secondary split on newlines
    if len(sentences) <= 1 and '\n' in text:
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        if len(lines) > 1:
            sentences = lines

    # Filter out empty/whitespace-only sentences
    result = [s.strip() for s in sentences if s.strip()]

    return result


def _score_sentence(
    sentence: str,
    query_terms: set[str],
    entity_terms: set[str],
) -> float:
    """Score a sentence by its relevance to the query and entities.

    Scoring criteria:
    - Query term overlap: how many query terms appear in the sentence
    - Entity term overlap: how many entity names appear in the sentence
    - Position bonus: first sentences get a small bonus (lead bias)
    - Length penalty: very short sentences (< 10 chars) are downweighted

    The scoring is intentionally simple and fast — no model calls,
    no embeddings, just lexical overlap. This is enough to pick the
    most relevant sentences for context compression.
    """
    if not sentence:
        return 0.0

    score = 0.0
    sent_lower = sentence.lower()

    # Query term overlap (each matching term adds to score)
    if query_terms:
        query_matches = sum(
            1 for term in query_terms
            if len(term) >= 3 and term.lower() in sent_lower
        )
        # Normalize by total query terms to avoid bias toward long queries
        score += 0.5 * (query_matches / max(len(query_terms), 1))

    # Entity term overlap (each matching entity adds more weight)
    if entity_terms:
        entity_matches = sum(
            1 for entity in entity_terms
            if len(entity) >= 2 and entity.lower() in sent_lower
        )
        # Entities are more specific signals — higher weight
        score += 0.4 * (entity_matches / max(len(entity_terms), 1))

    # Length bonus: prefer sentences with meaningful content
    word_count = len(sentence.split())
    if word_count >= 5:
        score += 0.1 * min(1.0, word_count / 15.0)  # Cap at 15 words
    elif word_count < 3:
        score *= 0.5  # Downweight very short sentences

    return score


def extractive_summarize(
    text: str,
    max_tokens: int,
    query_terms: set[str] | None = None,
    entity_terms: set[str] | None = None,
) -> str:
    """Extractive summarization: pick the most relevant sentences.

    Instead of hard character truncation (which cuts mid-sentence and
    loses important information at the end), this function:
    1. Splits the text into sentences
    2. Scores each sentence by relevance to query/entity terms
    3. Picks the top-scoring sentences that fit within the token budget
    4. Returns them in their original order (preserving coherence)

    This is lightweight (no model calls, no heavy dependencies) and
    produces more informative context than character truncation alone.

    Args:
        text: The full text to summarize.
        max_tokens: Maximum token budget for the summary.
        query_terms: Terms from the user query for relevance scoring.
        entity_terms: Entity names for relevance scoring.

    Returns:
        Extractively summarized text that fits within max_tokens.
    """
    if not text:
        return text

    query_terms = query_terms or set()
    entity_terms = entity_terms or set()

    # Check if text already fits within budget
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text

    # Split into sentences
    sentences = _split_sentences(text)
    if not sentences:
        # Can't split — fall back to hard truncation
        return _truncate_text(text, max_tokens)

    # If there's only one sentence, just truncate it
    if len(sentences) <= 1:
        return _truncate_text(text, max_tokens)

    # Score each sentence
    scored_sentences = []
    for idx, sentence in enumerate(sentences):
        relevance = _score_sentence(sentence, query_terms, entity_terms)
        # Add a small position bonus for first sentences (lead bias)
        position_bonus = 0.05 * max(0, 1.0 - idx / len(sentences))
        total_score = relevance + position_bonus
        scored_sentences.append((idx, sentence, total_score))

    # Select top-scoring sentences that fit within budget
    # Sort by score descending, greedily pick until budget is full
    scored_sentences.sort(key=lambda x: x[2], reverse=True)

    selected_indices: list[int] = []
    used_tokens = 0

    for idx, sentence, score in scored_sentences:
        sent_tokens = _estimate_tokens(sentence) + 1  # +1 for separator
        if used_tokens + sent_tokens <= max_tokens:
            selected_indices.append(idx)
            used_tokens += sent_tokens
        # Don't break early — we might fit shorter sentences after

    if not selected_indices:
        # Even the shortest sentence doesn't fit — take the first one
        # truncated (first sentences tend to be most important)
        return _truncate_text(sentences[0], max_tokens)

    # Reassemble in original order for coherence
    selected_indices.sort()
    result_sentences = [sentences[i] for i in selected_indices]
    result = " ".join(result_sentences)

    # Final safety check: if somehow we're over budget, truncate
    if _estimate_tokens(result) > max_tokens:
        result = _truncate_text(result, max_tokens)

    return result


# ---------------------------------------------------------------------------
# Smart Context Packer
# ---------------------------------------------------------------------------


class SmartContextPacker:
    """Budget-aware context assembly from RetrievalHit lists.

    The packer groups hits by retrieval channel, allocates token budgets
    per section, truncates intelligently, and produces a ContextPacket
    with clear provenance markers for the model.

    Phase 5.6 enhancement: When hits are too long for their section budget,
    the packer can now use extractive summarization instead of hard
    character truncation. This picks the most relevant sentences based
    on query/entity overlap, producing more informative context.

    Design decisions:
    - Evidence (FTS + VECTOR) gets the largest allocation
    - Wiki background gets its own section with [WIKI BACKGROUND] header
    - Procedural knowledge gets its own section with [PROCEDURES] header
    - Graph/debug context is kept minimal (only if hits exist)
    - Each hit is formatted with source ID, score, and channel provenance
    - Long hits are truncated to fit within section budget
    - The total stays within budget.total_tokens
    - Extractive summarization is used for long evidence hits (Phase 5.6)

    Graceful degradation (AIP-G-02):
    - If a section exceeds its allocation, it's truncated, not dropped
    - If all hits are from one channel, they get all the evidence budget
    - If budget is None, uses defaults
    - If extractive summarization fails, falls back to hard truncation
    """

    # Section headers for the model — clear, distinct, and consistent
    SECTION_HEADERS = {
        "evidence": "=== EVIDENCE (retrieved from project memory) ===",
        "wiki": "=== WIKI BACKGROUND (approved domain knowledge) ===",
        "procedural": "=== PROCEDURES (step-by-step guides and how-to) ===",
        "graph": "=== GRAPH CONTEXT (entity relationships) ===",
    }

    def __init__(
        self,
        budget: RetrievalBudget | None = None,
        chars_per_token: int = 4,
        enable_extractive_summarization: bool = True,
        query_terms: set[str] | None = None,
        entity_terms: set[str] | None = None,
    ) -> None:
        self._budget = budget or RetrievalBudget()
        self._chars_per_token = chars_per_token
        self._enable_extractive = enable_extractive_summarization
        self._query_terms = query_terms or set()
        self._entity_terms = entity_terms or set()

    def pack(self, hits: list[RetrievalHit]) -> ContextPacket:
        """Assemble hits into a structured, budget-aware context packet.

        Steps:
        1. Group hits by channel
        2. Allocate token budgets per section
        3. Format and truncate each section
        4. Assemble the final ContextPacket

        Args:
            hits: Curated list of RetrievalHit from the orchestrator.

        Returns:
            ContextPacket with sections, token counts, and truncation info.
        """
        started = time.monotonic()

        if not hits:
            return ContextPacket(
                budget=self._budget,
                elapsed_ms=(time.monotonic() - started) * 1000.0,
            )

        # Step 1: Group hits by channel
        evidence_hits: list[RetrievalHit] = []
        wiki_hits: list[RetrievalHit] = []
        procedural_hits: list[RetrievalHit] = []
        graph_hits: list[RetrievalHit] = []

        for hit in hits:
            if hit.retrieval_channel == RetrievalChannel.WIKI:
                wiki_hits.append(hit)
            elif hit.retrieval_channel == RetrievalChannel.PROCEDURAL:
                procedural_hits.append(hit)
            elif hit.retrieval_channel == RetrievalChannel.GRAPH:
                graph_hits.append(hit)
            else:
                # FTS + VECTOR + LEGACY → evidence section
                evidence_hits.append(hit)

        # Step 2: Allocate token budgets per section
        total = self._budget.total_tokens
        budget_map = {
            "evidence": int(total * self._budget.evidence_allocation),
            "wiki": int(total * self._budget.wiki_allocation),
            "procedural": int(total * self._budget.procedural_allocation),
            "graph": int(total * self._budget.graph_debug_allocation),
        }

        # Reclaim unused budget from empty sections
        active_sections = {
            k: v for k, v in budget_map.items()
            if (k == "evidence" and evidence_hits)
            or (k == "wiki" and wiki_hits)
            or (k == "procedural" and procedural_hits)
            or (k == "graph" and graph_hits)
        }

        if len(active_sections) < len(budget_map):
            # Redistribute unused budget proportionally to active sections
            unused = sum(v for k, v in budget_map.items() if k not in active_sections)
            if active_sections and unused > 0:
                # Give most of the unused budget to evidence (it's the most important)
                if "evidence" in active_sections and evidence_hits:
                    budget_map["evidence"] += int(unused * 0.6)
                    remaining = unused - int(unused * 0.6)
                    # Distribute rest proportionally
                    other_keys = [k for k in active_sections if k != "evidence"]
                    if other_keys:
                        per_key = remaining // len(other_keys)
                        for k in other_keys:
                            budget_map[k] += per_key

        # Step 3: Format each section
        sections: list[ContextSection] = []
        total_chars = 0
        total_tokens = 0
        total_truncated = 0
        total_compressed = 0

        # Evidence section (always first — most important)
        if evidence_hits:
            section = self._format_section(
                hits=evidence_hits,
                label="evidence",
                token_budget=budget_map["evidence"],
                header=self.SECTION_HEADERS["evidence"],
            )
            sections.append(section)
            total_chars += section.char_count
            total_tokens += section.est_tokens
            total_truncated += section.truncated_count
            total_compressed += section.compressed_count

        # Wiki section
        if wiki_hits:
            section = self._format_section(
                hits=wiki_hits,
                label="wiki",
                token_budget=budget_map["wiki"],
                header=self.SECTION_HEADERS["wiki"],
            )
            sections.append(section)
            total_chars += section.char_count
            total_tokens += section.est_tokens
            total_truncated += section.truncated_count
            total_compressed += section.compressed_count

        # Procedural section
        if procedural_hits:
            section = self._format_section(
                hits=procedural_hits,
                label="procedural",
                token_budget=budget_map["procedural"],
                header=self.SECTION_HEADERS["procedural"],
            )
            sections.append(section)
            total_chars += section.char_count
            total_tokens += section.est_tokens
            total_truncated += section.truncated_count
            total_compressed += section.compressed_count

        # Graph/debug section (last, smallest)
        if graph_hits:
            section = self._format_section(
                hits=graph_hits,
                label="graph",
                token_budget=budget_map["graph"],
                header=self.SECTION_HEADERS["graph"],
            )
            sections.append(section)
            total_chars += section.char_count
            total_tokens += section.est_tokens
            total_truncated += section.truncated_count
            total_compressed += section.compressed_count

        elapsed_ms = (time.monotonic() - started) * 1000.0

        return ContextPacket(
            sections=sections,
            total_chars=total_chars,
            total_est_tokens=total_tokens,
            budget=self._budget,
            hit_count=len(hits),
            truncated_hits=total_truncated,
            compressed_hits=total_compressed,
            elapsed_ms=round(elapsed_ms, 2),
        )

    def _format_section(
        self,
        hits: list[RetrievalHit],
        label: str,
        token_budget: int,
        header: str,
    ) -> ContextSection:
        """Format a list of hits into a section with budget enforcement.

        Each hit gets a provenance marker like:
        [1] (score=0.85, channel=fts, source=corpus_turn, id=turn_abc)
        Content text here...

        Hits are sorted by score descending within the section.
        Long hits are truncated to fit within the section budget,
        with earlier (higher-scored) hits getting priority.

        Phase 5.6: When a hit's content exceeds its token allocation,
        extractive summarization is used (if enabled) to pick the most
        relevant sentences instead of hard character truncation.
        """
        # Sort by score descending within section
        sorted_hits = sorted(hits, key=lambda h: h.score, reverse=True)

        header_tokens = _estimate_tokens(header) + 4  # +4 for blank lines
        remaining_budget = max(0, token_budget - header_tokens)
        entries: list[str] = []
        char_count = len(header) + 4
        est_tokens = header_tokens
        truncated_count = 0
        compressed_count = 0

        for i, hit in enumerate(sorted_hits, start=1):
            # Provenance marker line
            channel_label = hit.retrieval_channel.value if hit.retrieval_channel else "unknown"
            status_label = hit.evidence_status.value if hit.evidence_status else "raw"
            marker = (
                f"[{i}] (score={hit.score:.2f}, channel={channel_label}, "
                f"status={status_label}, source={hit.source_type}, id={hit.id})"
            )

            # Content: use text field (full content), fall back to snippet
            content = hit.text or hit.snippet or ""

            if not content:
                # Skip empty hits
                continue

            # Calculate how much budget this hit can use
            # Each hit gets a proportional share of remaining budget
            hits_remaining = len(sorted_hits) - i + 1
            hit_token_budget = max(50, remaining_budget // max(hits_remaining, 1))

            # Check if marker alone fits
            marker_tokens = _estimate_tokens(marker)
            if marker_tokens >= hit_token_budget:
                # Even the marker is too long — skip this hit
                truncated_count += 1
                continue

            content_token_budget = hit_token_budget - marker_tokens

            # Phase 5.6: Use extractive summarization for long content
            original_len = len(content)
            needs_compression = _estimate_tokens(content) > content_token_budget

            if needs_compression:
                if self._enable_extractive and content_token_budget >= 30:
                    # Try extractive summarization
                    try:
                        # Build entity terms from hit entities + query terms
                        hit_entity_terms = set(hit.entities) if hit.entities else set()
                        combined_entity_terms = self._entity_terms | hit_entity_terms

                        truncated_content = extractive_summarize(
                            content,
                            content_token_budget,
                            query_terms=self._query_terms,
                            entity_terms=combined_entity_terms,
                        )
                        if len(truncated_content) < original_len:
                            compressed_count += 1
                    except Exception:
                        # Graceful: fall back to hard truncation
                        truncated_content = _truncate_text(content, content_token_budget)
                        if len(truncated_content) < original_len:
                            truncated_count += 1
                else:
                    # Extractive disabled or budget too small — hard truncation
                    truncated_content = _truncate_text(content, content_token_budget)
                    if len(truncated_content) < original_len:
                        truncated_count += 1
            else:
                truncated_content = content

            entry = f"{marker}\n{truncated_content}"
            entry_tokens = _estimate_tokens(entry)

            # Check if adding this entry would exceed remaining budget
            if entry_tokens > remaining_budget:
                # Try with a shorter version
                min_content_tokens = 20  # bare minimum
                if marker_tokens + min_content_tokens <= remaining_budget:
                    short_content = _truncate_text(content, min_content_tokens)
                    entry = f"{marker}\n{short_content}"
                    entry_tokens = _estimate_tokens(entry)
                    truncated_count += 1
                else:
                    # Can't fit this hit at all
                    truncated_count += 1
                    continue

            entries.append(entry)
            char_count += len(entry) + 2  # +2 for blank line separator
            est_tokens += entry_tokens
            remaining_budget -= entry_tokens

        return ContextSection(
            label=label,
            header=header,
            entries=entries,
            char_count=char_count,
            est_tokens=est_tokens,
            truncated_count=truncated_count,
            compressed_count=compressed_count,
        )


# ---------------------------------------------------------------------------
# Convenience function for backward compatibility
# ---------------------------------------------------------------------------


def assemble_context(
    hits: list[RetrievalHit],
    budget: RetrievalBudget | None = None,
) -> str:
    """Assemble hits into a context string for model input.

    This is the drop-in replacement for the old _assemble_context()
    in ask_pipeline.py. It produces a richer, budget-aware context
    string while maintaining the same calling convention.

    Args:
        hits: Curated list of RetrievalHit from the orchestrator.
        budget: Token budget constraints. Defaults to RetrievalBudget().

    Returns:
        Formatted context string ready for model input.
    """
    packer = SmartContextPacker(budget=budget)
    packet = packer.pack(hits)
    return packet.to_prompt_string()


__all__ = [
    "SmartContextPacker",
    "ContextPacket",
    "ContextSection",
    "assemble_context",
    "extractive_summarize",
]
