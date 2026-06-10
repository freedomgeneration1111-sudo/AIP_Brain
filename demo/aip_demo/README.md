# AIP_Brain Demo Corpus

AIP_Brain includes a small prebuilt demo corpus under `demo/aip_demo`.

The demo contains curated Q&A turns about AIP_Brain, prebuilt wiki articles,
tags, entities, and embeddings. It is intended for smoke testing and alpha
demonstration only. It is not the operator's live dogfood corpus.

Live local databases remain ignored by Git.

## Contents

| Component | Count | Source |
|-----------|-------|--------|
| Q&A turns | 60 | `source/aip_qa_turns.jsonl` |
| Wiki articles | 24 | `source/wiki_articles.yaml` |
| Smoke questions | 15 | `expected/smoke_questions.yaml` |

## Topics Covered

The demo corpus covers 15 core AIP_Brain topics:
- AIP overview, DEFINER sovereignty, three-layer architecture
- Beast/Vigil/Sexton actors, CODEX wiki/librarian system
- Retrieval pipeline, artifact lifecycle (ECS), model slots
- Storage model, dogfood mode, no silent degradation
- Operator console/CLI, alpha limitations/hardening

## Rebuilding the Demo DB

The demo DB can be rebuilt from source with:

```bash
python scripts/demo/build_aip_demo_db.py --reset
python scripts/demo/verify_aip_demo_db.py
```

The `--reset` flag deletes any existing demo DB files before rebuilding.

## Running AIP Against the Demo DB

To run AIP with the demo database instead of your live data:

```bash
# Option 1: Environment variable
export AIP_DB_PATH=demo/aip_demo/db/state.db
uv run aip status

# Option 2: CLI flag (most commands support --db-path)
uv run aip status --db-path demo/aip_demo/db/state.db
```

## Embeddings

If an embedding provider is configured (see `[models.embedding]` in
`config/aip.config.toml`), the builder will generate real vector embeddings.
If no provider is available, embeddings will be marked as pending and the
demo DB will function with FTS5-only retrieval.

To generate embeddings after the initial build:

```bash
uv run aip corpus embed --limit 100 --db-path demo/aip_demo/db/state.db
```

## Verification

Run the verification script to check demo DB integrity:

```bash
python scripts/demo/verify_aip_demo_db.py
```

This checks:
- DB files exist and are non-empty
- Minimum 50+ Q&A turns present
- Minimum 20+ wiki articles present
- Tags and entities present
- Embeddings present or explicitly pending
- No private absolute paths in the data
- FTS5 index is functional (smoke test searches)

## Size Target

The demo DB targets 5-30 MB. If embeddings make it larger but still
under 50 MB, that is acceptable. If it exceeds 50 MB, the DB should
be moved to a release artifact and only the source files kept in the repo.
