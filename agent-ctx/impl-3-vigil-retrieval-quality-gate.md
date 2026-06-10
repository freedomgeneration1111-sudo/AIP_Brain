# Task: impl-3 — Vigil Retrieval Quality Gate (Sprint 6.4)

## Summary
Implemented a light retrieval quality sampling gate in the Vigil actor that periodically runs golden queries through the retrieval pipeline and flags precision@5 degradation.

## Changes Made

### 1. `src/aip/foundation/schemas/review.py` — VigilConfig fields
Added 4 retrieval quality config fields to `VigilConfig`:
- `retrieval_quality_sampling_enabled: bool = True` — toggle for the quality gate
- `retrieval_quality_sample_size: int = 5` — number of golden queries to sample per run
- `retrieval_quality_threshold: float = 0.3` — precision@5 threshold below which alerts fire
- `retrieval_quality_sample_interval_cycles: int = 6` — only run every N cycles

### 2. `config/aip.config.toml` — [vigil.retrieval_quality] section
Added new TOML section with:
- `sampling_enabled = true`
- `sample_size = 5`
- `precision_threshold = 0.3`
- `sample_interval_cycles = 6`

### 3. `src/aip/orchestration/actors/vigil.py` — Main implementation
- Added `import os` (needed for `os.environ.get("AIP_DB_PATH", ...)`)
- Added `_run_retrieval_quality_sample()` method:
  - Gates on `retrieval_quality_sampling_enabled` and cycle interval
  - Loads golden queries via `load_golden_queries()` from `aip.orchestration.retrieval_eval`
  - Creates temporary retrieval infrastructure (AskStores + RetrievalOrchestrator)
  - Runs hybrid retrieval (FTS + Vector + Corpus) for each sampled query
  - Computes precision@5 using `compute_precision_at_k` from retrieval_eval
  - Alerts via `_alert_manager.send_alert()` if mean precision@5 < threshold
  - Records results in `_cycle_report_history` under `"retrieval_quality_sample"` key
  - Returns dict: `{sampled_count, mean_precision_at_5, threshold, degraded}`
  - Gracefully skips if retrieval infra unavailable
- Added call to `_run_retrieval_quality_sample()` at end of `run_cycle()` (Step 7)
- Added retrieval quality result to `run_cycle()` return dict
- Added `retrieval_quality_sampling_enabled` to `get_status_summary()` for operator visibility

### 4. `src/aip/adapter/api/app.py` — Config loading
Updated VigilConfig creation to flatten the nested `[vigil.retrieval_quality]` TOML section:
- Maps TOML `sampling_enabled` → VigilConfig `retrieval_quality_sampling_enabled`
- Maps TOML `sample_size` → VigilConfig `retrieval_quality_sample_size`
- Maps TOML `precision_threshold` → VigilConfig `retrieval_quality_threshold`
- Maps TOML `sample_interval_cycles` → VigilConfig `retrieval_quality_sample_interval_cycles`

## Design Decisions
1. **Light by design**: Only samples a few queries (default 5), runs every 6 cycles (~6 hours with 1-hour cycles)
2. **Graceful degradation**: If vector store, embedding provider, or DB unavailable, silently skips the sample
3. **Temporary infrastructure**: Creates AskStores + RetrievalOrchestrator on-the-fly per sample, cleans up after. No persistent retrieval infrastructure stored in Vigil.
4. **Uses existing metrics**: Imports `compute_precision_at_k` and `load_golden_queries` from `aip.orchestration.retrieval_eval` — no metric reimplementation
5. **Alert type**: Uses `retrieval_quality_degradation` as alert_type (distinct from existing `quality_degradation`), severity `warning`, subject `retrieval_quality`
6. **Data flow**: Results flow through existing `_cycle_report_history` and `_quality_store.record_cycle()` pipelines

## Integration Tests Passed
- VigilConfig defaults verified
- TOML config values verified
- VigilConfig from flattened TOML verified
- Vigil has `_run_retrieval_quality_sample` method verified
- `compute_precision_at_k` works correctly
- Golden queries load correctly (20 queries)
