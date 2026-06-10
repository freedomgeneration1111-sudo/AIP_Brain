---
Task ID: impl-1
Agent: retrieval-eval-agent
Task: Sprint 6.4 — Improve Retrieval Evaluation Harness

Work Log:
- Read worklog.md to understand previous agent work (Sprint 6.2 observability, Sprint 6.3 stability hardening)
- Read existing files: retrieval_eval.py, golden_queries.json, eval.py, OrchestratorConfig, graph_store.py, corpus_turn_store.py
- Created scripts/update_golden_ids.py: FTS5 + graph_nodes mapping script for resolving placeholder IDs to real turn_ids
  - Uses FTS5 AND-query against corpus_turns_fts for top-K turn_id matching
  - Queries graph_nodes.canonical_name and aliases_json for entity population
  - Supports --db-path, --output, --dry-run flags
  - Handles missing DB gracefully (prints warning, skips)
- Updated tests/retrieval_goldens/golden_queries.json:
  - Changed all relevant_ids from "doc:*" placeholder format to "corpus:*" format (e.g. "doc:aip_overview" → "corpus:aip_overview_001")
  - This provides a consistent naming convention that matches how real corpus turns are stored
  - When the DB exists, the update_golden_ids.py script can resolve these to actual turn_ids
- Added --mode flag to aip eval retrieval CLI:
  - "hybrid" (default): FTS + Vector + Corpus with RRF channel weights (vector=0.6, fts=0.4, corpus=0.4)
  - "fts-only": FTS + Corpus only, Vector disabled, no channel_weights
  - "all": All channels enabled (FTS, Vector, Graph, Wiki, Procedural, Corpus)
- Updated _run_eval() to build OrchestratorConfig based on mode:
  - fts-only: enable_vector=False, channel_weights={}
  - hybrid: enable_vector=True, default channel_weights, Graph/Wiki/Procedural off
  - all: all enable_* flags True
  - Mock retriever path also builds a mode-consistent config for config_snapshot
- Added config_snapshot population in _run_eval():
  - Records mode, channels_enabled dict, channel_weights, k value
  - Baseline JSON now captures which eval mode was used for reproducibility
- Added docs/retrieval_benchmark_baseline.json save path:
  - When --save-baseline is used, baseline is saved to both eval_results/baseline.json AND docs/retrieval_benchmark_baseline.json
  - Graceful error handling if docs/ write fails
- Bumped eval_harness_version from "5.12" to "6.4"
- Verified all changes: syntax checks pass, CLI help shows --mode flag, golden queries load correctly

Stage Summary:
- 4 files created/modified:
  1. scripts/update_golden_ids.py (NEW) — FTS5 + graph_nodes ID resolver
  2. tests/retrieval_goldens/golden_queries.json (MODIFIED) — corpus: prefixed IDs
  3. src/aip/cli/eval.py (MODIFIED) — --mode flag, config_snapshot, docs/ baseline
  4. src/aip/orchestration/retrieval_eval.py (MODIFIED) — version bump to 6.4
- All 3 task deliverables completed:
  - Task 1: update_golden_ids.py script + updated golden_queries.json
  - Task 2: --mode flag (hybrid/fts-only/all) with proper OrchestratorConfig + config_snapshot
  - Task 3: docs/retrieval_benchmark_baseline.json save alongside eval_results/baseline.json
