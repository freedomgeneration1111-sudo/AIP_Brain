# Task: Add AdaptiveBudgetTuner Integration Points

## Summary

Added two integration points for the `AdaptiveBudgetTuner`:

### 1. CLI Command: `aip eval budget-tune`

**File**: `src/aip/cli/eval.py`

- Added `import json` at the top level (used by new helper functions)
- Added `_load_channel_contributions_from_eval()` — loads channel contribution data from the latest eval result JSON file
- Added `_load_channel_contributions_from_trace_store()` — async fallback that queries the event store for channel contribution data when no eval results exist
- Added `budget_tune` Click command registered as `@eval_cmd.command("budget-tune")` with:
  - `--db-path` (optional, default from AIP_DB_PATH env or "db/state.db")
  - `--auto-apply` (flag, default False)
  - `--max-change` (float, default 0.30)
  - `--min-budget` (int, default 1)
  - `--min-samples` (int, default 5)
- Human-readable output with formatted table showing Channel, Current, Suggested, Change, and Confidence
- When `--auto-apply` is used, shows the updated budget configuration

### 2. Dashboard Endpoint: `GET /api/v1/retrieval/budget-tune`

**File**: `src/aip/adapter/api/routes/retrieval_dashboard.py`

- Added Sprint 5.12 docstring to module header
- Added `retrieval_budget_tune` endpoint with:
  - `auto_apply` query param (bool, default False)
  - `max_change_fraction` query param (float, default 0.30, range 0.01-1.0)
  - Uses `_compute_channel_contribution_summary()` for channel data
  - Uses `_get_recent_traces()` for total query count
  - Creates `OrchestratorConfig` and `AdaptiveBudgetTuner`, runs tuning
  - Returns JSON with adjustments, applied status, summary, contributions, and current budgets
  - Read-only by default (auto_apply=False)

## Design Decisions

- Both integrations follow existing patterns (Click for CLI, FastAPI for API)
- The CLI has a richer data source strategy: eval results first, then trace store fallback
- The dashboard endpoint uses the existing `_compute_channel_contribution_summary()` helper (already in the same module)
- The dashboard endpoint does NOT persist changes — even with auto_apply=True, it only modifies a fresh config object for the response
- Adjustments are serialized as plain dicts for JSON compatibility in the API response
