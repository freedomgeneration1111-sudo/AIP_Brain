# AIP Technical Debt Register

**Owner:** B. Moses Jorgensen  
**Last Updated:** 2026-06-10

Each entry records a deliberate deferral — what was skipped, why, and what triggers remediation.

---

## DEBT-001 — `--merge-nodes aip_methodology aip` (Graph Node Alias Cleanup)

**Status:** Deferred  
**Phase:** 2B Knowledge Graph  
**Filed:** 2026-06-05

**What was deferred:**  
The bridge tag `aip_methodology->theology_research` references a domain node `aip_methodology`
that was renamed to `aip` in the domain registry before the knowledge graph was built.
`aip corpus graph --build-from-bridges` creates a node for `aip_methodology` as-is (from the
raw bridge tag data) because bridge tags in corpus_turns.bridges reflect the tag text at ingestion
time, not the current registry.

A `--merge-nodes aip_methodology aip` CLI command would merge the orphan node into the canonical
`aip` domain node, redirecting all edges to the target.

**Why deferred:**  
Only 5 bridge-tagged turns exist currently (sparse corpus). The `aip_methodology` node will be
one orphan node with 1 edge. The blast radius is minimal and the correct action is to retag
affected turns after the full corpus retag, then re-run `--build-from-bridges` with clean data.
Building a `--merge-nodes` command now would add complexity for a problem that self-corrects
after corpus retag.

**Remediation trigger:**  
After full corpus retag (2,649 currently untagged turns), re-run `aip corpus graph --build-from-bridges
--force`. If `aip_methodology` nodes persist at that point, implement `aip corpus graph --merge-nodes
<source_id> <target_id>` in GraphStore + CLI.

**Related work:**  
- `aip corpus graph --build-from-bridges` (current implementation creates as-is)
- `docs/entity_aliases.md` (canonical name registry — does not yet resolve old domain names)
- ROADMAP Phase 2B, Phase 3 (incremental graph updates)

---

## DEBT-002 — Full PPR Expansion in Augmented Chat (Phase 3 Deferral)

**Status:** Deferred  
**Phase:** 2B Knowledge Graph → Phase 3  
**Filed:** 2026-05-05

**What was deferred:**  
The full HippoRAG Personalized PageRank (PPR) expansion path in `chat.py` was deferred.
Current implementation in `_get_graph_neighbors()` does direct domain adjacency lookup only
(1-hop neighbors of the active domain). The `GraphRetriever.expand_query_via_graph()` method
with full PPR seeded on query entities is implemented but not wired into the chat path.

**Why deferred:**  
Query entity extraction from free-text requires either a fast NER pass or Beast LLM call —
both add latency to the chat response path. The constraint "DO NOT make graph retrieval block
the chat response path" applies. Domain neighbor lookup is synchronous and sub-millisecond.
Full PPR is valuable but the entity extraction step is the blocker.

**Remediation trigger:**  
Phase 3: Wire query entity extraction as a background pre-fetch (fire-and-forget before the
synthesis call, cache results by session). If the graph has >500 nodes and the extraction
pipeline can complete in <200ms, promote to full PPR path.

**Related work:**  
- `src/aip/orchestration/graph_retrieval.py` — `GraphRetriever.expand_query_via_graph()` is ready
- `src/aip/adapter/api/routes/chat.py` — `_get_graph_neighbors()` (current 1-hop implementation)

---

## DEBT-003 — MCP Tool Dispatch (Scaffold)

**Status:** Deferred  
**Phase:** 0 (scaffolded), Phase 5 (full implementation)  
**Filed:** 2026-06-04 (pre-existing)

**What was deferred:**  
MCP tool dispatch returns scaffold responses: `aip_search` returns empty, `aip_artifact_approve`
returns hardcoded True, other tools return `ok=True`. No real operation is dispatched.

**Remediation trigger:**  
Phase 5 multi-user deployment. Requires real stdio/SSE MCP transport + dispatching to live
search/approval/config services.

---

## DEBT-004 — GraphStore Connection Churn

**Status:** Active — low priority  
**Phase:** 2B Knowledge Graph  
**Filed:** 2026-06-06

**What was deferred:**  
`adapter/graph_store.py` opens and closes a new `sqlite3.connect()` on every method call
(upsert_node, upsert_edge, get_neighbors, get_all_nodes, etc.). For the current graph size
(28-36 nodes, 5-17 edges) this is not a performance problem, but it is architecturally inconsistent
with the rest of the adapter layer which uses connection pools or persistent async connections
(aiosqlite via SqliteConcurrencyManager).

**Why deferred:**  
The graph is read-heavy and small. Per-call connection overhead is microseconds at this scale.
DEBT-005 (aiosqlite conversion) is the correct fix and subsumes this one — both will be resolved
together in BUG-004.

**Remediation trigger:**  
Resolved as part of BUG-004 (GraphStore aiosqlite conversion). No separate action needed.

**Related work:**  
- `src/aip/adapter/graph_store.py` — docstring explicitly notes "Synchronous (no aiosqlite)"
- DEBT-005 below — the aiosqlite conversion resolves connection churn as a side effect
- BUG-004 in STATUS.md bug registry

---

## DEBT-005 — GraphStore Protocol Missing + Synchronous sqlite3

**Status:** Active — blocks BUG-004  
**Phase:** 2B Knowledge Graph  
**Filed:** 2026-06-06

