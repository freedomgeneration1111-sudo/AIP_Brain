#!/usr/bin/env python3
"""AIP Retrieval Evaluation Script — Phase 5.0 baseline measurement.

Usage:
    python scripts/eval_retrieval.py [--db-path DB_PATH] [--golden-dir DIR] [--output FILE]

Runs golden tests against the current retrieval stack (FTS5 + vector hybrid via
_search_sources) and produces baseline recall numbers. Future phases should
improve these numbers — the golden tests are the arbiter.

Output: JSON report with per-test metrics and overall summary.

This script is standalone — it imports from aip but does not require the
full server to be running. It opens the SQLite database directly and
calls CorpusTurnStore.search() to simulate the FTS5 path.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: add project root to path
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# ---------------------------------------------------------------------------
# Imports (after path setup)
# ---------------------------------------------------------------------------

import yaml  # noqa: E402

from aip.adapter.corpus_turn_store import CorpusTurnStore  # noqa: E402
from aip.foundation.schemas.retrieval_trace import (  # noqa: E402
    EvidenceStatus,
    GoldenTestResult,
    RetrievalBudget,
    RetrievalChannel,
    RetrievalHit,
    RetrievalQuery,
    RetrievalTrace,
    RetrieverTrace,
)


# ---------------------------------------------------------------------------
# Golden test loading
# ---------------------------------------------------------------------------


def load_golden_tests(golden_dir: Path) -> list[dict[str, Any]]:
    """Load all YAML golden test files from directory."""
    tests = []
    for yaml_file in sorted(golden_dir.glob("*.yaml")):
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
            data["_source_file"] = yaml_file.name
            tests.append(data)
    return tests


# ---------------------------------------------------------------------------
# Cluster matching
# ---------------------------------------------------------------------------


def hit_matches_cluster(hit: RetrievalHit, cluster_name: str, keywords: list[str] | None = None) -> bool:
    """Check if a retrieval hit matches a golden cluster.

    Matching strategy:
    1. Exact cluster name in hit entities list
    2. Cluster name substring in hit text (case-insensitive)
    3. Cluster name substring in hit domain
    4. Any keyword match in hit text (if keywords provided)
    """
    text_lower = hit.text.lower()
    cluster_lower = cluster_name.lower()

    # Check entities list
    if any(cluster_lower in e.lower() for e in hit.entities):
        return True

    # Check text content
    # Replace underscores with spaces for natural language matching
    cluster_natural = cluster_lower.replace("_", " ")
    if cluster_natural in text_lower:
        return True

    # Check domain
    if hit.domain and cluster_lower in hit.domain.lower():
        return True

    # Check keywords
    if keywords:
        for kw in keywords:
            if kw.lower() in text_lower:
                return True

    return False


# ---------------------------------------------------------------------------
# CorpusTurn → RetrievalHit conversion
# ---------------------------------------------------------------------------


def corpus_turn_to_hit(turn: Any, rank: int, channel: RetrievalChannel = RetrievalChannel.FTS) -> RetrievalHit:
    """Convert a CorpusTurn search result to a RetrievalHit."""
    # Build entities list from domains, tags, bridges
    entities: list[str] = []
    if hasattr(turn, "domains") and turn.domains:
        entities.extend(turn.domains)
    if hasattr(turn, "tags") and turn.tags:
        entities.extend(turn.tags)
    if hasattr(turn, "bridges") and turn.bridges:
        entities.extend(turn.bridges)

    # Build snippet (first 200 chars)
    snippet = ""
    if hasattr(turn, "searchable_text") and turn.searchable_text:
        snippet = turn.searchable_text[:200]

    # Parse timestamp
    recency_ts = None
    if hasattr(turn, "turn_timestamp") and turn.turn_timestamp:
        try:
            recency_ts = datetime.fromisoformat(turn.turn_timestamp)
        except (ValueError, TypeError):
            pass

    return RetrievalHit(
        id=turn.turn_id,
        source_type="corpus_turn",
        source_id=turn.turn_id,
        title=f"{turn.conversation_name} [{turn.primary_domain}]" if hasattr(turn, "conversation_name") else None,
        text=turn.searchable_text or "",
        snippet=snippet,
        rank=rank,
        score=1.0 - (rank - 1) * 0.02,  # position-based score (1.0, 0.98, 0.96, ...)
        confidence=float(turn.beast_confidence or 0.0),
        recency_ts=recency_ts,
        importance=float(turn.importance) if turn.importance else None,
        domain=turn.primary_domain or None,
        entities=entities,
        retrieval_channel=channel,
        evidence_status=EvidenceStatus.RAW,
        debug={
            "conversation_id": turn.conversation_id if hasattr(turn, "conversation_id") else "",
            "source_model": turn.source_model if hasattr(turn, "source_model") else "",
            "tagging_version": turn.tagging_version if hasattr(turn, "tagging_version") else 0,
        },
    )


# ---------------------------------------------------------------------------
# FTS5 baseline retrieval
# ---------------------------------------------------------------------------


def _sanitize_fts_query(query: str) -> str:
    """Robust FTS5 sanitization (same spirit as ask_pipeline)."""
    # Remove FTS5 special characters
    cleaned = re.sub(r'[?!.*+\-^(){}|~"\\]', " ", query)
    tokens = cleaned.split()
    stop_words = {"a", "an", "the", "is", "are", "was", "were", "be", "been",
                  "being", "have", "has", "had", "do", "does", "did", "will",
                  "would", "could", "should", "may", "might", "shall", "can",
                  "of", "in", "to", "for", "with", "on", "at", "by", "from",
                  "it", "its", "we", "our", "you", "your", "this", "that",
                  "what", "which", "who", "whom", "how", "when", "where", "why",
                  "about", "there", "here", "these", "those", "been", "some",
                  "very", "also", "just", "than", "then", "so", "if", "or",
                  "not", "no", "but", "and", "up", "out", "into", "over"}
    meaningful = [t for t in tokens if len(t) >= 2 and t.lower() not in stop_words]
    if not meaningful:
        meaningful = [t for t in tokens if len(t) >= 1 and t.lower() not in stop_words]
    if not meaningful:
        return query
    return " AND ".join(meaningful)


async def run_fts5_baseline(
    query: str,
    corpus_store: CorpusTurnStore,
    max_results: int = 40,
) -> list[RetrievalHit]:
    """Run FTS5-only retrieval (current baseline path).

    This simulates what _search_sources does via CorpusTurnStore.search().
    No vector search, no graph, no wiki — pure FTS5 BM25.
    """
    sanitized = _sanitize_fts_query(query)

    if not sanitized.strip():
        return []

    results = await corpus_store.search(
        query=sanitized,
        primary_domain=None,  # search ALL domains
        limit=max_results,
    )

    hits = []
    for i, turn in enumerate(results):
        hit = corpus_turn_to_hit(turn, rank=i + 1, channel=RetrievalChannel.FTS)
        hits.append(hit)

    return hits


# ---------------------------------------------------------------------------
# Golden test evaluation
# ---------------------------------------------------------------------------


def evaluate_golden_test(
    test: dict[str, Any],
    hits: list[RetrievalHit],
) -> GoldenTestResult:
    """Evaluate a golden test against retrieval results."""
    test_name = test.get("_source_file", "unknown").replace(".yaml", "")
    query = test.get("query", "")
    must_include = test.get("must_include_clusters", [])
    must_not_dominate = test.get("must_not_dominate", [])
    success_criteria = test.get("success", {})

    # Partition hits by cutoff
    hits_at_10 = hits[:10]
    hits_at_25 = hits[:25]
    hits_at_40 = hits[:40]

    # Check cluster matches at each cutoff
    cluster_hits: dict[str, list[RetrievalHit]] = {}
    for cluster in must_include:
        matching = [h for h in hits_at_40 if hit_matches_cluster(h, cluster)]
        if matching:
            cluster_hits[cluster] = matching

    # Calculate recall at each cutoff
    def recall_at(hits_subset: list[RetrievalHit]) -> float:
        if not must_include:
            return 1.0
        clusters_found = 0
        for cluster in must_include:
            if any(hit_matches_cluster(h, cluster) for h in hits_subset):
                clusters_found += 1
        return clusters_found / len(must_include)

    recall_10 = recall_at(hits_at_10)
    recall_25 = recall_at(hits_at_25)
    recall_40 = recall_at(hits_at_40)

    # Noise: fraction of top-10 not matching ANY must_include cluster
    noise_count = 0
    for h in hits_at_10:
        matches_any = any(hit_matches_cluster(h, c) for c in must_include)
        if not matches_any:
            noise_count += 1
    noise_top_10 = noise_count / max(len(hits_at_10), 1)

    # Check success criteria
    failures = []
    passed = True

    if "recall_at_10" in success_criteria and recall_10 < success_criteria["recall_at_10"]:
        failures.append(f"recall_at_10={recall_10:.2f} < {success_criteria['recall_at_10']}")
        passed = False
    if "recall_at_25" in success_criteria and recall_25 < success_criteria["recall_at_25"]:
        failures.append(f"recall_at_25={recall_25:.2f} < {success_criteria['recall_at_25']}")
        passed = False
    if "recall_at_40" in success_criteria and recall_40 < success_criteria["recall_at_40"]:
        failures.append(f"recall_at_40={recall_40:.2f} < {success_criteria['recall_at_40']}")
        passed = False
    if "noise_top_10_max" in success_criteria and noise_top_10 > success_criteria["noise_top_10_max"]:
        failures.append(f"noise_top_10={noise_top_10:.2f} > {success_criteria['noise_top_10_max']}")
        passed = False

    # Build trace
    trace = RetrievalTrace(
        query=RetrievalQuery(raw_query=query),
        retriever_traces=[
            RetrieverTrace(
                retriever_name="FTS5Baseline",
                enabled=True,
                degraded=False,
                hit_count=len(hits),
                top_score=hits[0].score if hits else 0.0,
                top_hit_ids=[h.id for h in hits[:10]],
                scores=[h.score for h in hits],
                debug={"channel": "fts5", "total_results": len(hits)},
            )
        ],
        total_hits=len(hits),
        final_selected_ids=[h.id for h in hits_at_25],
    )
    trace.compute_summary()

    return GoldenTestResult(
        test_name=test_name,
        query=query,
        total_hits=len(hits),
        hits_at_10=hits_at_10,
        hits_at_25=hits_at_25,
        hits_at_40=hits_at_40,
        cluster_hits=cluster_hits,
        recall_at_10=recall_10,
        recall_at_25=recall_25,
        recall_at_40=recall_40,
        noise_top_10=noise_top_10,
        passed=passed,
        failures=failures,
        trace=trace,
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def result_to_dict(result: GoldenTestResult) -> dict[str, Any]:
    """Convert a GoldenTestResult to a JSON-serializable dict."""
    return {
        "test_name": result.test_name,
        "query": result.query,
        "total_hits": result.total_hits,
        "metrics": {
            "recall_at_10": round(result.recall_at_10, 3),
            "recall_at_25": round(result.recall_at_25, 3),
            "recall_at_40": round(result.recall_at_40, 3),
            "noise_top_10": round(result.noise_top_10, 3),
        },
        "clusters_found": {k: len(v) for k, v in result.cluster_hits.items()},
        "passed": result.passed,
        "failures": result.failures,
        "top_10_domains": list(dict.fromkeys(h.domain for h in result.hits_at_10 if h.domain)),
        "top_10_ids": [h.id for h in result.hits_at_10],
    }


def generate_report(results: list[GoldenTestResult]) -> dict[str, Any]:
    """Generate overall evaluation report."""
    test_results = [result_to_dict(r) for r in results]

    overall = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "retrieval_stack": "FTS5-only (pre-retrieval-architecture baseline)",
        "num_tests": len(results),
        "num_passed": sum(1 for r in results if r.passed),
        "num_failed": sum(1 for r in results if not r.passed),
        "avg_recall_at_10": sum(r.recall_at_10 for r in results) / max(len(results), 1),
        "avg_recall_at_25": sum(r.recall_at_25 for r in results) / max(len(results), 1),
        "avg_recall_at_40": sum(r.recall_at_40 for r in results) / max(len(results), 1),
        "avg_noise_top_10": sum(r.noise_top_10 for r in results) / max(len(results), 1),
        "tests": test_results,
    }

    return overall


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser(description="AIP Retrieval Evaluation")
    parser.add_argument(
        "--db-path",
        default=os.environ.get("AIP_DB_PATH", "db/state.db"),
        help="Path to SQLite database (default: db/state.db)",
    )
    parser.add_argument(
        "--golden-dir",
        default=str(PROJECT_ROOT / "tests" / "retrieval_goldens"),
        help="Directory containing golden test YAML files",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "scripts" / "retrieval_baseline.json"),
        help="Output JSON file for baseline report",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=40,
        help="Max results per query (default: 40)",
    )
    args = parser.parse_args()

    # Validate paths
    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}", file=sys.stderr)
        print(f"  Run 'aip init' first, or set AIP_DB_PATH environment variable.", file=sys.stderr)
        sys.exit(1)

    golden_dir = Path(args.golden_dir)
    if not golden_dir.exists():
        print(f"ERROR: Golden test directory not found at {golden_dir}", file=sys.stderr)
        sys.exit(1)

    # Load golden tests
    golden_tests = load_golden_tests(golden_dir)
    if not golden_tests:
        print(f"ERROR: No golden tests found in {golden_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"AIP Retrieval Evaluation — Phase 5.0 Baseline")
    print(f"=" * 60)
    print(f"Database: {db_path}")
    print(f"Golden tests: {len(golden_tests)} ({golden_dir})")
    print(f"Retrieval stack: FTS5-only (pre-architecture)")
    print(f"Max results: {args.max_results}")
    print()

    # Open corpus store
    corpus_store = CorpusTurnStore(str(db_path))

    # Run each golden test
    results: list[GoldenTestResult] = []
    for test in golden_tests:
        query = test.get("query", "")
        test_name = test.get("_source_file", "unknown").replace(".yaml", "")
        print(f"  Running: {test_name}")
        print(f"    Query: {query}")

        start_time = time.monotonic()
        hits = await run_fts5_baseline(query, corpus_store, max_results=args.max_results)
        elapsed = time.monotonic() - start_time

        result = evaluate_golden_test(test, hits)
        results.append(result)

        print(f"    Hits: {result.total_hits} in {elapsed*1000:.0f}ms")
        print(f"    Recall@10={result.recall_at_10:.2f}  Recall@25={result.recall_at_25:.2f}  Recall@40={result.recall_at_40:.2f}")
        print(f"    Noise@10={result.noise_top_10:.2f}  {'PASS' if result.passed else 'FAIL'}")
        if result.failures:
            for f in result.failures:
                print(f"      FAIL: {f}")
        print()

    # Close store
    await corpus_store.close()

    # Generate report
    report = generate_report(results)

    # Save report
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # Print summary
    print("=" * 60)
    print("BASELINE SUMMARY")
    print(f"  Tests: {report['num_tests']} ({report['num_passed']} passed, {report['num_failed']} failed)")
    print(f"  Avg Recall@10: {report['avg_recall_at_10']:.3f}")
    print(f"  Avg Recall@25: {report['avg_recall_at_25']:.3f}")
    print(f"  Avg Recall@40: {report['avg_recall_at_40']:.3f}")
    print(f"  Avg Noise@10:  {report['avg_noise_top_10']:.3f}")
    print(f"  Report saved: {output_path}")
    print()
    print("These are PRE-ARCHITECTURE baselines.")
    print("Future phases (GraphRetriever, RRF fusion, entity-turn index)")
    print("should improve recall without increasing noise.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
