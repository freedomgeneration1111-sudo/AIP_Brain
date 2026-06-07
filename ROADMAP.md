# AIP Roadmap
# DEFINER: B. Moses Jorgensen
# Last Updated: 2026-06-07
# Process: Update this document after each significant build session or architectural decision.

---

## How to Read This Document

Status indicators:
- COMPLETE — built, tested, in production use
- IN PROGRESS — actively being built
- PLANNED — decided, not yet started
- PROPOSED — under consideration, not yet decided
- DEFERRED — decided to defer, reason noted

Architecture decisions are recorded in `docs/decisions/`. When a decision changes
the roadmap, update both documents.

---

## PHASE 0 — Foundation
*Core artifact lifecycle, storage, and evaluation pipeline.*
*Status: COMPLETE*

- COMPLETE Three-layer architecture (foundation → orchestration → adapter)
- COMPLETE ECS state machine (SPECIFIED→GENERATED→REVIEWED→APPROVED→SUPERSEDED)
- COMPLETE Persistent SQLite stores (artifacts, ECS, events, lexical, projects)
- COMPLETE FTS5 full-text search with domain filtering
- COMPLETE Model dispatch (Ollama + OpenAI-compatible, all slots)
- COMPLETE Review/approve/reject/export pipeline
- COMPLETE DEFINER sovereignty gates (no auto-approve in MANUAL mode)
- COMPLETE Auth system (laptop: disabled by default, production: required)
- COMPLETE FastAPI backend with 11 routers
- COMPLETE Click CLI (init, status, ingest, ask, review, export)
- COMPLETE CI gates (ruff format, ruff check, pytest 1000+ tests)
- COMPLETE Docker profiles (laptop + production)
- COMPLETE Beast actor (background scheduler, health checks, corpus maintenance)
- COMPLETE Vigil actor (model slot re-evaluation)
- COMPLETE Sexton actor (deterministic failure classification)
- COMPLETE Autonomy gate with audit trail
- COMPLETE Budget enforcement
- COMPLETE MCP server (scaffold — tool listing real, dispatch scaffold)

---

## PHASE 1 — Corpus Intelligence
*Turn-level corpus ingestion, tagging, and retrieval.*
*Status: IN PROGRESS*

### 1.1 Turn-Level Corpus Foundation
- COMPLETE CorpusTurn schema (atomic unit: user+assistant pair with thinking_text)
- COMPLETE CorpusTurnStore (SQLite + FTS5 + Beast tagging path)
- COMPLETE make_turn_id (deterministic, idempotent)
- COMPLETE thinking_text field (extended thinking preserved separately from assistant_text)

### 1.2 Source Parsers
- COMPLETE Claude export parser (conversations.json, handles all content block types)
- COMPLETE 2,691 turns ingested from claude_export_june_2026
- COMPLETE 1,743 turns with extended thinking blocks preserved
- PLANNED ChatGPT export parser (tree-structure conversation format)
- PLANNED DeepSeek export parser
- PLANNED GLM export parser
- PLANNED Gemini export parser
- PLANNED xAI/Grok export parser
- PLANNED Plain text / sermon transcript parser (for external corpora)
- PLANNED PDF parser (for academic papers and books)
- PLANNED Web crawl / sitestrip parser (for external research corpora)

### 1.3 Beast Turn Tagging
- COMPLETE Domain registry (docs/beast_domain_registry_v1.md)
- COMPLETE DomainRegistry loader (load_registry, DomainEntry, ConnectorEntry)
- COMPLETE Beast _run_turn_tagging (batch-8 LLM tagging)
- COMPLETE Domain proposal system (Beast proposes → DEFINER approves)
- COMPLETE Connector proposal system
- COMPLETE aip corpus tag CLI (--limit, --retag)
- COMPLETE 2,681 turns tagged (tagging_version > 0)
- COMPLETE Registry v1.0: 26 domains, 13 connectors
- IN PROGRESS Registry v1.1: aip hall model, ancient_archaeology, agi_philosophy
- PLANNED Registry v1.2: (future — based on Beast proposals and dogfood observations)

### 1.4 Embedding Pipeline
- COMPLETE OpenRouter embedding provider (openai_compatible)
- COMPLETE Runtime embedding provider updates via PATCH endpoint
- COMPLETE Embedding backfill endpoint (POST /admin/embeddings/backfill)
- PLANNED Embed corpus_turns.searchable_text using embedding slot
- PLANNED Store vectors keyed by turn_id in vector store
- PLANNED Hybrid FTS5+vector scoring in _search_sources
- PLANNED Background embedding pass in Beast cycle (embed after tagging)
- PLANNED Re-embedding pass when embedding model changes

