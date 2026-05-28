# Changelog

All notable changes to AIP 0.1 are documented here. This project follows the append-only convention — entries are added, never removed.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

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
