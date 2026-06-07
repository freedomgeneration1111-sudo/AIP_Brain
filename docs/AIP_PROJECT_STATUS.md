# AIP Brain — Project Status

**Last Updated:** 2026-06-07  
**Branch:** `moses-aip-brain`  
**Repo:** `github.com/freedomgeneration1111-sudo/AIP_Brain`  
**DEFINER:** B. Moses Jorgensen

---

## Quick Reference

| Metric | Value |
|--------|-------|
| Test baseline | 10 failed (pre-existing), 994 passed, 23 skipped |
| Lint status | 0 new errors introduced (all pre-existing) |
| Spec phases completed | Phases 1-4 of AIP_UNIFIED_CHAT_SPEC |
| Hygiene commits | H-1, H-2, H-3 |
| Bug-fix commits | 3 (Beast .call(), sticky pane, jump FAB) |
| Feature additions | Actor Log (event endpoint + compact widget + /actor-log page) |
| Advisory documents | 2 (Retrieval Architecture Memo + Ensemble Review Synthesis) |
| Build plan | AIP_RETRIEVAL_BUILD_MEMO.md (authoritative) |
| Next work | Phase 0 of retrieval build plan (golden tests + trace) |

---

## Architecture Overview

AIP Brain is a sovereign, locally-first knowledge engine with:

- **NiceGUI** web UI with FastAPI backend
- **SQLite** (aiosqlite) with FTS5 for all storage
- **Actor system:** Beast (corpus/analysis), Vigil (evaluation), Sexton (orchestration/tagging)
- **AipContainer** holds shared components: model_provider (ModelSlotResolver), beast, vigil, sexton_actor, stores
- **ModelSlotResolver** uses `.call()` method (not `.chat()`)
- **QueryableEventStore** backed by SQLite — `query(artifact_id, event_type, limit)`

### Current Retrieval Stack (pre-retrieval-architecture)

- `corpus_turns_fts` in `state.db` (auto-synced via triggers)
- `fts_index` in `lexical.db` (manually synced)
- `SqliteVssVectorStore` (768-dim, nomic-embed-text)
- `GraphStore` (853 entities, 430 edges) — decorative in retrieval, not structural
- `_search_sources()` has 4 divergent code paths with inconsistent scoring
- L2 retrieval module (`orchestration/retrieval.py`) exists but is never called

### Key Files Modified Across All Work

| File | Changes |
|------|---------|
| `gui/shell.py` | Unified chat panel, Beast pane (sticky, collapsible), cohort cards, mode picker, jump FAB, actor log widget, /actor-log page |
| `gui/api_client.py` | cohort_dispatch(), beast_compare(), beast_scan(), list_events(), list_model_library() |
| `src/aip/orchestration/actors/beast.py` | Soul.md injection, _run_cohort_comparison() (uses .call() not .chat()) |
| `src/aip/adapter/api/app.py` | Registered routes: models_library, beast_scan, beast_compare, chat_cohort, events |
| `src/aip/cli/init.py` | Added enabled_models table, beast_comparisons table |
| `src/aip/adapter/api/routes/actors.py` | sexton_actor + vigil last_cycle_time exposure |
| `src/aip/adapter/api/routes/events.py` | NEW — GET /api/v1/events endpoint |
| `src/aip/adapter/api/routes/ingest.py` | auto_save_chat_turn → corpus_turn_store.upsert_turn() wiring |

### Key Files Created

| File | Purpose |
|------|---------|
| `data/beast_soul.md` | Beast's personality and epistemic stance |
| `src/aip/adapter/api/routes/models_library.py` | Model library CRUD endpoints |
| `src/aip/adapter/api/routes/beast_scan.py` | GET /beast/scan endpoint |
| `src/aip/adapter/api/routes/beast_compare.py` | POST /beast/compare + GET /beast/comparison/{session_id} |
| `src/aip/adapter/api/routes/chat_cohort.py` | POST /chat/cohort (parallel dispatch) |
| `src/aip/adapter/api/routes/events.py` | GET /api/v1/events endpoint |

---

## Spec Documents — Status Tracker

