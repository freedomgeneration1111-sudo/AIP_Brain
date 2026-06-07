# AIP Worklog

This file has been archived. See:
- ROADMAP.md — phased build plan and status
- docs/decisions/ — architecture decision records
- STATUS.md — current system status and next priorities

Historical worklog preserved at archive/worklog_historical.md

---
Task ID: 5.14
Agent: Super Z (main)
Task: Sprint 5.14 — Storage reliability, Sexton verification, AI fingerprint cleanup

Work Log:
- Refactored CorpusTurnStore: removed blocking _ensure_tables_sync() from __init__, added async initialize() + lazy _get_conn(), extracted DDL to module-level constants (single source of truth), added mark_embedded(), has_bridge_tagged_turns(), count_domain_words_since(), get_domain_stats() async methods
- Updated Sexton actor: replaced all 5 raw sqlite3.connect() calls with CorpusTurnStore async methods (bridge detection, embedding mark, wiki word count, wiki domain data, reembed query)
- Refactored SqliteVssVectorStore: persistent connection per instance with _reset_conn() error recovery, added RuntimeMode enum (DEVELOPMENT/PRODUCTION/STRICT) for brute-force fallback policy, close() now properly releases connection
- Refactored SqliteFts5LexicalStore: persistent connection per instance with _reset_conn() error recovery, proper close() method
- Updated vector factory: resolves RuntimeMode from config, passes through to SqliteVssVectorStore
- AI fingerprint cleanup: removed all Sprint-log comments from retrieval_orchestrator.py, condensed OrchestratorConfig docstring, cleaned Sexton actor module docstring
- Wrote comprehensive Sprint 5.14 test suite (29 tests, all passing)

Stage Summary:
- CorpusTurnStore no longer performs blocking I/O in __init__
- All 5 raw sqlite3 calls removed from Sexton actor — all DB access now async via store methods
- Vector stores use persistent connections with error recovery
- RuntimeMode enum controls brute-force behavior (dev=graceful, prod=warn, strict=raise)
- Sprint-log comments removed from retrieval_orchestrator.py
- All Sprint 5.13 (20) + 5.14 (29) + Sexton (6) + orchestrator (43) + classification (17) tests pass
---