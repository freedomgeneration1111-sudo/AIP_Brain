# AIP_Brain Sprint 5.8→5.9 Worklog

---
Task ID: 1
Agent: main
Task: Sprint 5.8 — Completion & Debt Reduction

Work Log:
- Explored full codebase: ask_pipeline.py, retrieval_orchestrator.py, smart_context_packer.py, graph_retrieval.py, graph_store.py, trace_store_adapter.py, retrieval_dashboard.py, wiki.py, chat.py, ask.py
- Identified all deprecated code paths: _search_sources(), _assemble_context(), _source_type_matches(), _chunk_to_source_ref()
- Identified all callers of deprecated functions: chat.py, ask.py, test_ask.py
- Ran existing 36 tests (all pass) as baseline

---
Task ID: 1-a
Agent: main
Task: Deliverable 1 — Remove deprecated code paths

Work Log:
- Removed _search_sources() (~140 lines) from ask_pipeline.py
- Removed _assemble_context() (~30 lines) from ask_pipeline.py
- Removed _source_type_matches() and _chunk_to_source_ref() helper functions
- Removed Chunk import (no longer needed in ask_pipeline)
- Replaced with _hit_type_matches() for RetrievalHit-based source filtering
- Removed legacy fallback in _search_sources_with_trace() (was: fallback to _search_sources on orchestrator failure; now: return empty results)
- Removed legacy fallback in ask() (was: _assemble_context when packed_context is None; now: use packed_context or default message)
- Updated ask.py route: replaced _search_sources with _search_sources_with_trace
- Updated chat.py route: replaced _search_sources + _assemble_context with _search_sources_with_trace + SmartContextPacker
- Updated test_ask.py: removed deprecated imports, replaced TestSourceFiltering with _hit_type_matches, replaced TestContextAssembly with SmartContextPacker tests, fixed stale NO_PROJECT status assertion

Stage Summary:
- All deprecated functions removed from ask_pipeline.py
- All callers updated to use orchestrator + packer path
- Zero legacy fallback paths remain

---
Task ID: 1-b
Agent: main
Task: Deliverable 2 — Wire GraphRetriever as first-class channel

Work Log:
- Added _graph_retriever channel registration in _register_retriever_channels()
- Uses GraphRetriever from orchestration/graph_retrieval.py with PPR-based entity expansion
- Entity extraction from query: capitalized words first, then meaningful words as fallback
- Graph store sourced from stores.graph_store (new attribute) or created from db_path
- Toggleable via OrchestratorConfig.enable_graph (default: False, opt-in)
- Returns RetrievalHit instances with source_channel="graph" and metadata type "graph_entity"

Stage Summary:
- Graph channel fully wired in orchestrator
- Participates in parallel dispatch and RRF fusion when enabled
- Compatible with existing GraphStore (SQLite-backed)

---
Task ID: 1-c
Agent: main
Task: Deliverable 3 — Wire WikiRetriever and ProceduralRetriever

Work Log:
- Added _wiki_retriever channel registration: searches beast_wiki artifacts with ECS state filtering (APPROVED/GENERATED), scores by query term overlap + domain match
- Added _procedural_retriever channel registration: searches procedural_guide and compiled_knowledge artifacts, scores by query overlap + procedural keyword signals
- Both channels toggleable via OrchestratorConfig (enable_wiki, enable_procedural, default: False)
- Both participate in RRF fusion when enabled
- Added graph_store attribute to AskStores (with default=None for backward compat)
- Added graph_store creation in create_ask_stores()
- Updated AskStores construction in ask.py and chat.py routes to pass graph_store

Stage Summary:
- Wiki and Procedural channels fully wired
- All 6 channels (FTS, Vector, Corpus, Graph, Wiki, Procedural) registered in orchestrator
- AskStores now supports graph_store for efficient graph retrieval

---
Task ID: 1-d
Agent: main
Task: Deliverable 4 — Improve Retrieval Dashboard

Work Log:
- Enhanced GET /api/v1/retrieval/dashboard with:
  - p99 latency estimate
  - Per-channel health list with dispatch counts
  - Recent traces (last 10) with per-channel timing, hit counts, verdicts
  - Top failing queries (worst quality-gate outcomes)
  - Latency trend data
- Added GET /api/v1/retrieval/traces endpoint for detailed trace inspection
- Added GET /api/v1/retrieval/channels endpoint for per-channel health metrics
- Implemented _get_recent_traces() helper extracting from EventStore
- Implemented _get_top_failing_queries() helper prioritizing NO_RESULTS and NEEDS_MORE_CONTEXT

Stage Summary:
- Dashboard now provides meaningful operational visibility
- 4 API endpoints: /dashboard, /traces, /channels, /stats

---
Task ID: 1-e
Agent: main
Task: Write Sprint 5.8 tests

Work Log:
- Created tests/test_sprint58_completion.py with 25 new tests covering:
  - Deprecated code removal verification
  - _hit_type_matches filter logic
  - Graph channel dispatch and RRF fusion
  - Wiki channel dispatch
  - Procedural channel dispatch
  - All 6 channels together in parallel dispatch
  - Selective channel enable/disable
  - AskStores graph_store parameter
  - No legacy fallback in _search_sources_with_trace
  - SmartContextPacker as sole context assembly path
  - OrchestratorConfig toggle defaults

Stage Summary:
- 25 new Sprint 5.8 tests
- 36 existing Sprint 5.7 tests (still pass)
- 15 test_ask.py tests (updated, still pass)
- 31 test_retrieve_for_synthesis tests (unchanged, still pass)
- Total: 107 tests pass with zero regressions

---
Task ID: 2
Agent: main
Task: Sprint 5.9 — Intelligence & Evaluation

