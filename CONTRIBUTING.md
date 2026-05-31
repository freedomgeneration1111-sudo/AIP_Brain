# Contributing to AIP

Thank you for your interest in contributing to AI Poiesis (AIP).

## Development Setup

```bash
git clone https://github.com/freedomgeneration1111-sudo/aip.git
cd aip
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
