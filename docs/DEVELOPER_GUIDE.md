# Developer Guide

## Prerequisites

- **Python 3.11+** (3.12 recommended)
- **uv** package manager ([install guide](https://docs.astral.sh/uv/))
- **SQLite 3.39+** (bundled with Python; required for sqlite-vss)
- **Ollama** (optional, for local model inference)

## Setup

### 1. Clone and Install

```bash
git clone <repo-url> && cd aip
uv sync
```

This installs all dependencies defined in `pyproject.toml` including dev dependencies (pytest, pytest-asyncio).

### 2. Verify Installation

```bash
uv run python -c "import aip; print('AIP loaded successfully')"
```

### 3. Run Tests

```bash
# All tests
uv run pytest

# Unit tests only (skip acceptance)
uv run pytest tests/ --ignore=tests/acceptance

# Acceptance tests only
uv run pytest tests/acceptance/

# Specific test file
uv run pytest tests/test_ecs_graph.py -v

# With coverage
uv run pytest --cov=aip tests/
```

### 4. Start the API Server

```bash
# Start both backend (port 8000) and GUI (port 8080)
./scripts/start.sh

# Or start just the API backend
uv run uvicorn aip.adapter.api.app:create_app --host 0.0.0.0 --port 8000 --factory --reload
```

### 5. Start Ollama (for local models)

```bash
ollama serve
ollama pull llama3.2    # or your preferred model
```

## Project Conventions

### Append-Only Rule

All schema files (`schemas.py`, `protocols.py`) are amended by **addition only**. Never delete or reorder existing entries. Each phase adds a new section marked with a comment header:

```python
# --- Phase N additions (append only) ---
```

### Layer Dependency Rules

```
Foundation ← Orchestration ← Adapter
```

- **Foundation** (L0): Never imports from Orchestration or Adapter
- **Orchestration** (L1-L4): Imports from Foundation only
- **Adapter**: Imports from Foundation and Orchestration

### Process Rules (§11–§16)

| Rule | Description |
|------|-------------|
| §11 | `collaborator_can_approve` defaults to `False` |
| §12 | `CompilationState` is distinct from `EcsState` |
| §13 | No test may call an external API or require a network connection |
| §14 | Every `model_gen_assumption` must be non-null when compensating for model limitations |
| §15 | Schema changes are append-only; never delete fields |
| §16 | Every L4 trigger must carry `model_gen_assumption` |

### Code Style

- **Type hints** on all public functions and methods
- **Docstrings** on all classes and public methods (Google style)
- **async/await** for all store operations
- **No hardcoded model names** — use `ModelSlotConfig` and `model_slot_resolver`

## Configuration

Configuration is loaded from `config/aip.config.toml`. See [CONFIGURATION.md](./CONFIGURATION.md) for the full reference.

Key sections:
- `[embedding]` / `[models.embedding]` — Embedding provider and model (default: OpenRouter)
- `[retrieval]` / `[retrieval.channel_weights]` — Hybrid retrieval weights (vector=0.6, fts=0.4)
- `[budget]` — Token budget limits
- `[deployment]` — Laptop vs production profile
- `[auth]` — Authentication settings
- `[vigil]` / `[vigil.retrieval_quality]` — Quality monitoring and alerting

## CLI Commands

### Core Workflow

```bash
uv run aip init                          # Initialize database
uv run aip status                        # Check system and corpus status
uv run aip corpus ingest <path>          # Ingest conversations
uv run aip corpus tag --limit 100        # Tag corpus with domains
uv run aip ask "question" --project X    # Ask a question
uv run aip review list                   # List pending reviews
uv run aip review approve <id>           # Approve an artifact
uv run aip export artifact <id>          # Export to markdown
```

### Evaluation

```bash
uv run aip eval retrieval --mode hybrid  # Evaluate hybrid retrieval (P@5, R@10, MRR)
uv run aip eval retrieval --mode fts-only  # Evaluate FTS5-only baseline
uv run aip eval retrieval-ab --config-a ... --config-b ...  # A/B comparison
uv run python scripts/retrieval_weight_tuning.py  # Grid search channel weights
```

## Testing Strategy

### Unit Tests (`tests/`)
Each test file corresponds to a module or feature:
- `test_ecs_graph.py` — ECS state machine validation
- `test_budget_system.py` — Budget enforcement
- `test_vigil.py` — Vigil actor
- `test_definer_gate.py` — DEFINER sovereignty
- etc.

### Acceptance Tests (`tests/acceptance/`)
End-to-end verification of spec requirements:
- `test_acceptance_gates.py` — §22 gate compliance
- `test_ecs_lifecycle.py` — Full ECS lifecycle
- `test_definer_sovereignty.py` — DEFINER sovereignty enforcement
- `test_budget_enforcement.py` — Budget limits
- `test_vigil_health.py` — Vigil health checking
- `test_multi_surface_isolation.py` — Surface isolation
- `test_canonical_pipeline_e2e.py` — Full pipeline

### Running Tests with Docker

```bash
# Laptop profile
docker compose -f deploy/docker-compose.laptop.yml up --build

# Run tests inside container
docker compose exec aip pytest tests/
```

## Contributing

1. Create a feature branch from `main`
2. Make changes following append-only conventions
3. Add tests for any new functionality
4. Ensure all existing tests pass: `uv run pytest`
5. Ensure acceptance tests pass: `uv run pytest tests/acceptance/`
6. Submit for review — DEFINER approval required for canonical promotion 🔄

## Troubleshooting

### `sqlite-vss` not found
The sqlite-vss extension must be installed separately. On macOS:
```bash
uv pip install sqlite-vss
```

### Ollama connection refused
Ensure Ollama is running on `http://localhost:11434`:
```bash
ollama serve
```

### Port 8000 already in use
Set a different port via environment variable:
```bash
AIP_PORT=8080 uv run uvicorn aip.adapter.api.app:create_app --host 0.0.0.0 --port 8080 --factory
```