Work Log:
- Read full codebase state: retrieval_orchestrator.py, ask_pipeline.py, smart_context_packer.py, graph_retrieval.py, graph_store.py, channel_selector, entity_extractor, retrieval_eval
- Ran existing 61 Sprint 5.7/5.8 tests (all pass) as baseline

---
Task ID: 2-a
Agent: main
Task: Deliverable 1 — Improve Graph Channel Entity Extraction

Work Log:
- Created src/aip/orchestration/entity_extractor.py with EntityExtractor class
- Implemented extract_noun_phrases(): regex-based extraction of multi-word capitalised phrases, single proper nouns (excluding sentence starters), and quoted strings
- Implemented fuzzy_match_graph_entities(): matches candidate terms against GraphStore nodes using case-insensitive substring + alias matching with configurable threshold
- Implemented EntityExtractor class with 4 strategies: noun_phrase, graph_fuzzy, hybrid (default), llm
- EntityExtractorConfig: strategy, min_entity_length, max_candidates, use_graph_fuzzy, fuzzy_match_threshold, use_llm_fallback
- extract() for sync (noun_phrase/graph_fuzzy/hybrid), extract_async() for LLM fallback
- Updated ask_pipeline.py _graph_retriever to use EntityExtractor (hybrid strategy) instead of simple capitalized-word extraction

Stage Summary:
- Entity extraction is now configurable and significantly more robust
- Supports noun phrases, graph-fuzzy matching, hybrid, and LLM extraction strategies
- Graph channel uses hybrid strategy by default

---
Task ID: 2-b
Agent: main
Task: Deliverable 2 — Per-Channel Budget Allocation

Work Log:
- Added to OrchestratorConfig: max_hits_per_channel (global default), fts_max_hits, vector_max_hits, graph_max_hits, wiki_max_hits, procedural_max_hits, corpus_max_hits
- Added get_channel_max_hits() method with resolution: channel-specific → global default → 0 (unlimited)
- Added per-channel budget enforcement in _execute_retrieval_round() BEFORE RRF fusion
- Added max_hits_per_channel field to PackerConfig (packing stage)
- SmartContextPacker.pack() now enforces per-channel limits after RRF fusion (second gate)

Stage Summary:
- Channels can be limited per-retrieval to prevent dominance
- Two-level enforcement: orchestrator (pre-fusion) + packer (post-fusion)
- All limits are opt-in (0 = unlimited by default)

---
Task ID: 2-c
Agent: main
Task: Deliverable 3 — Adaptive/Smart Channel Selection (Light)

Work Log:
- Created src/aip/orchestration/channel_selector.py with ChannelSelector class
- Implemented analyze_query(): detects entity signals, procedural signals, wiki signals
- Regex patterns: _ENTITY_PATTERNS (capitalised phrases/proper nouns), _PROCEDURAL_PATTERNS (how-to/guide/tutorial), _WIKI_PATTERNS (what is/define/explain)
- ChannelSelector.select() returns ChannelSelectionResult with enable_graph/wiki/procedural suggestions
- ChannelSelector.apply_to_config() merges suggestions into OrchestratorConfig with explicit_channels override protection
- Integrated into ask_pipeline.py _search_sources_with_trace() via auto_channel_selection parameter (default: True)
- Selector only enables channels, never disables — explicit user settings take precedence

Stage Summary:
- Basic automatic channel enabling based on query characteristics
- Entity signals → Graph; procedural signals → Procedural; wiki signals → Wiki
- Rule-based, fast, predictable, no LLM calls
- Overridable via explicit_channels or auto_channel_selection=False

---
Task ID: 2-d
Agent: main
Task: Deliverable 4 — Retrieval Quality Evaluation Harness (Foundation)

Work Log:
- Created src/aip/orchestration/retrieval_eval.py with full evaluation harness
- Metric functions: compute_recall_at_k(), compute_precision_at_k(), compute_mrr(), compute_entity_coverage()
- GoldenQuery dataclass: query, relevant_ids, expected_entities, domain, tags
- RetrievalEvalHarness class: run() and run_from_file() methods
- EvalResult/QueryEvalResult with serialization (to_dict(), to_json())
- load_golden_queries() and create_default_golden_queries() utilities
- Created tests/retrieval_goldens/golden_queries.json with 5 sample golden queries

Stage Summary:
- Runnable evaluation harness produces Recall@k, Precision@k, MRR, entity coverage
- Golden queries loaded from JSON files (tests/retrieval_goldens/)
- Results serializable to JSON for regression tracking
- Handles retriever failures gracefully

---
Task ID: 2-e
Agent: main
Task: Write Sprint 5.9 tests

Work Log:
- Created tests/test_sprint59_intelligence_eval.py with 63 new tests covering:
  - Noun-phrase extraction (6 tests)
  - Graph-fuzzy matching (4 tests)
  - EntityExtractor strategies (7 tests)
  - OrchestratorConfig per-channel budget (5 tests)
  - Per-channel budget enforcement in orchestrator (3 tests)
  - SmartContextPacker per-channel limits (2 tests)
  - Query analysis signal detection (5 tests)
  - ChannelSelector auto-enable logic (8 tests)
  - Metric computation (10 tests)
  - Golden query loading (3 tests)
  - RetrievalEvalHarness (4 tests)
  - Channel selection + orchestrator integration (2 tests)
  - Per-channel budget + RRF integration (2 tests)
  - Per-channel budget balanced retrieval (2 tests)

Stage Summary:
- 63 new Sprint 5.9 tests
- 61 existing Sprint 5.7/5.8 tests (still pass)
- 31 test_ask.py tests (still pass)
- Total: 155 tests pass with zero regressions
