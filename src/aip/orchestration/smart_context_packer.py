"""SmartContextPacker — budget-aware context assembly with extractive summarization.

Sprint 5.6: Introduced as the primary context assembly path, replacing the
legacy ``_assemble_context()`` function that worked with ``SourceReference``
objects.  The packer operates on ``RetrievalHit`` instances produced by
``RetrievalOrchestrator`` and applies:

1. **Budget-aware packing** — fits as many hits as possible within a token
   budget, prioritising higher RRF-scored hits.
2. **Extractive summarization** — when a hit's full content would exceed
   the remaining budget, extracts the most relevant sentences instead of
   truncating mid-sentence.
3. **Provenance metadata** — each packed segment is annotated with its
   source ID, channel, and score so the model can cite accurately.

Sprint 5.7: This is now the **only active** context assembly path.
The legacy ``_assemble_context()`` in ``ask_pipeline.py`` was removed
in Sprint 5.8.

Layer: orchestration.  May import foundation, stdlib.  May NOT import
adapter directly.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from aip.foundation.schemas.retrieval import RetrievalHit

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class PackerConfig:
    """Configuration for SmartContextPacker.

    Attributes:
        max_context_tokens: Hard ceiling on packed context size (approximate
            token count).  A reasonable default for most LLM context windows
            is 4000 tokens; the packer uses a rough 1 token ≈ 4 chars
            heuristic.
        max_hits: Maximum number of hits to pack, regardless of budget.
        min_hits: Minimum hits to include before budget runs out.
        extractive_summary_ratio: When truncating a hit, keep this fraction
            of the highest-scoring sentences (0.0–1.0).  Default 0.5 means
            keep the best half of sentences.
        include_metadata: Whether to prepend provenance headers to each
            hit's content in the packed context.
        max_hits_per_channel: Sprint 5.9 — cap the number of hits that any
            single channel can contribute to the packed context.  0 = no
            limit.  This works in concert with the orchestrator-level
            ``OrchestratorConfig.max_hits_per_channel`` but applies at the
            *packing* stage (after fusion), not before.
    """

    max_context_tokens: int = 4000
    max_hits: int = 20
    min_hits: int = 3
    extractive_summary_ratio: float = 0.5
    include_metadata: bool = True
    max_hits_per_channel: int = 0  # Sprint 5.9: 0 = no per-channel limit


# ---------------------------------------------------------------------------
# Sentence extraction
# ---------------------------------------------------------------------------

def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using a simple regex.

    Handles common English sentence boundaries.  Not perfect but sufficient
    for extractive summarization of retrieval hits.
    """
    if not text:
        return []
    # Split on sentence-ending punctuation followed by whitespace+uppercase
    # or newlines.  Keep the delimiter attached.
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    # Also split on double newlines (paragraph breaks)
    result: list[str] = []
    for part in parts:
        sub_parts = re.split(r'\n\n+', part)
        result.extend(sp.strip() for sp in sub_parts if sp.strip())
    return result


