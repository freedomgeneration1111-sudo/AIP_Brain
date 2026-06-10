# AIP_Brain Worklog

---
Task ID: sprint-9
Agent: main
Task: Sprint 9 — Corpus Ingestion and Memory Reliability Sprint

Work Log:
- Explored codebase: found two parallel ingestion pipelines (legacy artifact+chunk vs new CorpusTurn), existing parsers (Claude, ChatGPT, Markdown, Plaintext), CorpusTurnStore with FTS5, Sexton/Beast/Vigil actors, API routes
- Schema changes: added content_hash, source_path, doc_version, embed_fail_count, last_embed_error to CorpusTurn dataclass and CorpusTurnStore DDL
- Added compute_content_hash(), make_document_conversation_id() helper functions
- Added 5 new DDL migrations for Sprint 9 columns, 3 new indexes (content_hash, source_path, embed_fail_count)
- Created document_parser.py: parse_markdown_document(), parse_text_document(), parse_document_file() — converts documents into CorpusTurns with source_model="document"
- Created corpus_ingest_pipeline.py: unified ingestion pipeline with ingest_file_to_corpus() and ingest_directory_to_corpus()
- Pipeline features: format auto-detection, dedup via content_hash, re-ingest detection (doc_version increment), provenance metadata, event recording
- Added 9 new CorpusTurnStore methods: check_content_hash, find_by_source_path, increment_doc_version, record_embed_failure, clear_embed_failure, get_backfill_queue, count_embed_failures, get_corpus_audit, get_corpus_status
- Updated corpus CLI: enhanced `aip corpus ingest` with --source-model document, --recursive, directory support; added `aip corpus status`, `aip corpus audit`, `aip corpus backfill`, `aip corpus list --unembedded/--failed/--document`
- Added 4 new API endpoints: GET /corpus/status, GET /corpus/audit, GET /corpus/backfill-queue, POST /corpus/ingest
- Updated Sexton: _persist_embedding_failures now records per-turn failures in CorpusTurnStore; mark_embedded now clears embed failure state
- Created scripts/dogfood_seed_corpus.sh: ingests AIP's own docs (architecture, ADRs, governance, roadmap, config, status, API reference, tech debt)
- Created test_corpus_ingestion_reliability.py with 60 tests across 10 test classes
- All 180 tests pass (60 new + 120 existing)

Stage Summary:
- Canonical corpus ingest flow: CLI and API share one pipeline, supports markdown/text/PDF/conversations/directories
- Document identity and dedup: content_hash auto-computed, re-ingest skips unchanged content, tracks doc_version for changed content
- Provenance guarantees: every turn knows source_path, section_heading, offset, ingest_timestamp, content_hash
- Embedding backfill reliability: failures tracked in CorpusTurnStore (embed_fail_count, last_embed_error), backfill queue prioritizes failures, clear on success
- Corpus audit commands: `aip corpus status` (quick), `aip corpus audit` (comprehensive), `aip corpus backfill` (retry failures), `aip corpus list --unembedded/--failed`
- Dogfood seed corpus script ready for AIP's own documentation

---
Task ID: 1
Agent: main
Task: Sprint 5.18 — Improve maintainability, observability, and performance of storage/graph layers

Work Log:
- Performed AI fingerprint audit across beast.py, sexton.py, and 7 CLI modules
- Cleaned up 14+ restating comments, 2 "Step N:" noise comments, and 3 verbose docstring patterns
- Added StoreHealthMixin to 6 remaining stores: SqliteBudgetStore, SqliteProjectStore, SqliteSessionStore, ReviewQueueStore, AutonomyGateImpl, auth SqliteSessionStore
- Migrated auth SqliteSessionStore from per-call connection pattern to persistent connection with WAL mode and recovery
- Added upsert_nodes_batch() and upsert_edges_batch() to GraphStore
- Refactored Sexton graph extraction loop to use batch operations, reducing per-entity transaction overhead
- Added _execute_with_retry() with bounded exponential backoff for SQLITE_BUSY errors in GraphStore
- Applied retry to all GraphStore write operations (upsert_node, upsert_edge, delete_node, batch methods, log_turn_extracted)
- Enhanced StoreHealthMixin with operation-level metrics: last_op timestamp, total_ops count, rolling avg latency
- Added _health_track_operation() calls to all GraphStore write methods
- Updated health endpoint to include autonomy_gate and auth_session_store in store_health
- Created test_store_health_integration.py with 6 tests for new mixin stores
- Added 18 new tests to test_graph_store.py: batch ops (6), BUSY retry (3), latency tracking (2), health metrics (1 updated)
- All 50 graph/health/E2E tests pass; 1290 total suite tests pass

