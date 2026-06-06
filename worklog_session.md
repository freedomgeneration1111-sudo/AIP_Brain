---
Task ID: 1
Agent: main
Task: Commits 1-4: Fix pipeline gaps per ADR-011 continuity check

Work Log:
- Read all relevant source files: chat.py, ingest.py (routes), corpus_turn_store.py, corpus_turn.py (schema), sexton.py, pipeline.py, dependencies.py, app.py
- Identified the two-pipeline gap: auto_save_chat_turn() writes to artifacts only, never to corpus_turns
- Confirmed CorpusTurnStore is already wired in AipContainer
- Confirmed make_turn_id() exists and takes (conversation_id: str, turn_index: int)
- Confirmed increment_turn_count() fires before auto-save in chat.py

Commit 1 (1a54d55): Fix two-pipeline problem
- chat.py: Capture turn_index BEFORE increment_turn_count() (0-based)
- chat.py: Pass turn_index + model_used to auto_save_chat_turn()
- chat.py: Expand auto-save condition to also fire when corpus_turn_store is available
- ingest.py: auto_save_chat_turn() now writes CorpusTurn to corpus_turn_store
- ingest.py: Legacy path (artifacts+lexical) and corpus_turns path are independent
- Resolved merge conflict with remote (rebase)

Commit 2 (33eb8c9): Add metadata_json column
- corpus_turn.py: Added metadata_json field to CorpusTurn dataclass
- corpus_turn_store.py: Added ALTER TABLE migration in _ensure_tables_sync() and _ensure_tables()
- corpus_turn_store.py: Updated write_turn() to include metadata_json in INSERT
- corpus_turn_store.py: Updated _row_to_turn() to read metadata_json

Commit 3 (a4a0ac5): Fix graph extraction JSON parsing
- sexton.py: Added _extract_json_array() helper function
- Applied to both _run_turn_tagging() and _run_graph_extraction()

Commit 4 (58ad4ca): Add asyncio.sleep(5) between LLM calls
- Added import asyncio to sexton.py
- Added await asyncio.sleep(5) after each LLM call in tagging, wiki, and graph extraction

Stage Summary:
- All 4 commits pushed to main: 1a54d55 → 33eb8c9 → a4a0ac5 → 58ad4ca
- Critical pipeline gap closed: live chat turns now flow into corpus_turns for Sexton processing
- metadata_json column ready for Vigil provenance tracking
- Free-model JSON parse failures resolved with robust extraction
- 429 rate limiting addressed with 5-second pauses between sequential LLM calls
