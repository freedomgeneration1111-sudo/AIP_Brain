# AIP Brain — Work Log

This file tracks all work completed on the AIP Brain project. Each section represents a discrete task or commit batch. Future threads should read this file to understand project state before beginning work.

---

## Project State Summary (as of 2026-06-07)

**Branch:** `moses-aip-brain`  
**Repo:** `github.com/freedomgeneration1111-sudo/AIP_Brain`  
**Test Baseline:** 10 failed (pre-existing), 994 passed, 23 skipped  
**Lint:** 0 new errors introduced in any phase (all pre-existing)

### Completed Phases

| Phase | Description | Commits | Status |
|-------|-------------|---------|--------|
| Phase 1 | Beast soul + model library | 4 | DONE |
| Phase 2 | Unified chat panel, model dropdown, mode picker, Beast pane | 4 | DONE |
| Phase 3 | Cohort dispatch, response cards, Beast comparison, DB table | 4 | DONE |
| Phase 4 | DEFINER profile edit, epistemic flags, Beast pop-out | — | DONE |
| H-1 | Hygiene: actor status display fixes (Vigil/Sexton last_cycle_time) | 1 | DONE |
| H-2 | Hygiene: Vigil logger fix + Sexton graph extraction JSON parse | 1 | DONE |
| H-3 | Hygiene: chat turn → corpus_turns wiring | 1 | DONE |
| Bug fixes | Beast .chat()→.call(), sticky pane, jump FAB | 1 | DONE |
| Actor Log | Event endpoint + compact STATUS widget + /actor-log page | 2 | DONE |
| One-line fixes | Events actor filter multiplier + graph extraction field names | 1 | DONE |
| Retrieval review | Ensemble review synthesis of GraphRAG plan | 1 doc | DONE |
| Build memo | Authoritative retrieval architecture plan | 1 doc + PDF | DONE |
| Doc sync | All docs updated to current state, pushed to GitHub | 1 | DONE |

### Key Architectural Decisions

1. **Unified Retriever Protocol** — All retrieval must go through a single `Retriever` interface returning `RetrievalHit`. No more divergent search paths.
2. **Retrieval is Attention** — Not a feature. The system must handle FTS5, vector, graph/PPR, wiki, procedural, trace, and budget modes under one discipline.
3. **Substrate Before Magic** — Build the retriever protocol, context budget, entity-turn index, and golden tests before adding GraphRAG.
4. **Evidence Status Matters** — Not all retrieval hits are equal. Approved vs. raw vs. model_output vs. superseded affects scoring.
5. **Mention Scan + Hub Leash + Type Filter = One Change** — Never ship mention scan alone.

### Current Build Plan

See `docs/retrieval/AIP_RETRIEVAL_BUILD_MEMO.md` for the authoritative 6-phase retrieval architecture plan:
- Phase 0: Golden tests + trace instrumentation
- Phase 1: Retriever protocol + context budget + RRF fusion
- Phase 2: Entity-turn index + mention scan + hub leash + edge densification
- Phase 3: GraphRetriever (direct mentions + PPR + hub control)
- Phase 4: Wiki/background retriever
- Phase 5: Context packer + answer quality
- Phase 6: Later intelligence (query rewriting, procedural retriever, consolidation)

### Spec Documents

| Document | Location | Status |
|----------|----------|--------|
| AIP_UNIFIED_CHAT_SPEC.md | docs/ | Phases 1-4 IMPLEMENTED |
| AIP_CORPUS_LIFECYCLE_SPEC.md | docs/ | Commits 1-3 DONE; Commits 4-5 DEFERRED to retrieval work |
| AIP_BRAIN_RETRIEVAL_ARCHITECTURE_MEMO.md | docs/retrieval/ | SUPERSEDED by build memo |
| RETRIEVAL_REVIEW_SYNTHESIS.md | docs/retrieval/ | REFERENCE — ensemble review synthesis |
| AIP_RETRIEVAL_BUILD_MEMO.md | docs/retrieval/ | AUTHORITATIVE — current build plan |
| AIP_PROJECT_STATUS.md | docs/ | AUTHORITATIVE — canonical state document |

