"""AIP eval CLI — retrieval quality evaluation commands.

Provides ``aip eval retrieval`` command that runs the
``RetrievalEvalHarness`` against golden queries and outputs structured
results (JSON + human-readable summary).

Also provides ``aip eval retrieval-ab`` for A/B comparison of two
evaluation configurations with delta metrics and winner determination,
and ``aip eval budget-tune`` for adaptive per-channel budget tuning
based on channel contribution data.

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

    # Use FTS-only mode (no Vector channel)
    aip eval retrieval --mode fts-only

    # Use all channels (FTS, Vector, Graph, Wiki, Procedural, Corpus)
    aip eval retrieval --mode all

    # Hybrid mode with RRF weights (default)
    aip eval retrieval --mode hybrid

    # A/B comparison: compare two saved evaluation results
    aip eval retrieval-ab --config-a eval_results/eval_a.json --config-b eval_results/eval_b.json

    # A/B comparison: run both configs live and compare
    aip eval retrieval-ab --config-a eval_results/eval_a.json --config-b eval_results/eval_b.json --label-a "FTS only" --label-b "All channels"

    # Budget tuning: suggest per-channel budget adjustments
    aip eval budget-tune

    # Budget tuning: auto-apply suggested adjustments
    aip eval budget-tune --auto-apply

    # Budget tuning: with custom parameters
    aip eval budget-tune --max-change 0.20 --min-budget 2 --min-samples 10
"""

from __future__ import annotations

import asyncio
import json
import os

import click


@click.group("eval")
def eval_cmd() -> None:
    """Evaluation commands for AIP retrieval quality."""
    pass


