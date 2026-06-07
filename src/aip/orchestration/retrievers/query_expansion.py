"""Query Expansion — enrich queries before retrieval dispatch.

For entity-centric queries, expands using graph neighbors so that
"Who is Komal?" also searches for "Freedom Generation School", "principal",
"Urdu", "brick kiln" etc. This gives FTS5 a better chance of finding
relevant turns that don't mention the entity name directly.

Two expansion strategies:
1. Graph-based expansion: detected entities → graph neighbors → search terms
2. Template-based expansion: heuristic patterns (Who is X, What is Y, etc.)

The expanded query is a SUPPLEMENT to the original, not a replacement.
The original query is always dispatched to all retrievers; the expanded
terms are dispatched as additional FTS queries that are merged into
the result pool.

Phase 5.3: Lightweight, configurable, no LLM calls.

Layer: orchestration. Imports from foundation (schemas) and adapter (GraphStore).
"""

from __future__ import annotations

import logging
import re
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
    # Terms added by expansion (entity names, aliases, neighbor names)
    expanded_fts_queries: list[str] = field(default_factory=list)
    # Full FTS5 queries derived from expanded terms
    source: str = "none"  # "graph" | "template" | "combined"
    entity_ids_used: list[str] = field(default_factory=list)
    # Which entity IDs triggered the expansion


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
# Main expansion function
# ---------------------------------------------------------------------------


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

    This is called BEFORE retrieval dispatch. The expanded terms are
    converted into additional FTS queries that supplement the original.

    The orchestrator is responsible for:
    1. Calling expand_query() after entity detection
    2. Running the original query through all retrievers
    3. Running expanded FTS queries through FTSRetriever
    4. Merging results with RRF (multi-source agreement)

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


__all__ = ["expand_query", "QueryExpansion"]
