# Task: Fix Ruff Lint Errors (E402 + E501)

## Summary

Fixed all ruff lint errors in `/home/z/my-project/AIP_Brain` for rules E402 and E501.

## Task 1: E402 Errors (4 fixes)

**File:** `tests/test_chunk5_retrieval_honesty.py`

Moved 4 imports from inline locations (after test class definitions) to the top of the file with the other imports:

1. `from aip.adapter.vector.sqlite_vss_store import SqliteVssVectorStore` (was line 79)
2. `from aip.foundation.schemas.retrieval import RetrievalTrace` (was line 213)
3. `from aip.foundation.schemas.ask import AskResult` (was line 254)
4. `from aip.adapter.vector._in_memory import InMemoryVectorStore` (was line 392)

All 4 imports are now consolidated in the top-of-file import block (lines 21-25).

## Task 2: E501 Errors (121 fixes)

Fixed all 121 line-too-long errors across 37 files. Techniques used:

### GUI files (8 fixes)
- **gui/archive/main.py**: Broke long label string with parentheses for implicit concatenation
- **gui/main.py**: Same pattern
- **gui/pages/ask.py**: Broke long lambda parameter lists across lines; broke async call parameters across lines
- **gui/pages/corpus.py**: Broke long label string with parentheses
- **gui/pages/maintenance.py**: Broke long CSS style string with parentheses
- **gui/shell.py**: Broke long CSS f-string into two f-string lines

### Script files (10 fixes)
- **scripts/demo/build_aip_demo_db.py**: Broke SQL column lists and VALUES clauses; broke long f-strings and SQL strings
- **scripts/retrieval_weight_tuning.py**: Broke long f-string format specs across lines

### Source files (83 fixes)
- **src/aip/adapter/alert_history_store.py**: Broke long comment across 3 lines
- **src/aip/adapter/alerting.py**: Broke long f-string messages with parentheses
- **src/aip/adapter/api/routes/actors.py**: Broke long dict key assignment
- **src/aip/adapter/api/routes/admin.py**: Broke long string messages with parentheses
- **src/aip/adapter/api/routes/beast_commentary.py**: Broke 5 long dict values with parentheses
- **src/aip/adapter/api/routes/chat.py**: Broke docstring, string messages, f-strings, and comments
- **src/aip/adapter/api/routes/corpus.py**: Broke long message strings with parentheses
- **src/aip/adapter/api/routes/graph_viz.py**: Broke CSS/HTML/JS lines within triple-quoted string
- **src/aip/adapter/api/routes/maintenance.py**: Broke long message string with parentheses
- **src/aip/adapter/api/routes/vigil_quality.py**: Fixed 31 E501 errors - broke CSS, HTML attributes, JS code lines
- **src/aip/adapter/api/routes/wiki.py**: Broke long SQL INSERT strings with parentheses
- **src/aip/adapter/corpus_turn_store.py**: Broke long SQL VALUES and GROUP_CONCAT clauses
- **src/aip/adapter/mcp/server.py**: Broke long f-string message across lines
- **src/aip/adapter/model_slot_resolver.py**: Broke long f-string log message
- **src/aip/adapter/vector/sqlite_vss_store.py**: Broke long SQL INSERT string
- **src/aip/adapter/vigil/vigil_quality_store.py**: Broke long SQL SELECT strings
- **src/aip/cli/corpus.py**: Broke long help string
- **src/aip/cli/eval.py**: Broke long docstring command example
- **src/aip/cli/ingest.py**: Broke long help strings (2 occurrences)
- **src/aip/cli/review.py**: Broke long f-string across lines
- **src/aip/cli/status.py**: Broke long SQL string with parentheses
- **src/aip/config/__init__.py**: Broke long f-string across lines
- **src/aip/foundation/schemas/ask.py**: Broke long comment across lines
- **src/aip/foundation/schemas/retrieval.py**: Broke long comment across lines
- **src/aip/orchestration/actors/beast.py**: Broke long string literals and f-strings
- **src/aip/orchestration/actors/vigil.py**: Broke long triple-quoted prompt text; broke long f-string detail
- **src/aip/orchestration/codex/librarian.py**: Broke long f-string keyword args

### Test files (20 fixes)
- **tests/test_artifact_lifecycle.py**: Broke long content string
- **tests/test_artifact_workbench_cycle9.py**: Broke long SQL strings (3 occurrences)
- **tests/test_codex.py**: Broke long resolution_notes string
- **tests/test_maintenance_center_cycle12.py**: Broke long docstring line
- **tests/test_sprint523_vigil_auto_tuning.py**: Broke long assistant_text strings (2 occurrences)
- **tests/test_wiki_storage_cycle71.py**: Broke long SQL INSERT string
- **tests/test_wiki_ui_cycle7.py**: Broke long SQL INSERT string

## Verification

```
ruff check AIP_Brain --select E402,E501
All checks passed!
```