@eval_cmd.command("retrieval")
@click.option(
    "--golden-queries",
    "-g",
    default=None,
    help="Path to golden queries JSON file. Defaults to tests/retrieval_goldens/golden_queries.json",
)
@click.option(
    "--gold",
    default=None,
    help="Path to gold evaluation YAML file (e.g. docs/evals/aip_alpha_gold.yaml). "
    "Shortcut for --golden-queries with YAML support.",
)
@click.option(
    "--output-dir",
    "-o",
    default="eval_results",
    help="Directory to save timestamped evaluation results (default: eval_results)",
)
@click.option(
    "--baseline",
    "-b",
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
    "--mode",
    "-m",
    type=click.Choice(["hybrid", "fts-only", "all"], case_sensitive=False),
    default="hybrid",
    help="Retrieval mode: 'hybrid' (FTS+Vector+Corpus with RRF weights), "
    "'fts-only' (FTS+Corpus, no Vector), 'all' (all channels incl. Graph/Wiki/Procedural)",
)
@click.option(
    "--save-baseline",
    is_flag=True,
    default=False,
    help="Save current results as the new baseline",
)
@click.option(
    "--diagnostic",
    is_flag=True,
    default=False,
    help="Show per-query diagnostic output: channel health, degradation warnings, "
    "and blame assignment (ingestion/embedding/retrieval/ranking/synthesis/missing)",
)
def retrieval_eval(
    golden_queries: str | None,
    gold: str | None,
    output_dir: str,
    baseline: str | None,
    k: int,
    db_path: str | None,
    fail_on_regression: bool,
    save_baseline: bool,
    mode: str,
    diagnostic: bool,
) -> None:
    """Run retrieval quality evaluation against golden queries.

    Evaluates the AIP retrieval pipeline using the golden query set,
    computes standard IR metrics (Recall@k, Precision@k, MRR, Entity
    Coverage), and outputs both a human-readable summary and a JSON
    file with timestamped results.

    If a baseline file is provided (via --baseline), the current metrics
    are compared against the baseline and any significant regressions
    are reported.

    Sprint 10: Use --gold to specify a YAML evaluation file:
        aip eval retrieval --gold docs/evals/aip_alpha_gold.yaml

    Use --diagnostic to show per-query channel health and blame assignment:
        aip eval retrieval --gold docs/evals/aip_alpha_gold.yaml --diagnostic

    To establish a baseline, run with --save-baseline:

        aip eval retrieval --save-baseline --output-dir eval_results

    Then in CI:

        aip eval retrieval --baseline eval_results/baseline.json --fail-on-regression
    """
    # Resolve DB path
    if db_path is None:
        db_path = os.environ.get("AIP_DB_PATH", "db/state.db")

    # Resolve golden queries path -- --gold is a shortcut for YAML files
    if gold is not None:
        golden_queries = gold
    if golden_queries is None:
        project_root = os.environ.get("AIP_PROJECT_ROOT", ".")
        golden_queries = os.path.join(project_root, "tests", "retrieval_goldens", "golden_queries.json")

    click.echo(f"Running retrieval evaluation (k={k}, mode={mode})...")
    click.echo(f"  Golden queries: {golden_queries}")
    click.echo(f"  Output dir:     {output_dir}")
    click.echo(f"  Mode:           {mode}")
    if baseline:
        click.echo(f"  Baseline:       {baseline}")

    # Run the async evaluation
    try:
        result = asyncio.run(
            _run_eval(
                golden_path=golden_queries,
                db_path=db_path,
                k=k,
                output_dir=output_dir,
                mode=mode,
            )
        )
    except Exception as exc:
        click.echo(f"Error running evaluation: {exc}", err=True)
        raise SystemExit(1)

    # Print human-readable summary
    click.echo("")
    click.echo(result.format_human_summary())

    # Sprint 10: Diagnostic output — per-query channel health and blame assignment
    if diagnostic and result.per_query_results:
        click.echo("")
        click.echo("=" * 70)
        click.echo("  Diagnostic Analysis — Per-Query Channel Health & Blame Assignment")
        click.echo("=" * 70)
        for r in result.per_query_results:
            query_display = r.query[:60] + ("..." if len(r.query) > 60 else "")
            click.echo(f"\n  Q: {query_display}")
            click.echo(
                f"     Recall={r.recall_at_k:.3f}  MRR={r.mrr:.3f}  "
                f"Retrieved={r.num_retrieved}  Relevant={r.num_relevant}"
            )

            # Determine blame assignment
            if r.num_retrieved == 0 and r.num_relevant > 0:
                blame = "retrieval"
                detail = "No documents retrieved for a question with known relevant sources"
            elif r.recall_at_k < 0.3 and r.num_relevant > 0:
                if r.num_retrieved > 0:
                    blame = "ranking"
                    detail = f"Documents retrieved but recall low ({r.recall_at_k:.1%}) — ranking issue"
                else:
                    blame = "retrieval"
                    detail = "No documents retrieved"
            elif r.recall_at_k >= 0.3 and r.mrr < 0.3:
                blame = "ranking"
                detail = f"Documents found but first relevant result ranked low (MRR={r.mrr:.3f})"
            elif r.num_relevant == 0:
                blame = "missing"
                detail = "No known relevant sources — source material may not exist in corpus"
            elif r.recall_at_k >= 0.5:
                blame = "synthesis"
                detail = "Retrieval healthy — if answer is weak, problem is in synthesis"
            else:
                blame = "retrieval"
                detail = "Moderate retrieval — may need embedding or ingestion improvements"

            # Check channel contributions for more specific diagnosis
            if r.channel_contributions:
                ch_summary = ", ".join(f"{ch}={cnt}" for ch, cnt in sorted(r.channel_contributions.items()))
                click.echo(f"     Channels: {ch_summary}")

                # If vector contributed 0, flag embedding
                if r.channel_contributions.get("vector", 0) == 0 and "vector" in r.channel_contributions:
                    blame = "embedding"
                    detail = "Vector channel returned 0 results — embedding may be missing or broken"

            click.echo(f"     Blame: {blame.upper()} — {detail}")

    # Save timestamped results
    saved_path = result.save_with_timestamp(output_dir)
    click.echo(f"\nResults saved to: {saved_path}")

    # Optionally save as baseline
    if save_baseline:
        baseline_path = os.path.join(output_dir, "baseline.json")
        result.to_json(baseline_path)
        click.echo(f"Baseline saved to: {baseline_path}")

        # Also save a copy to docs/ for easy reference
        docs_baseline_path = os.path.join(
            os.environ.get("AIP_PROJECT_ROOT", "."),
            "docs",
            "retrieval_benchmark_baseline.json",
        )
        try:
            os.makedirs(os.path.dirname(docs_baseline_path), exist_ok=True)
            result.to_json(docs_baseline_path)
            click.echo(f"Baseline copy saved to: {docs_baseline_path}")
        except OSError as exc:
            click.echo(f"Warning: Could not save baseline to docs/: {exc}", err=True)

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