### 1.5 Multi-Corpus Architecture
- PLANNED Corpus registry in config (named corpora with db_path)
- PLANNED --corpus flag on aip corpus ingest
- PLANNED Query-time corpus selection in augmented chat
- PLANNED Branham research corpus (1200 sermons + books + critic sites)
- PLANNED NBCM citations corpus (academic papers across relevant domains)
- SEE: ADR-004-multi-corpus-architecture.md

---

## PHASE 2 — Knowledge Synthesis
*Beast-generated wiki, knowledge graph, and cross-corpus intelligence.*
*Status: PARTIAL*

### 2.1 Beast Wiki Generation
- COMPLETE Domain article generation (300-500 words per active domain)
- COMPLETE Wiki articles as GENERATED artifacts → DEFINER review → APPROVED
- COMPLETE BeastContextPreparer reads approved wiki as domain overview
- PLANNED Wiki update triggered by corpus_modified events (not on timer)
- PLANNED Wiki versioning (new article supersedes old on regeneration)

### 2.2 Knowledge Graph
- COMPLETE Entity extraction from corpus_turns (people, concepts, projects, places)
- COMPLETE Relationship inference (who worked on what, what connects to what)
- COMPLETE Graph store (SQLite adjacency list)
- COMPLETE Graph-aware retrieval infrastructure (GraphRetriever with PPR)
- COMPLETE Graph visualization in UI (Cytoscape.js, /graph-viz)
- COMPLETE Entity alias registry (22 entries)
- PLANNED Graph retrieval wired into ask pipeline (see Retrieval Phase 3)
- SEE: ADR-007-knowledge-graph-architecture.md

### 2.3 Domain Export Packages
- PLANNED Export mechanism: filter corpus by domain → standalone package
- PLANNED Package format: db + wiki + graph + embeddings as archive
- PLANNED Versioned packages (v1.0, v2.0 as corpus grows)
- PLANNED Package recipient model (share without exposing personal corpus)
- SEE: ADR-004-multi-corpus-architecture.md

---

## PHASE 3 — Actor Intelligence
*Beast, Vigil, and Sexton functioning as genuine intelligence layer.*
*Status: PARTIAL*

### 3.1 Beast (Corpus Intelligence)
- COMPLETE Background scheduler (health check, corpus maintenance, entity check)
- COMPLETE Beast LLM slot (nvidia/nemotron-3-super-120b-a12b)
- COMPLETE Domain summary generation (event-driven, not timer-driven)
- COMPLETE BeastContextPreparer (retrieval + domain overview in augmented chat)
- COMPLETE Context advisory injected into synthesis model system prompt
- COMPLETE Cohort comparison (parallel model dispatch + Beast analysis)
- COMPLETE Beast soul.md (personality and epistemic stance)
- PLANNED Beast reads wiki artifacts as enhanced domain overview
- PLANNED Beast corpus health reporting (coverage gaps, stale artifacts)
- PLANNED Beast re-tagging trigger (when registry changes, retag affected turns)

### 3.2 Vigil (Quality Evaluation)
- COMPLETE Vigil scheduler (runs every 3600s)
- COMPLETE Vigil model slot (openai/gpt-oss-20b)
- COMPLETE Model slot change → mark canonicals for re-evaluation
- PLANNED Faithfulness scorer (does synthesis response reflect retrieved context?)
- PLANNED Drift detector (are Beast summaries still accurate as corpus grows?)
- PLANNED Quality gate (flag responses that cite sources poorly)
- PLANNED Vigil evaluation report as reviewable artifact

### 3.3 Sexton (Full Maintenance)
- COMPLETE Full-maintenance Sexton actor (actors/sexton.py, 1,341 lines, 5 ops)
- COMPLETE Deterministic rules for failure types A-F + 7 special conditions
- COMPLETE Sexton model slot (google/gemma-4-26b-a4b-it)
- **DEBT-006: NOT WIRED** — app.py still uses old sexton/sexton.py::run_classification_cycle()
- PLANNED Wire new Sexton into app.py lifespan (BUG-003)
- PLANNED LLM upgrade for complex multi-signal failure classification

---

## PHASE 4 — UI and Experience
*Making the knowledge engine usable and transparent.*
*Status: PARTIAL — Unified Chat COMPLETE*

