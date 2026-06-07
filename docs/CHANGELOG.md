# Changelog

All notable changes to AIP 0.1 are documented here. This project follows the append-only convention — entries are added, never removed.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

---

## [Unreleased] — 2026-06-07

### Retrieval Architecture — Phases 5.0–5.6 (COMPLETE)

#### Phase 5.0 — Measurement and Trace
- **Golden test suite** (`tests/retrieval_goldens/`): 6 YAML golden queries (Komal, GEF RF heating, frost alert device, AIP features, AIP retrieval architecture, FGS school registration) with must_include_clusters, must_not_dominate, and success thresholds.
- **Retrieval trace instrumentation** (`retrieval_trace.py`): RetrievalTrace, RetrievalHit, RetrievalQuery, RetrievalBudget dataclasses with full provenance tracking.
- **Baseline evaluation script** (`eval_retrieval.py`): Before/after CLI for retrieval quality measurement.
- **FTS5 sanitization**: Stop-word filtering + AND-join query construction + single-quote handling.

#### Phase 5.1 — Protocol Substrate
- **Retriever Protocol** (`retriever.py`): `@runtime_checkable` with `name: str` and `async def retrieve(query, *, budget, trace) -> RetrievalList`.
- **FTSRetriever** (`fts_retriever.py`): First conforming retriever, wraps FTS5 corpus_turns_fts search into RetrievalHit output.
- **RRF fusion** (`rrf_fusion.py`): Reciprocal Rank Fusion with k=60, plus AIP-specific modifiers (importance, confidence, evidence_status, freshness, diversity).
- **RetrievalOrchestrator** (`orchestrator.py`): Parallel dispatch to enabled retrievers, RRF fusion, budget enforcement, quality gate integration, auto-retry on NEEDS_MORE_CONTEXT.

#### Phase 5.2 — Entity-Turn Index + GraphRetriever
- **Entity-turn index**: entity_turn_index table (entity_id, turn_id, confidence, source) with backfill from evidence_turn_ids_json and mention scan.
- **GraphRetriever** (`graph_retriever.py`): Zone A (direct entity-mention recall) + Zone B (PPR expansion via networkx PageRank), hub leash (weight / log(degree+1)), multi-signal scoring.
- **EntitySeedSelector**: Exact canonical/alias match, acronym match, phrase longest-match-first, FTS5 entity search, token overlap scoring.
- **Hub leash**: Configurable hub_penalty_weight, cap on turns per expanded entity, generic domain cap.

#### Phase 5.3 — Query Expansion + Wiki + Entity Writes
- **LLM-powered query expansion** (`query_expansion.py`): Fast-model structured JSON output with entities, query_variants, likely_domains, query_mode.
- **WikiRetriever** (`wiki_retriever.py`): Domain selection from seeds + hits, budgeted multi-wiki injection, semantic matching via embedding similarity.
- **Sexton entity-turn writes**: Entity extraction results written to entity_turn_index during tagging cycles.
- **Hub leash tuning**: Configurable weights and caps, trace field for hub_penalty_applied.

#### Phase 5.4 — VectorRetriever + Semantic Wiki
- **VectorRetriever** (`vector_retriever.py`): 768-dim embedding similarity via SqliteVssVectorStore, conforming to Retriever protocol.
- **Semantic wiki matching**: Embedding-based domain article selection for WikiRetriever.
- **Trace and config polish**: Expanded trace fields, retriever-level configuration.

#### Phase 5.5 — Context Quality & Reliability
- **SmartContextPacker** (`context_packer.py`): Budget-aware, structured context assembly with 4 sections (evidence, wiki, procedural, graph), source caps, diversity rules, temporal span handling.
- **ProceduralRetriever** (`procedural_retriever.py`): How-to/procedure artifact retrieval with procedural query detection and scoring.
- **AnswerQualityGate** (`answer_quality_gate.py`): Heuristic context sufficiency check across 4 dimensions (coverage, confidence, diversity, freshness) with configurable thresholds.
- **TraceStore** (`trace_store.py`): SQLite-backed trace persistence with quality metrics recording.
- **ContextQualityStatus** enum: SUFFICIENT, MARGINAL, NEEDS_MORE_CONTEXT, EMPTY.

