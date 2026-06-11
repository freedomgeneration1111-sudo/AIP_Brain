#!/usr/bin/env python3
"""Grid search over FTS5 / Vector channel weight combinations.

Performs a systematic sweep of ``vector_weight`` values while keeping
``fts_weight = 1.0 - vector_weight`` and ``corpus_weight = fts_weight``
(so the two lexical channels share the same weight).  For each combination,
the ``RetrievalEvalHarness`` is run in "hybrid" mode and precision@5,
recall@10, and MRR are recorded.

A baseline "fts-only" evaluation is also run so that the best hybrid
configuration can be compared against pure lexical retrieval.

Results are:
  - Printed as a sorted table (by precision@5 descending).
  - Saved to ``docs/retrieval_weight_tuning_results.json``.

Usage::

    uv run python scripts/retrieval_weight_tuning.py --db-path db/state.db

Resilient: if the vector store is unavailable or has no data, the script
still runs and reports that hybrid couldn't be evaluated, with fts-only
as the only result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_config() -> dict | None:
    """Load AIP config from default location."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return None

    config_path = os.environ.get("AIP_CONFIG_PATH", "config/aip.config.toml")
    if not os.path.exists(config_path):
        return None

    with open(config_path, "rb") as f:
        return tomllib.load(f)


@dataclass
class WeightResult:
    """Metrics for a single weight combination."""

    vector_weight: float
    fts_weight: float
    corpus_weight: float
    precision_at_5: float = 0.0
    recall_at_10: float = 0.0
    mrr: float = 0.0
    mode: str = "hybrid"
    error: str = ""


# ---------------------------------------------------------------------------
# Core grid search
# ---------------------------------------------------------------------------


async def _run_single_eval(
    stores: Any,
    orchestrator: Any,
    queries: list[Any],
    mode: str,
    channel_weights: dict[str, float],
    k: int = 10,
) -> dict[str, float]:
    """Run one evaluation with the given config and return aggregate metrics."""
    from aip.orchestration.retrieval_orchestrator import OrchestratorConfig
    from aip.orchestration.retrieval_eval import RetrievalEvalHarness

    if mode == "fts-only":
        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=False,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=True,
            channel_weights={},
        )
    else:  # hybrid
        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_graph=False,
            enable_wiki=False,
            enable_procedural=False,
            enable_corpus=True,
            channel_weights=channel_weights,
        )

    async def _retriever_fn(query: str):
        return await orchestrator.retrieve(query, config=config)

    harness = RetrievalEvalHarness(k=k)
    result = await harness.run(queries, _retriever_fn)

    return {
        "precision_at_5": result.mean_precision_at_k,
        "recall_at_10": result.mean_recall_at_k,
        "mrr": result.mean_mrr,
    }


