# AIP Roadmap
# DEFINER: B. Moses Jorgensen
# Last Updated: 2026-06-10
# Process: Update this document after each significant build session or architectural decision.
# Release: 0.1.0-alpha (Alpha Test Release)

---

## How to Read This Document

Status indicators:
- ✅ COMPLETE — built, tested, in production use
- ⏳ IN PROGRESS — actively being built
- 🔲 PLANNED — decided, not yet started
- 💡 PROPOSED — under consideration, not yet decided
- ❌ DEFERRED — decided to defer, reason noted

Architecture decisions are recorded in `docs/decisions/`. When a decision changes
the roadmap, update both documents.

---

## PHASE 0 — Foundation
*Core artifact lifecycle, storage, and evaluation pipeline.*
*Status: ✅ COMPLETE*

- ✅ Three-layer architecture (foundation → orchestration → adapter)
- ✅ ECS state machine (SPECIFIED→GENERATED→REVIEWED→APPROVED→SUPERSEDED)
- ✅ Persistent SQLite stores (artifacts, ECS, events, lexical, projects)
- ✅ FTS5 full-text search with domain filtering
- ✅ Model dispatch (Ollama + OpenAI-compatible, all slots)
- ✅ Review/approve/reject/export pipeline
- ✅ DEFINER sovereignty gates (no auto-approve in MANUAL mode)
- ✅ Auth system (laptop: disabled by default, production: required)
- ✅ FastAPI backend with 11+ routers
- ✅ Click CLI (init, status, ingest, ask, review, export, eval)
- ✅ CI gates (ruff format, ruff check, pytest 1000+ tests)
- ✅ Docker profiles (laptop + production)
- ✅ Beast actor (background scheduler, health checks, context advisory)
- ✅ Vigil actor (quality evaluation, retrieval quality gate, LLM faithfulness)
- ✅ Sexton actor (built with all 5 ops; wiring gap — DEBT-006)
- ✅ Autonomy gate with audit trail
- ✅ Budget enforcement
- ✅ MCP server (scaffold — tool listing real, dispatch scaffold)
- ✅ Alerting system (webhook, email, WebSocket, SSE, digest, muting)
- ✅ VigilQualityStore (persistent quality history with retention and rollup)
- ✅ Read pool with auto-sizing
- ✅ Config hot-reload (safe keys)

---

## PHASE 1 — Corpus Intelligence
*Turn-level corpus ingestion, tagging, and retrieval.*
*Status: ✅ COMPLETE (core)*

### 1.1 Turn-Level Corpus Foundation
- ✅ CorpusTurn schema (atomic unit: user+assistant pair with thinking_text)
- ✅ CorpusTurnStore (SQLite + FTS5 + Beast tagging path)
- ✅ make_turn_id (deterministic, idempotent)
- ✅ thinking_text field (extended thinking preserved separately from assistant_text)

### 1.2 Source Parsers
- ✅ Claude export parser (conversations.json, handles all content block types)
- ✅ 2,691 turns ingested from claude_export_june_2026
- ✅ 1,743 turns with extended thinking blocks preserved
- 🔲 ChatGPT export parser (tree-structure conversation format)
- 🔲 DeepSeek export parser
- 🔲 GLM export parser
- 🔲 Gemini export parser
- 🔲 xAI/Grok export parser
- 🔲 Plain text / sermon transcript parser (for external corpora)
- 🔲 PDF parser (for academic papers and books)
- 🔲 Web crawl / sitestrip parser (for external research corpora)

### 1.3 Beast Turn Tagging
- ✅ Domain registry (docs/beast_domain_registry_v1.md)
- ✅ DomainRegistry loader (load_registry, DomainEntry, ConnectorEntry)
- ✅ Beast _run_turn_tagging (batch-8 LLM tagging)
- ✅ Domain proposal system (Beast proposes → DEFINER approves)
- ✅ Connector proposal system
- ✅ aip corpus tag CLI (--limit, --retag)
- ✅ 2,681 turns tagged (tagging_version > 0)
- ✅ Registry v1.0: 26 domains, 13 connectors
- ✅ Registry v1.1: aip hall model, ancient_archaeology, agi_philosophy
- 🔲 Registry v1.2: (future — based on Beast proposals and dogfood observations)