Stage Summary:
- All 6 targeted stores now expose connection health metrics via StoreHealthMixin
- GraphStore has batch upsert methods and Sexton uses them in extraction path
- SQLITE_BUSY transient errors handled with bounded retries (max 3, exponential backoff 50ms base)
- StoreHealthMixin now tracks operation-level metrics: last op time, total ops, avg latency
- 14+ AI fingerprint patterns removed from beast.py and CLI modules
- Auth session store fully migrated to persistent connection pattern with WAL mode

---
Task ID: sprint-5.28
Agent: main
Task: Sprint 5.28 — Alerting integration, admin endpoints, weekly rollup, lifespan smoke test

Work Log:
- Verified Sexton batch-reduction alert auto-wiring (already wired from Sprint 5.25)
- Enhanced alert context data in Sexton _auto_tune_batch_size() to include window_size, min/max batch size
- Added AlertManager.get_alert_history() method with filtering by alert_type, severity, since, limit
- Created GET /vigil/quality/alerts endpoint with full filtering support and config status exposure
- Created GET /vigil/quality/retention endpoint for retention status visibility
- Created POST /vigil/quality/retention/rollup endpoint for manual daily/weekly rollup triggering
- Created GET /vigil/quality/retention/rollup-stats endpoint for rollup statistics
- Implemented VigilQualityStore.run_weekly_rollup() — aggregates daily rollups older than N weeks into weekly summaries
- Implemented VigilQualityStore.get_rollup_stats() — returns daily/weekly rollup counts and time ranges
- Added weekly_rollup_age_weeks parameter to VigilQualityStore constructor (default 4)
- Updated app.py lifespan to pass weekly_rollup_age_weeks from config and schedule weekly rollup task
- Updated app.py shutdown to cancel weekly rollup scheduler
- Added Query parameter resolution for direct test calls to alerting endpoints
- Created test_sprint528_operator_tooling.py with 27 tests across 6 test classes
- All Sprint 5.27 tests (38) continue to pass
- All Sprint 5.28 tests (27) pass
- Pushed commit e2df1d3 to GitHub

Stage Summary:
- Batch size reductions in Sexton automatically generate alerts with full context
- /vigil/quality/alerts endpoint surfaces history, delivery failures, and config status
- /vigil/quality/retention and /vigil/quality/retention/rollup provide admin visibility and control
- /vigil/quality/retention/rollup-stats shows daily/weekly rollup statistics
- Weekly rollup aggregation reduces long-term storage while preserving trend data
- Lifespan smoke test validates full operational wiring on startup

---
Task ID: sprint-5.39
Agent: main
Task: Sprint 5.39 — Strengthen offline support, make learned model persistent, improve circuit breaker adaptability, move toward native protocol-level features

Work Log:
- Implemented Deliverable 1: Service Worker Offline Cache
  - Enhanced SW blob code with Cache API, IndexedDB offline queue, fetch event handler (cache-first for assets, network-first for API), sync event handler
  - Added offline action queueing in dashboard JS with banner, replay on reconnect
  - Added offline_cache_enabled config to AlertConfig