**What was deferred:**  
Two related gaps:

1. **No `GraphStore` Protocol in `foundation/protocols/storage.py`.** All other stores in the
   adapter layer (VectorStore, LexicalStore, CanonicalStore, ArtifactStore, etc.) have Protocol
   declarations for dependency injection and structural typing. GraphStore was added in Phase 2B
   without a Protocol, making it un-swappable and invisible to the DI system.

2. **`adapter/graph_store.py` uses synchronous `sqlite3`** rather than `aiosqlite`. All other
   async-path SQLite stores use aiosqlite. The graph store's docstring acknowledges this explicitly:
   "Synchronous (no aiosqlite)". This works today because graph routes call the store in sync
   context, but it introduces a blocking call risk in the async FastAPI event loop as graph
   operations grow.

**Why deferred:**  
The graph was delivered as a working Phase 2B MVP. Adding the Protocol and converting to aiosqlite
are correctness/architecture improvements, not urgent fixes. The blocking risk is low at current
graph size. The work was captured as BUG-004 for the next bug-fix pass.

**Remediation steps (BUG-004):**  
1. Add `GraphStore` Protocol to `src/aip/foundation/protocols/storage.py` with methods:
   `upsert_node`, `upsert_edge`, `get_node`, `get_neighbors`, `get_all_nodes`, `get_all_edges`,
   `get_stats`, `delete_node`, `delete_edge`, `initialize`.
2. Convert `src/aip/adapter/graph_store.py` from `sqlite3` to `aiosqlite` (all methods become
   `async`, connection opened once in `initialize()` and reused).
3. Add `container.graph_store` field to `AipContainer` and instantiate in app.py lifespan.
4. Update `src/aip/adapter/api/routes/graph.py` and `routes/chat.py` to use
   `container.graph_store` instead of constructing `GraphStore` inline.
5. Update `__all__` in `foundation/protocols/storage.py` to include `GraphStore`.

**Related work:**  
- `src/aip/adapter/graph_store.py` — current synchronous implementation
- `src/aip/foundation/protocols/storage.py` — all other store Protocols defined here
- `src/aip/adapter/api/routes/graph.py` — currently constructs GraphStore inline
- `src/aip/adapter/api/routes/chat.py` — also constructs GraphStore inline (see also DEBT-002, BUG-002)
- DEBT-004 above — aiosqlite conversion resolves connection churn

---

## DEBT-006 — `actors/sexton.py` Not Wired into app.py (CRITICAL)

**Status:** Active — BUG-003, highest priority for maintenance mode  
**Phase:** 3 Actor Intelligence  
**Filed:** 2026-06-06

**What was deferred:**  
ADR-011 (2026-06-06) drove a code refactor that built a full-maintenance Sexton actor at
`src/aip/orchestration/actors/sexton.py` (1,341 lines, 5 operations: tagging, embedding,
wiki generation, graph extraction, failure classification). This was committed in `7fe15a2`.

However, `app.py` was NOT updated to wire the new actor. The lifespan still:
- Imports `orchestration/sexton/sexton.py` (the old failure-classifier-only Sexton, ~220 lines)
- Instantiates it into `container.sexton`
- Calls `run_classification_cycle()` every 300s

The new `actors/sexton.py::Sexton.run_cycle()` is never called. As a result:
- **Automatic corpus tagging is not running**
- **Automatic embedding is not running**
- **Automatic wiki generation is not running**
- **Automatic graph extraction is not running**
- Only failure classification runs (the old Sexton)

**Why deferred:**  
The refactor was done in incremental commits focused on the code. The app.py wiring update
was identified as a separate task and captured here. It was not a silent omission — the
docstring in `actors/sexton.py` explicitly references the wiring gap.

**Impact:**  
This is the single highest-priority debt item. Until it is resolved:
- The full embedding pass cannot complete (2,716 turns unembedded)
- Hybrid retrieval quality is limited by low embedding coverage (~1.8%)
- Wiki generation and graph extraction are not running automatically

**Remediation steps (BUG-003):**  
In `src/aip/adapter/api/app.py`, replace the Sexton instantiation block and scheduler block:

1. **Instantiation** — import `actors/sexton.Sexton` instead of `sexton/sexton.Sexton`;
   pass the full store set: `sexton_provider`, `corpus_turn_store`, `embedding_provider`,
   `vector_store`, `artifact_store`, `ecs_store`, `event_store`, `trace_store`,
   `lexical_store`, `config`.

2. **Scheduler** — change `await container.sexton.run_classification_cycle()` to
   `await container.sexton.run_cycle()`.

3. **Interval** — `run_cycle()` reads `SextonConfig.classification_interval_seconds`
   (same field, same default 300s).

4. **Docstring fix** — update `actors/sexton.py` docstring to reference
   `container.sexton` (not `container.sexton_actor`).

**Related work:**  
- `src/aip/orchestration/actors/sexton.py` — the full-maintenance Sexton (built, unwired)
- `src/aip/orchestration/sexton/sexton.py` — the old failure-classifier Sexton (currently wired)
- `src/aip/adapter/api/app.py` — the wiring location
- `src/aip/adapter/api/dependencies.py` — `container.sexton` field (Any type)
- ADR-011 — the architectural decision that drove the refactor
- ROADMAP Phase 3.3 — Sexton status

---