---

## Detailed Work History

Pre-unified-chat history is preserved at `archive/worklog_historical.md`.

---

Task ID: phase1
Agent: main
Task: Phase 1 of AIP_UNIFIED_CHAT_SPEC — Beast soul + model library (4 commits)

Work Log:
- Commit 1 (5b10b71): Created data/beast_soul.md with bootstrap content from spec
- Commit 2 (8c68252): Added soul text loading + prepended to all 4 LLM call sites in Beast
- Commit 3 (1448a5c): Added enabled_models table to _init_state_db() schema
- Commit 4 (111d170): Created models_library.py with GET/POST/PATCH endpoints

Stage Summary:
- 4 commits, lint: 0 new errors, tests: no regressions
- Key invariants met: AIP-G-02 (soul.md fallback), AIP-G-09 (fetch is user-triggered)

---

Task ID: phase2
Agent: main
Task: Phase 2 of AIP_UNIFIED_CHAT_SPEC — Unified chat panel, model dropdown, mode picker, Beast pane (4 commits)

Work Log:
- Commit 1: Replaced _build_chat_panel with _build_unified_chat_panel, added augment toggle
- Commit 2: Added list_model_library(), model selector from enabled_models
- Commit 3: Added chat mode picker (Engineering/Research/Ideation/Teaching), system_prompt_modifier
- Commit 4: Created beast_scan.py, added Beast pane (320px collapsible sidebar)

Stage Summary:
- 4 commits, lint: 0 new errors, tests: no regressions
- Key: non-blocking Beast scan (fires after BARE response), AIP-G-02 (corpus unavailable state)

---

Task ID: phase3
Agent: main
Task: Phase 3 of AIP_UNIFIED_CHAT_SPEC — Cohort dispatch, response cards, Beast comparison, DB table (4 commits)

Work Log:
- Commit 1: Created chat_cohort.py (parallel asyncio.gather dispatch), cohort_dispatch() in api_client
- Commit 2: Multi-select model selector, cohort cards with accent colors, removed COHORT tab
- Commit 3: Beast cohort comparison (beast_compare.py, POST/GET), soul.md prepended
- Commit 4: Added beast_comparisons table, corpus turn writing for cohort responses

Stage Summary:
- 4 commits, lint: 0 new errors, tests: no regressions
- Key: per-model error isolation, AIP-G-01 (comparison = GENERATED not auto-approved)

---

Task ID: phase4
Agent: main
Task: Phase 4 of AIP_UNIFIED_CHAT_SPEC — DEFINER profile edit, epistemic flags, Beast pop-out

Work Log:
- DEFINER profile editable textarea in Settings
- Epistemic flags as checkboxes stored in config
- Beast pane pop-out in new tab
- Auto-detection toggle for chat mode keywords

Stage Summary:
- Phase 4 completed, test baseline unchanged

---

Task ID: hygiene-h1
Agent: main
Task: Hygiene H-1 — Actor status display fixes

Work Log:
- Fixed actors.py: Vigil _last_eval_time, Sexton _last_cycle_time for status dicts
- Added sexton_actor to /actors/status response

Stage Summary:
- Actor status panel now correctly shows Vigil and Sexton cycle times

---

Task ID: hygiene-h2
Agent: main
Task: Hygiene H-2 — Vigil logger fix + Sexton graph extraction JSON parse

Work Log:
- Fixed vigil.py: structlog get_logger replaces stdlib logging.getLogger
- Added JSON extraction wrapper for graph extraction prompt responses

Stage Summary:
- Vigil no longer crashes on _log() with unexpected kwargs
- Graph extraction now successfully parses JSON from model responses

---

Task ID: hygiene-h3
Agent: main
Task: Hygiene H-3 — Chat turn → corpus_turns wiring

Work Log:
- Fixed auto_save_chat_turn() in ingest.py: builds CorpusTurn and calls upsert_turn()
- Added optional corpus_turn_store parameter to ingest_conversation()

