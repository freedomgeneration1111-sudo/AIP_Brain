---
# Historical Ask Worklog (archived 2026-06-04)
# Superseded by: ROADMAP.md, docs/decisions/, STATUS.md
# Preserved for historical reference only.
---

---
Task ID: 1
Agent: main
Task: Implement source-grounded ask pipeline for Dogfood Build 0.1

Work Log:
- Audited existing codebase: ingestion pipeline, stores (ArtifactStore, LexicalStore, VectorStore, EventStore, ProjectStore, EcsStore), model provider (ModelSlotResolver), CLI structure, session/project concepts, ECS lifecycle, provenance models
- Designed ask pipeline to reuse existing AIP primitives (no parallel storage)
- Created foundation/schemas/ask.py with AskSource, SourceReference, AskResult schemas
- Created orchestration/ask_pipeline.py with full ask pipeline: project resolution, source search, context assembly, model dispatch, artifact saving, session trace
- Created cli/ask.py with `aip ask` command supporting --project, --source, --max-sources, --save-artifact, --model-slot, --show-context, --session, --db-path
- Wired `aip ask` into cli/main.py
- Added FTS5 query sanitization to handle user punctuation (?, !, etc.)
- Added project resolution by name OR project_id (fallback)
- Created 31 tests covering all 15 required test cases + utility tests + integration test with real FTS5 stores
- Created docs/internal/ask.md with full documentation
- Verified end-to-end: ingest → ask → artifact → provenance chain works with persistent stores
- All 879 tests passing (31 new ask tests + 848 existing)
- Pushed commit 4db61cf to GitHub

Stage Summary:
- Key design: LexicalStore (FTS5) is primary search backend because it is persistent; VectorStore is supplementary
- Same persistent store paths as ingestion pipeline (data/aip.db, data/lexical.db)
- Source filtering by metadata type: conversation_chunk vs other types
- Artifacts saved with ECS lifecycle: GENERATED state (not auto-approved)
- Full session trace in EventStore with prompt, sources, model, artifact, errors
- No fake answers, no silent failures, no parallel storage
