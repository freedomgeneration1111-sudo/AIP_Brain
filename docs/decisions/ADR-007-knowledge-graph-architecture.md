# ADR-007: Knowledge Graph Architecture

**Date:** 2026-06-04
**Status:** PROPOSED — not yet implemented
**DEFINER:** B. Moses Jorgensen

---

## Context

After the wiki layer (ADR-006) compiles domain knowledge into article form,
a third compiled layer is needed: a graph that represents relationships between
entities, concepts, and domains. The wiki answers "what is X?" The graph answers
"how does X relate to Y?" and enables bridge detection across domains — AIP's
most distinctive capability.

The June 2026 parallel research report (Claude, GPT, DeepSeek, Grok in parallel)
examined the landscape of graph-augmented retrieval systems: GraphRAG (Microsoft),
LightRAG, HippoRAG, and Neo4j-based approaches. This ADR records the decisions
that emerged from that research and subsequent architectural discussion.

## Decision

Implement a knowledge graph layer (Phase 2B) as the third compiled layer,
sitting above the wiki (Phase 2A) and above the raw corpus (Phase 1).

### Graph Implementation: SQLite Adjacency + Entity Aliases

**Do not use Neo4j or a dedicated graph database at this stage.**

Rationale: AIP is local-first and sovereignty-oriented. Neo4j introduces a
heavyweight database server process, vendor dependency, and operational complexity
disproportionate to the graph size AIP will generate in Phase 2B. SQLite is
already AIP's persistence layer. An adjacency table in SQLite is sufficient for
graphs up to hundreds of thousands of nodes and edges — well beyond AIP's
Phase 2B scope.

```sql
-- Entity nodes
CREATE TABLE entities (
    id          INTEGER PRIMARY KEY,
    canonical   TEXT NOT NULL,        -- canonical name (e.g. "B. Moses Jorgensen")
    domain      TEXT,                 -- primary domain tag
    type        TEXT,                 -- person | project | concept | place | manuscript
    description TEXT,                 -- short description (Beast-generated, DEFINER-approved)
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Alias resolution (co-reference table)
CREATE TABLE entity_aliases (
    alias       TEXT NOT NULL,
    entity_id   INTEGER REFERENCES entities(id),
    PRIMARY KEY (alias)
);

-- Edges
CREATE TABLE entity_edges (
    id          INTEGER PRIMARY KEY,
    from_id     INTEGER REFERENCES entities(id),
    to_id       INTEGER REFERENCES entities(id),
    relation    TEXT NOT NULL,        -- "is_author_of" | "references" | "builds_on" | "bridges_to" etc.
    strength    REAL DEFAULT 1.0,
    source_turn_ids TEXT,            -- JSON array of turn IDs that evidence this edge
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Co-reference Resolution: Entity Alias Table

The first deliverable of Phase 2B is `docs/entity_aliases.md` — a canonical
co-reference resolution table seeded manually (DEFINER), extended by Beast.

The alias table solves the problem: Moses is referenced in the corpus as
"Moses", "Musa", "Musa Messih", "B. Moses Jorgensen", "the user", "I". Without
co-reference resolution, the graph treats these as different entities. Every
distinct entity in the graph needs its canonical name plus all known aliases.

**Seed entries for entity_aliases.md:**

```
# Entity Alias Table v1.0
# Format: canonical_name | aliases

