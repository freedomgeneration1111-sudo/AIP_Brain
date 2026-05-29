# AIP Cleanup Baseline

This document records the exact state of the repository before any cleanup or further changes. It is intended as a reference point so that future work can be measured against a known, documented baseline.

---

## 1. Repository State

| Field | Value |
|---|---|
| **Branch** | `main` |
| **Commit** | `c90236d` |
| **Date** | 2026-05-29 |
| **Python** | 3.12 |
| **Package manager** | uv |
| **Build system** | hatchling |

---

## 2. How to Work With This Repo

### Install dependencies

```bash
uv sync --dev
```

### Run the full test suite

```bash
uv run pytest
```

With CI mode (deterministic fixtures, no network calls):

```bash
CI=true uv run pytest -q --tb=short
```

### Run a specific test file

```bash
uv run pytest tests/test_definer_gate.py
```

### Run tests matching a keyword

```bash
uv run pytest -k "test_definer_sovereignty"
```

### Run with coverage

```bash
uv run pytest --cov=aip --cov-report=term-missing
```

### Run the CLI

```bash
uv run aip init            # Initialize project (creates config, databases)
uv run aip status          # Show system status and store health
uv run aip session start   # Start a new session
uv run aip project list    # List projects
uv run aip project show    # Show project details
uv run aip project create  # Create a new project
uv run aip config show     # Display current configuration
```

### Start the API server locally

```bash
uv run uvicorn aip.adapter.api.app:create_app --factory --reload
```

The API is served at `http://localhost:8000` by default. Interactive docs at `/docs` (Swagger) and `/redoc`.

### Run linting

```bash
uv run ruff check .           # Check for issues (currently 601 findings)
uv run ruff check --fix .     # Auto-fix the 497 fixable issues
```

---

## 3. Current Test Status

| Metric | Value |
|---|---|
| **Total tests collected** | 640 |
| **Passing** | 624 |
| **Failing** | 6 |
| **Skipped** | 10 |

### Failing Tests

| Test | File | Reason |
|---|---|---|
| `test_phase3_code_has_no_hardcoded_models` | `tests/test_phase3_network_gate.py` | `embed_providers.py` contains hardcoded model name `nomic-embed-text` |
| `test_phase3_import_boundaries` | `tests/test_phase3_network_gate.py` | `embed_providers.py` (orchestration) imports from `adapter.embedding.ollama_embed` — layer violation |
| `test_all_prior_phase_tests_still_pass` | `tests/test_phase9_acceptance.py` | `ModuleNotFoundError: No module named 'tests'` — import path issue |
| `test_profiler_returns_real_metrics` | `tests/test_phase9_adapter_promotion.py` | `psutil` reports `memory_mb=0` in this environment |
| `test_performance_profiler_metrics` | `tests/test_phase9_async_fix.py` | Same as above — `psutil` reports `memory_mb=0` |
| `test_orchestration_no_adapter_imports` | `tests/test_phase9_layer_violations.py` | `embed_providers.py` imports from `adapter` — should use `model_provider_proxy` |

**Root cause clusters:**
- **3 failures** stem from `orchestration/embed_providers.py` violating layer boundaries (imports adapter, hardcodes model name)
- **2 failures** stem from `psutil` returning 0 for memory in the CI/container environment (perf profiler stub)
- **1 failure** is an import path bug (`tests` not on `sys.path`)

### Lint Status

- **601 ruff errors** total (497 auto-fixable)
- Breakdown: 342 F401 (unused imports), 138 I001 (import sorting), 37 F821 (undefined names), 29 F841 (unused variables), 28 E402 (module-level import not at top), plus smaller counts of W293, W291, E701, F811, F541, E401, E741
- Ruff is currently **non-blocking** in CI (`continue-on-error: true`)

---

## 4. Current Surface

### Public CLI Commands

| Command | Description |
|---|---|
| `aip init` | Initialize AIP for this machine (creates config, databases) |
| `aip status` | Print system status from Protocols (offline-first) |
| `aip config show` | Read config values from `config/aip.config.toml` |
| `aip project list` | List projects |
| `aip project show` | Show project details |
| `aip project create` | Create a new project |
| `aip session start` | Start a new session (loads ACE Playbook) |

Entry point: `src/aip/cli/main.py` → `aip.cli.main:cli`

### API Server

11 routers mounted under `/api/v1`:

| Router | Prefix | File |
|---|---|---|
| health | `/api/v1` | `routes/health.py` |
| projects | `/api/v1` | `routes/projects.py` |
| sessions | `/api/v1` | `routes/sessions.py` |
| review | `/api/v1` | `routes/review.py` |
| artifacts | `/api/v1` | `routes/artifacts.py` |
| admin | `/api/v1` | `routes/admin.py` |
| memory | `/api/v1` | `routes/memory.py` |
| chat | `/api/v1` | `routes/chat.py` |
| collaborators | `/api/v1/collaborators` | `collaborators.py` |
| plugins | `/api/v1/plugins` | `plugins.py` |
| performance | `/api/v1/performance` | `performance.py` |

Entry point: `src/aip/adapter/api/app.py` → `create_app()`

### MCP Server

- Entry point: `src/aip/adapter/mcp/server.py` → `McpServer.start(transport="stdio")`
- Tool registry and autonomy gate enforcement work, but tool dispatch returns hardcoded results

### Key Internal Entry Points

| Function | Location | Purpose |
|---|---|---|
| `create_app()` | `adapter/api/app.py:326` | FastAPI application factory |
| `cli()` | `cli/main.py:20` | Click CLI entry point |
| `WorkflowRunner.run()` | `orchestration/workflow/runner.py:36` | Execute a workflow definition |
| `WorkflowEngine.run_workflow()` | `orchestration/workflow/engine.py:84` | High-level workflow execution |
| `BeastActor.run_health_check()` | `orchestration/actors/beast.py:82` | Health check cycle |
| `BeastActor.run_corpus_maintenance()` | `orchestration/actors/beast.py:202` | Corpus re-embedding cycle |
| `Sexton.run_classification_cycle()` | `orchestration/sexton/sexton.py:206` | Failure classification cycle |
| `VigilActor.run()` | `orchestration/actors/vigil.py:109` | Canonical monitoring cycle |

---

## 5. Known Limitations & Stubs

### Dangerous Defaults

| Default | Location | Risk |
|---|---|---|
| `auth_enabled = false` | `config/aip.config.toml:177` | All requests treated as DEFINER; no authentication enforced |
| `budget_remaining = None` | `orchestration/workflow/context.py:27` | Infinite budget — `consume_budget()` always returns True |
| Zero-vector embeddings | `adapter/storage/sqlite_knowledge_store.py` | `store_compiled` inserts `[0.0]*384`, making semantic search non-functional |
| Zero-vector migration | `adapter/vector/migrate.py` | Migration uses zero-vectors, destroying real embeddings |

### Major Intentional Stubs

| Component | Issue | Risk Level |
|---|---|---|
| `workflow_01.py` | `_AlwaysApproveDialogNode` always approves; `ScriptNode.run()` never executes | Critical |
| `router.py` (adaptive) | `update_weights()` is `pass`; exploration weight hardcoded at 10% | Critical |
| `mcp/server.py` | All tool dispatch returns hardcoded results | High |
| `ecs_store_guardrailed.py` | In-memory only — state lost on process restart | High |
| `health.py` | Embedding status hardcoded healthy | High |
| `perf.py` | Memory usage returns fake proportional breakdown | Medium |
| `context.py` | Infinite budget by default | Critical |
| `sexton.py` | `derive_intervention_rule()` returns None | Medium |

### Incomplete / Alpha-Quality Areas

- **MANUAL mode UI**: `ManualReviewRequired` exception carries full context but no review queue store or approval UI exists
- **Adaptive router**: Not adaptive — `update_weights()` is a no-op
- **ECS persistence**: In-memory cache only; state lost on restart
- **Vector migration**: Destroys real embeddings by inserting zero-vectors
- **Knowledge store embeddings**: All compiled knowledge gets zero-vectors, making semantic search non-functional
- **Model provider dispatch**: Works for Ollama and OpenAI-compatible only; no Anthropic, no streaming
- **Chat API**: System prompt is templated, not model-generated
- **Performance API**: Routes delegate to uninitialized `PerformanceProfiler`
- **Workflow engine**: `ScriptNode.run()` is a placeholder
- **Sexton intervention rules**: Returns None (stub)

---

## 6. Key Documentation Locations

| Document | Location | Purpose |
|---|---|---|
| Project status | `STATUS.md` | Current maturity, what's runnable, known issues |
| Implementation status | `docs/implementation_status.md` | Module-by-module analysis with scaffolding estimates |
| This baseline | `CLEANUP_BASELINE.md` | Pre-cleanup reference point |
| Architecture overview | `README.md` | Quick start, architecture diagram, capabilities table |
| Docker deployment | `deploy/README.md` | Docker Compose profiles and instructions |
| CI workflow | `.github/workflows/ci.yml` | GitHub Actions configuration |
| Config reference | `config/aip.config.toml` | All configuration with comments |
| Build specifications | `specs/` | Authoritative build spec documents |