### 1.4 Embedding Pipeline & Hybrid Retrieval
- ✅ Embed corpus_turns.searchable_text using embedding slot (infrastructure complete)
- ✅ Store vectors keyed by turn_id in vector store (SqliteVssVectorStore)
- ✅ Hybrid FTS5+vector scoring via RRF fusion in RetrievalOrchestrator
- ✅ Channel weights configurable in aip.config.toml (vector=0.6, fts=0.4, corpus=0.4)
- ✅ Coverage-aware gating (min_vector_coverage=0.10, graceful FTS5 fallback)
- ✅ Background embedding pass in Sexton _run_embedding_pass (built, not wired — DEBT-006)
- ✅ Re-embedding on model slot change (infrastructure complete)
- ✅ Retrieval evaluation harness (`aip eval retrieval` with --mode flag)
- ✅ Channel weight tuning script (`scripts/retrieval_weight_tuning.py`)
- ✅ Vigil retrieval quality gate (periodic precision@5 sampling with alerting)
- ✅ Golden queries with corpus-mapped IDs (`tests/retrieval_goldens/golden_queries.json`)
- ✅ Baseline benchmark (`docs/retrieval_benchmark_baseline.json`)

**Remaining gap:** ~1.8% embedding coverage (50/2766 turns). Full pass requires DEBT-006 fix.

### 1.5 Multi-Corpus Architecture
- 🔲 Corpus registry in config (named corpora with db_path)
- 🔲 --corpus flag on aip corpus ingest
- 🔲 Query-time corpus selection in augmented chat
- 🔲 Branham research corpus (1200 sermons + books + critic sites)
- 🔲 NBCM citations corpus (academic papers across relevant domains)
- SEE: ADR-004-multi-corpus-architecture.md

---

## PHASE 2 — Knowledge Synthesis
*Beast-generated wiki, knowledge graph, and cross-corpus intelligence.*
*Status: ✅ COMPLETE (core)*

### 2.1 Beast Wiki Generation
- ✅ Domain article generation (300-500 words per active domain)
- ✅ Wiki articles as GENERATED artifacts → DEFINER review → APPROVED
- ✅ BeastContextPreparer reads approved wiki as domain overview
- ✅ Wiki update triggered by Sexton cycle (not on timer)
- ✅ Wiki versioning (new article supersedes old on regeneration)

### 2.2 Knowledge Graph
- ✅ Entity extraction from corpus_turns (people, concepts, projects, places)
- ✅ Relationship inference (bridge-tagged turns → graph edges)
- ✅ Graph store (SQLite, synchronous GraphStore)
- ✅ Graph-aware retrieval (PersonalizedPageRank in GraphRetriever)
- ✅ Graph visualization in UI (Cytoscape.js at /graph-viz)
- ✅ Entity alias registry (22 entries)
- SEE: ADR-007-knowledge-graph-architecture.md

### 2.3 Domain Export Packages
- 🔲 Export mechanism: filter corpus by domain → standalone package
- 🔲 Package format: db + wiki + graph + embeddings as archive
- 🔲 Versioned packages (v1.0, v2.0 as corpus grows)
- 🔲 Package recipient model (share without exposing personal corpus)
- SEE: ADR-004-multi-corpus-architecture.md

---

## PHASE 3 — Actor Intelligence
*Beast, Vigil, and Sexton functioning as genuine intelligence layer.*
*Status: ✅ COMPLETE (code); DEBT-006 wiring gap remains*

### 3.1 Beast (Corpus Intelligence)
- ✅ Background scheduler (health check, entity check, heartbeat)
- ✅ Beast LLM slot (nvidia/nemotron-3-super-120b-a12b)
- ✅ Domain summary generation (event-driven, not timer-driven)
- ✅ BeastContextPreparer (retrieval + domain overview in augmented chat)
- ✅ Context advisory injected into synthesis model system prompt
- 🔲 Beast reads wiki artifacts as enhanced domain overview (maintenance)
- 🔲 Beast corpus health reporting (coverage gaps, stale artifacts) (maintenance)
- 🔲 Beast re-tagging trigger (when registry changes, retag affected turns) (maintenance)

### 3.2 Vigil (Quality Evaluation)
- ✅ Vigil scheduler (runs every 3600s)
- ✅ Vigil model slot (openai/gpt-oss-20b)
- ✅ Model slot change → mark canonicals for re-evaluation
- ✅ Faithfulness scorer (LLM-powered faithfulness checking, graduated Sprint 5.24)
- ✅ Citation rate scoring (pure-Python, always runs)
- ✅ Quality gate (flag responses that cite sources poorly)
- ✅ Vigil evaluation report as reviewable artifact
- ✅ Retrieval quality gate (precision@5 sampling with alerting, Sprint 6.4)
- ✅ VigilQualityStore (persistent history with retention/rollup)
- ✅ Trend tracking and degradation alerting

