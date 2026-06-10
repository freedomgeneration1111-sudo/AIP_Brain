# AIP Technical Debt Register

**Owner:** B. Moses Jorgensen  
**Last Updated:** 2026-06-10 (Chunk 4: async-safe storage + datastore truth)

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

## DEBT-003 — MCP Tool Dispatch (Not Runtime-Wired + Fail-Open Risk)

**Status:** Active — non-live governance debt  
**Phase:** 0 (scaffolded), Phase 5 (full implementation)  
**Filed:** 2026-06-04 (pre-existing)

**What was deferred:**  
MCP tool dispatch performs real mutations (ECS transitions, canonical writes, search via Protocols) but is NOT wired into app.py runtime. The `autonomy_gate=None` escape hatch in `server.py:213` silently bypasses gate enforcement for write/admin tools — this must be hardened to fail-closed before MCP is wired.

**Remediation trigger:**  
Phase 5 multi-user deployment. Must harden `autonomy_gate=None` fail-closed before MCP is wired into runtime.

---

## DEBT-007 — CLI Commands Using Blocking sqlite3.connect() (Async-Path Risk)

**Status:** Active — low priority  
**Phase:** Chunk 4 (Async-safe storage)  
**Filed:** 2026-06-10

**What was deferred:**  
Several CLI command files (`cli/init.py`, `cli/backup.py`, `cli/project.py`, `cli/ingest.py`,
`cli/history.py`, `cli/status.py`, `cli/session.py`, `cli/corpus.py`) use synchronous
`sqlite3.connect()` directly. This is acceptable in CLI context (no event loop to block),
but the `admin.py` route at `src/aip/adapter/api/routes/admin.py:308` also uses a blocking
`sqlite3.connect()` — this DOES run in the async FastAPI event loop and should be converted
to use the store layer.

**Why deferred:**  
CLI commands run synchronously (no event loop) so blocking sqlite3.connect() is correct there.
The admin.py route is the only remaining async-path offender and it's read-only with a short
query duration. The risk is low but should be addressed when the admin routes are next touched.

**Remediation trigger:**  
Next time admin.py routes are modified, convert the direct sqlite3.connect() call to use
the existing store methods (entity_store, event_store, etc.) or add a dedicated admin
query method to the appropriate store.

**Related work:**  
- Chunk 4 — resolved the same pattern in AcePlaybook, Beast, and VSS probe
- `src/aip/adapter/api/routes/admin.py:308` — remaining async-path blocking call

---

## DEBT-004 — GraphStore Connection Churn

**Status:** Resolved — Chunk 4 confirmed aiosqlite conversion is already complete  
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

**Chunk 4 status:** GraphStore has been converted to aiosqlite with persistent connection
pattern (initialize() + _get_conn() + close()). This debt item is resolved.

**Related work:**  
- `src/aip/adapter/graph_store.py` — now uses aiosqlite with ReadPoolMixin
- DEBT-005 below — also resolved by the same conversion

---

## DEBT-005 — GraphStore Protocol Missing + Synchronous sqlite3

**Status:** Resolved — Chunk 4 confirmed both gaps are closed  
**Phase:** 2B Knowledge Graph  
**Filed:** 2026-06-06

**What was deferred:**  
Two related gaps:

1. **No `GraphStore` Protocol in `foundation/protocols/storage.py`.** All other stores in the
   adapter layer (VectorStore, LexicalStore, CanonicalStore, ArtifactStore, etc.) have Protocol
   declarations for dependency injection and structural typing. GraphStore was added in Phase 2B
   without a Protocol, making it un-swappable and invisible to the DI system.

2. **`adapter/graph_store.py` uses synchronous `sqlite3`** rather than `aiosqlite`. All other
   async-path SQLite stores use aiosqlite.

**Chunk 4 status:** Both gaps are resolved. GraphStore Protocol exists in
`foundation/protocols/storage.py`, and the implementation uses aiosqlite with
persistent connection + ReadPoolMixin. The store is wired into AipContainer and
registered in the store registry.

**Related work:**  
- `src/aip/adapter/graph_store.py` — now uses aiosqlite with ReadPoolMixin
- `src/aip/foundation/protocols/storage.py` — GraphStore Protocol added
- `src/aip/adapter/api/app.py` — GraphStore wired in lifespan startup

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