def extractive_summarize(
    text: str,
    max_chars: int,
    query: str = "",
    ratio: float = 0.5,
) -> str:
    """Extract the most relevant sentences from *text* within *max_chars*.

    Relevance heuristic: sentences that contain query terms rank higher.
    Among equal-relevance sentences, earlier sentences rank higher
    (position bias).

    If the full text fits within *max_chars*, it is returned unchanged.

    Args:
        text: The hit content to summarize.
        max_chars: Maximum character budget for the summary.
        query: Original query (used for relevance scoring).
        ratio: Fraction of sentences to keep if budget is tight.

    Returns:
        Summarized text that fits within the character budget.
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text

    sentences = _split_sentences(text)
    if not sentences:
        return text[:max_chars]

    # Score each sentence
    query_terms = set(query.lower().split()) if query else set()

    def _score(idx: int, s: str) -> float:
        s_lower = s.lower()
        term_overlap = sum(1 for t in query_terms if t in s_lower)
        position_bonus = 1.0 - (idx / len(sentences)) * 0.3
        length_bonus = min(len(s) / 100.0, 1.0)  # prefer substantive sentences
        return term_overlap * 2.0 + position_bonus + length_bonus * 0.5

    scored = [(idx, s, _score(idx, s)) for idx, s in enumerate(sentences)]

    # Select top sentences within budget
    target_count = max(1, int(len(sentences) * ratio))
    scored.sort(key=lambda x: x[2], reverse=True)
    selected = sorted(scored[:target_count], key=lambda x: x[0])  # restore order

    result_parts: list[str] = []
    total_chars = 0
    for _, s, _ in selected:
        if total_chars + len(s) + 2 > max_chars:
            break
        result_parts.append(s)
        total_chars += len(s) + 2

    if not result_parts:
        # Fallback: first sentence
        return sentences[0][:max_chars]

    return "\n\n".join(result_parts)


# ---------------------------------------------------------------------------
# SmartContextPacker
# ---------------------------------------------------------------------------

@dataclass
class PackedContext:
    """Result of SmartContextPacker.pack().

    Attributes:
        context_text: The assembled context string ready for model input.
        hits_packed: Number of hits that were fully included.
        hits_summarized: Number of hits that were extractively summarized.
        hits_skipped: Number of hits that didn't fit in the budget.
        estimated_tokens: Approximate token count of the packed context.
    """

    context_text: str
    hits_packed: int = 0
    hits_summarized: int = 0
    hits_skipped: int = 0
    estimated_tokens: int = 0


class SmartContextPacker:
    """Budget-aware context packer for retrieval hits.

    Takes a list of ``RetrievalHit`` instances (already sorted by
    ``rrf_score`` descending from RRF fusion) and assembles them into a
    single context string that fits within a token budget.

    Usage::

        packer = SmartContextPacker(config=PackerConfig(max_context_tokens=4000))
        packed = packer.pack(hits, query="What is AIP?")
        context_for_model = packed.context_text
    """

    def __init__(self, config: PackerConfig | None = None) -> None:
        self._config = config or PackerConfig()

    @property
    def config(self) -> PackerConfig:
        return self._config

    def pack(
        self,
        hits: list[RetrievalHit],
        query: str = "",
    ) -> PackedContext:
        """Pack retrieval hits into a context string within budget.

        Hits are processed in RRF-score order (assumed sorted descending).
        Each hit is either included in full, extractively summarized, or
        skipped if the budget is exhausted.

        Args:
            hits: Retrieval hits (should be sorted by rrf_score descending).
            query: The original query (used for extractive summarization).

        Returns:
            PackedContext with the assembled string and packing statistics.
        """
        if not hits:
            return PackedContext(
                context_text="No relevant sources found in project memory.",
                hits_packed=0,
                hits_summarized=0,
                hits_skipped=0,
                estimated_tokens=0,
            )

        max_chars = self._config.max_context_tokens * 4  # rough 1 token ≈ 4 chars
        max_hits = self._config.max_hits
        min_hits = self._config.min_hits
        per_channel_limit = self._config.max_hits_per_channel

        # Sprint 5.9: Enforce per-channel hit limits before packing.
        # This ensures no single channel dominates the context even after
        # RRF fusion.  Hits are already sorted by rrf_score (descending).
        if per_channel_limit > 0:
            channel_counts: dict[str, int] = {}
            filtered_hits: list[RetrievalHit] = []
            for hit in hits:
                ch = hit.source_channel or "unknown"
                current_count = channel_counts.get(ch, 0)
                if current_count < per_channel_limit:
                    filtered_hits.append(hit)
                    channel_counts[ch] = current_count + 1
            hits = filtered_hits

        parts: list[str] = []
        total_chars = 0
        hits_packed = 0
        hits_summarized = 0
        hits_skipped = 0

        for idx, hit in enumerate(hits[:max_hits]):
            content = hit.content or ""
            header = ""
            if self._config.include_metadata:
                header = (
                    f"[Source {idx + 1}: {hit.id} "
                    f"(score={hit.rrf_score:.4f}, channel={hit.source_channel})]\n"
                )

            full_len = len(header) + len(content) + 2  # +2 for paragraph break

            if total_chars + full_len <= max_chars:
                # Full inclusion
                parts.append(header + content)
                total_chars += full_len
                hits_packed += 1
            elif hits_packed < min_hits:
                # Must include at least min_hits — use extractive summary
                remaining = max_chars - total_chars - len(header) - 2
                if remaining > 50:
                    summary = extractive_summarize(
                        content,
                        max_chars=remaining,
                        query=query,
                        ratio=self._config.extractive_summary_ratio,
                    )
                    parts.append(header + summary)
                    total_chars += len(header) + len(summary) + 2
                    hits_summarized += 1
                else:
                    hits_skipped += 1
            else:
                # Over budget and past minimum — try to summarize, else skip
                remaining = max_chars - total_chars - len(header) - 2
                if remaining > 50:
                    summary = extractive_summarize(
                        content,
                        max_chars=remaining,
                        query=query,
                        ratio=self._config.extractive_summary_ratio,
                    )
                    if summary:
                        parts.append(header + summary)
                        total_chars += len(header) + len(summary) + 2
                        hits_summarized += 1
                    else:
                        hits_skipped += 1
                else:
                    hits_skipped += 1

        context_text = "\n\n".join(parts)
        estimated_tokens = len(context_text) // 4  # rough estimate

        return PackedContext(
            context_text=context_text,
            hits_packed=hits_packed,
            hits_summarized=hits_summarized,
            hits_skipped=hits_skipped,
            estimated_tokens=estimated_tokens,
        )
