# AIP — AI Poiesis

[![CI](https://github.com/freedomgeneration1111-sudo/aip/actions/workflows/ci.yml/badge.svg)](https://github.com/freedomgeneration1111-sudo/aip/actions/workflows/ci.yml)

**Product:** AI Poiesis (AIP) v0.1
**Status:** Alpha — stabilization pass complete, no fake runtime success paths

A local-first sovereign knowledge engine with a three-layer architecture (foundation, orchestration, adapter), Protocol-based dependency injection, and an honest evaluation pipeline.

> **No artifact may bypass DEFINER gates (§1.7).**

## What AIP Does

AIP manages the lifecycle of knowledge artifacts — from specification through synthesis, evaluation, and canonical promotion — with:

- **DEFINER sovereignty**: Every artifact promotion requires gate approval. No UI, workflow, Beast cadence, MCP call, or queued task may bypass the DEFINER gates.
- **Honest evaluation pipeline**: The evaluation pipeline does not silently pass artifacts. CI fixture data is
flagged (`ci_fixture=True`) and blocked from production promotion. Default scores are 0.0 on evaluation
failure.
- **Real model dispatch**: `ModelSlotResolver` supports Ollama and OpenAI-compatible HTTP calls with environment variable configuration. No model call silently returns a placeholder.
- **Async-safe storage**: All adapter-layer SQLite stores use `aiosqlite` — no blocking `sqlite3.connect()` inside async methods.
- **L4 trajectory monitoring**: Failure streak detection, anxiety detection, and loop detection with configurable thresholds and context reset.
- **Autonomy gate**: All privileged writes (admin escalation, artifact promotion) go through an audited AutonomyGate. Non-DEFINERs cannot bypass authorization.

## Architecture

```
src/aip/
├── foundation/          # Pure validation, schemas, protocols, ECS graph
│   ├── ecs_graph.py     # Declarative ECS state machine (gold standard)
│   ├── protocols/       # Protocol interfaces for DI (7 domain modules)
│   │   ├── storage.py   # VectorStore, LexicalStore, CanonicalStore, ArtifactStore, etc.
│   │   ├── model.py     # ModelProvider, EmbeddingProvider
│   │   ├── auth.py      # AuthStore, AutonomyGate
│   │   ├── budget.py    # BudgetStore
│   │   ├── actors.py    # VigilStore
│   │   ├── knowledge.py # KnowledgeStore
│   │   └── plugin.py    # PluginProvider
│   ├── schemas/         # Dataclass definitions (11 domain modules)
│   │   ├── base.py      # EcsState, ContractRule, Event, etc.
│   │   ├── auth.py      # AutonomyLevel, AuthRole, CollaboratorConfig
│   │   ├── evaluation.py # EvaluationScore, FaithfulnessResult, FailureClassification
│   │   ├── review.py    # ReviewVerdict, CanonicalPromotionConfig
│   │   └── ...          # retrieval, trajectory, workflow, vector, budget, surface, config
│   └── validation.py    # Structural validation rules
├── orchestration/       # Business logic, evaluation, actors
│   ├── actors/          # Beast (health/corpus), Vigil (canonical monitoring)
│   │   ├── beast.py     # Health checks, corpus maintenance, entity consistency
│   │   └── vigil.py     # Canonical health monitoring, model-slot re-evaluation
│   ├── sexton/          # Failure classification, intervention rules, ACE playbooks
│   ├── workflow/        # YAML-driven workflow engine
│   │   ├── node.py      # ScriptNode (disabled), AgentNode, ConditionNode, ReviewNode, etc.
│   │   ├── runner.py    # SequentialRunner with pause/resume
│   │   ├── loader.py    # YAML workflow parser
│   │   └── workflow_01.py # Default workflow with evaluation + commit pipeline
│   ├── canonical_pipeline.py # Artifact evaluation + promotion pipeline
│   ├── review.py        # Review orchestration (PENDING verdicts in production)
│   └── ...              # retrieval, session, router, budget, recovery, embed_providers
└── adapter/             # External interfaces and storage
    ├── api/             # FastAPI app + 11 routers
    ├── auth/            # Session store, middleware, collaborator management
    ├── cli/             # Click-based CLI (init, status, session, project, config)
    ├── mcp/             # MCP tool server (scaffold — returns structured NOT_IMPLEMENTED)
    ├── vector/          # pgvector / sqlite-vss / in-memory factory + migration
    ├── canonical/       # Canonical store with DEFINER enforcement
    ├── autonomy/        # AutonomyGate with audit trail
    ├── ecs_store_persistent.py # SQLite-backed ECS store (survives restart)
    ├── review_queue_store.py   # SQLite-backed review queue for MANUAL mode
    └── ...              # Entity, event, budget, vigil, lexical, embedding stores
```

Layer discipline: `adapter` → `foundation` only; `orchestration` → `foundation` only; `adapter` never imports `orchestration` directly.

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- Ollama (optional, for real model inference)

### Install & Run

```bash
# Clone and install
git clone https://github.com/freedomgeneration1111-sudo/aip.git
cd aip
uv sync

# Initialize a project (creates config, database files)
uv run aip init

# Start the API server
uv run uvicorn aip.adapter.api.app:create_app --factory --reload

# Or use Docker (laptop profile — SQLite + Ollama)
cd deploy
docker compose --profile laptop up --build
```

### Running Tests

```bash
# Full test suite (760+ tests)
uv run pytest

# Specific modules
uv run pytest tests/test_definer_gate.py
uv run pytest tests/acceptance/

# With coverage
uv run pytest --cov=aip --cov-report=term-missing
```

### CLI Usage

```bash
uv run aip init          # Initialize project with hardware detection
uv run aip status        # Show system status and store health
uv run aip session start # Start a new session
uv run aip project list  # List projects
uv run aip config show   # Display current configuration
```

### Lint & Format

```bash
uv run ruff format .     # Format all Python files
uv run ruff check .      # Lint check (blocking in CI)
```

## Configuration

Primary config file: `config/aip.config.toml`

Key sections:
- `[embedding]` — provider ("fake" for CI, "ollama" for real)
- `[auth]` — auth_enabled, session timeout, bcrypt rounds
- `[deployment]` — profile (laptop/production), vector backend, model provider
- `[budget]` — token limits and warning thresholds
- `[beast]` — health check and corpus maintenance intervals
- `[review]` — faithfulness/coherence thresholds, definer approval requirements

Environment variable overrides:
- `AIP_OLLAMA_BASE_URL` — Ollama endpoint (default: `http://localhost:11434`)
- `AIP_OPENAI_API_KEY` — OpenAI-compatible API key
- `AIP_<SLOT>_BASE_URL` — Per-slot provider URL override
- `CI=true` — CI mode (deterministic fixtures, no network calls)

## Current Capabilities

| Capability | Status | Notes |
|---|---|---|
| Artifact CRUD + versioned storage | Working | |
| ECS state machine (persistent SQLite) | Working | Survives restart |
| Structural validation (3 rules) | Working | |
| L3a evaluation (faithfulness, domain coherence) | Working | Model dispatch or CI fixture |
| L3b adversarial evaluation | Working | Model dispatch or CI fixture |
| DEFINER gate (AUTO_APPROVE_STUB + MANUAL) | Working | MANUAL uses ReviewQueueStore |
| Review pipeline (PENDING verdicts) | Working | No auto-approve in production |
| Canonical promotion with DEFINER approval | Working | |
| Model provider dispatch (Ollama, OpenAI) | Working | |
| Ollama embedding client | Working | |
| Beast actor (health, corpus, entity checks) | Working | Background scheduler |
| Autonomy gate with audit trail | Working | |
| FastAPI API (11 routers) | Working | |
| CLI (init, status, session, project, config) | Working | |
| Rate limiting (token bucket) | Working | |
| FTS5 full-text search | Working | |
| Vector search (pgvector / sqlite-vss / in-memory) | Working | |
| Vector migration (cursor + probe) | Working | Cursor-based when supported |
| Budget tracking | Working | |
| L4 trajectory monitoring | Working | |
| Vigil model-slot re-evaluation | Working | Marks canonicals for re-evaluation |
| Sexton intervention derivation | Working | Deterministic rules for known conditions |
| Performance API | Working | Returns DISABLED/BACKEND_UNAVAILABLE when unconfigured |
| MCP tool server | Scaffold | Tool listing + autonomy gate real; dispatch returns structured NOT_IMPLEMENTED |
| ScriptNode execution | Disabled | Returns structured DISABLED in production; fixture mode only for tests |
| Config safety validation | Working | Blocks unsafe production configs at startup |

## CI

CI runs on every push and pull request to `main`. The workflow (`.github/workflows/ci.yml`) installs
dependencies with `uv`, runs both `ruff format --check .` and `ruff check .` as **blocking gates**, and executes
the full test suite with `CI=true`.

## Alpha Limitations

- **MCP dispatch is scaffold**: Tool listing and autonomy gate are real, but actual tool dispatch returns
  structured `NOT_IMPLEMENTED` responses. Search, approval, and config operations do not execute real logic
  through MCP yet.
- **ScriptNode is disabled**: Production workflows cannot execute arbitrary scripts. Fixture mode allows test
  simulation only.
- **Adaptive router is not adaptive**: `update_weights()` is a no-op. Exploration/exploitation uses random
  sampling.
- **Review queue UI is minimal**: MANUAL mode review is functional via API/CLI, but there is no web-based
  approval interface.
- **Per-component performance metrics are estimated**: The PerformanceProfiler uses proportional breakdown
  rather than per-component measurement.
- **Vigil marks all canonicals on model-slot change**: Conservative re-evaluation marking; a more targeted
  approach is deferred.

## Documentation

- `STATUS.md` — Current project status, maturity, and known issues
- `docs/ARCHITECTURE.md` — Architecture overview and design principles
- `docs/CONFIGURATION.md` — Configuration reference
- `docs/implementation_status.md` — Detailed module-by-module analysis
- `deploy/README.md` — Docker deployment guide

## License

MIT — see [LICENSE](LICENSE).
