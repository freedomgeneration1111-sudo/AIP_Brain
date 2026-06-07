"""Query Expansion — enrich queries before retrieval dispatch.

For entity-centric queries, expands using graph neighbors so that
"Who is Komal?" also searches for "Freedom Generation School", "principal",
"Urdu", "brick kiln" etc. This gives FTS5 a better chance of finding
relevant turns that don't mention the entity name directly.

Three expansion strategies:
1. Graph-based expansion: detected entities → graph neighbors → search terms
2. Template-based expansion: heuristic patterns (Who is X, What is Y, etc.)
3. LLM-based expansion: model-assisted reformulation for better recall

The expanded query is a SUPPLEMENT to the original, not a replacement.
The original query is always dispatched to all retrievers; the expanded
terms are dispatched as additional FTS queries that are merged into
the result pool.

Phase 5.3: Lightweight, configurable, graph + template only.
Phase 5.4: LLM-based expansion added as third strategy. Falls back
    gracefully to graph + template if the model call fails or is disabled.

Layer: orchestration. Imports from foundation (schemas) and adapter (GraphStore).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from aip.foundation.schemas.retrieval_trace import RetrievalQuery

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Expansion result
# ---------------------------------------------------------------------------


@dataclass
class QueryExpansion:
    """Result of query expansion — populated into trace.query_expansions."""

    original_query: str
    expanded_terms: list[str] = field(default_factory=list)
    # Terms added by expansion (entity names, aliases, neighbor names, LLM terms)
    expanded_fts_queries: list[str] = field(default_factory=list)
    # Full FTS5 queries derived from expanded terms
    source: str = "none"  # "graph" | "template" | "llm" | "combined"
    entity_ids_used: list[str] = field(default_factory=list)
    # Which entity IDs triggered the expansion
    llm_expansion_used: bool = False
    # Whether LLM expansion was actually used (vs. fell back)
    llm_latency_ms: float = 0.0
    # LLM call latency in milliseconds (0 if not used)


# ---------------------------------------------------------------------------
# Template-based expansion (heuristic, no graph required)
# ---------------------------------------------------------------------------


# Pattern → expansion template. When a query matches a pattern, the
# captured group is used as the seed for expansion.
_EXPANSION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)who is (.+?)[\?]?$", re.IGNORECASE), "person_identity"),
    (re.compile(r"(?i)what is (.+?)[\?]?$", re.IGNORECASE), "concept_definition"),
    (re.compile(r"(?i)what does (.+?) do[\?]?$", re.IGNORECASE), "person_role"),
    (re.compile(r"(?i)tell me about (.+?)[\?]?$", re.IGNORECASE), "general"),
    (re.compile(r"(?i)how does (.+?) (work|function|operate|handle)[\?]?$", re.IGNORECASE), "process"),
]


def _template_expand(query: str) -> list[str]:
    """Apply template-based expansion rules.

    Returns list of expanded search terms (not full FTS queries —
    just the semantic seeds that should be searched alongside the original).
    """
    terms: list[str] = []
    for pattern, expansion_type in _EXPANSION_PATTERNS:
        m = pattern.match(query.strip())
        if m:
            seed = m.group(1).strip()
            if expansion_type == "person_identity":
                # "Who is Komal?" → also search for role, organization, location
                terms.extend([seed, f"{seed} role", f"{seed} organization"])
            elif expansion_type == "person_role":
                # "What does Komal do?" → also search for work, project, title
                terms.extend([seed, f"{seed} work", f"{seed} title"])
            elif expansion_type == "concept_definition":
                terms.extend([seed, f"{seed} definition", f"{seed} overview"])
            elif expansion_type == "process":
                terms.extend([seed, f"{seed} process", f"{seed} procedure"])
            elif expansion_type == "general":
                terms.extend([seed])
            break  # Only use first matching pattern

    return terms


# ---------------------------------------------------------------------------
# Graph-based expansion
# ---------------------------------------------------------------------------


def _graph_expand(
    entity_ids: list[str],
    graph_store: Any,
    max_neighbors: int = 8,
) -> list[str]:
    """Expand using graph neighbors of detected entities.

    For each entity, get its direct neighbors in the knowledge graph.
    Their canonical names become additional search terms. This is the
    key mechanism for multi-hop recall: "Komal" → "Freedom Generation School".

    Args:
        entity_ids: Detected entity IDs from the query.
        graph_store: GraphStore with get_neighbors() method.
        max_neighbors: Maximum neighbors per entity.

    Returns:
        List of expanded search term strings.
    """
    if not entity_ids or graph_store is None:
        return []

    expanded: list[str] = []
    seen: set[str] = set()

    for eid in entity_ids:
        try:
            neighbors = graph_store.get_neighbors(eid, min_confidence=0.4)
            for node in neighbors[:max_neighbors]:
                name = node.canonical_name
                if name.lower() not in seen and name != eid:
                    seen.add(name.lower())
                    expanded.append(name)
                    # Also add aliases as search terms
                    for alias in (node.aliases or [])[:2]:
                        if alias.lower() not in seen:
                            seen.add(alias.lower())
                            expanded.append(alias)
        except Exception as exc:
            logger.debug("Graph expansion failed for entity %s: %s", eid, exc)
            continue

    return expanded


# ---------------------------------------------------------------------------
# LLM-based expansion (Phase 5.4)
# ---------------------------------------------------------------------------


_LLM_EXPANSION_SYSTEM_PROMPT = (
    "You are a search query expansion assistant. Given a user's question, "
    "generate 1-3 alternative search queries that would help find relevant "
    "information in a knowledge base. Focus on:\n"
    "- Synonyms and related terms\n"
    "- More specific or more general formulations\n"
    "- Different phrasings that capture the same intent\n"
    "- Named entities, organizations, or technical terms related to the query\n\n"
    "Return ONLY a JSON array of strings, no explanation. Example:\n"
    '["alternative query 1", "alternative query 2", "alternative query 3"]\n\n'
    "Keep expansions concise (5-10 words each). Do not repeat the original query."
)

_LLM_EXPANSION_MAX_TOKENS = 200
_LLM_EXPANSION_TIMEOUT_MS = 3000


async def _llm_expand(
    query: str,
    model_provider: Any,
    slot_name: str = "fast",
    max_expansions: int = 3,
) -> tuple[list[str], float]:
    """Expand a query using an LLM model.

    Uses a fast/small model slot to generate 1-3 reformulated versions
    of the query that improve recall. The LLM is asked to produce
    alternative search queries focusing on synonyms, related terms,
    and different phrasings.

    This is significantly smarter than template-based expansion for
    complex or ambiguous queries. For example:
    - "How does the alert system work?" → ["alert notification pipeline",
      "monitoring threshold triggers", "frost alert device configuration"]
    - "Who is Komal?" → ["Komal principal role", "Komal Freedom Generation School"]

    Args:
        query: The raw user query string.
        model_provider: ModelProvider protocol implementation with call() method.
        slot_name: Model slot to use (default "fast" for speed).
        max_expansions: Maximum expanded queries to return.

    Returns:
        Tuple of (expanded_terms, latency_ms). On failure, returns ([], 0.0).
    """
    if not query or model_provider is None:
        return [], 0.0

    started = time.monotonic()

    try:
        result = await model_provider.call(
            slot_name,
            messages=[
                {"role": "system", "content": _LLM_EXPANSION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Expand this query: {query}"},
            ],
            temperature=0.3,
            max_tokens=_LLM_EXPANSION_MAX_TOKENS,
        )

        latency_ms = (time.monotonic() - started) * 1000.0

        if result.get("error"):
            logger.debug("LLM expansion model call error: %s", result.get("error_message", ""))
            return [], latency_ms

        content = result.get("content", "").strip()
        if not content:
            return [], latency_ms

        # Parse the JSON array from the response
        # The LLM should return a JSON array, but be lenient about parsing
        expanded: list[str] = []

        # Try direct JSON parse first
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, str) and item.strip():
                        expanded.append(item.strip())
            elif isinstance(parsed, str):
                expanded.append(parsed.strip())
        except json.JSONDecodeError:
            # Fallback: try to extract quoted strings or comma-separated items
            # This handles cases where the LLM wraps the JSON in markdown
            # or adds extra text
            import re as _re
            # Try to find a JSON array in the response
            json_match = _re.search(r'\[.*?\]', content, _re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group())
                    if isinstance(parsed, list):
                        for item in parsed:
                            if isinstance(item, str) and item.strip():
                                expanded.append(item.strip())
                except json.JSONDecodeError:
                    pass

            # Last resort: split by newlines and clean up
            if not expanded:
                for line in content.split("\n"):
                    line = line.strip().lstrip("- ").lstrip("0123456789. ").strip('"\'')
                    if line and len(line) > 3:
                        expanded.append(line)

        # Cap at max_expansions
        expanded = expanded[:max_expansions]

        # Filter out expansions that are too similar to the original query
        query_lower = query.lower().strip()
        filtered = [
            e for e in expanded
            if e.lower().strip() != query_lower
        ]

        return filtered, latency_ms

    except Exception as exc:
        latency_ms = (time.monotonic() - started) * 1000.0
        logger.debug("LLM expansion failed (non-fatal): %s", exc)
        return [], latency_ms


# ---------------------------------------------------------------------------
# Main expansion function
# ---------------------------------------------------------------------------


async def expand_query_async(
    query: RetrievalQuery,
    *,
    detected_entities: list[tuple[str, float]] | None = None,
    graph_store: Any = None,
    model_provider: Any = None,
    model_slot: str = "fast",
    max_expanded_terms: int = 15,
    max_expanded_queries: int = 3,
    enable_template: bool = True,
    enable_graph: bool = True,
    enable_llm: bool = True,
) -> QueryExpansion:
    """Expand a retrieval query using graph + template + LLM strategies.

    This is the async version that supports LLM-based expansion.
    The LLM call is attempted first (highest quality), then supplemented
    by graph-based and template-based expansions.

    The orchestrator is responsible for:
    1. Calling expand_query_async() after entity detection
    2. Running the original query through all retrievers
    3. Running expanded FTS queries through FTSRetriever
    4. Merging results with RRF (multi-source agreement)

    Graceful degradation (AIP-G-02):
    - If LLM call fails, falls back to graph + template
    - If graph is unavailable, uses template + LLM
    - If everything fails, returns the original query unexpanded

    Args:
        query: The original RetrievalQuery.
        detected_entities: List of (entity_id, confidence) from entity detection.
        graph_store: GraphStore for neighbor expansion.
        model_provider: ModelProvider for LLM-based expansion.
        model_slot: Model slot name for expansion (default "fast").
        max_expanded_terms: Maximum number of expanded terms.
        max_expanded_queries: Maximum additional FTS queries to generate.
        enable_template: Enable template-based expansion.
        enable_graph: Enable graph-based expansion.
        enable_llm: Enable LLM-based expansion.

    Returns:
        QueryExpansion with expanded terms and FTS queries.
    """
    result = QueryExpansion(original_query=query.raw_query)

    all_terms: list[str] = []
    entity_ids_used: list[str] = []
    seen_lower: set[str] = set()

    # --- Graph-based expansion (high precision, uses knowledge graph) ---
    if enable_graph and detected_entities:
        entity_ids = [eid for eid, _ in detected_entities]
        entity_ids_used = entity_ids
        graph_terms = _graph_expand(entity_ids, graph_store)
        for gt in graph_terms:
            if gt.lower() not in seen_lower:
                all_terms.append(gt)
                seen_lower.add(gt.lower())
        if graph_terms:
            result.source = "graph"

    # --- LLM-based expansion (highest quality, model-assisted) ---
    if enable_llm and model_provider is not None:
        try:
            llm_terms, latency_ms = await _llm_expand(
                query.raw_query, model_provider, slot_name=model_slot
            )
            if llm_terms:
                result.llm_expansion_used = True
                result.llm_latency_ms = round(latency_ms, 2)
                for lt in llm_terms:
                    if lt.lower() not in seen_lower:
                        all_terms.append(lt)
                        seen_lower.add(lt.lower())
                if result.source == "graph":
                    result.source = "combined"
                elif result.source == "none":
                    result.source = "llm"
        except Exception as exc:
            logger.debug("LLM expansion failed (non-fatal, falling back): %s", exc)

    # --- Template-based expansion (fallback / supplement) ---
    if enable_template:
        template_terms = _template_expand(query.raw_query)
        for tt in template_terms:
            if tt.lower() not in seen_lower:
                all_terms.append(tt)
                seen_lower.add(tt.lower())
        if template_terms and result.source == "none":
            result.source = "template"
        elif template_terms and result.source in ("graph", "llm"):
            result.source = "combined"

    # Cap at max terms
    all_terms = all_terms[:max_expanded_terms]
    result.expanded_terms = all_terms
    result.entity_ids_used = entity_ids_used

    # Generate additional FTS queries from expanded terms
    if all_terms:
        from aip.orchestration.retrievers.fts_retriever import sanitize_fts_query

        # Strategy: group expanded terms into a small number of FTS queries
        # Each query is 2-4 terms AND-joined, covering different aspects
        chunk_size = max(2, len(all_terms) // max_expanded_queries)
        for i in range(0, len(all_terms), chunk_size):
            chunk = all_terms[i:i + chunk_size]
            if chunk:
                fts_q = sanitize_fts_query(" ".join(chunk))
                if fts_q:
                    result.expanded_fts_queries.append(fts_q)
            if len(result.expanded_fts_queries) >= max_expanded_queries:
                break

    return result


def expand_query(
    query: RetrievalQuery,
    *,
    detected_entities: list[tuple[str, float]] | None = None,
    graph_store: Any = None,
    max_expanded_terms: int = 15,
    max_expanded_queries: int = 3,
    enable_template: bool = True,
    enable_graph: bool = True,
) -> QueryExpansion:
    """Expand a retrieval query using graph neighbors + template rules.

    SYNCHRONOUS version — does NOT support LLM expansion.
    For LLM-based expansion, use expand_query_async() instead.

    This is kept for backward compatibility and for code paths that
    cannot use async (e.g., the eval script).

    Args:
        query: The original RetrievalQuery.
        detected_entities: List of (entity_id, confidence) from entity detection.
        graph_store: GraphStore for neighbor expansion.
        max_expanded_terms: Maximum number of expanded terms.
        max_expanded_queries: Maximum additional FTS queries to generate.
        enable_template: Enable template-based expansion.
        enable_graph: Enable graph-based expansion.

    Returns:
        QueryExpansion with expanded terms and FTS queries.
    """
    result = QueryExpansion(original_query=query.raw_query)

    all_terms: list[str] = []
    entity_ids_used: list[str] = []

    # Graph-based expansion (highest value)
    if enable_graph and detected_entities:
        entity_ids = [eid for eid, _ in detected_entities]
        entity_ids_used = entity_ids
        graph_terms = _graph_expand(entity_ids, graph_store)
        all_terms.extend(graph_terms)
        result.source = "graph"

    # Template-based expansion (fallback / supplement)
    if enable_template:
        template_terms = _template_expand(query.raw_query)
        # Only add template terms not already covered by graph expansion
        seen_lower = {t.lower() for t in all_terms}
        for tt in template_terms:
            if tt.lower() not in seen_lower:
                all_terms.append(tt)
                seen_lower.add(tt.lower())
        if all_terms and result.source == "none":
            result.source = "template"
        elif all_terms and result.source == "graph":
            result.source = "combined"

    # Cap at max terms
    all_terms = all_terms[:max_expanded_terms]
    result.expanded_terms = all_terms
    result.entity_ids_used = entity_ids_used

    # Generate additional FTS queries from expanded terms
    if all_terms:
        from aip.orchestration.retrievers.fts_retriever import sanitize_fts_query

        # Strategy: group expanded terms into a small number of FTS queries
        # Each query is 2-4 terms AND-joined, covering different aspects
        chunk_size = max(2, len(all_terms) // max_expanded_queries)
        for i in range(0, len(all_terms), chunk_size):
            chunk = all_terms[i:i + chunk_size]
            if chunk:
                fts_q = sanitize_fts_query(" ".join(chunk))
                if fts_q:
                    result.expanded_fts_queries.append(fts_q)
            if len(result.expanded_fts_queries) >= max_expanded_queries:
                break

    return result


__all__ = ["expand_query", "expand_query_async", "QueryExpansion"]