async def run_grid_search(db_path: str, golden_path: str) -> list[WeightResult]:
    """Execute the full grid search and return results."""
    from aip.orchestration.retrieval_eval import load_golden_queries

    # Load golden queries
    queries = load_golden_queries(golden_path)
    if not queries:
        print(f"ERROR: No golden queries found at: {golden_path}")
        sys.exit(1)
    print(f"Loaded {len(queries)} golden queries")

    # Create stores and orchestrator
    stores = None
    orchestrator = None
    vector_available = False

    try:
        from aip.orchestration.ask_pipeline import create_ask_stores, _register_retriever_channels
        from aip.orchestration.retrieval_orchestrator import get_orchestrator_cache

        stores = await create_ask_stores(db_path)

        # Check if vector store actually has data
        if stores.vector_store is not None and stores.embedding_provider is not None:
            try:
                # Quick probe: try to retrieve with a dummy vector
                import numpy as np

                probe_vec = [0.0] * 768
                hits = await stores.vector_store.retrieve(probe_vec, top_k=1)
                vector_available = True
                print(f"Vector store available (probe returned {len(hits)} hits)")
            except Exception as exc:
                print(f"Vector store probe failed: {exc}")
                vector_available = False
        else:
            print("Vector store or embedding provider not available")
    except Exception as exc:
        print(f"Warning: Could not create stores: {exc}")
        stores = None

    if stores is not None:
        from aip.orchestration.retrieval_orchestrator import get_orchestrator_cache
        from aip.orchestration.ask_pipeline import _register_retriever_channels

        cache = get_orchestrator_cache()
        cache.invalidate()  # Force fresh orchestrator
        store_key = id(stores.lexical_store) ^ id(stores.vector_store) ^ id(stores.corpus_turn_store)
        orchestrator = cache.get_or_create(
            store_key=store_key,
            register_fn=lambda orch: _register_retriever_channels(orch, stores),
        )

    # Define the weight grid
    vector_weights = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    results: list[WeightResult] = []

    # ---- Run FTS-only baseline ----
    print("\n--- Running FTS-only baseline ---")
    if orchestrator is not None:
        try:
            metrics = await _run_single_eval(
                stores=stores,
                orchestrator=orchestrator,
                queries=queries,
                mode="fts-only",
                channel_weights={},
                k=10,
            )
            fts_result = WeightResult(
                vector_weight=0.0,
                fts_weight=1.0,
                corpus_weight=1.0,
                precision_at_5=metrics["precision_at_5"],
                recall_at_10=metrics["recall_at_10"],
                mrr=metrics["mrr"],
                mode="fts-only",
            )
            results.append(fts_result)
            print(
                f"  FTS-only: P@5={metrics['precision_at_5']:.4f}  "
                f"R@10={metrics['recall_at_10']:.4f}  "
                f"MRR={metrics['mrr']:.4f}"
            )
        except Exception as exc:
            print(f"  FTS-only evaluation failed: {exc}")
            results.append(
                WeightResult(
                    vector_weight=0.0,
                    fts_weight=1.0,
                    corpus_weight=1.0,
                    mode="fts-only",
                    error=str(exc),
                )
            )
    else:
        print("  Skipping FTS-only: no stores available")
        results.append(
            WeightResult(
                vector_weight=0.0,
                fts_weight=1.0,
                corpus_weight=1.0,
                mode="fts-only",
                error="no stores available",
            )
        )

    # ---- Run hybrid grid search ----
    if not vector_available:
        print("\n--- Skipping hybrid grid search: vector store unavailable ---")
        print("    Only FTS-only baseline is available.")
    elif orchestrator is None:
        print("\n--- Skipping hybrid grid search: orchestrator unavailable ---")
    else:
        print("\n--- Running hybrid grid search ---")
        for vw in vector_weights:
            fts_w = round(1.0 - vw, 2)
            corpus_w = fts_w
            weights = {"vector": vw, "fts": fts_w, "corpus": corpus_w}

            print(f"  vector={vw:.1f}  fts={fts_w:.1f}  corpus={corpus_w:.1f} ... ", end="", flush=True)
            try:
                start = time.monotonic()
                metrics = await _run_single_eval(
                    stores=stores,
                    orchestrator=orchestrator,
                    queries=queries,
                    mode="hybrid",
                    channel_weights=weights,
                    k=10,
                )
                elapsed = time.monotonic() - start
                wr = WeightResult(
                    vector_weight=vw,
                    fts_weight=fts_w,
                    corpus_weight=corpus_w,
                    precision_at_5=metrics["precision_at_5"],
                    recall_at_10=metrics["recall_at_10"],
                    mrr=metrics["mrr"],
                    mode="hybrid",
                )
                results.append(wr)
                print(
                    f"P@5={metrics['precision_at_5']:.4f}  "
                    f"R@10={metrics['recall_at_10']:.4f}  "
                    f"MRR={metrics['mrr']:.4f}  ({elapsed:.1f}s)"
                )
            except Exception as exc:
                print(f"FAILED: {exc}")
                results.append(
                    WeightResult(
                        vector_weight=vw,
                        fts_weight=fts_w,
                        corpus_weight=corpus_w,
                        mode="hybrid",
                        error=str(exc),
                    )
                )

    # Clean up stores
    if stores is not None:
        try:
            await stores.close()
        except Exception:
            pass

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_results_table(results: list[WeightResult]) -> None:
    """Print a formatted results table sorted by precision@5 descending."""
    # Filter out errored results for the main table
    valid = [r for r in results if not r.error]
    errored = [r for r in results if r.error]

    if not valid and not errored:
        print("No results to display.")
        return

    print("\n" + "=" * 90)
    print("  Channel Weight Tuning Results (sorted by Precision@5 descending)")
    print("=" * 90)

    if valid:
        valid.sort(key=lambda r: r.precision_at_5, reverse=True)
        header = f"  {'Mode':<10} {'VecW':>6} {'FtsW':>6} {'CorpW':>6}  {'P@5':>8} {'R@10':>8} {'MRR':>8}"
        print(header)
        print(f"  {'-' * 10} {'-' * 6} {'-' * 6} {'-' * 6}  {'-' * 8} {'-' * 8} {'-' * 8}")
        for r in valid:
            print(
                f"  {r.mode:<10} {r.vector_weight:>6.1f} {r.fts_weight:>6.1f} "
                f"{r.corpus_weight:>6.1f}  {r.precision_at_5:>8.4f} "
                f"{r.recall_at_10:>8.4f} {r.mrr:>8.4f}"
            )

    if errored:
        print()
        print("  Errored combinations:")
        for r in errored:
            print(
                f"  {r.mode:<10} vec={r.vector_weight:.1f} "
                f"fts={r.fts_weight:.1f} corpus={r.corpus_weight:.1f}  "
                f"ERROR: {r.error}"
            )

    print("=" * 90)


