"""GraphRetriever — graph-based multi-hop retrieval via entity-turn index + PPR.

Implements the Retriever protocol. Two-zone retrieval:
  Zone A (Direct Mentions): query entities → entity_turn_index → turn IDs
  Zone B (PPR Expansion): seed entities → Personalized PageRank → expanded
      entities → entity_turn_index → turn IDs

This is the key retrieval component that enables multi-hop recall: a query
about "Komal" can find turns about "Freedom Generation School" even if
those turns never mention "Komal" directly, because the graph connects them.

Design decisions:
- GraphStore is synchronous (no aiosqlite) — graph is small, read-heavy
- CorpusTurnStore is used to hydrate turn content from turn IDs
- Zone A is always attempted first (high precision)
- Zone B is attempted if networkx is available (graceful skip if not)
- Hub leash: per-entity result cap prevents hub entities from drowning others
- Type filter: optional entity_type filter on seed entities
- Confidence calibration: Zone A hits get higher base scores than Zone B
- Graceful degradation (AIP-G-02): if graph_store is None or fails, return []

Layer: orchestration. Imports from foundation (schemas, protocols) and
adapter (GraphStore, CorpusTurnStore).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from aip.foundation.schemas.retrieval_trace import (
    EvidenceStatus,
    RetrievalBudget,
    RetrievalChannel,
    RetrievalHit,
    RetrievalQuery,
    RetrievalTrace,
    RetrieverTrace,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Query entity detection
# ---------------------------------------------------------------------------


def detect_query_entities(
    query: str,
    graph_store: Any,
    max_entities: int = 10,
    entity_type_filter: list[str] | None = None,
) -> list[tuple[str, float]]:
    """Detect entities in the query by matching against graph_nodes.

    Strategy (cheap, no LLM):
    1. Split query into n-grams and single tokens
    2. Search graph_nodes by canonical_name substring match
    3. Also search aliases
    4. Score by: exact match > multi-word substring > single-word substring
    5. Return top-k entities as (entity_id, confidence) pairs

    Args:
        query: The raw user query string.
        graph_store: GraphStore instance with search_nodes() method.
        max_entities: Maximum entities to return.
        entity_type_filter: Optional list of entity types to filter by
            (e.g. ["PERSON", "ORGANIZATION"]). When set, only entities
            whose entity_type is in this list are returned.

    Returns:
        List of (entity_id, confidence) tuples, sorted by confidence descending.
    """
    if not query or graph_store is None:
        return []

    candidates: dict[str, float] = {}
    candidate_types: dict[str, str] = {}  # entity_id → entity_type

    # Strategy 1: Search by query fragments
    # Strip punctuation from tokens for matching
    import re as _re
    raw_tokens = query.split()
    tokens = [_re.sub(r'[?!.,;:*+\-^(){}|~"\\]', '', t) for t in raw_tokens]
    tokens = [t for t in tokens if t]  # remove empty strings after stripping
    search_terms = list(tokens)  # individual tokens

    # Add 2-grams and 3-grams (for multi-word entity names like "Freedom Generation")
    for i in range(len(tokens) - 1):
        search_terms.append(f"{tokens[i]} {tokens[i+1]}")
    for i in range(len(tokens) - 2):
        search_terms.append(f"{tokens[i]} {tokens[i+1]} {tokens[i+2]}")

    for term in search_terms:
        if len(term) < 2:
            continue
        try:
            matches = graph_store.search_nodes(term, limit=5)
            for node in matches:
                score = candidates.get(node.id, 0.0)
                # Exact match (case-insensitive) gets highest score
                if node.canonical_name.lower() == term.lower():
                    score = max(score, 0.95)
                # Multi-word substring match
                elif " " in term:
                    score = max(score, 0.75)
                # Single-word substring match
                else:
                    score = max(score, 0.5)
                candidates[node.id] = score
                candidate_types[node.id] = node.entity_type
        except Exception:
            continue

    # Apply entity_type filter if specified
    if entity_type_filter:
        type_set = {t.upper() for t in entity_type_filter}
        candidates = {
            eid: score for eid, score in candidates.items()
            if candidate_types.get(eid, "").upper() in type_set
        }

    # Sort by confidence and return top-k
    sorted_entities = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
    return sorted_entities[:max_entities]


# ---------------------------------------------------------------------------
# Hub leash
# ---------------------------------------------------------------------------


def apply_hub_leash(
    turn_entries: list[dict],
    max_per_entity: int = 15,
) -> list[dict]:
    """Apply hub leash — cap the number of turns per entity.

    Hub entities (like "Komal" with 100+ turns) can drown out other
    entities' contributions. The hub leash ensures each entity gets
    at most max_per_entity turns, leaving room for diverse results.

    Args:
        turn_entries: List of dicts with entity_id, turn_id, confidence, source.
        max_per_entity: Maximum turns per entity.

    Returns:
        Filtered list with hub entity turns capped.
    """
    entity_counts: dict[str, int] = {}
    leashed: list[dict] = []

    for entry in turn_entries:
        eid = entry.get("entity_id", "")
        count = entity_counts.get(eid, 0)
        if count >= max_per_entity:
            continue
        leashed.append(entry)
        entity_counts[eid] = count + 1

    return leashed


# ---------------------------------------------------------------------------
# Turn hydration (turn_id → RetrievalHit)
# ---------------------------------------------------------------------------


async def _hydrate_turn_hits(
    turn_ids: list[str],
    corpus_store: Any,
    base_score: float = 0.8,
    zone: str = "A",
    entity_map: dict[str, list[str]] | None = None,
) -> list[RetrievalHit]:
    """Convert turn IDs to RetrievalHit objects by fetching from CorpusTurnStore.

    Args:
        turn_ids: List of turn_id strings to hydrate.
        corpus_store: CorpusTurnStore with get_turn() method.
        base_score: Starting score for this zone's hits.
        zone: "A" for direct mentions, "B" for PPR-expanded.
        entity_map: Optional turn_id → [entity_ids] mapping for entities field.

    Returns:
        List of RetrievalHit objects.
    """
    hits: list[RetrievalHit] = []
    for i, tid in enumerate(turn_ids):
        try:
            turn = await corpus_store.get_turn(tid)
            if turn is None:
                continue

            # Build entities list
            entities: list[str] = []
            if entity_map and tid in entity_map:
                entities = entity_map[tid]
            if hasattr(turn, "domains") and turn.domains:
                entities.extend(turn.domains)
            if hasattr(turn, "tags") and turn.tags:
                entities.extend(turn.tags)

            # Score decays by position within zone
            position_decay = 1.0 - (i / max(len(turn_ids), 1)) * 0.3
            importance_boost = float(turn.importance or 0.0) * 0.2
            score = base_score * position_decay + importance_boost

            # Parse timestamp
            recency_ts = None
            if hasattr(turn, "turn_timestamp") and turn.turn_timestamp:
                try:
                    recency_ts = datetime.fromisoformat(turn.turn_timestamp)
                except (ValueError, TypeError):
                    pass

            snippet = ""
            if hasattr(turn, "searchable_text") and turn.searchable_text:
                snippet = turn.searchable_text[:200]

            hit = RetrievalHit(
                id=turn.turn_id,
                source_type="corpus_turn",
                source_id=turn.turn_id,
                title=(
                    f"{turn.conversation_name} [{turn.primary_domain}]"
                    if hasattr(turn, "conversation_name")
                    else None
                ),
                text=turn.searchable_text or "",
                snippet=snippet,
                rank=i + 1,
                score=round(score, 4),
                confidence=float(turn.beast_confidence or 0.0),
                recency_ts=recency_ts,
                importance=float(turn.importance) if turn.importance else None,
                domain=turn.primary_domain or None,
                entities=entities,
                retrieval_channel=RetrievalChannel.GRAPH,
                evidence_status=EvidenceStatus.RAW,
                debug={
                    "zone": zone,
                    "conversation_id": turn.conversation_id if hasattr(turn, "conversation_id") else "",
                    "seed_entity": entity_map.get(tid, [""])[0] if entity_map and tid in entity_map else "",
                },
            )
            hits.append(hit)
        except Exception as exc:
            logger.debug("Failed to hydrate turn %s: %s", tid, exc)
            continue

    return hits


# ---------------------------------------------------------------------------
# GraphRetriever
# ---------------------------------------------------------------------------


class GraphRetriever:
    """Graph-based retriever using entity-turn index + Personalized PageRank.

    Implements the Retriever protocol. Two-zone retrieval:
    - Zone A: Direct entity mentions via entity_turn_index
    - Zone B: PPR-expanded entity neighborhood → entity_turn_index

    Graceful degradation (AIP-G-02):
    - If graph_store is None, returns [] (no error)
    - If networkx is not installed, Zone B is skipped silently
    - If entity_turn_index is empty, returns [] (graceful: not yet populated)
    - If CorpusTurnStore is None, returns [] (cannot hydrate turns)
    """

    def __init__(
        self,
        graph_store: Any = None,
        corpus_turn_store: Any = None,
        hub_leash: int = 15,
        ppr_alpha: float = 0.85,
        max_zone_b_entities: int = 20,
    ) -> None:
        self._graph_store = graph_store
        self._corpus_store = corpus_turn_store
        self._hub_leash = hub_leash
        self._ppr_alpha = ppr_alpha
        self._max_zone_b_entities = max_zone_b_entities

    @property
    def name(self) -> str:
        return "GraphRetriever"

    async def retrieve(
        self,
        query: RetrievalQuery,
        *,
        budget: RetrievalBudget,
        trace: RetrievalTrace,
    ) -> list[RetrievalHit]:
        """Execute graph-based retrieval with Zone A + Zone B.

        Steps:
        1. Detect entities in the query
        2. Zone A: Get direct-mention turns from entity_turn_index
        3. Zone B: PPR expansion from seed entities → more entities → turns
        4. Merge, deduplicate, score, and return
        """
        if self._graph_store is None or self._corpus_store is None:
            return []

        started = time.monotonic()
        errors: list[str] = []
        zone_a_count = 0
        zone_b_count = 0
        detected_entities: list[str] = []
        expanded_entities: list[str] = []

        try:
            # Step 1: Detect entities in the query
            entity_scores = detect_query_entities(
                query.raw_query, self._graph_store
            )
            detected_entities = [eid for eid, _ in entity_scores]
            entity_confidences = {eid: conf for eid, conf in entity_scores}

            # Populate trace
            trace.detected_entities = detected_entities
            trace.entity_confidences = entity_confidences

            if not detected_entities:
                # No entities found — graph retrieval cannot help
                self._record_trace(
                    trace, started, 0, 0.0, [],
                    detected_entities=[], expanded_entities=[],
                    errors=["no_entities_detected"],
                )
                return []

            # Step 2: Zone A — Direct mentions
            zone_a_hits: list[RetrievalHit] = []
            try:
                turn_entries = self._graph_store.get_turns_for_entities(
                    detected_entities,
                    min_confidence=0.3,
                    limit=budget.max_sources * 2,
                )

                # Apply hub leash
                turn_entries = apply_hub_leash(turn_entries, max_per_entity=self._hub_leash)

                # Build entity_map for hydration
                entity_map: dict[str, list[str]] = {}
                turn_ids_ordered: list[str] = []
                for entry in turn_entries:
                    tid = entry["turn_id"]
                    eid = entry["entity_id"]
                    if tid not in entity_map:
                        entity_map[tid] = []
                        turn_ids_ordered.append(tid)
                    if eid not in entity_map[tid]:
                        entity_map[tid].append(eid)

                # Deduplicate turn_ids while preserving order
                seen_tids: set[str] = set()
                unique_tids: list[str] = []
                for tid in turn_ids_ordered:
                    if tid not in seen_tids:
                        seen_tids.add(tid)
                        unique_tids.append(tid)

                zone_a_hits = await _hydrate_turn_hits(
                    unique_tids,
                    self._corpus_store,
                    base_score=0.85,
                    zone="A",
                    entity_map=entity_map,
                )
                zone_a_count = len(zone_a_hits)

            except Exception as exc:
                msg = f"Zone A failed: {exc}"
                logger.warning(msg)
                errors.append(msg)

            # Step 3: Zone B — PPR Expansion
            zone_b_hits: list[RetrievalHit] = []
            try:
                expanded = self._ppr_expand(detected_entities)
                if expanded:
                    expanded_entities = expanded
                    trace.graph_expanded_entities = expanded

                    # Get turns for expanded entities (excluding already-found Zone A entities)
                    expanded_turn_entries = self._graph_store.get_turns_for_entities(
                        expanded,
                        min_confidence=0.3,
                        limit=budget.max_sources,
                    )
                    expanded_turn_entries = apply_hub_leash(
                        expanded_turn_entries, max_per_entity=self._hub_leash // 2
                    )

                    # Build entity map for Zone B
                    entity_map_b: dict[str, list[str]] = {}
                    b_tids: list[str] = []
                    for entry in expanded_turn_entries:
                        tid = entry["turn_id"]
                        eid = entry["entity_id"]
                        if tid not in entity_map_b:
                            entity_map_b[tid] = []
                            b_tids.append(tid)
                        if eid not in entity_map_b[tid]:
                            entity_map_b[tid].append(eid)

                    # Deduplicate and exclude Zone A turns
                    seen_b: set[str] = set()
                    unique_b_tids: list[str] = []
                    for tid in b_tids:
                        if tid not in seen_b and tid not in seen_tids:
                            seen_b.add(tid)
                            unique_b_tids.append(tid)

                    zone_b_hits = await _hydrate_turn_hits(
                        unique_b_tids,
                        self._corpus_store,
                        base_score=0.65,  # lower base score for expanded
                        zone="B",
                        entity_map=entity_map_b,
                    )
                    zone_b_count = len(zone_b_hits)

            except Exception as exc:
                msg = f"Zone B failed: {exc}"
                logger.debug(msg)  # Zone B failure is non-critical
                errors.append(msg)

            # Step 4: Merge Zone A + Zone B
            all_hits = zone_a_hits + zone_b_hits

            # Deduplicate by turn_id (Zone A takes priority)
            seen_final: set[str] = set()
            unique_hits: list[RetrievalHit] = []
            for hit in all_hits:
                if hit.id not in seen_final:
                    seen_final.add(hit.id)
                    unique_hits.append(hit)

            # Sort by score descending and apply budget cap
            unique_hits.sort(key=lambda h: h.score, reverse=True)
            unique_hits = unique_hits[: budget.max_sources]

            # Re-rank
            for i, hit in enumerate(unique_hits, start=1):
                hit.rank = i

            # Record trace
            self._record_trace(
                trace, started, len(unique_hits),
                unique_hits[0].score if unique_hits else 0.0,
                [h.id for h in unique_hits[:10]],
                detected_entities=detected_entities,
                expanded_entities=expanded_entities,
                errors=errors,
                zone_a_count=zone_a_count,
                zone_b_count=zone_b_count,
            )

            return unique_hits

        except Exception as exc:
            logger.error("GraphRetriever failed: %s", exc)
            self._record_trace(
                trace, started, 0, 0.0, [],
                detected_entities=detected_entities,
                expanded_entities=[],
                errors=[str(exc)],
            )
            trace.fallbacks_triggered.append("graph_retriever_error")
            return []

    def _ppr_expand(self, seed_entity_ids: list[str]) -> list[str]:
        """Personalized PageRank expansion from seed entities.

        Returns expanded entity IDs (excluding seeds) ranked by PPR score.
        Returns [] if networkx is not available or graph is empty.
        """
        try:
            import networkx as nx
        except ImportError:
            return []

        if self._graph_store is None:
            return []

        nodes = self._graph_store.get_all_nodes(min_confidence=0.3)
        edges = self._graph_store.get_all_edges(min_confidence=0.3)

        if not nodes:
            return []

        G: nx.DiGraph = nx.DiGraph()
        for n in nodes:
            G.add_node(n.id, label=n.canonical_name, entity_type=n.entity_type)
        for e in edges:
            if G.has_node(e.source_id) and G.has_node(e.target_id):
                G.add_edge(e.source_id, e.target_id, weight=float(e.weight or 1.0))

        if len(G.nodes) == 0:
            return []

        # Build personalization vector from seed entities
        seed_set = {s.lower() for s in seed_entity_ids}
        personalization: dict[str, float] = {}
        for node_id in G.nodes:
            label = G.nodes[node_id].get("label", node_id)
            if node_id.lower() in seed_set or label.lower() in seed_set:
                personalization[node_id] = 1.0
            else:
                personalization[node_id] = 0.0

        if not any(v > 0 for v in personalization.values()):
            return []

        try:
            scores = nx.pagerank(
                G, personalization=personalization,
                alpha=self._ppr_alpha, max_iter=100,
            )
        except Exception:
            return []

        # Return expanded entities (not seeds) ranked by PPR score
        sorted_nodes = sorted(scores.items(), key=lambda kv: -kv[1])
        results = []
        for node_id, _ in sorted_nodes:
            if node_id.lower() not in seed_set and len(results) < self._max_zone_b_entities:
                results.append(node_id)
        return results

    def _record_trace(
        self,
        trace: RetrievalTrace,
        started: float,
        hit_count: int,
        top_score: float,
        top_hit_ids: list[str],
        detected_entities: list[str],
        expanded_entities: list[str],
        errors: list[str] | None = None,
        zone_a_count: int = 0,
        zone_b_count: int = 0,
    ) -> None:
        """Record retriever trace into the shared RetrievalTrace."""
        elapsed_ms = (time.monotonic() - started) * 1000.0
        degraded = bool(errors) and hit_count > 0
        error_msg = "; ".join(errors) if errors else None

        retriever_trace = RetrieverTrace(
            retriever_name=self.name,
            enabled=True,
            degraded=degraded,
            error=error_msg,
            started_at=datetime.now(),
            finished_at=datetime.now(),
            elapsed_ms=round(elapsed_ms, 2),
            hit_count=hit_count,
            top_score=top_score,
            top_hit_ids=top_hit_ids,
            debug={
                "channel": "graph",
                "detected_entities": detected_entities[:10],
                "expanded_entities": expanded_entities[:10],
                "zone_a_count": zone_a_count,
                "zone_b_count": zone_b_count,
                "hub_leash": self._hub_leash,
                "ppr_alpha": self._ppr_alpha,
                "max_zone_b_entities": self._max_zone_b_entities,
                "zones_used": (
                    ["A"] if zone_a_count > 0 and zone_b_count == 0
                    else ["B"] if zone_b_count > 0 and zone_a_count == 0
                    else ["A", "B"] if zone_a_count > 0 and zone_b_count > 0
                    else []
                ),
                "domains": [],  # populated by orchestrator from hits
            },
        )
        trace.retriever_traces.append(retriever_trace)
        trace.direct_mentions_count = zone_a_count


__all__ = ["GraphRetriever", "detect_query_entities", "apply_hub_leash"]
