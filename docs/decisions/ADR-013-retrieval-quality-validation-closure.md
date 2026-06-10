# ADR-013: Sprint 6.4 — Retrieval Quality Validation and Project Closure

**Date:** 2026-06-10
**Status:** ACCEPTED
**DEFINER:** B. Moses Jorgensen

---

## Context

Sprint 6.4 is the final sprint of the AIP v0.1 active development phase. The system has
a complete retrieval pipeline with multi-channel dispatch (FTS5, Vector, Graph, Wiki,
Procedural, Corpus) and RRF fusion, but the quality of hybrid retrieval relative to
FTS5-only has never been formally measured. Meanwhile, embedding coverage stands at
~1.8% (50/2,766 turns), which limits the observable benefit of the vector channel.

Two questions needed answering before closing the development phase:

1. **Does hybrid retrieval actually improve over FTS5-only?** Without measurement,
   the entire embedding pipeline investment is unvalidated.

2. **Is the system ready for maintenance mode?** Documentation was stale, the project
   had no formal closure, and there was no protocol for ongoing maintenance.

The retrieval evaluation harness (`retrieval_eval.py`) already existed but had two
critical gaps: golden queries used placeholder IDs that would never match real corpus
turns, and the CLI had no way to force FTS5-only mode for comparison.

## Decision

### 1. Improve the Retrieval Evaluation Harness (Pragmatic, Not Perfect)

Rather than building a new harness, we improved the existing one:

- **Added `--mode` flag** to `aip eval retrieval` CLI with three options: `hybrid`
  (default, FTS + Vector + Corpus with RRF weights), `fts-only` (FTS + Corpus, no
  vector, no channel weights), and `all` (all six channels enabled).
- **Updated golden queries** with corpus-mapped IDs so evaluation metrics produce
  non-zero values against the real corpus.
- **Added `scripts/update_golden_ids.py`** to map queries to actual turn IDs from
  the database.
- **Baseline save** now also writes to `docs/retrieval_benchmark_baseline.json`
  for project-level visibility.

This is deliberately pragmatic. The harness provides directional signal — enough to
confirm hybrid is better than FTS5-only — without over-investing in a perfect IR
evaluation framework.

### 2. Channel Weight Tuning via Grid Search

- **Created `scripts/retrieval_weight_tuning.py`** that sweeps `vector_weight` across
  [0.2–0.8] with `fts_weight = 1.0 - vector_weight`, runs the eval harness for each
  combination, and identifies the best-performing weights.
- **Wired config channel weights** into `OrchestratorConfig` via
  `[retrieval.channel_weights]` in `aip.config.toml`. The ask pipeline now reads
  weights from config instead of using hardcoded defaults.

Default weights remain `vector=0.6, fts=0.4, corpus=0.4` based on the RRF paper and
initial tuning. These should be re-evaluated after full embedding coverage is achieved.

### 3. Vigil Retrieval Quality Gate (Light)

- **Added `_run_retrieval_quality_sample()`** to the Vigil actor. Every N cycles
  (configurable, default 6 = ~6 hours), Vigil samples a few golden queries through
  the retrieval pipeline, computes precision@5, and emits an alert if it drops below
  a threshold (default 0.3).
- This is intentionally lightweight: 3-5 queries per sample, no heavy computation.
  The goal is degradation detection, not comprehensive evaluation.

### 4. Documentation and Project Closure

- Updated STATUS.md, ROADMAP.md, and TECH_DEBT.md to reflect the actual current state.
- Created `docs/Maintenance_Protocol.md` for ongoing operations.
- Wrote this ADR to document the decisions and scope.

### 5. Scope Exclusions

The following were explicitly excluded from this sprint:

- **No new retrieval features** — this sprint measures, does not improve.
- **No full embedding pass** — that requires DEBT-006 fix (Sexton wiring), which is
  a maintenance task, not a sprint deliverable.
- **No perfect golden query set** — the updated IDs are sufficient for directional
  signal. A comprehensive relevance judgment set would require human annotation.
- **No UI changes** — the focus is backend validation and documentation.

## Alternatives Considered

**Build a new evaluation framework from scratch** — rejected. The existing
`retrieval_eval.py` is well-architected with all required metrics, A/B comparison,
and regression checking. Improving it was more efficient.

**Wait for full embedding coverage before evaluating** — rejected. Even with low
coverage, the harness infrastructure needs to exist. Running the evaluation now
establishes the baseline; re-running after full embedding will show the improvement.

**Skip the Vigil quality gate** — rejected. Without periodic sampling, retrieval
degradation would be invisible until someone manually runs the eval CLI. The light
gate provides early warning at minimal cost.

**Full TREC-style evaluation with human relevance judgments** — rejected. This would
require significant human effort to judge relevance for each query-document pair.
The pragmatic approach (FTS5 as a proxy for relevance, combined with spot-checking)
is sufficient for this phase.

## Consequences

- **Positive**: The system now has a validated evaluation harness that can measure
  retrieval quality over time. Channel weights are configurable through the standard
  config file. Vigil provides automated degradation detection.

- **Positive**: Documentation is accurate and the project has a clear maintenance
  protocol. Future contributors can understand the system state without archaeology.

- **Negative**: The hybrid vs FTS5 comparison will be inconclusive until full
  embedding coverage is achieved. With only ~1.8% coverage, the vector channel
  contributes minimally, and hybrid may not show the target ≥20% improvement yet.

- **Negative**: Golden queries are mapped to corpus IDs via FTS5 search, which means
  they're biased toward FTS5-friendly results. This is acceptable for directional
  signal but would not meet academic rigor.

- **Mitigation**: Re-run `scripts/retrieval_weight_tuning.py` and `aip eval retrieval`
  after DEBT-006 is fixed and the full embedding pass completes. Update the baseline
  at that point.

## Related

- ADR-011: Actor Role Boundaries (Vigil scope for quality evaluation)
- ADR-012: Single-Writer Sufficiency for SQLite Stores
- `src/aip/orchestration/retrieval_eval.py` — evaluation harness
- `src/aip/orchestration/retrieval_orchestrator.py` — RRF fusion and channel weights
- `src/aip/cli/eval.py` — eval CLI with --mode flag
- `scripts/retrieval_weight_tuning.py` — grid search
- `config/aip.config.toml` — `[retrieval.channel_weights]` section
- `docs/Maintenance_Protocol.md` — ongoing maintenance procedures
