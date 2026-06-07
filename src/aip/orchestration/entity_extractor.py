"""EntityExtractor — configurable entity extraction for the Graph channel.

Replaces the simple capitalized-word extraction with a more
robust, multi-strategy approach:

1. **Noun-phrase extraction** — lightweight regex/POS-inspired heuristics
   that identify multi-word entities (e.g. "Knowledge Graph", "New York")
   without requiring an external NER model.

2. **Graph-fuzzy matching** — when a ``GraphStore`` is available, candidate
   terms are matched against known entities in the graph using fuzzy
   (case-insensitive substring / alias) matching.  This lets the graph
   channel find entities even when the user's phrasing differs from the
   canonical name.

3. **LLM extraction** (optional) — when confidence is low and an LLM
   callable is provided, a lightweight prompt extracts entities.  This is
   disabled by default and only activated when configured.

Added ``create_llm_entity_fn()`` factory that wires
``EntityExtractor`` to the existing ``ModelProvider`` / ``ModelSlotResolver``
via the orchestration-layer proxy.  New config fields:
``entity_extraction_mode``, ``llm_entity_extraction_model``,
``llm_fallback_threshold``.  The LLM is only used as a fallback (not on
every query) to control cost and latency.

The extractor is configurable via ``EntityExtractorConfig`` so that
different deployments can pick the right trade-off between speed and
accuracy.

Layer: orchestration.  May import foundation, stdlib.  May NOT import
adapter directly — GraphStore and ModelProvider are injected.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Type alias for an optional LLM entity extraction callable.
# Signature: async (query: str) -> list[str]
LLMEntityFn = Callable[[str], Awaitable[list[str]]]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class EntityExtractorConfig:
    """Configuration for EntityExtractor.

    Attributes:
        strategy: Primary extraction strategy.
            - ``"noun_phrase"``: Regex-based noun phrase extraction (default,
              fast, no external deps).
            - ``"graph_fuzzy"``: Extract candidate terms then fuzzy-match
              against the graph's known entities.
            - ``"hybrid"``: Run noun_phrase first, then augment with
              graph_fuzzy matches (recommended).
            - ``"llm"``: Use an LLM callable for extraction (slowest but
              most accurate).
        min_entity_length: Minimum character length for a candidate entity.
        max_candidates: Maximum number of candidate entities to return.
        use_graph_fuzzy: Whether to also fuzzy-match against the graph
            when strategy is ``"noun_phrase"`` or ``"hybrid"``.
        fuzzy_match_threshold: Minimum similarity ratio (0-1) for fuzzy
            matching against graph entities.  Lower = more permissive.
        use_llm_fallback: Whether to fall back to LLM extraction when
            the primary strategy finds very few entities (<
            ``llm_fallback_threshold``).
        entity_extraction_mode: Overall mode controlling how the extractor
            operates.  ``"local"`` (default) — only uses local heuristics
            (noun_phrase + graph_fuzzy).  ``"hybrid_llm"`` — enables LLM
            fallback when primary extraction finds few entities.
            ``"llm_primary"`` — uses LLM as the primary extraction method.
        llm_entity_extraction_model: Model slot name to use for LLM-based
            entity extraction.  Defaults to ``"fast"`` (a lightweight,
            fast model slot).  Falls back to ``"synthesis"`` if ``"fast"``
            is not configured.
        llm_fallback_threshold: If the primary strategy finds fewer than
            this many entities, the LLM fallback is triggered (when
            ``use_llm_fallback=True`` or ``entity_extraction_mode="hybrid_llm"``).
            Default: 2.
    """

    strategy: str = "hybrid"
    min_entity_length: int = 3
    max_candidates: int = 8
    use_graph_fuzzy: bool = True
    fuzzy_match_threshold: float = 0.6
    use_llm_fallback: bool = False
    # LLM entity extraction configuration
    entity_extraction_mode: str = "local"  # "local" | "hybrid_llm" | "llm_primary"
    llm_entity_extraction_model: str = "fast"  # model slot for LLM extraction
    llm_fallback_threshold: int = 2  # trigger LLM when primary finds < N entities


# ---------------------------------------------------------------------------
# Noun-phrase extraction (lightweight, no external deps)
# ---------------------------------------------------------------------------

# Pattern for capitalised phrases: one or more capitalised words in sequence.
# E.g. "Knowledge Graph", "New York", "AIP Brain"
_CAP_PHRASE_RE = re.compile(
    r'\b([A-Z][a-z]*(?:\s+[A-Z][a-z]*)+)\b'
)

# Pattern for single capitalised words that look like proper nouns
# (excludes common sentence starters after filtering)
_SINGLE_CAP_RE = re.compile(
    r'\b([A-Z][a-zA-Z]{2,})\b'
)

# Known sentence-starting words that are NOT entities
_SENTENCE_STARTERS = frozenset({
    "The", "This", "That", "These", "Those", "What", "Which", "Who",
    "How", "When", "Where", "Why", "Is", "Are", "Was", "Were", "Can",
    "Could", "Should", "Would", "Will", "Do", "Does", "Did", "Has",
    "Have", "Had", "There", "Here", "It", "We", "They", "You", "He",
    "She", "But", "And", "Or", "If", "So", "Then", "Just", "Also",
    "Not", "No", "Yes", "Let", "May", "Might", "Must", "Shall",
})

# Stop words for single-word filtering
_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "of", "in", "to", "for", "with", "on", "at", "by", "from",
    "it", "its", "we", "our", "you", "your", "this", "that",
})


def extract_noun_phrases(query: str, min_length: int = 3) -> list[str]:
    """Extract noun phrases and proper nouns from a query string.

    This is a lightweight, regex-based approach that does not require
    any external NLP model.  It identifies:

    1. Multi-word capitalised phrases (e.g. "Knowledge Graph").
    2. Single capitalised words that are not common sentence starters.
    3. Quoted strings (e.g. "my entity").

    Args:
        query: The user query string.
        min_length: Minimum character length for extracted entities.

    Returns:
        List of candidate entity strings, deduplicated, in order of
        appearance.
    """
    seen: set[str] = set()
    results: list[str] = []

    # 1. Multi-word capitalised phrases
    for match in _CAP_PHRASE_RE.finditer(query):
        phrase = match.group(1).strip()
        if len(phrase) >= min_length and phrase not in seen:
            seen.add(phrase)
            results.append(phrase)

    # 2. Single capitalised words (exclude sentence starters)
    for match in _SINGLE_CAP_RE.finditer(query):
        word = match.group(1)
        if (
            len(word) >= min_length
            and word not in _SENTENCE_STARTERS
            and word.lower() not in _STOP_WORDS
            and word not in seen
        ):
            seen.add(word)
            results.append(word)

    # 3. Quoted strings — explicit entity mentions
    for match in re.finditer(r'["\x27]([^"\x27]+)["\x27]', query):
        quoted = match.group(1).strip()
        if len(quoted) >= min_length and quoted not in seen:
            seen.add(quoted)
            results.append(quoted)

    return results


# ---------------------------------------------------------------------------
# Graph fuzzy matching
# ---------------------------------------------------------------------------

async def fuzzy_match_graph_entities(
    candidates: list[str],
    graph_store: Any,
    threshold: float = 0.6,
    max_matches: int = 10,
) -> list[str]:
    """Match candidate terms against known entities in the graph store.

    Uses case-insensitive substring matching and alias matching.  Each
    candidate is compared against all graph node canonical names and
    aliases.  A match is declared when the candidate is a case-insensitive
    substring of the entity name or alias, or vice versa.

    Args:
        candidates: List of candidate entity strings from noun-phrase
            extraction or other heuristics.
        graph_store: A GraphStore instance with async ``search_nodes()`` and
            ``get_all_nodes()`` methods.
        threshold: Minimum similarity (0-1).  Since we use substring
            matching, this acts as a minimum overlap ratio.
        max_matches: Maximum number of matched entities to return.

    Returns:
        List of canonical entity names from the graph that match the
        candidates, deduplicated.
    """
    if not candidates or graph_store is None:
        return []

    matched: list[str] = []
    seen_ids: set[str] = set()

    for candidate in candidates:
        cand_lower = candidate.lower()

        # First try the graph's built-in search (substring match on
        # canonical_name)
        try:
            search_results = await graph_store.search_nodes(
                query=candidate, limit=5,
            )
        except Exception:
            search_results = []

        for node in search_results:
            if node.id in seen_ids:
                continue
            node_name_lower = node.canonical_name.lower()

            # Substring match in either direction
            if (
                cand_lower in node_name_lower
                or node_name_lower in cand_lower
            ):
                # Compute overlap ratio for threshold check
                shorter = min(len(cand_lower), len(node_name_lower))
                longer = max(len(cand_lower), len(node_name_lower))
                ratio = shorter / longer if longer > 0 else 0
                if ratio >= threshold:
                    seen_ids.add(node.id)
                    matched.append(node.canonical_name)

            # Also check aliases
            for alias in (node.aliases or []):
                alias_lower = alias.lower()
                if cand_lower in alias_lower or alias_lower in cand_lower:
                    shorter = min(len(cand_lower), len(alias_lower))
                    longer = max(len(cand_lower), len(alias_lower))
                    ratio = shorter / longer if longer > 0 else 0
                    if ratio >= threshold and node.id not in seen_ids:
                        seen_ids.add(node.id)
                        matched.append(node.canonical_name)
                        break

    return matched[:max_matches]


# ---------------------------------------------------------------------------
# EntityExtractor (main class)
# ---------------------------------------------------------------------------

class EntityExtractor:
    """Configurable entity extractor for the Graph retrieval channel.

    Combines multiple extraction strategies and graph-fuzzy matching to
    produce a list of seed entities for ``GraphRetriever.expand_query_via_graph()``.

    Usage::

        extractor = EntityExtractor(config=EntityExtractorConfig(strategy="hybrid"))
        entities = extractor.extract("How does Knowledge Graph connect to AIP?", graph_store=store)
        # entities → ["Knowledge Graph", "AIP", ...]
    """

    def __init__(
        self,
        config: EntityExtractorConfig | None = None,
        graph_store: Any = None,
        llm_fn: LLMEntityFn | None = None,
    ) -> None:
        self._config = config or EntityExtractorConfig()
        self._graph_store = graph_store
        self._llm_fn = llm_fn

    @property
    def config(self) -> EntityExtractorConfig:
        return self._config

    async def extract(self, query: str, graph_store: Any | None = None) -> list[str]:
        """Extract entities from a query using the configured strategy.

        Args:
            query: The user's query string.
            graph_store: Optional graph store override.  Uses the one
                provided at construction if not specified.

        Returns:
            List of entity strings suitable for seeding the Graph channel.
        """
        store = graph_store or self._graph_store
        cfg = self._config
        strategy = cfg.strategy

        # Step 1: Noun-phrase extraction (always runs as base)
        noun_phrases = extract_noun_phrases(query, min_length=cfg.min_entity_length)

        if strategy == "noun_phrase":
            candidates = noun_phrases
        elif strategy == "graph_fuzzy":
            # Use noun phrases as candidates, but only return graph matches
            if store is not None:
                candidates = await fuzzy_match_graph_entities(
                    noun_phrases, store,
                    threshold=cfg.fuzzy_match_threshold,
                    max_matches=cfg.max_candidates,
                )
            else:
                candidates = noun_phrases
        elif strategy == "hybrid":
            # Start with noun phrases, augment with graph fuzzy matches
            candidates = list(noun_phrases)
            if cfg.use_graph_fuzzy and store is not None:
                graph_matches = await fuzzy_match_graph_entities(
                    noun_phrases + [w for w in query.split() if len(w) >= cfg.min_entity_length],
                    store,
                    threshold=cfg.fuzzy_match_threshold,
                    max_matches=cfg.max_candidates,
                )
                # Add graph matches that aren't already in candidates
                seen_lower = {c.lower() for c in candidates}
                for gm in graph_matches:
                    if gm.lower() not in seen_lower:
                        candidates.append(gm)
                        seen_lower.add(gm.lower())
        elif strategy == "llm":
            # LLM extraction is async — fall back to noun phrases here
            # and let extract_async() handle the LLM call.
            candidates = noun_phrases
        else:
            logger.warning("Unknown entity extraction strategy: %s, falling back to noun_phrase", strategy)
            candidates = noun_phrases

        # Apply max_candidates limit
        return candidates[:cfg.max_candidates]

    async def extract_async(self, query: str, graph_store: Any | None = None) -> list[str]:
        """Async entity extraction — includes LLM fallback support.

        Same as ``extract()`` but also supports the LLM extraction
        strategy and fallback.  Now respects ``entity_extraction_mode`` and
        ``llm_fallback_threshold`` settings for more fine-grained control
        over when LLM extraction is invoked.
        """
        cfg = self._config
        mode = cfg.entity_extraction_mode

        # For llm_primary mode, try LLM first
        if mode == "llm_primary" and self._llm_fn is not None:
            try:
                llm_entities = await self._llm_fn(query)
                if llm_entities:
                    return llm_entities[:cfg.max_candidates]
            except Exception as exc:
                logger.debug("LLM primary entity extraction failed, falling back: %s", exc)
            # LLM failed — fall through to local extraction

        # Start with the local extraction (now async)
        candidates = await self.extract(query, graph_store=graph_store)

        # Determine if LLM fallback should be triggered
        should_use_llm = (
            (cfg.use_llm_fallback or mode == "hybrid_llm")
            and len(candidates) < cfg.llm_fallback_threshold
            and self._llm_fn is not None
        )

        if should_use_llm:
            try:
                llm_entities = await self._llm_fn(query)
                if llm_entities:
                    # Merge with existing candidates (local results first)
                    seen_lower = {c.lower() for c in candidates}
                    for ent in llm_entities:
                        if ent.lower() not in seen_lower:
                            candidates.append(ent)
                            seen_lower.add(ent.lower())
            except Exception as exc:
                logger.debug("LLM entity extraction fallback failed: %s", exc)

        # Legacy support: strategy == "llm" with no local results
        if cfg.strategy == "llm" and self._llm_fn is not None and not candidates:
            try:
                llm_entities = await self._llm_fn(query)
                if llm_entities:
                    candidates = llm_entities[:cfg.max_candidates]
            except Exception as exc:
                logger.debug("LLM entity extraction failed: %s", exc)
                # Fall back to noun phrases
                candidates = extract_noun_phrases(query, min_length=cfg.min_entity_length)

        return candidates[:cfg.max_candidates]


# ---------------------------------------------------------------------------
# LLM entity extraction factory
# ---------------------------------------------------------------------------

# System prompt for LLM-based entity extraction
_LLM_ENTITY_SYSTEM_PROMPT = (
    "You are an entity extraction assistant.  Given a user query, extract "
    "all named entities, technical terms, and proper nouns that could be "
    "used to seed a knowledge graph search.  Return ONLY a JSON array of "
    "entity strings, nothing else.  Example: [\"Knowledge Graph\", \"AIP\", "
    "\"Personalized PageRank\"].  Keep entities concise (1-4 words each).  "
    "Do NOT include common words, verbs, or adjectives unless they are part "
    "of a proper noun."
)


def create_llm_entity_fn(
    model_provider: Any,
    slot_name: str = "fast",
    fallback_slot: str = "synthesis",
) -> LLMEntityFn:
    """Create an LLM entity extraction callable wired to a ModelProvider.

    This factory bridges the ``EntityExtractor`` (which expects an
    ``LLMEntityFn``) with the AIP model provider infrastructure.  The
    returned callable sends a structured prompt to the model and parses
    the JSON response into a list of entity strings.

    Graceful degradation: if the model call fails (network error, timeout,
    invalid response), the callable returns an empty list rather than
    raising.  The caller (``EntityExtractor.extract_async()``) will then
    fall back to local extraction.

    The function tries the ``slot_name`` first (default: ``"fast"`` for
    a lightweight model).  If that slot is not configured, it falls back
    to ``fallback_slot`` (default: ``"synthesis"``).

    Args:
        model_provider: A ModelProvider / ModelSlotResolver instance with
            an ``async call(slot_name, messages, **kwargs)`` method.
        slot_name: Model slot to use for entity extraction.
        fallback_slot: Fallback slot if the primary is unavailable.

    Returns:
        An async callable ``(query: str) -> list[str]`` suitable for
        passing to ``EntityExtractor(llm_fn=...)``.
    """

    async def _llm_entity_fn(query: str) -> list[str]:
        messages = [
            {"role": "system", "content": _LLM_ENTITY_SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract entities from this query:\n\n{query}"},
        ]

        # Try primary slot, then fallback
        for slot in (slot_name, fallback_slot):
            try:
                result = await model_provider.call(
                    slot, messages, temperature=0.1, max_tokens=200,
                )
                if result.get("error"):
                    continue

                content = result.get("content", "")
                if not content:
                    continue

                # Parse the JSON array from the model response
                entities = _parse_llm_entity_response(content)
                if entities:
                    return entities

            except Exception as exc:
                logger.debug("LLM entity extraction call failed on slot '%s': %s", slot, exc)
                continue

        # All slots failed — return empty (caller falls back to local)
        return []

    return _llm_entity_fn


def _parse_llm_entity_response(content: str) -> list[str]:
    """Parse an LLM response into a list of entity strings.

    The model is prompted to return a JSON array, but we handle various
    formats gracefully: raw JSON arrays, markdown-wrapped JSON, and
    comma-separated lists.

    Args:
        content: Raw LLM response text.

    Returns:
        List of entity strings, or empty list if parsing fails.
    """
    import json as _json

    content = content.strip()

    # Try direct JSON parse
    try:
        parsed = _json.loads(content)
        if isinstance(parsed, list):
            return [str(e).strip() for e in parsed if str(e).strip()]
    except (_json.JSONDecodeError, ValueError):
        pass

    # Try extracting JSON from markdown code blocks
    json_match = re.search(r'```(?:json)?\s*\n?\s*(\[.*?\])\s*\n?\s*```', content, re.DOTALL)
    if json_match:
        try:
            parsed = _json.loads(json_match.group(1))
            if isinstance(parsed, list):
                return [str(e).strip() for e in parsed if str(e).strip()]
        except (_json.JSONDecodeError, ValueError):
            pass

    # Try finding a JSON array anywhere in the response
    bracket_match = re.search(r'\[.*?\]', content, re.DOTALL)
    if bracket_match:
        try:
            parsed = _json.loads(bracket_match.group(0))
            if isinstance(parsed, list):
                return [str(e).strip() for e in parsed if str(e).strip()]
        except (_json.JSONDecodeError, ValueError):
            pass

    # Last resort: split by commas or newlines, clean up
    parts = re.split(r'[,\n]', content)
    candidates = []
    for part in parts:
        cleaned = part.strip().strip('"\'[]').strip()
        if len(cleaned) >= 2:
            candidates.append(cleaned)
    return candidates[:10]  # cap at 10
