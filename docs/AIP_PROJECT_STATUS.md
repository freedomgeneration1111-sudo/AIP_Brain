# AIP Brain — Project Status

**Last Updated:** 2026-06-07  
**Branch:** `moses-aip-brain`  
**Repo:** `github.com/freedomgeneration1111-sudo/AIP_Brain`  
**DEFINER:** B. Moses Jorgensen

---

## Quick Reference

| Metric | Value |
|--------|-------|
| Test baseline | 13 failed (pre-existing), 1227 passed, 23 skipped |
| Retrieval tests | 161 passed (Phases 5.0–5.6), 0 failed |
| Lint status | 0 new errors introduced (all pre-existing) |
| Spec phases completed | Phases 1-4 of AIP_UNIFIED_CHAT_SPEC |
| Retrieval phases completed | Phases 5.0–5.6 (full build plan) |
| Hygiene commits | H-1, H-2, H-3 |
| Bug-fix commits | 3 (Beast .call(), sticky pane, jump FAB) |
| Feature additions | Actor Log, Full Retrieval Architecture |
| Advisory documents | 2 (Retrieval Architecture Memo + Ensemble Review Synthesis) |
| Build plan | AIP_RETRIEVAL_BUILD_MEMO.md (authoritative) — ALL 6 PHASES BUILT |
| Next work | Sprint 5.7 — Integration Testing & Production Hardening |

---

## Architecture Overview

AIP Brain is a sovereign, locally-first knowledge engine with:

- **NiceGUI** web UI with FastAPI backend
- **SQLite** (aiosqlite) with FTS5 for all storage
- **Actor system:** Beast (corpus/analysis), Vigil (evaluation), Sexton (orchestration/tagging)
- **AipContainer** holds shared components: model_provider (ModelSlotResolver), beast, vigil, sexton_actor, stores
- **ModelSlotResolver** uses `.call()` method (not `.chat()`)
- **QueryableEventStore** backed by SQLite — `query(artifact_id, event_type, limit)`

### Current Retrieval Stack (post-retrieval-architecture — Phases 5.0–5.6 COMPLETE)

- **Retriever Protocol** — `@runtime_checkable` with `name: str` and `async def retrieve()`
- **5 conforming retrievers**: FTSRetriever, VectorRetriever, GraphRetriever, WikiRetriever, ProceduralRetriever
- **RetrievalOrchestrator** — RRF fusion, importance weighting, quality gate, auto-retry on NEEDS_MORE_CONTEXT
- **SmartContextPacker** — Budget-aware context assembly with extractive summarization
- **AnswerQualityGate** — Heuristic + optional model-assisted sufficiency checks
- **TraceStore** — SQLite-backed trace persistence with dashboard analytics
- **Entity-Turn Index** — entity_turn_index table with mention scan + backfill
- **GraphRetriever** — Zone A (direct mentions) + Zone B (PPR expansion) with hub leash
- **LLM Query Expansion** — Fast-model-powered query rewriting with structured JSON output
- **Auto-Retry** — NEEDS_MORE_CONTEXT triggers second retrieval round (max 1 retry)
- **Context Compression** — Extractive summarization for long evidence hits
- **161 retrieval tests passing** across Phases 5.0–5.6

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
| 0 | Measurement and Trace | **COMPLETE** | Golden tests (6 queries), retrieval trace instrumentation, baselines |
| 1 | Protocol Substrate | **COMPLETE** | Retriever protocol, RetrievalHit, ContextBudget, RRF, FTS+Vector wrapped |
| 2 | Entity-Turn Index + Coverage | **COMPLETE** | entity_turn_index, mention scan, hub leash, edge densification |
| 3 | GraphRetriever | **COMPLETE** | EntitySeedSelector, PPR, direct mentions, hub control, RRF integration |
| 4 | Wiki/Background Retriever | **COMPLETE** | WikiRetriever, domain selection, budgeted injection, LLM query expansion, VectorRetriever |
| 5 | Context Packer + Quality | **COMPLETE** | SmartContextPacker, ProceduralRetriever, AnswerQualityGate, TraceStore + metrics |
| 6 | Later Intelligence | **COMPLETE** | Auto-retry, extractive summarization, dashboard analytics, model-assisted quality gate |

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
3. Read `STATUS.md` for top-level project status, test baseline, and bug registry
4. Read `ROADMAP.md` for phase-by-phase progress
5. The worklog.md files have full commit-level history if you need detail

### Current Priority

**Sprint 5.7 — Integration Testing & Production Hardening**: The full retrieval architecture (Phases 5.0–5.6) is built and all 161 retrieval tests pass. The next sprint should focus on:

1. **End-to-end integration test** — Wire the full retrieval stack into `ask_pipeline.py` and verify the complete flow from user query → retrieval → quality gate → model synthesis with a live or mock database
2. **Production wiring** — Wire `RetrievalOrchestrator`, `SmartContextPacker`, `AnswerQualityGate`, and `TraceStore` into the FastAPI `app.py` lifespan and `AipContainer`
3. **CLI dashboard command** — Expose `aip retrieval dashboard` CLI command using TraceStore analytics
4. **Performance benchmarks** — Measure retrieval latency with all 5 retrievers active, establish latency budget
5. **Bug-003 fix** — Wire new Sexton actor (DEBT-006) so that entity extraction and graph population run automatically
