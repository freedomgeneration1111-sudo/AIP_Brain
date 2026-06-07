# AIP Brain — Build Worklog

**Branch:** `moses-aip-brain`
**DEFINER:** B. Moses Jorgensen
**Last Updated:** 2026-06-07

---

## Sprint 5.6 — Autonomy, Quality & Observability

**Date:** 2026-06-07
**Status:** COMPLETE

### Work Log
- Read all key source files: ask_pipeline.py, retrieval_trace.py, context_packer.py, answer_quality_gate.py, trace_store.py, orchestrator.py, procedural_retriever.py, __init__.py, tests
- Added retry fields to RetrievalTrace schema: retry_triggered, retry_reason, retry_round, retry_strategies_tried, retry_quality_improved, retry_first_status, retry_first_scores
- Added auto-retry logic to RetrievalOrchestrator with 4 strategies: LLM expansion, relaxed domain, broader entity seeding, increased max_sources
- Extracted _execute_retrieval_round() method for code reuse between initial and retry retrieval
- Added extractive summarization to SmartContextPacker: _split_sentences, _score_sentence, extractive_summarize functions
- SmartContextPacker now uses extractive summarization for long evidence hits (sentence scoring by query/entity overlap)
- Added ContextSection.compressed_count and ContextPacket.compressed_hits tracking
- Added TraceStore dashboard analytics: get_dashboard_summary(), query_retry_stats(), query_retriever_stats()
- Added retry columns to TraceStore SQLite schema with safe ALTER TABLE migration
- Added model-assisted sufficiency check to AnswerQualityGate: evaluate_model_assisted(), _run_model_sufficiency_check()
- Added QualityGateConfig.enable_model_assisted and model_assisted_slot options
- Refactored ask_pipeline.py: added _RetrievalResult dataclass, _search_sources_with_trace(), wired trace persistence, SmartContextPacker direct hit usage, quality warning in system prompt
- Updated __init__.py exports with extractive_summarize
- Wrote 38 comprehensive tests in test_phase56_retrieval.py
- All 161 retrieval tests pass (Phase 5.0-5.6)

### Stage Summary
- P0 (Auto-Retry): Complete — NEEDS_MORE_CONTEXT triggers retry with strategy escalation, trace records retry info
- P1 (Context Compression): Complete — Extractive summarization picks most relevant sentences instead of hard truncation
- P2 (Trace Dashboard): Complete — Dashboard summary, retry stats, retriever contribution stats all queryable
- P3 (Quality Gate Enhancements): Complete — Optional model-assisted sufficiency check for MARGINAL cases
- Key files modified: retrieval_trace.py, orchestrator.py, context_packer.py, answer_quality_gate.py, trace_store.py, ask_pipeline.py, __init__.py
- Key files created: tests/test_phase56_retrieval.py

---

## Sprint 5.5 — Context Quality & Reliability

**Date:** 2026-06-07
**Status:** COMPLETE

### Work Log
- Read all existing codebase files: protocols, schemas, retrievers, orchestrator, ask_pipeline, tests
- Created SmartContextPacker with budget-aware, structured context assembly (4 sections: evidence, wiki, procedural, graph)
- Created ProceduralRetriever implementing Retriever protocol with procedural query detection and scoring
- Created AnswerQualityGate with configurable thresholds and 4 evaluation dimensions
- Created TraceStore for SQLite-backed trace persistence with quality metrics
- Updated RetrievalBudget with procedural_allocation (0.05) and max_procedures (3)
- Added ContextQualityStatus enum (SUFFICIENT, MARGINAL, NEEDS_MORE_CONTEXT, EMPTY)
- Added quality gate and procedural fields to RetrievalTrace
- Updated RetrievalOrchestrator with enable_procedural_retrieval toggle, quality_gate integration, procedural_tokens in budget usage
- Updated ask_pipeline._assemble_context() with structured formatting and SmartContextPacker support
- Updated ask_pipeline._search_sources() with ProceduralRetriever and Quality Gate wiring
- Updated __init__.py exports for all new components
- Fixed test_retrieval_trace.py budget allocation assertions
- Wrote 56 comprehensive tests in test_phase55_retrieval.py
- All 161 retrieval tests pass across all test files

