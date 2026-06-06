# AIP — AI Poiesis

[![CI](https://github.com/freedomgeneration1111-sudo/AIP_Brain/actions/workflows/ci.yml/badge.svg)](https://github.com/freedomgeneration1111-sudo/AIP_Brain/actions/workflows/ci.yml)

**Product:** AI Poiesis (AIP) v0.1
**Status:** Alpha — dogfood-ready vertical slice (ingest → ask → review → approve → export)

A local-first sovereign knowledge engine with a three-layer architecture (foundation, orchestration, adapter), Protocol-based dependency injection, and an honest evaluation pipeline.

> **No artifact may bypass DEFINER gates (§1.7).**

## Alpha Status and Known Boundaries

AIP v0.1 is alpha software in active development by a single DEFINER
(B. Moses Jorgensen). The following capabilities are working:

- Conversation ingestion from Claude exports (aip corpus ingest)
- Beast turn tagging with 28-domain registry (aip corpus tag)
- FTS5 full-text search across 2,700+ tagged turns
- Source-grounded augmented chat (CHAT and AUGMENTED modes)
- ECS artifact lifecycle (GENERATED → REVIEWED → APPROVED)
- DEFINER review and approval gates
- Markdown export with provenance

The following are planned but not yet built:
- Vector embeddings (vectors.db is empty — FTS5 only for now)
- Beast wiki generation (domain articles)
- Knowledge graph (entity extraction + Cytoscape.js visualization)
- DEFINER profile injection in augmented chat
- Multi-corpus support (Branham research corpus, NBCM citations)

The following are scaffold (real structure, placeholder dispatch):
- MCP tool dispatch
- Adaptive router weight learning
- ScriptNode production execution

See ROADMAP.md for the full phased build plan.
See STATUS.md for current test counts and known issues.

## Quick Start

1. **Clone:** `git clone https://github.com/freedomgeneration1111-sudo/AIP_Brain`
2. **Install:** `cd AIP_Brain && uv sync`
3. **Configure:** `cp config/aip.config.toml.example config/aip.config.toml` — add your OpenRouter API key
4. **Bootstrap:** `bash examples/seed_corpus/seed_bootstrap.sh`
5. **Start:** `./scripts/start.sh`
6. **Open:** [http://127.0.0.1:8080](http://127.0.0.1:8080)

The bootstrap seeds AIP with its own self-knowledge (51 entities, 23 Q&A turns about AIP architecture). Augmented chat works immediately.

**Full first-run guide:** [`DOGFOOD_READY.md`](DOGFOOD_READY.md)

## CLI Usage

```bash
# System
uv run aip init                              # Initialize databases and config
uv run aip status                            # Show system status and store health

# Project management
uv run aip project create --name X --domain Y  # Create a project
uv run aip project list                       # List projects

# Ingestion
uv run aip ingest file <path> --project X     # Import a conversation file
uv run aip ingest directory <path> --project X # Import all files in a directory
# (also supports --domain instead of --project)

# Ask
uv run aip ask "<question>" --project X       # Ask a source-grounded question
uv run aip ask "<question>" --project X --save-artifact  # Save answer as draft artifact
uv run aip ask "<question>" --project X --show-context   # Show retrieved context

# Review
uv run aip review list --project X            # List artifacts pending review
uv run aip review show <artifact_id>          # Show artifact content and lifecycle
uv run aip review sources <artifact_id>       # Show source/provenance links
uv run aip review approve <artifact_id>       # Approve (GENERATED → REVIEWED → APPROVED)
uv run aip review reject <artifact_id> --note "reason"  # Reject artifact
uv run aip review needs-revision <artifact_id> --note "instruction"  # Request revision

# Export
uv run aip export artifact <artifact_id> --format markdown --out ./out.md
uv run aip export project <name> --format markdown --out ./out.md
```

## Running Tests

```bash
# Full test suite (900+ tests)
uv run pytest

# Dogfood smoke test (clean-checkout verification)
bash scripts/dogfood_smoke_test.sh

# Specific modules
uv run pytest tests/test_ingestion.py
uv run pytest tests/test_ask.py
uv run pytest tests/test_review_export.py

# With coverage
uv run pytest --cov=aip --cov-report=term-missing
```

## Lint & Format

```bash
uv run ruff format .     # Format all Python files
uv run ruff check .      # Lint check (blocking in CI)
```

## What AIP Does

AIP manages the lifecycle of knowledge artifacts — from specification through synthesis, evaluation, and canonical promotion — with:

- **DEFINER sovereignty**: Every artifact promotion requires gate approval. No UI, workflow, Beast cadence, MCP call, or queued task may bypass the DEFINER gates.
- **Honest evaluation pipeline**: The evaluation pipeline does not silently pass artifacts. CI fixture data is
flagged (`ci_fixture=True`) and blocked from production promotion. Default scores are 0.0 on evaluation
failure.
- **Real model dispatch**: `ModelSlotResolver` supports Ollama and OpenAI-compatible HTTP calls with environment variable configuration. No model call silently returns a placeholder.
- **Async-safe storage**: All adapter-layer SQLite stores use `aiosqlite` — no blocking `sqlite3.connect()` inside async methods.
- **Unified datastore**: All CLI commands use the same database path (initialized by `aip init`). No manual `--db-path` needed for the normal dogfood path.
- **Source-grounded answers**: Every generated answer includes provenance back to ingested sources. No fabricated information.
- **Review and export**: Artifacts go through explicit ECS lifecycle transitions. Only APPROVED artifacts export without `--force`.

## Architecture

```
src/aip/
├── foundation/          # Pure validation, schemas, protocols, ECS graph
│   ├── ecs_graph.py     # Declarative ECS state machine (gold standard)
│   ├── protocols/       # Protocol interfaces for DI (7 domain modules)
│   ├── schemas/         # Dataclass definitions (ingestion, ask, review, etc.)
│   └── validation.py    # Structural validation rules
├── orchestration/       # Business logic, evaluation, actors
│   ├── ingestion/       # Parse → persist → chunk → index pipeline
│   ├── ask_pipeline.py  # Retrieve → assemble → dispatch → persist pipeline
│   ├── review_export_pipeline.py  # Review, approve, reject, export pipeline
│   ├── actors/          # Beast (health/corpus), Vigil (canonical monitoring)
│   ├── sexton/          # Failure classification, intervention rules
│   └── workflow/        # YAML-driven workflow engine
└── adapter/             # External interfaces and storage
    ├── api/             # FastAPI app + 11 routers
    ├── cli/             # Click-based CLI (init, status, ingest, ask, review, export)
    ├── artifact_store_versioned.py # Version-preserved artifact storage
    ├── ecs_store_persistent.py # Persistent ECS state machine
    ├── event_store_queryable.py  # Append-only event store with query
    ├── lexical/         # FTS5 full-text search store
    ├── vector/          # pgvector / sqlite-vss / in-memory factory
    ├── canonical/       # Canonical store with DEFINER enforcement
    ├── project/         # SQLite project store
    └── ...              # Auth, budget, vigil, embedding, autonomy stores
```

Layer discipline: `adapter` → `foundation` only; `orchestration` → `foundation` only; `adapter` never imports `orchestration` directly.

## Configuration

Primary config file: `config/aip.config.toml`

Key sections:
- `[database]` — `db_path` (default: `db/state.db`)
- `[vector_backend]` — provider, host, port
- `[embedding]` — provider ("fake" for CI, "ollama" for real)
- `[auth]` — auth_enabled, session timeout, bcrypt rounds
- `[deployment]` — profile (laptop/production), vector backend, model provider
- `[budget]` — token limits and warning thresholds
- `[beast]` — health check and corpus maintenance intervals
- `[review]` — faithfulness/coherence thresholds, definer approval requirements

Environment variable overrides:
- `AIP_DB_PATH` — Override default database path
- `AIP_SYNTHESIS_BASE_URL` — Synthesis model API endpoint
- `AIP_SYNTHESIS_MODEL` — Synthesis model name
- `AIP_SYNTHESIS_API_KEY` — Synthesis model API key
- `AIP_OLLAMA_BASE_URL` — Ollama endpoint (default: `http://localhost:11434`)
- `AIP_<SLOT>_BASE_URL` — Per-slot provider URL override
- `CI=true` — CI mode (deterministic fixtures, no network calls)

## Current Capabilities

| Capability | Status | Notes |
|---|---|---|
| Conversation ingestion (ChatGPT, markdown, plaintext) | Working | `aip ingest file/directory` |
| FTS5 full-text search | Working | Persistent lexical index |
| Source-grounded ask pipeline | Working | `aip ask` with provenance |
| ECS state machine (persistent SQLite) | Working | SPECIFIED→GENERATED→REVIEWED→APPROVED→SUPERSEDED |
| Review, approve, reject, needs-revision | Working | `aip review list/show/sources/approve/reject/needs-revision` |
| Markdown export with metadata | Working | `aip export artifact/project` |
| Unified datastore (single db/state.db) | Working | All CLI commands share same DB |
| Project-domain alignment (--project on ingest) | Working | `aip ingest --project X` resolves domain |
| Model provider dispatch (Ollama, OpenAI) | Working | Environment variable configuration |
| DEFINER gate (AUTO_APPROVE_STUB + MANUAL) | Working | MANUAL uses ReviewQueueStore |
| Canonical promotion with DEFINER approval | Working | |
| Beast actor (health, corpus, entity checks) | Working | Background scheduler |
| Autonomy gate with audit trail | Working | |
| FastAPI API (11 routers) | Working | |
| L4 trajectory monitoring | Working | |
| Vigil model-slot re-evaluation | Working | |
| Sexton intervention derivation | Working | |
| Config safety validation | Working | |
| Vector search (pgvector / sqlite-vss / in-memory) | Working | |
| Claude export ingestion (conversations.json) | Working | aip corpus ingest |
| Beast domain tagging (28 domains) | Working | aip corpus tag --limit N --retag |
| Domain registry (beast_domain_registry_v1.md) | Working | 26+ domains, event-driven |
| Beast context advisory (augmented chat) | Working | Domain overview + retrieved turns |
| Corpus turn store (FTS5 indexed) | Working | 2,700+ turns, thinking_text preserved |
| Vector embeddings | Not built | vectors.db empty — Phase 1.4 |
| Beast wiki articles | Not built | Phase 2A |
| Knowledge graph | Not built | Phase 2B |
| DEFINER profile injection | Not built | Phase 2A |
| Dogfood smoke test | Working | `bash scripts/dogfood_smoke_test.sh` |
| MCP tool server | Scaffold | Returns structured NOT_IMPLEMENTED |
| ScriptNode execution | Disabled | Production safe |

## CI

CI runs on every push and pull request to `main`. The workflow (`.github/workflows/ci.yml`) installs
dependencies with `uv`, runs both `ruff format --check .` and `ruff check .` as **blocking gates**, and executes
the full test suite with `CI=true`.

## Governance

This component conforms to the [AIP Governance Contract](AIP_GOVERNANCE.md)
(invariants AIP-G-01 through AIP-G-11). Conformance is checked by
`tests/test_governance_conformance.py`. See the contract's conformance
matrix for this component's current status, including any open findings.

## Documentation

- [`DOGFOOD_READY.md`](DOGFOOD_READY.md) — First-run dogfood guide (start here!)
- `STATUS.md` — Current project status, maturity, and known issues
- `ROADMAP.md` — Phased build plan, Phase 0 through Phase 5
- `docs/decisions/` — Architecture Decision Records (ADR-001 through ADR-007)
- `docs/beast_domain_registry_v1.md` — Domain taxonomy for Beast corpus tagging
- `docs/entity_aliases.md` — Canonical entity name resolution for knowledge graph
- `examples/seed_corpus/` — AIP self-knowledge Q&A seed corpus + ingest script
- `docs/ARCHITECTURE.md` — Architecture overview and design principles
- `docs/CONFIGURATION.md` — Configuration reference
- `docs/internal/ingestion.md` — Ingestion pipeline documentation
- `docs/internal/ask.md` — Ask pipeline documentation
- `docs/internal/review_export.md` — Review and export pipeline documentation
- `deploy/README.md` — Docker deployment guide

## License

MIT — see [LICENSE](LICENSE).