B. Moses Jorgensen | Moses, Musa, Musa Messih, the user, the DEFINER
AIP | AI Poiesis, aip_brain, AIP Brain, the system
Freedom Generation School | FG School, Freedom Gen, FGS
Komal Jorgensen | Komal, the principal
GEF | Generational Energy Formations, GEF white paper
NBCM | Null-Boundary Constraint Manifold, null-boundary framework
EZ water | exclusion zone water, structured water, fourth phase water
Beast | Beast actor, the Beast, beast agent
Sexton | Sexton orchestrator, sexton agent
Zaman | [local ministry contact]
Irfan | [local ministry contact]
Jorgensen Service Company | JSC
```

This table is extended as Beast encounters new references in corpus analysis.
Beast proposes additions; DEFINER approves. No auto-addition of aliases.

### Retrieval: HippoRAG-Inspired Personalized PageRank

The June 2026 research identified HippoRAG's Personalized PageRank (PPR)
approach as superior to naive graph traversal for knowledge retrieval:

- Given a query, identify query entity nodes (via alias resolution)
- Run PPR from those seed nodes across the graph
- High-PPR nodes are semantically related to the query even without
  direct edge connection
- Combine PPR scores with FTS5 + vector retrieval scores via RRF

This approach handles "how does NBCM relate to EZ water?" by traversing
edges rather than requiring a document that explicitly contains both terms.
It is AIP's bridge detection mechanism — finding non-obvious cross-domain
connections that Moses cannot retrieve by keyword alone.

**AIP's competitive differentiation vs. GraphRAG/LightRAG:**

Microsoft GraphRAG auto-generates entities and relations without human review.
The graph is entirely machine-authored. AIP's DEFINER gate means the canonical
entity table and alias resolution are human-validated. This produces a smaller
but higher-accuracy graph. For a personal knowledge engine, accuracy of
entity identity is more important than volume.

### Visualization: Cytoscape.js (Phase 4 GUI)

Graph visualization is deferred to Phase 4.1 (GUI enrichment). Target:
Cytoscape.js embedded in the NiceGUI frontend. Nodes colored by domain,
edges labeled by relation type, PPR-score filtering to reduce noise.

## Alternatives Considered

**Neo4j** — rejected. Server process, vendor dependency, operational overhead.
SQLite adjacency is sufficient for Phase 2B scale and maintains AIP's
local-first architecture. Migrate to Neo4j only if graph exceeds 500K+ edges
or if advanced graph query patterns (path finding, cycle detection) become
necessary.

**Auto-generated graph (GraphRAG / LightRAG pattern)** — rejected for canonical
layer. Machine-generated entity extraction without DEFINER review produces
entity proliferation (same person under 20 names, concepts split arbitrarily).
Beast can propose entities and edges; only DEFINER-approved entries enter
the canonical graph. This is slower but produces a trustworthy graph.

**Vector-only cross-domain retrieval (no graph)** — insufficient. Vector
similarity finds semantically similar text. It does not find structural
relationships between entities across domains. A graph edge "NBCM bridges_to
EZ water" is not discoverable by vector search unless a document explicitly
makes that connection. PPR traversal finds it via the graph topology.

**Dedicated graph embedding (node2vec, GraphSAGE)** — deferred. Too complex
for Phase 2B. Revisit when graph has 10K+ nodes and simple PPR is insufficient.

## Consequences

- Phase 2B requires Phase 2A (wiki) to be substantially complete first —
  the wiki provides the entity seed population
- Entity alias table requires initial DEFINER seeding (~2-4 hours of manual
  work to produce a comprehensive starting set)
- Beast entity extraction needs a new actor method (entity_propose) that
  presents candidate entities and edges for DEFINER review
- The PPR retrieval path requires implementing scipy or networkx PPR in the
  context advisory layer — adds a Python dependency
- Graph quality is gated by alias resolution quality. Poor co-reference
  resolution produces a fragmented, misleading graph
- Visualization (Phase 4) is needed to make the graph useful for exploration;
  before Phase 4, the graph is primarily useful as a retrieval mechanism,
  not as a browsable artifact

## Related

- ADR-001: Turn-Level Corpus Ingestion (raw layer)
- ADR-002: Beast Domain Registry (entity domain classification)
- ADR-006: Beast Wiki Architecture (compiled layer above raw corpus)
- docs/entity_aliases.md (co-reference resolution table)
- ROADMAP.md: Phase 2B
- Research basis: June 2026 parallel research report on GraphRAG/LightRAG/HippoRAG