def find_best_hybrid(results: list[WeightResult]) -> WeightResult | None:
    """Find the best hybrid result by precision@5."""
    valid_hybrid = [r for r in results if r.mode == "hybrid" and not r.error]
    if not valid_hybrid:
        return None
    return max(valid_hybrid, key=lambda r: r.precision_at_5)


def find_fts_only(results: list[WeightResult]) -> WeightResult | None:
    """Find the FTS-only baseline result."""
    fts_results = [r for r in results if r.mode == "fts-only" and not r.error]
    if not fts_results:
        return None
    return fts_results[0]


def print_comparison(best_hybrid: WeightResult | None, fts_only: WeightResult | None) -> None:
    """Print a comparison between the best hybrid and FTS-only baseline."""
    print("\n" + "=" * 90)
    print("  Best Hybrid vs. FTS-Only Comparison")
    print("=" * 90)

    if best_hybrid is None and fts_only is None:
        print("  No valid results to compare.")
        print("=" * 90)
        return

    if best_hybrid is None:
        print("  No valid hybrid results — vector store likely unavailable.")
        print("  FTS-only is the only mode evaluated.")
        if fts_only is not None:
            print(
                f"    FTS-only: P@5={fts_only.precision_at_5:.4f}  R@10={fts_only.recall_at_10:.4f}  MRR={fts_only.mrr:.4f}"
            )
        print("=" * 90)
        return

    if fts_only is None:
        print("  No valid FTS-only baseline available for comparison.")
        print(f"  Best hybrid: vector={best_hybrid.vector_weight:.1f} fts={best_hybrid.fts_weight:.1f}")
        print(
            f"    P@5={best_hybrid.precision_at_5:.4f}  R@10={best_hybrid.recall_at_10:.4f}  MRR={best_hybrid.mrr:.4f}"
        )
        print("=" * 90)
        return

    # Compute improvement percentages
    def _pct_improvement(hybrid_val: float, fts_val: float) -> float:
        if fts_val == 0.0:
            return 0.0 if hybrid_val == 0.0 else float("inf")
        return ((hybrid_val - fts_val) / fts_val) * 100.0

    p5_imp = _pct_improvement(best_hybrid.precision_at_5, fts_only.precision_at_5)
    r10_imp = _pct_improvement(best_hybrid.recall_at_10, fts_only.recall_at_10)
    mrr_imp = _pct_improvement(best_hybrid.mrr, fts_only.mrr)

    print(
        f"  Best hybrid weights: vector={best_hybrid.vector_weight:.1f}  fts={best_hybrid.fts_weight:.1f}  corpus={best_hybrid.corpus_weight:.1f}"
    )
    print()
    print(f"  {'Metric':<15} {'Hybrid':>10} {'FTS-only':>10} {'Improvement':>12}")
    print(f"  {'-' * 15} {'-' * 10} {'-' * 10} {'-' * 12}")
    print(
        f"  {'Precision@5':<15} {best_hybrid.precision_at_5:>10.4f} {fts_only.precision_at_5:>10.4f} {p5_imp:>+11.1f}%"
    )
    print(f"  {'Recall@10':<15} {best_hybrid.recall_at_10:>10.4f} {fts_only.recall_at_10:>10.4f} {r10_imp:>+11.1f}%")
    print(f"  {'MRR':<15} {best_hybrid.mrr:>10.4f} {fts_only.mrr:>10.4f} {mrr_imp:>+11.1f}%")

    if p5_imp > 0:
        print(f"\n  Hybrid retrieval improves Precision@5 by {p5_imp:.1f}% over FTS-only.")
    elif p5_imp < 0:
        print(f"\n  Hybrid retrieval DEGRADES Precision@5 by {abs(p5_imp):.1f}% vs FTS-only.")
    else:
        print("\n  Hybrid and FTS-only have equal Precision@5.")

    print("=" * 90)


