# ADR-007: Knowledge Graph Architecture

**Date:** 2026-06-04
**Status:** ACCEPTED
**DEFINER:** B. Moses Jorgensen

## Context

AIP's corpus has 2,775+ turns tagged with domains, importance scores,
and cross-domain bridge tags. The bridge tags represent the DEFINER's
most original thinking — connections between domains that most people
working in one field don't make to another. These bridges are stored
as text tags on turns but are not navigable as a graph structure.

The DEFINER's use case for a knowledge graph:
(1) Mind map for complex cross-domain work — interactive navigation
(2) Thought provocation — surfacing unexpected entity connections
(3) Augmented chat retrieval expansion via graph traversal

Research (June 2026): At AIP's scale (3,000-50,000 nodes), SQLite
adjacency tables are adequate. Kuzu (the most promising embedded
graph database) was acquired by Apple in October 2025 and archived.

## Decision

**Storage:** Two SQLite tables in state.db:

  graph_nodes(id, entity_type, canonical_name, domain,
              confidence, created_at, updated_at)

  graph_edges(source_id, target_id, relationship_type,
              bridge_tag, confidence, evidence_turn_ids, created_at)

**Entity types:** PERSON, PROJECT, CONCEPT, PLACE, ORGANIZATION, MANUSCRIPT

**Relationship types (minimal):**
  WORKS_ON, CONNECTS, LOCATED_IN, FUNDED_BY, AUTHORED, RELATES_TO

**Construction sequence:**
  Phase 1: Bridge tags as seed edges — immediate, no LLM extraction.
  Read corpus_turns.bridges → create nodes and edges. Real navigable
  graph exists immediately.

  Phase 2: Beast OpenIE entity extraction on high-importance turns.
  Schemaless — extract entity-relation-entity triples without forcing
  typed schemas. Relationship types emerge from corpus.

**HippoRAG-Inspired Retrieval (Adopted):**
  Personalized PageRank (PPR) seeded on query entities activates
  relevant subgraph in single traversal step. Naturally surfaces
  cross-domain connections without explicit queries. Implemented via
  NetworkX nx.pagerank() with personalization vector.

  The brick kiln → GEF → water science → Balmelli chain emerges from
  PPR without the DEFINER knowing to query for it.

  Similarity edges added after embedding pipeline using vector
  similarity — why embedding must precede full graph construction.

**Co-reference:** Entity alias table at docs/entity_aliases.md.
Beast reads before creating nodes. Prevents fragmentation when same
entity appears under different names across turns or time periods.

**Confidence tiers:**
  >0.7 — displayed by default
  0.4-0.7 — available on demand, visually distinct
  <0.4 — stored but hidden

**Visualization:** Cytoscape.js in GUI. Mind map mode with domain,
entity type, and confidence filtering.

**Independent discovery:** Beast discovers entities from corpus turns
independently, not seeded from DEFINER profile. Unexpected discoveries
are the primary value.

## The Bridge Tags Insight

AIP already has the most valuable graph edges. Every corpus_turn with
non-empty bridges field contains an approved cross-domain connection.
The first graph build simply reads these fields and creates edges.
Approximately 400-800 bridge tag instances exist in current corpus.

## Failure Mode Mitigation

**Entity explosion:** Strict entity type constraints.
**Co-reference fragmentation:** Entity alias table is primary defense.
**Maintenance burden:** Incremental extraction (new turns only).
**Registry/graph inconsistency:** Domain renames require simultaneous
migration of corpus_turns AND graph_nodes — enforced as paired operation.

## Validation Caveat

HippoRAG benchmarks are on structured QA datasets, not personal
conversation corpora. Performance on first-person, cross-domain text
with evolving terminology requires calibration. Plan for tuning period.

Implementation reference: github.com/osu-nlp-group/hipporag
Papers: arXiv:2405.14831 (NeurIPS 2024), arXiv:2502.14802 (ICML 2025)

## Alternatives Considered

**Neo4j / FalkorDB** — rejected. Server process, operational overhead,
overkill at personal scale.
**Kuzu** — ideal but archived October 2025 (Apple acquisition).
**LightRAG as drop-in** — rejected. Auto-approve model inferior to
AIP's DEFINER gate. AIP implements LightRAG's core insight with
superior governance.
**Entity seeding from DEFINER profile** — rejected. Independent
discovery surfaces unexpected connections that are the graph's
primary value.

## Related
- ADR-002: Beast Domain Registry
- ADR-003: Beast Context Advisory
- ADR-006: Wiki Architecture
- ROADMAP.md Phase 2B
- docs/entity_aliases.md
