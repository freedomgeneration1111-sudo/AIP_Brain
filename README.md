# AIP — AI Poiesis

[![CI](https://github.com/freedomgeneration1111-sudo/aip/actions/workflows/ci.yml/badge.svg)](https://github.com/freedomgeneration1111-sudo/aip/actions/workflows/ci.yml)

**Product:** AI Poiesis (AIP) v0.1
**Status:** Alpha — Phase 9 stabilization complete, core architecture sound

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
│   ├── protocols.py     # Protocol interfaces for DI
│   ├── schemas.py       # 730+ lines of dataclass definitions
│   └── validation.py    # Structural validation rules
├── orchestration/       # Business logic, evaluation, actors
│   ├── nodes/           # Pipeline nodes (synthesis, eval, definer_gate, commit)
│   ├── actors/          # Beast (health/corpus), Vigil (canonical monitoring)
│   └── workflow/        # YAML-driven workflow engine
└── adapter/             # External interfaces and storage
    ├── api/             # FastAPI app + 11 routers
    ├── auth/            # Session store, middleware, collaborator management
    ├── cli/             # Click-based CLI (init, status, session, project, config)
    ├── mcp/             # MCP tool server (stdio/SSE transport)
    ├── vector/          # pgvector / sqlite-vss / in-memory factory
    ├── canonical/       # Canonical store with DEFINER enforcement
    ├── autonomy/        # AutonomyGate with audit trail
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
# Full test suite (640 tests)
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

| Capability | Status |
|---|---|
| Artifact CRUD + versioned storage | Working |
| ECS state machine enforcement | Working |
| Structural validation (3 rules) | Working |
| L3a evaluation (faithfulness, domain coherence) | Working with model dispatch |
| L3b adversarial evaluation | Working with model dispatch |
| DEFINER gate (AUTO_APPROVE_STUB + MANUAL) | Working |
| Review pipeline (PENDING verdicts) | Working |
| Canonical promotion with DEFINER approval | Working |
| Model provider dispatch (Ollama, OpenAI) | Working |
| Ollama embedding client | Working |
| Beast actor (health, corpus, entity checks) | Working with background scheduler |
| Autonomy gate with audit trail | Working |
| FastAPI API (11 routers) | Working |
| CLI (init, status, session, project, config) | Working |
| Rate limiting (token bucket) | Working |
| FTS5 full-text search | Working |
| Vector search (pgvector / sqlite-vss / in-memory) | Working |
| Budget tracking | Working |
| L4 trajectory monitoring | Working |

## CI

CI runs on every push and pull request to `main`. The workflow (`.github/workflows/ci.yml`) installs
dependencies with `uv`, runs `ruff check` (soft — non-blocking for now), and executes the full test suite with
`CI=true`.

## Known Limitations

See `STATUS.md` for a detailed breakdown of stubs, scaffolding, and remaining work.

## Documentation

- `STATUS.md` — Current project status, maturity, and known issues
- `docs/implementation_status.md` — Detailed module-by-module analysis with scaffolding estimates
- `specs/` — Authoritative build specifications
- `deploy/README.md` — Docker deployment guide

## License

Proprietary — see repository settings.
