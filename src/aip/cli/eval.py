"""AIP eval CLI — retrieval quality evaluation commands.

Sprint 5.10: Provides ``aip eval retrieval`` command that runs the
``RetrievalEvalHarness`` against golden queries and outputs structured
results (JSON + human-readable summary).

Usage::

    # Run evaluation with default golden queries
    aip eval retrieval

    # Run with custom golden queries file
    aip eval retrieval --golden-queries path/to/queries.json

    # Save results to a specific directory
    aip eval retrieval --output-dir eval_results

    # Compare against a baseline (regression check)
    aip eval retrieval --baseline eval_results/eval_baseline.json

    # Set the k parameter
    aip eval retrieval --k 5
"""

from __future__ import annotations

import asyncio
import os
import sys

import click


@click.group("eval")
def eval_cmd() -> None:
    """Evaluation commands for AIP retrieval quality."""
    pass


@eval_cmd.command("retrieval")
@click.option(
    "--golden-queries", "-g",
    default=None,
    help="Path to golden queries JSON file. Defaults to tests/retrieval_goldens/golden_queries.json",
)
@click.option(
    "--output-dir", "-o",
    default="eval_results",
    help="Directory to save timestamped evaluation results (default: eval_results)",
)
@click.option(
    "--baseline", "-b",
    default=None,
    help="Path to baseline evaluation JSON for regression comparison",
)
@click.option(
    "--k",
    default=10,
    type=int,
    help="Cutoff rank for Recall@k and Precision@k (default: 10)",
)
@click.option(
    "--db-path",
    default=None,
    help="Database path for AIP stores (default: from config or AIP_DB_PATH env)",
)
@click.option(
    "--fail-on-regression",
    is_flag=True,
    default=False,
    help="Exit with non-zero code if regression check fails",
)
@click.option(
    "--save-baseline",
    is_flag=True,
    default=False,
    help="Save current results as the new baseline",
)
def retrieval_eval(
    golden_queries: str | None,
    output_dir: str,
    baseline: str | None,
    k: int,
    db_path: str | None,
    fail_on_regression: bool,
    save_baseline: bool,
) -> None:
    """Run retrieval quality evaluation against golden queries.

    Evaluates the AIP retrieval pipeline using the golden query set,
    computes standard IR metrics (Recall@k, Precision@k, MRR, Entity
    Coverage), and outputs both a human-readable summary and a JSON
    file with timestamped results.

    If a baseline file is provided (via --baseline), the current metrics
    are compared against the baseline and any significant regressions
    are reported.

    To establish a baseline, run with --save-baseline:

        aip eval retrieval --save-baseline --output-dir eval_results

    Then in CI:

        aip eval retrieval --baseline eval_results/baseline.json --fail-on-regression
    """
    # Resolve DB path
    if db_path is None:
        db_path = os.environ.get("AIP_DB_PATH", "db/state.db")

    # Resolve golden queries path
    if golden_queries is None:
        project_root = os.environ.get("AIP_PROJECT_ROOT", ".")
        golden_queries = os.path.join(
            project_root, "tests", "retrieval_goldens", "golden_queries.json"
        )

    click.echo(f"Running retrieval evaluation (k={k})...")
    click.echo(f"  Golden queries: {golden_queries}")
    click.echo(f"  Output dir:     {output_dir}")
    if baseline:
        click.echo(f"  Baseline:       {baseline}")

    # Run the async evaluation
    try:
        result = asyncio.run(_run_eval(
            golden_path=golden_queries,
            db_path=db_path,
            k=k,
            output_dir=output_dir,
        ))
    except Exception as exc:
        click.echo(f"Error running evaluation: {exc}", err=True)
        raise SystemExit(1)

    # Print human-readable summary
    click.echo("")
    click.echo(result.format_human_summary())

    # Save timestamped results
    saved_path = result.save_with_timestamp(output_dir)
    click.echo(f"\nResults saved to: {saved_path}")

    # Optionally save as baseline
    if save_baseline:
        baseline_path = os.path.join(output_dir, "baseline.json")
        result.to_json(baseline_path)
        click.echo(f"Baseline saved to: {baseline_path}")

    # Regression check
    if baseline:
        from aip.orchestration.retrieval_eval import compare_against_baseline
        check = compare_against_baseline(result, baseline)
        click.echo("")
        click.echo(check.format_report())

        if not check.passed and fail_on_regression:
            raise SystemExit(1)
    elif save_baseline:
        # Also run self-comparison as sanity check
        baseline_path = os.path.join(output_dir, "baseline.json")
        from aip.orchestration.retrieval_eval import compare_against_baseline
        check = compare_against_baseline(result, baseline_path)
        click.echo(f"\nSelf-comparison check: {'PASSED' if check.passed else 'FAILED'}")


async def _run_eval(
    golden_path: str,
    db_path: str,
    k: int,
    output_dir: str,
):
    """Run the evaluation harness with the full AIP retrieval stack."""
    from aip.orchestration.retrieval_eval import (
        RetrievalEvalHarness,
        load_golden_queries,
    )

    # Load golden queries
    queries = load_golden_queries(golden_path)
    if not queries:
        click.echo(f"No golden queries found at: {golden_path}", err=True)
        click.echo("Create a golden queries file with sample queries, or specify --golden-queries")
        raise SystemExit(1)

    click.echo(f"  Loaded {len(queries)} golden queries")

    # Create stores and retriever function
    try:
        from aip.orchestration.ask_pipeline import AskStores, create_ask_stores
        stores = await create_ask_stores(db_path)
    except Exception as exc:
        click.echo(f"Warning: Could not create full stores ({exc}), using mock retriever", err=True)
        stores = None

    if stores is not None:
        # Build a real retriever function using the orchestrator
        from aip.orchestration.retrieval_orchestrator import (
            OrchestratorConfig,
            get_orchestrator_cache,
        )
        from aip.orchestration.ask_pipeline import _register_retriever_channels

        cache = get_orchestrator_cache()
        store_key = id(stores.lexical_store) ^ id(stores.vector_store) ^ id(stores.corpus_turn_store)
        orchestrator = cache.get_or_create(
            store_key=store_key,
            register_fn=lambda orch: _register_retriever_channels(orch, stores),
        )

        # Use all channels for evaluation
        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=True,
            enable_graph=True,
            enable_wiki=True,
            enable_procedural=True,
            enable_corpus=True,
        )

        async def _retriever_fn(query: str):
            return await orchestrator.retrieve(query, config=config)

    else:
        # Fallback: mock retriever for testing the harness itself
        from aip.foundation.schemas.retrieval import RetrievalHit, RetrievalTrace

        async def _retriever_fn(query: str):
            hits = [
                RetrievalHit(id="doc:mock_1", content=f"Mock result for: {query}", score=0.9, source_channel="fts"),
            ]
            trace = RetrievalTrace(query=query, channel_contributions={"fts": 1})
            return hits, trace

    # Run the harness
    harness = RetrievalEvalHarness(k=k)
    result = await harness.run(queries, _retriever_fn)

    # Clean up stores
    if stores is not None:
        try:
            await stores.close()
        except Exception:
            pass

    return result