### 3.3 Sexton (Background Maintenance)
- ✅ Deterministic rules for failure types A-F + 7 special conditions
- ✅ Full Sexton actor (actors/sexton.py, 5 operations: tagging, embedding, wiki, graph, classification)
- ✅ Sexton model slot (google/gemma-4-26b-a4b-it)
- ❌ **NOT WIRED** — DEBT-006: app.py still calls old Sexton. All maintenance ops are dead code until wired.

---

## PHASE 4 — UI and Experience
*Making the knowledge engine usable and transparent.*
*Status: PARTIAL*

### 4.1 Augmented Chat UI
- ✅ Basic chat working (CHAT and AUGMENTED tabs)
- ✅ Auto-save to corpus on chat turn completion
- ✅ Beast context advisory injected in augmented mode
- 🔲 Show retrieved domain in chat
- 🔲 Show source citations inline in response
- 🔲 Show domain overview in chat (collapsible Beast summary)
- 🔲 Corpus selector in UI

### 4.2 Corpus Browser
- ✅ aip history list / aip history show (CLI)
- 🔲 Domain distribution view
- 🔲 Turn browser (search by domain, filter by importance)
- 🔲 Turn detail view
- 🔲 Domain proposal review UI

### 4.3 Knowledge Graph UI
- ✅ Interactive graph visualization (/graph-viz, Cytoscape.js)
- 🔲 Entity search and navigation
- 🔲 Relationship explorer

### 4.4 Slot and Model Management
- ✅ Actor Roles panel in GUI
- ✅ Five slots visible (synthesis, beast, vigil, sexton, embedding)
- 🔲 Per-slot model selector in Actor Roles panel
- 🔲 Slot health indicator

---

## PHASE 5 — Production and Scale
*Multi-user deployment, hardening, and sharing.*
*Status: DEFERRED (maintenance mode)*

- 🔲 Multi-user support (per-user corpora, shared canonicals)
- 🔲 Real MCP tool dispatch (search, approve, config via MCP)
- 🔲 Adaptive router (weight routes from outcomes, not random)
- 🔲 ScriptNode sandbox (safe execution environment)
- 🔲 Streaming model support
- 🔲 PostgreSQL migration for production
- 🔲 Review queue web UI for MANUAL mode
- 🔲 Per-component performance metrics (not estimated)
- 🔲 Onboarding flow for new users (export import wizard)

---

## Maintenance Mode

**Effective:** 2026-06-10 (post Sprint 6.4)
**See:** `docs/Maintenance_Protocol.md` for operational procedures

The active development phase is complete. The system is stable for local development,
evaluation, and dogfood usage. Future work is limited to:

1. **Bug fixes** — Address remaining bugs (BUG-001 through BUG-004) as needed
2. **DEBT-006** — Wire the new Sexton actor into app.py (highest priority debt item)
3. **Embedding pass** — Once Sexton is wired, let it complete the full embedding pass (~2,716 turns)
4. **Re-evaluate retrieval** — After full embedding, re-run `aip eval retrieval` and `scripts/retrieval_weight_tuning.py` to validate hybrid improvement
5. **Parser additions** — Add source parsers (ChatGPT, DeepSeek, etc.) as needed
6. **UI improvements** — Iterative UX enhancements based on dogfood feedback

No new feature sprints are planned. All changes should be small, incremental, and tested.

---

## Ongoing / Evergreen

- 🔄 Domain registry maintenance (review Beast proposals, update registry)
- 🔄 Corpus retag passes (after registry updates)
- 🔄 Monthly Claude export ingest
- 🔄 Other platform exports as parsers are built
- 🔄 STATUS.md kept current after each build session
- 🔄 ADRs written for each significant architectural decision
- 🔄 Re-run retrieval evaluation after significant corpus changes

---

## Version History

| Date       | Change                                      | Author  |
|------------|---------------------------------------------|---------|
| 2026-06-04 | Initial roadmap created from repo audit     | Claude + Moses |
| 2026-06-04 | Phase 1 corpus work reflected               | Claude + Moses |
| 2026-06-10 | Sprint 6.4 completion; maintenance mode     | Claude + Moses |
| 2026-06-10 | Alpha test release; documentation refresh   | Claude + Moses |