#### Phase 5.6 — Autonomy, Quality & Observability
- **Auto-Retry on NEEDS_MORE_CONTEXT**: Second retrieval round with strategy escalation (LLM expansion, relaxed domain, broader entity seeding, increased max_sources). Max 1 retry. Retry info recorded in RetrievalTrace (retry_triggered, retry_reason, retry_round, retry_strategies_tried).
- **Context Compression / Smart Truncation**: Extractive summarization in SmartContextPacker for long evidence hits. Sentence-boundary splitting + scoring by query/entity overlap. ContextSection tracks compressed_count.
- **Trace Dashboard Foundation**: TraceStore analytics methods — get_dashboard_summary(), query_retry_stats(), query_retriever_stats(). Quality status distribution over time, average entity coverage, most common fallback/retry reasons, per-retriever contribution stats.
- **Quality Gate Enhancements**: Optional model-assisted sufficiency check in AnswerQualityGate for MARGINAL cases. QualityGateConfig.enable_model_assisted and model_assisted_slot options. Pure heuristic remains default/fast path.

#### Retrieval Test Suite
- **161 retrieval tests passing** across 4 test files:
  - `tests/test_retrieval_trace.py` — Schema and budget tests
  - `tests/test_phase54_retrieval.py` — VectorRetriever + query expansion tests
  - `tests/test_phase55_retrieval.py` — ContextPacker + QualityGate + ProceduralRetriever + TraceStore tests
  - `tests/test_phase56_retrieval.py` — Auto-retry + extractive summarization + dashboard analytics + model-assisted gate tests

### Unified Chat Spec — Phases 1-4

#### Added
- **Beast soul.md** (`data/beast_soul.md`): Beast personality and epistemic stance, prepended to all 4 LLM call sites (domain summaries, turn tagging, wiki generation, graph extraction). Graceful fallback per AIP-G-02.
- **Enabled models table** in `_init_state_db()`: Reads config/enabled_models.json with INSERT OR IGNORE.
- **Model library CRUD** (`routes/models_library.py`): GET /models/library, POST /models/library/fetch (OpenRouter), PATCH /models/library/{model_id}, POST /models/library/custom (BYOK).
- **Unified chat panel**: Single surface replacing CHAT/AUGMENTED tabs. Augment toggle + mode status chip (BARE/AUGMENTED).
- **Model selector**: Multi-select (max 5) loading from enabled_models. Falls back to static opts.
- **Chat mode picker**: Engineering/Research/Ideation/Teaching with auto-detection keywords. System prompt modifier prepended to synthesis prompt.
- **Beast scan** (`routes/beast_scan.py`): GET /beast/scan endpoint. Fires AFTER BARE response (non-blocking per AIP-G-02).
- **Beast pane**: 320px collapsible sidebar with scan results. Collapse/pop-out buttons. Sticky positioning.
- **Cohort dispatch** (`routes/chat_cohort.py`): POST /chat/cohort with parallel asyncio.gather, per-model error isolation, shared augmented context.
- **Cohort response cards**: Per-model cards with left-border accent colors from _COHORT_PALETTE.
- **Beast comparison** (`routes/beast_compare.py`): POST /beast/compare, GET /beast/comparison/{session_id}. Soul.md prepended to comparison prompt.
- **Beast comparisons table** in _init_state_db(). Corpus turn writing for cohort model responses.
- **Chat mode modifier**: system_prompt_modifier parameter through full stack (shell → api_client → ask → pipeline).
- **DEFINER profile edit**: Editable textarea in Settings panel.
- **Epistemic flags**: No flattery, flag uncertainty, suggest validation, report conflicts — stored in config.
- **Beast pop-out**: Opens Beast pane as standalone page in new tab.
- **Jump-to-input FAB**: Amber floating button, fixed position, scrolls .aip-msgs to bottom.

### Actor Log Feature

#### Added
- **Events endpoint** (`routes/events.py`): GET /api/v1/events with limit, actor, event_type query params. Queries container.event_store.query().
- **Actor log widget**: Compact "RECENT ACTOR EVENTS" in STATUS tab with last 5 events, color-coded dots (beast=amber, sexton=teal, vigil=purple).
- **Standalone /actor-log page**: Filter bar (actor type, event type), event cards with expandable payloads, 15s auto-refresh.
- **list_events()** in api_client.py.

