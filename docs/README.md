# AIP 0.1 — AI Poiesis

**Local-first sovereign knowledge engine**

AIP (AI Poiesis) is a local-first, sovereign knowledge synthesis engine that guides an artifact from
specification through generation, review, and canonical promotion — with the human DEFINER retaining absolute
sovereignty at every stage.

**Status:** Alpha Test Release (0.1.0-alpha) — suitable for evaluation and dogfood usage.
See [STATUS.md](../STATUS.md) for known limitations and [DOGFOOD_READY.md](../DOGFOOD_READY.md) for
the first-run guide.

## Core Principles

1. **DEFINER Sovereignty** (§1.7): No UI, workflow, Beast cadence, MCP call, or queued task may bypass the DEFINER gates. The human always has the final say.

2. **Laptop-Viable** (§2.1): The entire system must work on a laptop with 4–6 GB RAM, using SQLite + sqlite-vss for vector storage and Ollama for local model inference.

3. **Architecture Conformance**: Five layers (L0–L4) with strict dependency rules. Foundation never imports Orchestration; Orchestration never imports Adapter.

4. **Append-Only Evolution**: All schema files, protocols, and configuration are amended by addition only — never rewritten. This preserves backward compatibility across all 8 build phases.

## Architecture Overview

```
┌─────────────────────────────────────────────┐
│  Surfaces (API, CLI, MCP, Chat, Review)     │  Adapter Layer
├─────────────────────────────────────────────┤
│  Orchestration (Pipeline, Actors, Budget)   │  Orchestration Layer
├─────────────────────────────────────────────┤
│  Foundation (Protocols, Schemas, ECS Graph) │  Foundation Layer
└─────────────────────────────────────────────┘
```

- **Foundation**: Protocols (VectorStore, LexicalStore, CanonicalStore, etc.), Schemas (EcsState, BudgetConfig, etc.), ECS state graph
- **Orchestration**: Canonical pipeline, Sexton/Beast/Vigil actors, Budget management, Workflow engine
- **Adapter**: REST API (FastAPI), CLI (Click), MCP server, SQLite adapters, Auth middleware

## ECS Lifecycle

Every artifact follows the state machine:

```
SPECIFIED → GENERATED → REVIEWED → APPROVED → SUPERSEDED
                ↓           ↓
              FAILED     REJECTED → GENERATED (re-synthesis loop)
```

- **SPECIFIED**: Human defines what they want
- **GENERATED**: AI synthesizes content
- **REVIEWED**: Automated evaluation passes (faithfulness + domain coherence)
- **APPROVED**: DEFINER explicitly approves
- **SUPERSEDED**: New version replaces this one

## Quick Start

```bash
# Install dependencies
uv sync

# Initialize the database
uv run aip init

# Ingest your Claude conversations
uv run aip corpus ingest /path/to/conversations.json --source-model claude

# Start the API server + GUI
./scripts/start.sh

# Run tests
uv run pytest

# Run acceptance tests
uv run pytest tests/acceptance/
```

For the complete first-run guide, see [DOGFOOD_READY.md](../DOGFOOD_READY.md).

## Deployment Profiles

- **Laptop**: SQLite + sqlite-vss + Ollama — works offline on developer hardware
- **Production**: PostgreSQL + pgvector + API models + authentication — for team deployments

## Project Structure

```
aip/
├── src/aip/
│   ├── foundation/       # Protocols, schemas, ECS graph, validation
│   ├── orchestration/    # Pipeline, actors, budget, workflow engine
│   └── adapter/          # API, CLI, MCP, auth, vector stores, vigil
├── tests/                # Unit + acceptance tests
├── config/               # TOML configuration files
├── deploy/               # Docker, compose, health checks
├── docs/                 # Documentation
├── prompts/              # LLM prompt templates
└── workflows/            # Workflow YAML definitions
```

## Documentation

| Document | Description |
|----------|-------------|
| [STATUS.md](../STATUS.md) | Current system state, known limitations, corpus metrics |
| [ROADMAP.md](../ROADMAP.md) | Phase status, maintenance mode, future work |
| [TECH_DEBT.md](../TECH_DEBT.md) | Deferred work register with remediation triggers |
| [DOGFOOD_READY.md](../DOGFOOD_READY.md) | First-run guide for new users |
| [AIP_GOVERNANCE.md](../AIP_GOVERNANCE.md) | Binding governance contract (AIP-G-01 through AIP-G-11) |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Three-layer architecture, subsystems, data flow |
| [API_REFERENCE.md](./API_REFERENCE.md) | REST API endpoint reference |
| [CONFIGURATION.md](./CONFIGURATION.md) | Configuration reference for `aip.config.toml` |
| [DEVELOPER_GUIDE.md](./DEVELOPER_GUIDE.md) | Development setup and conventions |
| [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md) | Laptop and production deployment |
| [Maintenance_Protocol.md](./Maintenance_Protocol.md) | Operational procedures for maintenance phase |
| [CHANGELOG.md](./CHANGELOG.md) | Append-only change history |

## License

MIT — see [LICENSE](../LICENSE).