def _load_config() -> dict | None:
    """Load AIP config from default location (same as ask_pipeline._load_config)."""
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


def _get_channel_weights_from_config(config: dict | None) -> dict[str, float]:
    """Extract channel_weights from the parsed config dict.

    Looks for ``retrieval.channel_weights`` in the config.  Returns an empty
    dict if not found, which means OrchestratorConfig defaults will be used.
    """
    if config is None:
        return {}
    cw = config.get("retrieval", {}).get("channel_weights", {})
    return {k: float(v) for k, v in cw.items() if isinstance(v, (int, float))}


async def _run_eval(
    golden_path: str,
    db_path: str,
    k: int,
    output_dir: str,
    mode: str = "hybrid",
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

    # Load config for channel weights (Sprint 6.4)
    app_config = _load_config()
    config_channel_weights = _get_channel_weights_from_config(app_config)
    if config_channel_weights:
        click.echo(f"  Channel weights (from config): {config_channel_weights}")

    # Create stores and retriever function
    try:
        from aip.orchestration.ask_pipeline import create_ask_stores

        stores = await create_ask_stores(db_path)
    except Exception as exc:
        click.echo(f"Warning: Could not create full stores ({exc}), using mock retriever", err=True)
        stores = None

    if stores is not None:
        # Build a real retriever function using the orchestrator
        from aip.orchestration.ask_pipeline import _register_retriever_channels
        from aip.orchestration.retrieval_orchestrator import (
            OrchestratorConfig,
            get_orchestrator_cache,
        )

        cache = get_orchestrator_cache()
        store_key = id(stores.lexical_store) ^ id(stores.vector_store) ^ id(stores.corpus_turn_store)
        orchestrator = cache.get_or_create(
            store_key=store_key,
            register_fn=lambda orch: _register_retriever_channels(orch, stores),
        )

        # Build OrchestratorConfig based on mode
        if mode == "fts-only":
            config = OrchestratorConfig(
                enable_fts=True,
                enable_vector=False,
                enable_graph=False,
                enable_wiki=False,
                enable_procedural=False,
                enable_corpus=True,
                channel_weights={},  # No weights for FTS-only
            )
        elif mode == "all":
            config = OrchestratorConfig(
                enable_fts=True,
                enable_vector=True,
                enable_graph=True,
                enable_wiki=True,
                enable_procedural=True,
                enable_corpus=True,
            )
        else:  # hybrid (default)
            # Sprint 6.4: use channel weights from config if available,
            # otherwise fall back to OrchestratorConfig defaults.
            config = OrchestratorConfig(
                enable_fts=True,
                enable_vector=True,
                enable_graph=False,
                enable_wiki=False,
                enable_procedural=False,
                enable_corpus=True,
            )
            if config_channel_weights:
                config.channel_weights = config_channel_weights

        async def _retriever_fn(query: str):
            return await orchestrator.retrieve(query, config=config)

    else:
        # Fallback: mock retriever for testing the harness itself
        from aip.foundation.schemas.retrieval import RetrievalHit, RetrievalTrace
        from aip.orchestration.retrieval_orchestrator import OrchestratorConfig

        # Build a placeholder config for config_snapshot in mock mode
        config = OrchestratorConfig(
            enable_fts=True,
            enable_vector=(mode != "fts-only"),
            enable_graph=(mode == "all"),
            enable_wiki=(mode == "all"),
            enable_procedural=(mode == "all"),
            enable_corpus=True,
        )

        async def _retriever_fn(query: str):
            hits = [
                RetrievalHit(id="doc:mock_1", content=f"Mock result for: {query}", score=0.9, source_channel="fts"),
            ]
            trace = RetrievalTrace(query=query, channel_contributions={"fts": 1})
            return hits, trace

    # Run the harness
    harness = RetrievalEvalHarness(k=k)
    result = await harness.run(queries, _retriever_fn)

    # Capture mode in config_snapshot for baseline tracking
    result.config_snapshot["mode"] = mode
    result.config_snapshot["channels_enabled"] = {
        "fts": config.enable_fts,
        "vector": config.enable_vector,
        "graph": config.enable_graph,
        "wiki": config.enable_wiki,
        "procedural": config.enable_procedural,
        "corpus": config.enable_corpus,
    }
    result.config_snapshot["channel_weights"] = config.channel_weights
    result.config_snapshot["k"] = k

    # Clean up stores
    if stores is not None:
        try:
            await stores.close()
        except Exception:
            pass

    return result


@eval_cmd.command("retrieval-ab")
@click.option(
    "--config-a",
    "-a",
    required=True,
    help="Path to evaluation JSON for configuration A",
)
@click.option(
    "--config-b",
    "-b",
    required=True,
    help="Path to evaluation JSON for configuration B",
)
@click.option(
    "--label-a",
    default=None,
    help="Human-readable label for config A (default: file path)",
)
@click.option(
    "--label-b",
    default=None,
    help="Human-readable label for config B (default: file path)",
)
@click.option(
    "--output-dir",
    "-o",
    default="eval_results",
    help="Directory to save A/B comparison results (default: eval_results)",
)
def retrieval_ab_eval(
    config_a: str,
    config_b: str,
    label_a: str | None,
    label_b: str | None,
    output_dir: str,
) -> None:
    """Compare two evaluation runs (A/B test) with delta metrics.

    Loads two previously saved evaluation results and produces a comparison
    report with delta metrics for Recall, Precision, MRR, and Entity
    Coverage.  Also shows channel contribution deltas and per-query
    highlights.

    To use this command, first run two evaluations with different configs:

        aip eval retrieval --output-dir eval_results
        # (change config)
        aip eval retrieval --output-dir eval_results

    Then compare:

        aip eval retrieval-ab \\
            --config-a eval_results/eval_20260608T100000.json \\
            --config-b eval_results/eval_20260608T110000.json \\
            --label-a "FTS+Vector only" \\
            --label-b "All channels"
    """
    import json as _json

    from aip.orchestration.retrieval_eval import (
        EvalResult,
        QueryEvalResult,
        compare_eval_results,
    )

    # Load config A
    if not os.path.exists(config_a):
        click.echo(f"Error: Config A file not found: {config_a}", err=True)
        raise SystemExit(1)
    try:
        with open(config_a) as f:
            data_a = _json.load(f)
    except Exception as exc:
        click.echo(f"Error loading config A: {exc}", err=True)
        raise SystemExit(1)

    # Load config B
    if not os.path.exists(config_b):
        click.echo(f"Error: Config B file not found: {config_b}", err=True)
        raise SystemExit(1)
    try:
        with open(config_b) as f:
            data_b = _json.load(f)
    except Exception as exc:
        click.echo(f"Error loading config B: {exc}", err=True)
        raise SystemExit(1)

    # Reconstruct EvalResult objects from JSON data
    def _reconstruct(data: dict) -> EvalResult:
        per_query = []
        for pq in data.get("per_query_results", []):
            per_query.append(
                QueryEvalResult(
                    query=pq.get("query", ""),
                    recall_at_k=pq.get("recall_at_k", 0),
                    precision_at_k=pq.get("precision_at_k", 0),
                    mrr=pq.get("mrr", 0),
                    entity_coverage=pq.get("entity_coverage", 0),
                    num_retrieved=pq.get("num_retrieved", 0),
                    num_relevant=pq.get("num_relevant", 0),
                    retrieved_ids=pq.get("retrieved_ids", []),
                    elapsed_ms=pq.get("elapsed_ms", 0),
                    channel_contributions=pq.get("channel_contributions", {}),
                )
            )
        return EvalResult(
            timestamp=data.get("timestamp", ""),
            total_queries=data.get("total_queries", 0),
            mean_recall_at_k=data.get("mean_recall_at_k", 0),
            mean_precision_at_k=data.get("mean_precision_at_k", 0),
            mean_mrr=data.get("mean_mrr", 0),
            mean_entity_coverage=data.get("mean_entity_coverage", 0),
            per_query_results=per_query,
            config_snapshot=data.get("config_snapshot", {}),
            channel_contribution_summary=data.get("channel_contribution_summary", {}),
        )

    result_a = _reconstruct(data_a)
    result_b = _reconstruct(data_b)

    # Set labels
    a_label = label_a or config_a
    b_label = label_b or config_b

    click.echo("Comparing A/B evaluation results...")
    click.echo(f"  Config A: {a_label}")
    click.echo(f"  Config B: {b_label}")

    # Run comparison
    comparison = compare_eval_results(
        result_a=result_a,
        result_b=result_b,
        config_a_label=a_label,
        config_b_label=b_label,
    )

    # Print report
    click.echo("")
    click.echo(comparison.format_report())

    # Save comparison results
    os.makedirs(output_dir, exist_ok=True)
    comparison_path = os.path.join(output_dir, "ab_comparison.json")
    comparison.to_json(comparison_path)
    click.echo(f"\nA/B comparison saved to: {comparison_path}")


# ---------------------------------------------------------------------------
# budget-tune: Adaptive per-channel budget tuning
# ---------------------------------------------------------------------------


def _load_channel_contributions_from_eval() -> tuple[dict[str, int], int]:
    """Try to load channel contribution data from the latest eval result.

    Returns:
        Tuple of (channel_contributions dict, total_queries).
        Returns ({}, 0) if no eval results are available.
    """
    eval_dir = os.environ.get("AIP_EVAL_DIR", "eval_results")
    if not os.path.isdir(eval_dir):
        return {}, 0

    try:
        eval_files = [f for f in os.listdir(eval_dir) if f.startswith("eval_") and f.endswith(".json")]
    except OSError:
        return {}, 0

    if not eval_files:
        return {}, 0

    # Sort by filename (which includes timestamp) — last is most recent
    eval_files.sort(reverse=True)
    latest_path = os.path.join(eval_dir, eval_files[0])

    try:
        with open(latest_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}, 0

    contributions = data.get("channel_contribution_summary", {})
    total_queries = data.get("total_queries", 0)

    # Convert values to int if they aren't already
    return {k: int(v) for k, v in contributions.items()}, int(total_queries)


async def _load_channel_contributions_from_trace_store(
    db_path: str,
) -> tuple[dict[str, int], int]:
    """Try to load channel contribution data from the trace store.

    Falls back to this when no eval results exist.

    Returns:
        Tuple of (channel_contributions dict, total_queries).
        Returns ({}, 0) if the trace store is unavailable.
    """
    try:
        from aip.orchestration.ask_pipeline import create_ask_stores

        stores = await create_ask_stores(db_path)
    except Exception:
        return {}, 0

    try:
        # Use the event store to query recent traces for contribution data
        if stores is None or not hasattr(stores, "event_store") or stores.event_store is None:
            return {}, 0

        # Query recent ask_query events
        events = await stores.event_store.query(
            event_type="ask_query",
            limit=200,
        )

        contributions: dict[str, int] = {}
        total_queries = 0

        for ev in events:
            metadata = getattr(ev, "metadata", {}) or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

            # Only count events with retrieval trace data
            if "retrieval_total_ms" not in metadata and "retrieval_verdict" not in metadata:
                continue

            total_queries += 1

            # Extract channel contributions
            ch_contrib_raw = metadata.get("retrieval_channel_contributions", "{}")
            try:
                ch_contrib = json.loads(ch_contrib_raw) if isinstance(ch_contrib_raw, str) else ch_contrib_raw
            except (json.JSONDecodeError, TypeError):
                ch_contrib = {}

            for ch, count in ch_contrib.items():
                contributions[ch] = contributions.get(ch, 0) + int(count)

        return contributions, total_queries
    except Exception:
        return {}, 0
    finally:
        try:
            await stores.close()
        except Exception:
            pass


@eval_cmd.command("budget-tune")
@click.option(
    "--db-path",
    default=None,
    help="Database path for AIP stores (default: from AIP_DB_PATH env or db/state.db)",
)
@click.option(
    "--auto-apply",
    is_flag=True,
    default=False,
    help="Automatically apply suggested budget adjustments (default: show suggestions only)",
)
@click.option(
    "--max-change",
    default=0.30,
    type=float,
    help="Maximum fractional change per channel per tuning cycle (default: 0.30)",
)
@click.option(
    "--min-budget",
    default=1,
    type=int,
    help="Minimum per-channel budget — never reduce below this (default: 1)",
)
@click.option(
    "--min-samples",
    default=5,
    type=int,
    help="Minimum number of queries required to produce tuning suggestions (default: 5)",
)
def budget_tune(
    db_path: str | None,
    auto_apply: bool,
    max_change: float,
    min_budget: int,
    min_samples: int,
) -> None:
    """Adaptively tune per-channel retrieval budgets.

    Analyzes channel contribution data from recent evaluation results
    (or live trace data as a fallback) and suggests budget adjustments
    for each retrieval channel.  Channels that consistently contribute
    few hits may have their budgets reduced, while high-value channels
    may receive budget increases.

    By default this command only shows suggestions.  Use --auto-apply
    to apply the adjustments to the current OrchestratorConfig.

    Examples::

        # Show budget suggestions only (safe, read-only)
        aip eval budget-tune

        # Auto-apply suggested adjustments
        aip eval budget-tune --auto-apply

        # Conservative tuning with smaller max change
        aip eval budget-tune --max-change 0.15 --min-samples 10
    """
    from aip.orchestration.adaptive_budget import AdaptiveBudgetTuner
    from aip.orchestration.retrieval_orchestrator import OrchestratorConfig

    # Resolve DB path
    if db_path is None:
        db_path = os.environ.get("AIP_DB_PATH", "db/state.db")

    click.echo("Running adaptive budget tuning...")
    click.echo(f"  max_change:   {max_change}")
    click.echo(f"  min_budget:   {min_budget}")
    click.echo(f"  min_samples:  {min_samples}")
    click.echo(f"  auto_apply:   {auto_apply}")

    # Step 1: Try to load channel contributions from eval results
    channel_contributions, total_queries = _load_channel_contributions_from_eval()

    if channel_contributions:
        click.echo("\n  Data source:   eval results (latest)")
        click.echo(f"  Total queries: {total_queries}")
    else:
        # Step 2: Fallback — try trace store
        click.echo("\n  No eval results found, trying trace store...")
        channel_contributions, total_queries = asyncio.run(_load_channel_contributions_from_trace_store(db_path))
        if channel_contributions:
            click.echo("  Data source:   trace store")
            click.echo(f"  Total queries: {total_queries}")
        else:
            click.echo("")
            click.echo("No channel contribution data available.")
            click.echo("Run 'aip eval retrieval' first, or ensure the trace store has data.")
            return

    # Step 3: Create OrchestratorConfig and AdaptiveBudgetTuner
    config = OrchestratorConfig()
    tuner = AdaptiveBudgetTuner(
        max_change_fraction=max_change,
        min_budget=min_budget,
        min_samples=min_samples,
        auto_apply=auto_apply,
    )

    # Step 4: Run the tuner
    result = tuner.tune(
        config=config,
        channel_contributions=channel_contributions,
        total_queries=total_queries,
    )

    # Step 5: Print results
    click.echo("")
    click.echo("=" * 60)
    click.echo("  Budget Tuning Result")
    click.echo("=" * 60)
    click.echo(f"  {result.summary}")

    if not result.adjustments:
        click.echo("")
        click.echo("  No budget adjustments suggested.")
        click.echo("  Current budgets appear well-tuned for the observed data.")
    else:
        click.echo("")
        if auto_apply:
            click.echo("  Status: APPLIED (auto-apply enabled)")
        else:
            click.echo("  Status: SUGGESTIONS ONLY (use --auto-apply to apply)")
        click.echo("")

        # Print a table of adjustments
        click.echo(f"  {'Channel':<14} {'Current':>8} {'Suggested':>10} {'Change':>8} {'Confidence':>10}")
        click.echo(f"  {'-' * 14} {'-' * 8} {'-' * 10} {'-' * 8} {'-' * 10}")
        for adj in result.adjustments:
            change = adj.suggested_budget - adj.current_budget
            change_str = f"{change:+d}"
            click.echo(
                f"  {adj.channel_name:<14} {adj.current_budget:>8} "
                f"{adj.suggested_budget:>10} {change_str:>8} {adj.confidence:>10.2f}"
            )

        # Print reasons
        click.echo("")
        click.echo("  Adjustment reasons:")
        for adj in result.adjustments:
            click.echo(f"    [{adj.channel_name}] {adj.reason}")

    # Show current config snapshot after tuning
    if auto_apply and result.adjustments:
        click.echo("")
        click.echo("  Updated budget configuration:")
        click.echo(f"    fts_max_hits:        {config.fts_max_hits}")
        click.echo(f"    vector_max_hits:     {config.vector_max_hits}")
        click.echo(f"    graph_max_hits:      {config.graph_max_hits}")
        click.echo(f"    wiki_max_hits:       {config.wiki_max_hits}")
        click.echo(f"    procedural_max_hits: {config.procedural_max_hits}")
        click.echo(f"    corpus_max_hits:     {config.corpus_max_hits}")

    click.echo("")
    click.echo("=" * 60)
