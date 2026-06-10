# Changelog

All notable changes to AIP 0.1 are documented here. This project follows the append-only convention — entries are added, never removed.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

---

## [Unreleased]

### Sprint 6.0 — Bug Fixes and Sexton Validation

#### Fixed
- **BUG-001**: `aip init` creates no default project (documented, deferred to maintenance)
- **BUG-002**: `chat.py` uses wrong DB path for GraphStore (documented, deferred to maintenance)
- **BUG-004**: GraphStore has no Protocol, uses sync sqlite3 (documented as DEBT-005, deferred)
- Sexton validation rules strengthened with deterministic A-F classification + 7 special conditions

### Sprint 6.1 — Embedding Pipeline and Hybrid Retrieval

#### Added
- **Hybrid retrieval with RRF fusion**: `RetrievalOrchestrator` in `orchestration/retrieval_orchestrator.py`
  dispatches parallel queries across FTS5, Vector, and Corpus channels, then fuses results
  using weighted Reciprocal Rank Fusion (RRF, k=60). Channel weights are configurable via
  `[retrieval.channel_weights]` in `aip.config.toml` (default: vector=0.6, fts=0.4, corpus=0.4).
- **Coverage-aware gating**: The orchestrator gracefully falls back to FTS5-only when vector
  coverage is below `min_vector_coverage` (default 0.10), preventing degraded hybrid results
  on largely unembedded corpora.
- **OpenAI-compatible embedding client** (`aip.adapter.embedding.openai_embed.py`):
  New `OpenAICompatibleEmbeddingClient` that calls `/v1/embeddings` endpoints
  (OpenRouter, OpenAI, DeepSeek, etc.). Includes `MockOpenAICompatibleEmbeddingClient`
  for CI/testing.
- **`openai_compatible` provider in `embed_providers.py`**: The orchestration-layer
  embedding provider loader now supports `provider = "openai_compatible"` alongside
  the existing `ollama` and `fake` options.
- **Background embedding pass in Sexton**: `_run_embedding_pass()` in the new Sexton actor
  processes unembedded turns in batches of ~50 per cycle (built, not wired — DEBT-006).
- **Re-embedding on model slot change**: Infrastructure complete for re-generating vectors
  when the embedding model is changed at runtime.
- **Retrieval evaluation harness** (`orchestration/retrieval_eval.py`): Full harness with
  P@5, R@10, MRR, and entity coverage metrics. Supports `--mode` flag (hybrid/fts-only/all)
  via `aip eval retrieval` CLI. Includes A/B comparison and regression detection.
- **Golden queries with corpus-mapped IDs** (`tests/retrieval_goldens/golden_queries.json`):
  Updated from placeholder IDs to actual corpus turn IDs via FTS5 matching.
- **Channel weight tuning script** (`scripts/retrieval_weight_tuning.py`): Grid search over
  vector/fts weight combinations, reports best weights for config.
- **Vigil retrieval quality gate** (`vigil.py::_run_retrieval_quality_sample()`): Periodic
  precision@5 sampling with alerting on degradation. Configurable via
  `[vigil.retrieval_quality]` in `aip.config.toml`.
- **VigilQualityStore** (persistent): Quality history with retention and rollup, enabling
  trend tracking and degradation alerting over time.

#### Changed
- `[embedding]` in `aip.config.toml` now defaults to `provider = "openai_compatible"`
  with `model = "nvidia/llama-nemotron-embed-vl-1b-v2:free"` and
  `base_url = "https://openrouter.ai/api"` instead of `provider = "fake"`.
- `app.py` lifespan: Replaced inline embedding provider creation with
  `_create_embedding_provider()` which properly resolves from slot config.
- `ask_pipeline.py` now reads channel weights from config and constructs
  `OrchestratorConfig` dynamically.

### Sprint 6.4 — Retrieval Quality Validation and Project Closure

#### Added
- **ADR-013**: Formal record of Sprint 6.4 decisions — pragmatic eval harness, channel
  weight tuning, Vigil quality gate, documentation closure, and scope exclusions.
- **`docs/Maintenance_Protocol.md`**: Operational procedures for the maintenance phase
  including priority tasks (DEBT-006, retrieval re-evaluation), regular maintenance
  schedules, monitoring guidance, and emergency procedures.
- **Alpha test release documentation**: All project documentation refreshed and validated
  for alpha tester consumption.

#### Changed
- **STATUS.md**: Updated with alpha release framing, explicit known limitations for testers,
  and current corpus/retrieval/actor state.
- **ROADMAP.md**: Added alpha release version history entry.
- **TECH_DEBT.md**: No new items; all existing items verified current.
- **CHANGELOG.md**: Comprehensive Sprint 6.0–6.4 entries added.

#### Scope Exclusions
- No new retrieval features — this sprint measures, does not improve
- No full embedding pass — requires DEBT-006 fix (maintenance task)
- No UI changes — focus was backend validation and documentation
- No perfect golden query set — sufficient for directional signal

---

## [0.1.0-alpha-pre] — 2026-06-02

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