- Implemented Deliverable 2: Transition Probability Persistence + Retraining
  - Added save_transition_probabilities, load_transition_probabilities, record_retraining_event, get_retraining_events to AlertHistoryStore
  - Schema migration v6→v7 with model_retraining_events table
  - Added persist_transition_model, load_transition_model, retrain_transition_model, check_retrain_needed to AlertManager
  - Added transition_persistence_enabled, retrain_interval_seconds, retrain_after_n_alerts config fields
- Implemented Deliverable 3: Circuit Breaker Auto-Tuning
  - Added compute_cb_auto_tune_threshold, get_cb_effective_threshold, update_cb_auto_tune, get_cb_auto_tune_status to AlertManager
  - Modified _check_circuit_breaker to use effective threshold
  - Added auto-tune config fields and dashboard display elements
- Implemented Deliverable 4: Delivery Receipt Polling
  - Added start_receipt_polling, stop_receipt_polling, poll_email_delivery_status, update_email_delivery_status, get_enhanced_delivery_receipts, get_delivery_polling_status
  - Modified _record_delivery_receipts to track email "sent" status when polling enabled
  - Added polling config fields (delivery_receipt_polling_enabled, email_read_tracking_enabled, email_delivery_webhook_url)
- Implemented Deliverable 5: Native WebSocket Per-Message Deflate
  - Added ws_native_permessage_deflate_enabled config
  - Added set_ws_permessage_deflate_negotiated, compress_ws_message_native_aware, decompress_ws_message_native_aware, get_native_deflate_status
  - Modified WS endpoint to support compression="deflate" negotiation with ?compression=deflate query param
  - Updated all dashboard WS URLs to include compression=deflate parameter
- Created test_sprint539_resilience_intelligence.py with 72 tests across 6 test classes
- All 72 Sprint 5.39 tests pass
- All 61 Sprint 5.38 tests continue to pass

Stage Summary:
- Dashboard works meaningfully offline with queued actions that replay on reconnect
- Learned prediction model persists across restarts via SQLite and retrains periodically
- Circuit breaker can automatically adjust its threshold based on historical patterns
- Delivery status (including email where possible) is visible per channel via polling
- WebSocket uses native permessage-deflate compression when available, with graceful fallback
- All changes are backward-compatible with safe defaults

---
Task ID: sprint-6.4
Agent: main
Task: Sprint 6.4 — Retrieval Quality Validation and Project Closure (final sprint)

Work Log:
- Performed comprehensive code review of retrieval evaluation harness, hybrid retrieval implementation, documentation state, ADR structure, and Vigil integration
- Deliverable 1: Retrieval Evaluation Harness improvements
  - Created scripts/update_golden_ids.py to map golden queries to real corpus turn IDs
  - Updated tests/retrieval_goldens/golden_queries.json with corpus-mapped IDs
  - Added --mode flag (hybrid/fts-only/all) to aip eval retrieval CLI
  - Added config_snapshot to EvalResult capturing mode, channels, weights, and k
  - Added baseline save to docs/retrieval_benchmark_baseline.json alongside eval_results/baseline.json
  - Bumped eval_harness_version from "5.12" to "6.4"
- Deliverable 2: Channel Weight Tuning
  - Created scripts/retrieval_weight_tuning.py grid search over vector_weight [0.2-0.8]
  - Added [retrieval.channel_weights] section to config/aip.config.toml
  - Wired config channel weights into OrchestratorConfig via ask_pipeline.py _search_sources_with_trace()
  - Updated aip eval retrieval CLI to read channel weights from config in hybrid mode
- Deliverable 3: Vigil Retrieval Quality Gate
  - Added _run_retrieval_quality_sample() method to Vigil actor
  - Samples 3-5 golden queries every N cycles, computes precision@5
  - Alerts on degradation via _alert_manager if precision@5 drops below threshold
  - Added retrieval quality config fields to VigilConfig
  - Added [vigil.retrieval_quality] section to aip.config.toml
  - Updated app.py VigilConfig creation to flatten nested config
