# Contributing to AIP

Thank you for your interest in contributing to AI Poiesis (AIP).

> **Alpha Test Release**: AIP v0.1 is in alpha testing. The project is in maintenance mode —
> contributions should be small, incremental bug fixes or documentation improvements. No new
> feature development is planned. See [ROADMAP.md](ROADMAP.md) for the maintenance mode scope.

## Development Setup

```bash
git clone https://github.com/freedomgeneration1111-sudo/AIP_Brain.git
cd AIP_Brain
uv sync
```

## Code Style

- Python 3.11+ with type hints where practical
- Formatted with `ruff format` (line-length=120)
- Linted with `ruff check` (rules: E, F, W, I)
- All adapter-layer SQLite stores use `aiosqlite`

## Running Tests

```bash
uv run pytest                    # Full suite
uv run pytest tests/test_X.py    # Specific file
uv run pytest --cov=aip          # With coverage
```

## Before Submitting

```bash
uv run ruff format .
uv run ruff check .
uv run pytest
```

All three must pass. CI enforces the same gates.

## Architecture

AIP uses a three-layer architecture with strict dependency rules:

- **foundation** — Pure validation, schemas, protocols, ECS graph. No imports from other layers.
- **orchestration** — Business logic, evaluation, actors. Imports from foundation only.
- **adapter** — External interfaces, storage, API, CLI. Imports from foundation only (never orchestration directly).

## Design Principles

- **No fake success paths.** If a feature is not implemented, return a structured error (NOT_IMPLEMENTED, DISABLED, BACKEND_UNAVAILABLE) rather than pretending it works.
- **DEFINER sovereignty.** No artifact promotion may bypass the DEFINER gates.
- **Honest evaluation.** CI fixture data is flagged and blocked from production promotion.
- **Local-first.** The system must work on a laptop with 4-6 GB RAM.

## Commit Messages

Write clear, concise commit messages that describe what changed and why. Avoid references to internal build process phases, chunk numbers, or agent workflow steps.

## Development Hygiene (Non-Negotiable)

Every development session that makes an architectural decision or completes
a planned item must update ROADMAP.md, STATUS.md, and create an ADR before
closing. No exceptions.

ADR format: docs/decisions/ADR-NNN-[topic].md
Commit format: "docs: [what changed]"