### Hygiene and Bug Fixes

#### Fixed
- **H-1**: Actor status display — Vigil uses _last_eval_time, Sexton uses _last_cycle_time for last_cycle_time in status dicts. Added sexton_actor to /actors/status.
- **H-2**: Vigil logger — structlog get_logger replaces stdlib logging.getLogger (which doesn't accept kwargs like 'status'). Graph extraction — JSON extraction wrapper for model responses.
- **H-3**: Chat turn → corpus_turns wiring — auto_save_chat_turn() now builds CorpusTurn and calls upsert_turn() after ingest. Added corpus_turn_store parameter to ingest_conversation().
- **Beast .call() fix**: self._beast_provider.chat() → self._beast_provider.call(). ModelSlotResolver has no .chat() method.
- **Events actor filter**: Changed limit * 3 to min(limit * 20, 500) for post-filtering.
- **Graph extraction field names**: entities_extracted/edges_extracted → entities_created/relationships_created.

### Retrieval Architecture Planning

#### Added
- **Retrieval review synthesis** (`docs/retrieval/RETRIEVAL_REVIEW_SYNTHESIS.md`): Four-AI ensemble review of GraphRAG plan. Key lesson: code-grounded reviews disagreed with memo-grounded reviews.
- **Retrieval build memo** (`docs/retrieval/AIP_RETRIEVAL_BUILD_MEMO.md`): Authoritative 23-section plan covering Retriever protocol, golden tests, entity-turn index, mention scan, GraphRetriever, PPR, RRF fusion, wiki retrieval, context budget, and 6-phase build order.
- **Project status doc** (`docs/AIP_PROJECT_STATUS.md`): Canonical state document for thread onboarding with architecture overview, phase tracker, file inventory, and invariants.

---

## [Unreleased] — 2026-06-02

### Phase 9 — Real OpenRouter Embedding + Backfill

#### Added
- **OpenAI-compatible embedding client** (`aip.adapter.embedding.openai_embed.py`):
  New `OpenAICompatibleEmbeddingClient` that calls `/v1/embeddings` endpoints
  (OpenRouter, OpenAI, DeepSeek, etc.). Includes `MockOpenAICompatibleEmbeddingClient`
  for CI/testing.
- **`openai_compatible` provider in `embed_providers.py`**: The orchestration-layer
  embedding provider loader now supports `provider = "openai_compatible"` alongside
  the existing `ollama` and `fake` options. Resolves model, base_url, and api_key
  from config with env var fallbacks (AIP_EMBEDDING_API_KEY, AIP_OPENAI_API_KEY).
- **Runtime embedding provider updates**: `PATCH /models/slots/embedding/model` now
  recreates `container.embedding_provider` at runtime when the embedding slot is
  changed. Also updates references in vector_store, Beast, and knowledge_store so
  the new model takes effect immediately without a server restart.
- **Embedding backfill endpoint** (`POST /admin/embeddings/backfill`): Generates
  vector embeddings for lexical documents that don't yet have vector entries.
  Supports domain filtering, batch size, limit, and dry-run mode. This is the
  primary mechanism for generating vectors for data ingested before an embedding
  provider was configured (e.g., CLI ingestion with embedding_provider=None).
- **Unified embedding config**: The `_create_embedding_provider()` function in
  `app.py` resolves the embedding provider from `[models.embedding]` slot config
  first (same as what the UI manages), then falls back to the legacy `[embedding]`
  section. This bridges the gap between the ModelSlotResolver's slot system and
  the EmbeddingProvider infrastructure.

#### Changed
- `[embedding]` in `aip.config.toml` now defaults to `provider = "openai_compatible"`
  with `model = "text-embedding-3-small"` and `base_url = "https://openrouter.ai/api"`
  instead of `provider = "fake"`. This makes embedding functional out of the box
  when an API key is provided via env var.
- `app.py` lifespan: Replaced inline embedding provider creation with
  `_create_embedding_provider()` which properly resolves from slot config.
- `models.py` route: Uses `aip.logging.get_logger` instead of stdlib `logging`.

#### Fixed
- **Embedding config disconnect resolved**: Previously, `[models.embedding]` slot
  and `[embedding]` section were disconnected — changing one didn't affect the other.
  Now the slot config takes priority, and UI changes propagate to the actual
  embedding provider at runtime.
- **Beast and knowledge store embedding references**: When the embedding provider
  is updated at runtime, Beast's `_embed` and knowledge store's `_embedding_provider`
  references are also updated, so they use the new model immediately.

---

## [0.1.0-alpha] — 2025-03-04

### Phase 1 — Foundation Bootstrap
- Initial foundation schemas (EcsState, ContractRule, Chunk, RetrievalResult)
- Protocol stubs (VectorStore, CanonicalStore, ArtifactStore, TraceStore, EventStore, EcsStore)
- ECS state graph validation
- In-memory budget store and autonomy gate stubs
- Deterministic fake embedding provider
- Retrieval harness with configurable weights

### Phase 2 — Review & Provenance
- ReviewVerdict, ReviewContext, EcsTransition dataclasses
- Artifact versioning (read by version, list_versions)
- EventStore.query() for review node and DEFINER audit
- Review node implementation

### Phase 3 — Real Embedding & L4 Trajectory
- ModelProvider and EmbeddingProvider protocols
- Ollama embedding adapter
- L4 trajectory regulation: loop detector, anxiety detector, failure streak detector
- SessionContext tracking
- Context reset on anxiety threshold

### Phase 4 — Vector Backend Migration
- PgvectorConfig and MigrationStatus schemas
- PostgreSQL pgvector adapter implementation
- SQLite-vss to pgvector migration tool
- VectorStore.health_check() protocol addition
- Vector connection pool manager

### Phase 5 — Actor Layer (Sexton, Beast, Budget)
- Sexton failure classification actor (A–F taxonomy)
- Beast maintenance cadence actor
- BudgetManager with session/project/daily enforcement
- ACE Playbook procedural intervention rules
- Adaptive model router with exploration weight
- RoutingWeight per domain×model tracking

### Phase 6 — Surfaces (API, CLI, MCP, Chat)
- FastAPI REST API with 8 route modules
- Click CLI with init, status, config, project, session commands
- MCP server with search and artifact tools
- Chat surface with context assembly
- SurfaceConfig, ApiRoute, McpToolDef schemas
- AutonomyEscalation across all surfaces
- Review queue and chat message schemas

### Phase 7 — Hardening & Release Prep
- Vigil actor with canonical health checking
- Authentication system (session + API key)
- AuthMiddleware and RateLimitMiddleware
- SqliteBudgetStore with persistent ledger
- CanonicalPipeline (10-step REVIEWED→APPROVED→CANONICAL)
- EcsStoreGuardrailed with valid transition enforcement
- ArtifactStoreVersioned with full version history
- EventStoreQueryable with cross-session queries
- Deployment profiles (laptop, production)
- Docker and docker-compose for both profiles
- Acceptance test suite (7 test files)
- Health check script

### Phase 8 — Knowledge, Plugins, Release
- KnowledgeStore protocol and SQLite implementation
- Knowledge compilation pipeline
- Plugin system with YAML providers and sandbox mode
- Collaborator access control (read-only, collaborator roles)
- Performance tuning (profiling, batch embed, SQLite WAL concurrency)
- SqliteConcurrencyManager for multi-database WAL connections
- Model slot resolver (no hardcoded model names)
- Release metadata schema
- Complete documentation suite

---

## Release Gates (§22)

The following gates must pass for release:

- [x] All Foundation protocols are runtime-checkable
- [x] ECS graph rejects invalid transitions
- [x] DEFINER sovereignty enforced via AutonomyGate
- [x] Budget hard stop blocks overspend
- [x] Canonical pipeline completes end-to-end
- [x] No hardcoded model names in codebase
- [x] collaborator_can_approve defaults to False
- [x] All surfaces respect AutonomyGate
- [x] MCP cannot bypass DEFINER gates
- [x] Rate limiting protects model budget