### Stage Summary
- 4 new files: context_packer.py, procedural_retriever.py, answer_quality_gate.py, trace_store.py
- 5 modified files: retrieval_trace.py, orchestrator.py, ask_pipeline.py, __init__.py, test_retrieval_trace.py
- 1 new test file: test_phase55_retrieval.py (56 tests)

---

## Sprint 5.4 — VectorRetriever + LLM Query Expansion + Semantic Wiki

**Date:** 2026-06-07
**Status:** COMPLETE

### Work Log
- Created VectorRetriever implementing Retriever protocol (768-dim embedding similarity via SqliteVssVectorStore)
- Added LLM-powered query expansion with structured JSON output (entities, query_variants, likely_domains, query_mode)
- Enhanced WikiRetriever with semantic matching via embedding similarity for domain article selection
- Added trace and configuration polish for retriever-level configuration
- Wrote comprehensive tests in test_phase54_retrieval.py
- All retrieval tests pass

### Stage Summary
- New files: vector_retriever.py, test_phase54_retrieval.py
- Modified files: query_expansion.py, wiki_retriever.py, __init__.py

---

## Sprint 5.3 — Query Expansion + Wiki + Entity-Turn Writes

**Date:** 2026-06-07
**Status:** COMPLETE
**Git commit:** 40ccfd8

### Work Log
- Implemented LLM-powered query expansion (fast model, structured JSON output)
- Created WikiRetriever with domain selection from seeds + hits, budgeted multi-wiki injection
- Added Sexton entity-turn writes during tagging cycles
- Configurable hub leash tuning with weights and caps
- Trace field for hub_penalty_applied

### Stage Summary
- Commit: 40ccfd8 Phase5.3

---

## Sprint 5.2 — Entity-Turn Index + GraphRetriever

**Date:** 2026-06-07
**Status:** COMPLETE
**Git commit:** d2d235f

### Work Log
- Created entity_turn_index table (entity_id, turn_id, confidence, source)
- Built GraphRetriever with Zone A (direct mentions) + Zone B (PPR expansion via networkx PageRank)
- Implemented EntitySeedSelector with exact/alias/acronym/phrase/FTS5/token-overlap matching
- Hub leash formula: weight / log(degree + 1), configurable caps
- Backfill from evidence_turn_ids_json, mention scan, staleness prune

### Stage Summary
- Commit: d2d235f Phase5.2

---

## Sprint 5.1 — Protocol Substrate

**Date:** 2026-06-07
**Status:** COMPLETE
**Git commit:** 1a1949d

### Work Log
- Created Retriever protocol (@runtime_checkable with name + retrieve())
- Created RetrievalHit, RetrievalList, RetrievalTrace, RetrievalQuery, RetrievalBudget dataclasses
- Created RRF fusion service (k=60, importance/confidence/evidence modifiers)
- Created FTSRetriever (first conforming retriever)
- Created RetrievalOrchestrator with parallel dispatch + fusion

### Stage Summary
- Commit: 1a1949d Phase5.1

---

## Sprint 5.0 — Measurement and Trace

**Date:** 2026-06-07
**Status:** COMPLETE
**Git commit:** f1bc145

### Work Log
- Created golden test suite (tests/retrieval_goldens/) with 6 YAML queries
- Created retrieval trace instrumentation
- Created baseline evaluation script (eval_retrieval.py)
- FTS5 sanitization improvements (stop-word filtering, AND-join, single-quote handling)

### Stage Summary
- Commit: f1bc145 Phase5.0
- Follow-up fix: e7145be (single quote in FTS5 sanitization)
- Follow-up fix: 5ab9c45 (strengthen FTS5 sanitization — stop-word filtering + AND-join)

---

## Earlier Work (Pre-Retrieval Architecture)

See `archive/worklog_historical.md` and `worklog_session.md` for commits before Sprint 5.0.
Key commits: unified chat phases 1-4, hygiene H-1/H-2/H-3, bug fixes, actor log, docs sync.
