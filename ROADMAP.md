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
- ✅ 52 turns from claude_export_2024_2025 ingested
- ✅ 1,743 turns with extended thinking blocks preserved
- ✅ Seed corpus Q&A (32 turns, examples/seed_corpus/)
- ✅ DEFINER profile v1.0 drafted (examples/seed_corpus/definer_profile_v1.md)
- ✅ 52 turns ingested from claude_export_2024_2025
- ✅ Seed corpus Q&A (32 turns, examples/seed_corpus/)
- ✅ DEFINER profile v1.0 drafted (examples/seed_corpus/definer_profile_v1.md)
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
- ✅ Registry v1.1 (aip hall model, ancient_archaeology, agi_philosophy)
- 🔲 Entity alias table (docs/entity_aliases.md) for co-reference resolution
- 🔲 Entity alias table (docs/entity_aliases.md) for co-reference resolution
- 🔲 Registry v1.2: (future — based on Beast proposals and dogfood observations)

### 1.4 Embedding Pipeline
- 🔲 Embed corpus_turns.searchable_text using embedding slot
- 🔲 Store vectors keyed by turn_id in vector store
- 🔲 Hybrid FTS5+vector scoring in _search_sources
- 🔲 Background embedding pass in Beast cycle (embed after tagging)
- 🔲 Re-embedding pass when embedding model changes
- 🔲 DEFINER profile injection in augmented chat (profile prepended to system prompt)
- 🔲 DEFINER profile injection in augmented chat (tiered, ~600 tokens)

### 1.5 Multi-Corpus Architecture
- 🔲 Corpus registry in config (named corpora with db_path)
- 🔲 --corpus flag on aip corpus ingest
- 🔲 Query-time corpus selection in augmented chat
- 🔲 Branham research corpus (1200 sermons + books + critic sites)
- 🔲 NBCM citations corpus (academic papers across relevant domains)
- SEE: ADR-004-multi-corpus-architecture.md

---

## PHASE 2A — Beast Wiki
*Concept-level knowledge base, human-browsable and LLM-injectable.*
*Status: NOT STARTED — planned after embedding pipeline*
*Reference: ADR-006-wiki-architecture.md*

### Design Principles
- Beast-generated, DEFINER-approved — never auto-canonical
- Scope: domain-level first (28 articles), expanding to concept-level
  (NBCM alone may reach 100 articles, brick kiln 50+)
- Dual purpose: human browsing to reconnect with thinking +
  LLM orientation context injected into augmented chat
- Publication pipeline: approved wiki articles are the spine of
  manuscripts (Architecture of Mercy, NBCM paper, bonded labor doc)
- Trigger: event-driven. Beast wiki pass when cumulative new tokens
  processed in a domain exceeds ~1M tokens since last generation.
  Not timer-driven.

### 2A.1 Wiki Generation
- 🔲 Beast wiki article generation (domain-level first, 28 articles)
- 🔲 Article structure: Overview / Key Concepts / Cross-Domain
     Connections / Current State / Evolution / Key Turns / Open Questions
- 🔲 Overview section (3-5 sentences) injected into augmented chat
- 🔲 Wiki articles as GENERATED → DEFINER review → APPROVED
- 🔲 Token-threshold trigger per domain (~1M new tokens)
- 🔲 Wiki versioning (ECS: new article supersedes old)

### 2A.2 Wiki UI
- 🔲 Built-in markdown editor in GUI
- 🔲 DEFINER injects <comment> tags at convenience
- 🔲 Reviewed/unreviewed indicator per article
- 🔲 Article list view filterable by domain and review status
- 🔲 Publication export: approved articles exportable as manuscript sections

### 2A.3 DEFINER Profile System
- 🔲 Tiered profile injection in augmented chat (~600 tokens)
- 🔲 DEFINER direct edit via UI markdown editor (immediate effect)
- 🔲 Vigil metacognition cycle: proposes amendments as GENERATED artifacts
- 🔲 Beast pattern detection: flags emerging corpus patterns not in profile

## PHASE 2B — Knowledge Graph
*Entity graph as interactive mind map for cross-domain synthesis.*
*Status: NOT STARTED — planned after Phase 2A wiki*
*Reference: ADR-007-knowledge-graph-architecture.md*

### Design Principles
- Mind map for complex work — interactive, filterable, thought-provoking
- Beast discovers entities independently (not seeded from profile)
- Cross-domain connections are primary value — bridge tags are most
  important edges
- HippoRAG-inspired: Personalized PageRank (PPR) on schemaless KG
  for associative multi-hop retrieval in single traversal step
- Entity alias table resolves co-reference and terminology evolution
- Confidence tiers: >0.7 displayed, 0.4-0.7 on request, <0.4 stored
- Storage: SQLite adjacency tables — adequate to 50,000+ nodes
- Bridge tags from corpus_turns.bridges are the first graph edges
  (no LLM extraction needed for initial build)

### 2B.1 Graph Storage
- 🔲 graph_nodes table in state.db
- 🔲 graph_edges table in state.db
- 🔲 Entity types: PERSON, PROJECT, CONCEPT, PLACE, ORGANIZATION, MANUSCRIPT
- 🔲 Relationship types: WORKS_ON, CONNECTS, LOCATED_IN, FUNDED_BY,
     AUTHORED, RELATES_TO
- 🔲 docs/entity_aliases.md (canonical co-reference resolution)

### 2B.2 Graph Construction
- 🔲 Phase 1: Bridge tags as seed edges (immediate, no LLM extraction)
- 🔲 Phase 2: Beast OpenIE entity extraction on high-importance turns
- 🔲 PPR retrieval: NetworkX nx.pagerank() with personalization vector
- 🔲 Similarity edges after embedding pipeline
- 🔲 Incremental updates triggered by corpus_modified events

### 2B.3 Graph UI
- 🔲 Cytoscape.js interactive visualization in GUI
- 🔲 Mind map mode: filterable by domain, entity type, confidence
- 🔲 Node detail panel: entity info, connected turns, wiki article link
- 🔲 Graph-augmented retrieval in augmented chat (PPR expansion)

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
- 🔄 Wiki article review and approval (Beast generates, DEFINER approves)
- 🔄 Entity alias table maintenance (evolving terminology resolution)
- 🔄 Graph health report review (orphan nodes, co-reference proposals)
- 🔄 Wiki article review and approval
- 🔄 Entity alias table maintenance
- 🔄 Graph health report review

---

## Version History

| Date       | Change                                      | Author  |
|------------|---------------------------------------------|---------|
| 2026-06-04 | Initial roadmap created from repo audit     | Claude + Moses |
| 2026-06-04 | Phase 1 corpus work reflected               | Claude + Moses |
| 2026-06-04 | Phase 2A wiki + Phase 2B graph added from research | Claude + Moses |
| 2026-06-04 | Phase 2A wiki + Phase 2B graph + HippoRAG adoption | Claude + Moses |
