# ADR-007: Knowledge Graph Architecture

**Date:** 2026-06-04
**Status:** ACCEPTED
**DEFINER:** B. Moses Jorgensen

## Context

AIP's corpus has 2,743+ turns tagged with domains, importance scores, and
cross-domain bridge tags. The bridge tags (e.g., nbcm→theology_research,
gef_tech→bonded_labor) represent the DEFINER's most original thinking —
connections between domains that most people working in one field don't
make to another. These bridges are currently stored as text tags on turns
but are not navigable as a graph structure.

The DEFINER's use case for a knowledge graph is primarily:
(1) Mind map for complex cross-domain work — interactive navigation of
    connections across NBCM, theology, water science, GEF, bonded labor,
    AIP methodology
(2) Thought provocation — surfacing unexpected entity connections that
    the DEFINER did not consciously notice
(3) Augmented chat retrieval expansion — a query about Balmelli should
    also retrieve EZ water turns because the graph shows the connection

Research report (June 2026) confirmed: at AIP's scale (3,000-50,000 nodes),
SQLite adjacency tables are adequate. Graph databases earn their complexity
only at very large scale with complex traversal algorithms. The "graph as
mind map" use case is achievable with SQLite + NetworkX + Cytoscape.js.

Kuzu (the most promising embedded graph database) was acquired by Apple
in October 2025 and archived. DuckPGQ is emerging as an alternative but
not yet mature.

## Decision

**Storage:** Two SQLite tables in state.db (no new database file):

  graph_nodes(id, entity_type, canonical_name, domain, confidence,
              created_at, updated_at)

  graph_edges(source_id, target_id, relationship_type, bridge_tag,
              confidence, evidence_turn_ids, created_at)

**Entity types (minimal, extensible via DEFINER proposal):**
  PERSON, PROJECT, CONCEPT, PLACE, ORGANIZATION, MANUSCRIPT

**Relationship types (minimal, extensible):**
  WORKS_ON, CONNECTS, LOCATED_IN, FUNDED_BY, AUTHORED, RELATES_TO

**Construction sequence:**
  Phase 1: Bridge tags as seed edges — immediate, no LLM extraction.
  Read corpus_turns where bridges != '[]' → create nodes and edges.
  This gives a real, navigable graph immediately.

  Phase 2: Beast entity extraction on high-importance turns.
  Incremental, triggered by corpus_modified events, processes new
  turns only (not full corpus rebuild).

**Co-reference resolution:** Entity alias table at docs/entity_aliases.md.
Beast reads this before creating nodes. Prevents fragmentation when the
same entity is named differently across turns or time periods (e.g.,
"observation" vs "record formation" in NBCM domain).

**Confidence tiers:**
  > 0.7 — displayed by default
  0.4-0.7 — available on demand, visually distinct
  < 0.4 — stored but hidden (available for deep research)

**Visualization:** Cytoscape.js in GUI. Mind map mode with domain
filtering, entity type filtering, confidence tier filtering.
Node detail panel shows connected corpus turns and wiki article link.

**Independent discovery:** Beast discovers entities from corpus turns
independently, not seeded from DEFINER profile. The graph reflects what
is actually in the corpus, not what the DEFINER thinks is in the corpus.
Unexpected discoveries are the primary value.

## The Bridge Tags Insight

AIP already has the most valuable graph edges. Every corpus_turn with
a non-empty bridges field contains an approved cross-domain connection.
The first graph build simply reads these fields and creates edges. This
means the graph has real content immediately without any LLM extraction.

The 2,743 corpus turns contain approximately 400-800 bridge tag instances
(estimated from domain distribution data). These are the edges that matter
most for the DEFINER's cross-domain synthesis work.

## Failure Mode Mitigation

**Entity explosion:** Strict entity type constraints. Beast proposes new
entity types; DEFINER approves before they become valid.

**Co-reference fragmentation:** Entity alias table is the primary defense.
Beast files co-reference proposals when it encounters ambiguous mentions.

**Maintenance burden:** Incremental extraction (new turns only) keeps
Beast graph work proportional to corpus growth, not corpus size.

**Registry/graph inconsistency:** Domain renames require simultaneous
migration of corpus_turns.primary_domain AND graph_nodes.domain.
This must be enforced as a paired operation, never one without the other.

## Alternatives Considered

**Dedicated graph database (Neo4j, FalkorDB)** — rejected for laptop
deployment. Requires server process, higher operational overhead, overkill
at personal scale.

**Kuzu** — was the ideal choice (embedded, Rust-based, Python API).
Archived October 2025 after Apple acquisition. Not available.

**LightRAG as drop-in** — rejected. LightRAG is a reference architecture,
not a drop-in for AIP. Its auto-approve model is architecturally inferior
to AIP's DEFINER gate. AIP implements LightRAG's core insight (dual-level
graph+vector retrieval) with superior governance.

**Entity seeding from DEFINER profile** — rejected in favor of independent
discovery. Profile-seeded approach gives control but misses the unexpected
connections that are the graph's primary value.

## Consequences

- Graph visualization requires Cytoscape.js frontend investment
- Entity alias table is a new DEFINER maintenance responsibility
- Graph-augmented retrieval adds latency to augmented chat queries
  (graph neighbor expansion before FTS5+vector search)
- Domain renames require paired migration (corpus + graph) — process
  documented in registry update ADR

## Related

- ADR-002: Beast Domain Registry (domain taxonomy feeds graph node domains)
- ADR-003: Beast Context Advisory (graph-augmented retrieval extends this)
- ADR-006: Wiki Architecture (wiki article linked from graph node panel)
- ROADMAP.md Phase 2B
- docs/entity_aliases.md (to be created)
---
## HippoRAG-Inspired Retrieval (Adopted)

Research (June 2026) identified HippoRAG (OSU NLP, NeurIPS 2024 +
ICML 2025) as the conceptually correct retrieval architecture for
AIP's graph. Key adoption decisions:

SCHEMALESS EXTRACTION: Beast performs Open Information Extraction
(OpenIE) on high-importance turns — extracts entity-relation-entity
triples without forcing typed schemas. Relationship types emerge from
the corpus rather than being imposed on it.

PERSONALIZED PAGERANK: Graph retrieval uses PPR seeded on query
entities. Activates relevant subgraph in a single traversal step.
Naturally surfaces cross-domain connections without explicit queries.
Implemented via NetworkX nx.pagerank() with personalization vector.
No new dependencies required.

WHY THIS FITS AIP: PPR traversal is exactly the "show me everything
connected to X" use case the DEFINER requires for cross-domain
synthesis work. The brick kiln → GEF → water science → Balmelli
chain emerges from PPR without the DEFINER knowing to query for it.

SIMILARITY EDGES: After embedding pipeline (Phase 1.4), add edges
between semantically similar entities using vector similarity. This
is why embedding must precede graph construction.

TYPED RELATIONSHIPS (revised): Minimal typed layer retained for
display purposes (WORKS_ON, CONNECTS, AUTHORED etc.) but not used
for retrieval. PPR traverses all edges regardless of type.

VALIDATION CAVEAT: HippoRAG benchmarks are on structured QA datasets,
not personal conversation corpora. Performance on first-person,
cross-domain text with evolving terminology requires calibration.
Plan for a tuning period after initial implementation.

Implementation reference: github.com/osu-nlp-group/hipporag
Papers: arXiv:2405.14831, arXiv:2502.14802