### 4.1 Unified Chat UI
- COMPLETE Unified chat panel (BARE + AUGMENTED in single surface)
- COMPLETE Model selector with multi-select for cohort mode
- COMPLETE Chat mode picker (Engineering/Research/Ideation/Teaching)
- COMPLETE Beast pane (sticky, collapsible sidebar with scan + comparison)
- COMPLETE Cohort dispatch (parallel model dispatch with per-model cards)
- COMPLETE DEFINER profile edit in Settings
- COMPLETE Epistemic flags (no flattery, flag uncertainty, suggest validation, report conflicts)
- COMPLETE Beast pop-out (standalone Beast pane page)
- COMPLETE Jump-to-input floating button
- COMPLETE Actor log (compact STATUS widget + standalone /actor-log page)
- COMPLETE Auto-save to corpus on chat turn completion
- PLANNED Show source citations inline in response
- PLANNED Show Beast confidence and tagging version for retrieved turns
- PLANNED Corpus selector in UI (which corpus to search: personal/branham/nbcm)

### 4.2 Corpus Browser
- COMPLETE aip history list / aip history show (CLI)
- PLANNED Domain distribution view (how many turns per domain)
- PLANNED Turn browser (search by domain, filter by importance, date range)
- PLANNED Turn detail view (full user+assistant+thinking, tags, bridges)
- PLANNED Domain proposal review UI (approve/reject Beast proposals)
- PLANNED Wiki article browser (read Beast-generated domain articles)

### 4.3 Knowledge Graph UI
- COMPLETE Interactive graph visualization (Cytoscape.js, /graph-viz)
- COMPLETE Entity search and navigation
- PLANNED Relationship explorer with retrieval trace view

### 4.4 Slot and Model Management
- COMPLETE Actor Roles panel in GUI
- COMPLETE Five slots visible (synthesis, beast, vigil, sexton, embedding)
- COMPLETE Model library CRUD (fetch from OpenRouter, add custom models)
- PLANNED Per-slot model selector in Actor Roles panel (not just top-bar)
- PLANNED Slot health indicator (green/red/grey with reason)
- PLANNED Budget display (tokens used this session, this month)

---

## PHASE 5 — Retrieval Architecture
*Unified retrieval substrate: protocol, fusion, entity-turn index, GraphRetriever, wiki, context.*
*Status: COMPLETE (Phases 5.0–5.6)*
*Authoritative plan: docs/retrieval/AIP_RETRIEVAL_BUILD_MEMO.md*

### 5.0 Phase 0 — Measurement and Trace
- COMPLETE Retrieval golden tests (tests/retrieval_goldens/) — 6 queries (Komal, GEF RF heating, frost alert, AIP features, retrieval architecture, FGS school)
- COMPLETE Retrieval trace instrumentation — RetrievalTrace dataclass with full trace fields
- COMPLETE Before/after CLI or debug endpoint — eval_retrieval.py
- COMPLETE Current baseline measurements

### 5.1 Phase 1 — Protocol Substrate
- COMPLETE Retriever protocol (Retriever(Protocol)) — @runtime_checkable with name + retrieve()
- COMPLETE RetrievalHit / RetrievalList / RetrievalTrace dataclasses
- COMPLETE ContextBudget with token allocation (evidence 60%, wiki 15%, procedural 5%, graph 5%)
- COMPLETE RRF fusion service (k=60, importance/confidence/evidence modifiers)
- COMPLETE FTSRetriever wrapped into protocol
- COMPLETE RetrievalOrchestrator with parallel dispatch + fusion

### 5.2 Phase 2 — Entity-Turn Index and Coverage
- COMPLETE entity_turn_index schema (entity_id, turn_id, confidence, source)
- COMPLETE Backfill from evidence_turn_ids_json
- COMPLETE Write during Sexton extraction
- COMPLETE Staleness prune
- COMPLETE Mention scan with type filter + alias rules
- COMPLETE Hub leash (weight / log(degree + 1))
- COMPLETE GraphRetriever with Zone A (direct mentions) + Zone B (PPR expansion)

### 5.3 Phase 3 — GraphRetriever + Query Expansion + Wiki
- COMPLETE EntitySeedSelector (exact, alias, acronym, phrase, FTS5, token overlap)
- COMPLETE networkx graph builder with cache
- COMPLETE Direct-mention zone (Zone A)
- COMPLETE PPR expansion zone (Zone B)
- COMPLETE Hub leash and confidence/importance scoring
- COMPLETE Graph retrieval trace
- COMPLETE Conforming RetrievalList output
- COMPLETE RRF fusion with FTS/vector
- COMPLETE LLM-powered query expansion (fast model, structured JSON output)
- COMPLETE WikiRetriever with domain selection + budgeted injection
- COMPLETE Sexton entity-turn writes during tagging

