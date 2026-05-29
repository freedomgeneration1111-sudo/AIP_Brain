---
Task ID: 3
Agent: main
Task: Implement review, approve, reject, and export pipeline (Prompt 3 of Dogfood Build 0.1)

Work Log:
- Inspected entire codebase: ECS states, artifact store, ask pipeline, ingestion, CLI patterns, event store, review gates
- Discovered REJECTED was in ECS transition graph but missing from EcsState enum — fixed
- Added GENERATED → REJECTED transition to ecs_graph.py (existing review.py code attempted this but it wasn't in the graph)
- Added REJECTED to EcsState enum in base.py
- Added read_metadata(), read_with_metadata(), list_artifacts_by_metadata() to VersionedArtifactStore
- Added list_by_state() to PersistentEcsStore
- Made SqliteProjectStore.create_project() idempotent (returns existing project if already exists)
- Created src/aip/orchestration/review_export_pipeline.py with full review/export orchestration
- Created src/aip/cli/review.py with aip review list/show/sources/approve/reject/needs-revision
- Created src/aip/cli/export.py with aip export artifact/project
- Registered new CLI commands in main.py
- Created tests/test_review_export.py with 36 tests covering all 20 required test cases plus CLI registration
- Created docs/internal/review_export.md with complete documentation
- All existing tests still pass (test_ask, test_ingestion, test_ecs_graph, test_manual_review_queue, etc.)

Stage Summary:
- ECS lifecycle states: SPECIFIED, GENERATED, REVIEWED, APPROVED, REJECTED, SUPERSEDED, FAILED
- APPROVED path: GENERATED → REVIEWED → APPROVED (two ECS transitions, canonical write)
- REJECTED path: GENERATED → REJECTED (artifact preserved, never deleted)
- NEEDS_REVISION: verdict only, artifact stays in current state, instruction stored as event
- Export refuses rejected by default, warns for unreviewed, --force to override
- 36 new tests, all passing