Stage Summary:
- Chat turns, augmented turns, and cohort turns now flow into corpus_turns
- All writes use INSERT OR IGNORE — idempotent and non-blocking

---

Task ID: bugfix-3fix
Agent: main
Task: Three fixes in one commit (e58875f)

Work Log:
- FIX 1: Beast comparison AttributeError — self._beast_provider.chat() → .call()
- FIX 2: Beast pane sticky positioning (position:sticky; top:0; height:100vh)
- FIX 3: Jump-to-input floating button (amber FAB, fixed position)

Stage Summary:
- One commit (e58875f), all three fixes verified

---

Task ID: actor-log
Agent: main
Task: Actor Log feature — event endpoint + compact STATUS widget + standalone /actor-log page (2 commits)

Work Log:
- Commit 1 (0756cfb): Created events.py with GET /api/v1/events, list_events() in api_client
- Commit 2 (f7508f4): Compact actor log widget + standalone /actor-log page with filters + auto-refresh

Stage Summary:
- Event types: beast_health_check, beast_corpus_maintenance, sexton_vigil_start, vigil_eval_complete
- Color-coded dots: beast=amber, sexton=teal, vigil=purple

---

Task ID: oneline-fixes
Agent: main
Task: Two one-line fixes in one commit (315ca17)

Work Log:
- FIX 1: Changed actor filter multiplier from limit * 3 to min(limit * 20, 500)
- FIX 2: Fixed graph extraction field names: entities_created/relationships_created

Stage Summary:
- Actor log returns sufficient results when filtering by actor
- Graph extraction summaries show correct field names

---

Task ID: retrieval-review
Agent: main
Task: Ensemble review of AIP Brain retrieval architecture

Work Log:
- Four AIs reviewed the GraphRAG build plan
- Synthesized into RETRIEVAL_REVIEW_SYNTHESIS.md
- Key finding: substrate must be promoted ahead of graph excitement
- Beast review (code-grounded) was highest signal

Stage Summary:
- Document: docs/retrieval/RETRIEVAL_REVIEW_SYNTHESIS.md
- Key conclusion: Green-light GraphRAG but build retrieval substrate first

---

Task ID: build-memo
Agent: main
Task: Create AIP Retrieval Architecture Build Memo — authoritative plan document

Work Log:
- Synthesized 23-section build memo from comprehensive architecture plan
- Generated PDF and markdown versions
- PDF: 128KB with LiberationSerif/Carlito/DejaVuMono fonts

Stage Summary:
- PDF: docs/retrieval/AIP_Retrieval_Architecture_Build_Memo.pdf (in workspace download/)
- Markdown: docs/retrieval/AIP_RETRIEVAL_BUILD_MEMO.md
- 23 sections, 16 tables, 6-phase build order

---

Task ID: doc-sync
Agent: main
Task: Update all docs and logs to reflect current project state, push to GitHub

Work Log:
- Updated STATUS.md: added unified chat completion, retrieval build plan, current retrieval stack
- Updated ROADMAP.md: added Phase 5 (Retrieval Architecture with 6 sub-phases), updated Phase 2-4 status
- Updated TECH_DEBT.md: added DEBT-009 (4 divergent retrieval paths), DEBT-010 (0.7 importance bias), DEBT-011 (BYOK UI)
- Updated DEBT-002 to reference Retrieval Phase 3 instead of generic Phase 3
- Updated worklog.md: full session history with all phases and fixes
- Updated docs/CHANGELOG.md: added unified chat entries
- Added docs/retrieval/ directory with build memo, architecture memo, review synthesis
- Added docs/AIP_PROJECT_STATUS.md as canonical state document
- Committed and pushed to moses-aip-brain

Stage Summary:
- All project documentation now current and pushed to GitHub
- Key additions: retrieval build plan, project status doc, retrieval-specific tech debt
- ROADMAP now reflects retrieval architecture as Phase 5 with 6 sub-phases