### 5.4 Phase 4 — Vector + LLM Expansion
- COMPLETE VectorRetriever (768-dim embedding similarity via SqliteVssVectorStore)
- COMPLETE Semantic wiki matching (embedding-based domain article selection)
- COMPLETE Query expansion refinements
- COMPLETE Trace and configuration polish

### 5.5 Phase 5 — Context Packer and Answer Quality
- COMPLETE SmartContextPacker — Budget-aware, structured context assembly (4 sections: evidence, wiki, procedural, graph)
- COMPLETE ProceduralRetriever — How-to/procedure artifact retrieval with procedural query detection
- COMPLETE AnswerQualityGate — Heuristic context sufficiency check (4 dimensions: coverage, confidence, diversity, freshness)
- COMPLETE TraceStore — SQLite-backed trace persistence with quality metrics
- COMPLETE ContextQualityStatus enum (SUFFICIENT, MARGINAL, NEEDS_MORE_CONTEXT, EMPTY)

### 5.6 Phase 6 — Autonomy, Quality & Observability
- COMPLETE Auto-Retry on NEEDS_MORE_CONTEXT — Second retrieval round with strategy escalation (max 1 retry)
- COMPLETE Context Compression / Smart Truncation — Extractive summarization for long evidence hits
- COMPLETE Trace Dashboard Foundation — Dashboard summary, retry stats, retriever contribution stats
- COMPLETE Quality Gate Enhancements — Optional model-assisted sufficiency check for MARGINAL cases

---

## PHASE 6 — Production and Scale
*Multi-user deployment, hardening, and sharing.*
*Status: NOT STARTED*

- PLANNED Multi-user support (per-user corpora, shared canonicals)
- PLANNED Real MCP tool dispatch (search, approve, config via MCP)
- PLANNED Adaptive router (weight routes from outcomes, not random)
- PLANNED ScriptNode sandbox (safe execution environment)
- PLANNED Streaming model support
- PLANNED PostgreSQL migration for production
- PLANNED Review queue web UI for MANUAL mode
- PLANNED Per-component performance metrics (not estimated)
- PLANNED Onboarding flow for new users (export import wizard)

---

## Ongoing / Evergreen

- Domain registry maintenance (review Beast proposals, update registry)
- Corpus retag passes (after registry updates)
- Monthly Claude export ingest
- Other platform exports (GPT, DeepSeek, GLM, Gemini, xAI) as parsers are built
- STATUS.md kept current after each build session
- ADRs written for each significant architectural decision

---

## Spec Documents — Status Tracker

| Document | Location | Status | Notes |
|----------|----------|--------|-------|
| AIP_UNIFIED_CHAT_SPEC.md | docs/ | IMPLEMENTED | Phases 1-4 complete |
| AIP_CORPUS_LIFECYCLE_SPEC.md | docs/ | PARTIAL | Commits 1-3 done; 4-5 deferred to retrieval work |
| AIP_BRAIN_RETRIEVAL_ARCHITECTURE_MEMO.md | docs/retrieval/ | SUPERSEDED | By build memo. Still valid as reference |
| RETRIEVAL_REVIEW_SYNTHESIS.md | docs/retrieval/ | REFERENCE | Ensemble review. Key lesson: code-grounded reviews disagreed with memo-grounded reviews |
| AIP_RETRIEVAL_BUILD_MEMO.md | docs/retrieval/ | AUTHORITATIVE | Current build plan. 23 sections, 6-phase build order |
| AIP_PROJECT_STATUS.md | docs/ | AUTHORITATIVE | Canonical state document for thread onboarding |

---

## Version History

| Date       | Change                                      | Author  |
|------------|---------------------------------------------|---------|
| 2026-06-04 | Initial roadmap created from repo audit     | Claude + Moses |
| 2026-06-04 | Phase 1 corpus work reflected               | Claude + Moses |
| 2026-06-07 | Unified Chat phases 1-4 + hygiene + bug fixes reflected | GLM + Moses |
| 2026-06-07 | Retrieval Architecture Phase 5 added (6 sub-phases) | GLM + Moses |
| 2026-06-07 | Phase 2 Knowledge Synthesis updated to PARTIAL | GLM + Moses |
| 2026-06-07 | Phase 3 Actor Intelligence updated to PARTIAL (DEBT-006 noted) | GLM + Moses |
| 2026-06-07 | Phase 4 UI updated to PARTIAL — Unified Chat complete | GLM + Moses |
| 2026-06-07 | Spec documents tracker added | GLM + Moses |
