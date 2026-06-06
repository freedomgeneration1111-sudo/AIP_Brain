# AIP Roadmap
# DEFINER: B. Moses Jorgensen
# Last Updated: 2026-06-04
# Process: Update this document after each significant build session or architectural decision.

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
*Status: COMPLETE*

- ✅ Three-layer architecture (foundation → orchestration → adapter)
- ✅ ECS state machine (SPECIFIED→GENERATED→REVIEWED→APPROVED→SUPERSEDED)
- ✅ Persistent SQLite stores (artifacts, ECS, events, lexical, projects)
- ✅ FTS5 full-text search with domain filtering
- ✅ Model dispatch (Ollama + OpenAI-compatible, all slots)
- ✅ Review/approve/reject/export pipeline
- ✅ DEFINER sovereignty gates (no auto-approve in MANUAL mode)
- ✅ Auth system (laptop: disabled by default, production: required)
- ✅ FastAPI backend with 11 routers
- ✅ Click CLI (init, status, ingest, ask, review, export)
- ✅ CI gates (ruff format, ruff check, pytest 1000+ tests)
- ✅ Docker profiles (laptop + production)
- ✅ Beast actor (background scheduler, health checks, corpus maintenance)
- ✅ Vigil actor (model slot re-evaluation)
- ✅ Sexton actor (deterministic failure classification)
- ✅ Autonomy gate with audit trail
- ✅ Budget enforcement
- ✅ MCP server (scaffold — tool listing real, dispatch scaffold)

---

## PHASE 1 — Corpus Intelligence
*Turn-level corpus ingestion, tagging, and retrieval.*
*Status: IN PROGRESS*

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
- ⏳ Registry v1.1: aip hall model, ancient_archaeology, agi_philosophy
- 🔲 Registry v1.2: (future — based on Beast proposals and dogfood observations)

### 1.4 Embedding Pipeline
- 🔲 Embed corpus_turns.searchable_text using embedding slot
- 🔲 Store vectors keyed by turn_id in vector store
- 🔲 Hybrid FTS5+vector scoring in _search_sources
- 🔲 Background embedding pass in Beast cycle (embed after tagging)
- 🔲 Re-embedding pass when embedding model changes

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
*Status: NOT STARTED*

### 2.1 Beast Wiki Generation
- 🔲 Domain article generation (300-500 words per active domain)
- 🔲 Wiki articles as GENERATED artifacts → DEFINER review → APPROVED
- 🔲 BeastContextPreparer reads approved wiki as domain overview
- 🔲 Wiki update triggered by corpus_modified events (not on timer)
- 🔲 Wiki versioning (new article supersedes old on regeneration)

### 2.2 Knowledge Graph
- 🔲 Entity extraction from corpus_turns (people, concepts, projects, places)
- 🔲 Relationship inference (who worked on what, what connects to what)
- 🔲 Graph store (SQLite adjacency list or dedicated graph db)
- 🔲 Graph-aware retrieval (follow relationships during augmented chat)
- 🔲 Graph visualization in UI
- SEE: ADR-005-knowledge-graph-design.md (to be written)

### 2.3 Domain Export Packages
- 🔲 Export mechanism: filter corpus by domain → standalone package
- 🔲 Package format: db + wiki + graph + embeddings as archive
- 🔲 Versioned packages (v1.0, v2.0 as corpus grows)
- 🔲 Package recipient model (share without exposing personal corpus)
- SEE: ADR-004-multi-corpus-architecture.md

---

## PHASE 3 — Actor Intelligence
*Beast, Vigil, and Sexton functioning as genuine intelligence layer.*
*Status: PARTIAL*

### 3.1 Beast (Corpus Intelligence)
- ✅ Background scheduler (health check, corpus maintenance, entity check)
- ✅ Beast LLM slot (nvidia/nemotron-3-super-120b-a12b)
- ✅ Domain summary generation (event-driven, not timer-driven)
- ✅ BeastContextPreparer (retrieval + domain overview in augmented chat)
- ✅ Context advisory injected into synthesis model system prompt
- 🔲 Beast reads wiki artifacts as enhanced domain overview
- 🔲 Beast corpus health reporting (coverage gaps, stale artifacts)
- 🔲 Beast re-tagging trigger (when registry changes, retag affected turns)

### 3.2 Vigil (Quality Evaluation)
- ✅ Vigil scheduler (runs every 3600s)
- ✅ Vigil model slot (openai/gpt-oss-20b)
- ✅ Model slot change → mark canonicals for re-evaluation
- 🔲 Faithfulness scorer (does synthesis response reflect retrieved context?)
- 🔲 Drift detector (are Beast summaries still accurate as corpus grows?)
- 🔲 Quality gate (flag responses that cite sources poorly)
- 🔲 Vigil evaluation report as reviewable artifact

### 3.3 Sexton (Failure Classification)
- ✅ Deterministic rules for failure types A-F + 7 special conditions
- ✅ Sexton scheduler (runs every 300s)
- ✅ Sexton model slot (google/gemma-4-26b-a4b-it)
- 💡 LLM upgrade for complex multi-signal failure classification
  (DEFERRED: build when real failure patterns emerge from dogfood usage)

---

## PHASE 4 — UI and Experience
*Making the knowledge engine usable and transparent.*
*Status: MINIMAL*

### 4.1 Augmented Chat UI
- ✅ Basic chat working (CHAT and AUGMENTED tabs)
- ✅ Auto-save to corpus on chat turn completion
- ✅ Beast context advisory injected in augmented mode
- 🔲 Show retrieved domain in chat (which domain was searched)
- 🔲 Show source citations inline in response
- 🔲 Show domain overview in chat (collapsible Beast summary)
- 🔲 Show Beast confidence and tagging version for retrieved turns
- 🔲 Corpus selector in UI (which corpus to search: personal/branham/nbcm)

### 4.2 Corpus Browser
- ✅ aip history list / aip history show (CLI)
- 🔲 Domain distribution view (how many turns per domain)
- 🔲 Turn browser (search by domain, filter by importance, date range)
- 🔲 Turn detail view (full user+assistant+thinking, tags, bridges)
- 🔲 Domain proposal review UI (approve/reject Beast proposals)
- 🔲 Wiki article browser (read Beast-generated domain articles)

### 4.3 Knowledge Graph UI
- 🔲 Interactive graph visualization
- 🔲 Entity search and navigation
- 🔲 Relationship explorer
- (Depends on Phase 2.2)

### 4.4 Slot and Model Management
- ✅ Actor Roles panel in GUI
- ✅ Five slots visible (synthesis, beast, vigil, sexton, embedding)
- 🔲 Per-slot model selector in Actor Roles panel (not just top-bar)
- 🔲 Slot health indicator (green/red/grey with reason)
- 🔲 Budget display (tokens used this session, this month)

---

## PHASE 5 — Production and Scale
*Multi-user deployment, hardening, and sharing.*
*Status: NOT STARTED*

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

## Ongoing / Evergreen

- 🔄 Domain registry maintenance (review Beast proposals, update registry)
- 🔄 Corpus retag passes (after registry updates)
- 🔄 Monthly Claude export ingest
- 🔄 Other platform exports (GPT, DeepSeek, GLM, Gemini, xAI) as parsers are built
- 🔄 STATUS.md kept current after each build session
- 🔄 ADRs written for each significant architectural decision

---

## Version History

| Date       | Change                                      | Author  |
|------------|---------------------------------------------|---------|
| 2026-06-04 | Initial roadmap created from repo audit     | Claude + Moses |
| 2026-06-04 | Phase 1 corpus work reflected               | Claude + Moses |