- Deliverable 4: Documentation & Project Closure
  - Updated STATUS.md: marked maintenance mode, updated retrieval quality section, refreshed corpus stats
  - Updated ROADMAP.md: marked Phase 0/1/2/3 as COMPLETE, added Maintenance Mode section, updated version history
  - Updated TECH_DEBT.md: clarified DEBT-006 as highest priority, updated DEBT-006 impact description
  - Wrote ADR-013: documented Sprint 6.4 decisions, scope exclusions, and consequences
  - Created docs/Maintenance_Protocol.md with operational procedures for maintenance phase

Stage Summary:
- Retrieval evaluation harness supports FTS5-only vs Hybrid comparison via --mode flag
- Channel weights are configurable through aip.config.toml and read by the orchestrator
- Vigil provides automated retrieval quality degradation detection
- All major documentation is accurate and current
- ADR-013 written; project explicitly marked as entering maintenance mode
- docs/Maintenance_Protocol.md created with emergency procedures and regular maintenance schedule
- Sprint 6.4 exit criteria addressed: harness exists, hybrid comparison supported, documentation current, ADR-013 written
- Note: Hybrid ≥20% precision@5 improvement target requires full embedding pass (currently ~1.8% coverage)

---
Task ID: alpha-doc-refresh
Agent: main
Task: Bring all documentation up to date for alpha test release

Work Log:
- Audited full codebase state: git history, test counts, config.toml, all 14 ADRs, existing documentation
- Identified key discrepancies: stale dates, wrong license, missing config sections, outdated architecture descriptions
- Updated STATUS.md: added alpha release framing, expanded known limitations for testers, fixed eval harness description
- Updated ROADMAP.md: added alpha release version history entry
- Updated CHANGELOG.md: added comprehensive Sprint 6.0–6.4 entries, fixed Unreleased header, renamed old Phase 9 entry
- Updated docs/README.md: fixed license (MIT not proprietary), added alpha status badge, refreshed quick start, added documentation index table
- Updated CONTRIBUTING.md: fixed repo URL (AIP_Brain not aip), added alpha maintenance mode guidance
- Updated docs/ARCHITECTURE.md: rewrote Sexton/Beast/Vigil actor descriptions to reflect ADR-011 refactor, added retrieval pipeline diagram and description
- Updated docs/CONFIGURATION.md: added [retrieval.channel_weights], [vigil.retrieval_quality], [models.*] slots (5), [read_pool], [alerting], [config_hot_reload], [database] sections; updated [embedding] defaults to openai_compatible; fixed architecture_revision to 6.4
- Updated docs/DEPLOYMENT_GUIDE.md: fixed [embedding] default from "fake" to "openai_compatible"
- Updated docs/DEVELOPER_GUIDE.md: added CLI commands section, eval CLI section, updated config key references
- Updated docs/API_REFERENCE.md: added Graph, Corpus, Vigil Quality, Retrieval Dashboard sections; updated hot-reloadable keys table; updated phase reference to Sprint 6.4
- Updated DOGFOOD_READY.md: rewrote header with alpha test release caveats, what works well, known limitations
- Updated docs/implementation_status.md: added alpha release summary header with Sprint 6.0–6.4 changes, updated date and scaffolding percentage
- Updated docs/Maintenance_Protocol.md: added alpha test release framing, tester guidance
- Updated config/aip.config.toml: fixed architecture_revision from "5.2" to "6.4"

Stage Summary:
- 16 documentation files updated for alpha test release
- Key fixes: license corrected (MIT), repo URL corrected, architecture revision aligned (6.4), embedding defaults updated (openai_compatible), retrieval pipeline documented, actor descriptions aligned with ADR-011
- Missing sections added to CONFIGURATION.md: channel weights, vigil quality, model slots, read pool, alerting, hot-reload, database
- New API endpoint sections added: Graph, Corpus, Vigil Quality, Retrieval Dashboard, Embeddings Backfill
- Alpha tester guidance added to DOGFOOD_READY.md, CONTRIBUTING.md, Maintenance_Protocol.md, STATUS.md