def save_results(results: list[WeightResult], output_path: str) -> None:
    """Save results to a JSON file."""
    best_hybrid = find_best_hybrid(results)
    fts_only = find_fts_only(results)

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "grid_search_version": "1.0",
        "vector_weights_searched": [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        "fts_weight_rule": "1.0 - vector_weight",
        "corpus_weight_rule": "same as fts_weight",
        "results": [
            {
                "mode": r.mode,
                "vector_weight": r.vector_weight,
                "fts_weight": r.fts_weight,
                "corpus_weight": r.corpus_weight,
                "precision_at_5": round(r.precision_at_5, 4),
                "recall_at_10": round(r.recall_at_10, 4),
                "mrr": round(r.mrr, 4),
                "error": r.error,
            }
            for r in results
        ],
        "best_hybrid": (
            {
                "vector_weight": best_hybrid.vector_weight,
                "fts_weight": best_hybrid.fts_weight,
                "corpus_weight": best_hybrid.corpus_weight,
                "precision_at_5": round(best_hybrid.precision_at_5, 4),
                "recall_at_10": round(best_hybrid.recall_at_10, 4),
                "mrr": round(best_hybrid.mrr, 4),
            }
            if best_hybrid
            else None
        ),
        "fts_only_baseline": (
            {
                "precision_at_5": round(fts_only.precision_at_5, 4),
                "recall_at_10": round(fts_only.recall_at_10, 4),
                "mrr": round(fts_only.mrr, 4),
            }
            if fts_only
            else None
        ),
    }

    # Compute improvement if both are available
    if best_hybrid and fts_only:

        def _pct(h: float, f: float) -> float | None:
            if f == 0.0:
                return None
            return round(((h - f) / f) * 100.0, 1)

        output["improvement_vs_fts_only"] = {
            "precision_at_5_pct": _pct(best_hybrid.precision_at_5, fts_only.precision_at_5),
            "recall_at_10_pct": _pct(best_hybrid.recall_at_10, fts_only.recall_at_10),
            "mrr_pct": _pct(best_hybrid.mrr, fts_only.mrr),
        }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grid search over FTS5/Vector channel weight combinations for AIP retrieval",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Database path for AIP stores (default: from AIP_DB_PATH env or db/state.db)",
    )
    parser.add_argument(
        "--golden-queries",
        default=None,
        help="Path to golden queries JSON file (default: tests/retrieval_goldens/golden_queries.json)",
    )
    parser.add_argument(
        "--output",
        default="docs/retrieval_weight_tuning_results.json",
        help="Output path for results JSON (default: docs/retrieval_weight_tuning_results.json)",
    )
    args = parser.parse_args()

    # Resolve paths
    db_path = args.db_path or os.environ.get("AIP_DB_PATH", "db/state.db")
    golden_path = args.golden_queries or os.path.join(
        os.environ.get("AIP_PROJECT_ROOT", "."), "tests", "retrieval_goldens", "golden_queries.json"
    )
    output_path = args.output

    print("=" * 90)
    print("  AIP Channel Weight Tuning — Grid Search")
    print("=" * 90)
    print(f"  DB path:        {db_path}")
    print(f"  Golden queries: {golden_path}")
    print(f"  Output:         {output_path}")
    print(f"  Timestamp:      {datetime.now(timezone.utc).isoformat()}")
    print()

    # Run the async grid search
    import asyncio

    results = asyncio.run(run_grid_search(db_path=db_path, golden_path=golden_path))

    # Print results
    print_results_table(results)

    # Find best and compare
    best_hybrid = find_best_hybrid(results)
    fts_only = find_fts_only(results)
    print_comparison(best_hybrid, fts_only)

    # Save results
    save_results(results, output_path)

    # Print recommendation
    if best_hybrid and not best_hybrid.error:
        print(f"\n  RECOMMENDED: Set the following in config/aip.config.toml:")
        print(f"    [retrieval.channel_weights]")
        print(f"    vector = {best_hybrid.vector_weight:.1f}")
        print(f"    fts = {best_hybrid.fts_weight:.1f}")
        print(f"    corpus = {best_hybrid.corpus_weight:.1f}")
    elif fts_only and not fts_only.error:
        print("\n  RECOMMENDATION: Use fts-only mode (vector store unavailable or ineffective).")


if __name__ == "__main__":
    main()