| Document | Location | Status | Notes |
|----------|----------|--------|-------|
| AIP_UNIFIED_CHAT_SPEC.md | /upload/ | **IMPLEMENTED** | Phases 1-4 complete |
| AIP_CORPUS_LIFECYCLE_SPEC.md | /upload/ | **PARTIAL** | Commits 1-3 done (actor status, chat→corpus, pipeline wiring). Commits 4-5 (graph extraction JSON fix, wiki edit surface) deferred to retrieval work |
| AIP_BRAIN_RETRIEVAL_ARCHITECTURE_MEMO.md | /upload/ | **SUPERSEDED** | By build memo. Contains 7 theses about retrieval architecture. Still valid as reference. |
| RETRIEVAL_REVIEW_SYNTHESIS.md | /upload/ | **REFERENCE** | Ensemble review. Key lesson: reviews that read code disagreed with reviews that read the memo. |
| AIP_RETRIEVAL_BUILD_MEMO.md | /upload/ | **AUTHORITATIVE** | The current build plan. 23 sections, 6-phase build order. |
| AIP_Retrieval_Architecture_Build_Memo.pdf | /download/ | **AUTHORITATIVE** | PDF version of build memo. 128KB. |

---

## Invariants (Must Preserve)

| ID | Rule | Current Compliance |
|----|------|--------------------|
| AIP-G-01 | No auto-approve. DEFINER must explicitly approve all canon. | Compliant — wiki edits create REVIEWED artifacts, Beast comparisons are GENERATED |
| AIP-G-02 | Never fake. If data unavailable, show honest error state. | Compliant — Beast pane shows "corpus unavailable", soul.md fallback is graceful |
| AIP-G-09 | No cloud egress except user-triggered model library fetch. | Compliant — all storage is local SQLite + networkx |
| Non-blocking | Beast scan/comparison never blocks chat response. | Compliant — fires after response or in parallel |
| Idempotent | All corpus writes use INSERT OR IGNORE/REPLACE. | Compliant — upsert_turn() is idempotent |
| DEFINER sovereignty | No autonomous promotion to canon. | Compliant — all approvals are DEFINER-gated |

---

## Retrieval Build Plan — Phase Tracker

| Phase | Name | Status | Key Deliverables |
|-------|------|--------|-----------------|
| 0 | Measurement and Trace | **NOT STARTED** | Golden tests, trace instrumentation, baselines |
| 1 | Protocol Substrate | **NOT STARTED** | Retriever protocol, RetrievalHit, ContextBudget, RRF, FTS+Vector wrapped |
| 2 | Entity-Turn Index + Coverage | **NOT STARTED** | entity_turn_index, mention scan, hub leash, edge densification |
| 3 | GraphRetriever | **NOT STARTED** | EntitySeedSelector, PPR, direct mentions, hub control, RRF integration |
| 4 | Wiki/Background Retriever | **NOT STARTED** | WikiRetriever, domain selection, budgeted injection |
| 5 | Context Packer + Quality | **NOT STARTED** | Diversity, source caps, evidence status, answer modes |
| 6 | Later Intelligence | **NOT STARTED** | Query rewriting, procedural retriever, consolidation, adaptation |

---

## Known Issues / Technical Debt

1. **Two FTS5 indexes** — `corpus_turns_fts` (state.db, auto-synced) and `fts_index` (lexical.db, manual). Can drift. Fix: consolidate under unified search_index (Retrieval Phase 1).
2. **768-dim embedding lock-in** — No embedding model migration path. Vectors store no model_id or dimension metadata. Fix: store model_id + dim per vector (cheap insurance, can slot in anytime).
3. **L2 retrieval module unused** — `orchestration/retrieval.py` has RerankWeights (semantic 0.60, recency 0.15, authority 0.15, frequency 0.10) but nobody calls it. Will be superseded by Retriever protocol.
4. **Graph is decorative** — GraphStore used for display (Beast scan neighbors, chat graph injection) but plays no role in retrieval scoring. Fix: GraphRetriever in Phase 3.
5. **0.7 importance threshold** — Sexton only extracts graph edges at importance ≥ 0.7. Everyday-but-crucial entities (like Komal) have weak graph connectivity. Fix: edge densification in Phase 2.
6. **Pre-existing test failures** — 10 tests fail consistently. These are not from any work in this branch.
7. **Graph extraction field names** — Fixed in commit 315ca17 (entities_created/relationships_created, not entities_extracted/edges_extracted).

---

## How to Resume Work

### Starting a New Thread

1. Read this file (`AIP_PROJECT_STATUS.md`) for current state
2. Read `AIP_RETRIEVAL_BUILD_MEMO.md` for the authoritative build plan
3. State which phase you're starting: "We're starting Phase N of the retrieval build plan"
4. The worklog.md has full commit-level history if you need detail

### Current Priority

**Phase 0: Measurement and Trace** — Create golden tests (`tests/retrieval_goldens/`), retrieval trace instrumentation, before/after CLI or debug endpoint, and current baseline measurements. This is the foundation that validates all subsequent retrieval work.
