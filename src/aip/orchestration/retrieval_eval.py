"""Retrieval Quality Evaluation Harness — data-driven quality evaluation.

Sprint 5.9: Foundation for systematic retrieval quality evaluation.  Runs
a set of golden queries against the retrieval pipeline and computes
standard IR metrics:

- **Recall@k**: Fraction of relevant documents retrieved in top-k.
- **Precision@k**: Fraction of top-k results that are relevant.
- **MRR** (Mean Reciprocal Rank): Average of 1/rank of the first relevant
  result across queries.
- **Entity coverage**: Fraction of expected entities found in the
  retrieval results (specifically for Graph channel evaluation).

Golden queries are loaded from JSON files in ``tests/retrieval_goldens/``.
Each golden query specifies the query string, expected relevant document
IDs, and optionally expected entities.

The harness produces structured results (``EvalResult``) that can be:
- Printed to the console for ad-hoc evaluation.
- Saved to JSON for regression tracking over time.
- Compared against baseline results to detect quality regressions.

Layer: orchestration.  May import foundation, stdlib.  May NOT import
adapter directly — stores are injected via the retriever factory.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from aip.foundation.schemas.retrieval import RetrievalHit

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Golden query types
# ---------------------------------------------------------------------------

@dataclass
class GoldenQuery:
    """A single golden test query with expected relevant results.

    Attributes:
        query: The query string to evaluate.
        relevant_ids: Set of document/chunk IDs that should be considered
            relevant for this query.
        expected_entities: Optional list of entity names that should be
            surfaced by the Graph channel for this query.
        domain: Optional domain hint for the query.
        tags: Optional tags for categorising golden queries.
    """

    query: str
    relevant_ids: list[str] = field(default_factory=list)
    expected_entities: list[str] = field(default_factory=list)
    domain: str = ""
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def compute_recall_at_k(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int = 10,
) -> float:
    """Compute Recall@k: fraction of relevant docs found in top-k.

    Args:
        retrieved_ids: Ordered list of retrieved document IDs.
        relevant_ids: Set of IDs that are relevant.
        k: Cutoff rank.

    Returns:
        Recall value between 0.0 and 1.0.
    """
    if not relevant_ids:
        return 0.0
    relevant_set = set(relevant_ids)
    top_k = set(retrieved_ids[:k])
    hits = len(top_k & relevant_set)
    return hits / len(relevant_set)


def compute_precision_at_k(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int = 10,
) -> float:
    """Compute Precision@k: fraction of top-k results that are relevant.

    Args:
        retrieved_ids: Ordered list of retrieved document IDs.
        relevant_ids: Set of IDs that are relevant.
        k: Cutoff rank.

    Returns:
        Precision value between 0.0 and 1.0.
    """
    if k == 0:
        return 0.0
    relevant_set = set(relevant_ids)
    top_k = retrieved_ids[:k]
    hits = sum(1 for doc_id in top_k if doc_id in relevant_set)
    return hits / k


def compute_mrr(
    retrieved_ids: list[str],
    relevant_ids: list[str],
) -> float:
    """Compute Reciprocal Rank: 1/rank of the first relevant result.

    Args:
        retrieved_ids: Ordered list of retrieved document IDs.
        relevant_ids: Set of IDs that are relevant.

    Returns:
        MRR value between 0.0 and 1.0.  Returns 0.0 if no relevant
        document is found.
    """
    if not relevant_ids:
        return 0.0
    relevant_set = set(relevant_ids)
    for rank, doc_id in enumerate(retrieved_ids, start=1):
        if doc_id in relevant_set:
            return 1.0 / rank
    return 0.0


def compute_entity_coverage(
    retrieved_hits: list[RetrievalHit],
    expected_entities: list[str],
) -> float:
    """Compute entity coverage: fraction of expected entities found in results.

    Checks both the hit IDs (which may contain entity names for graph hits)
    and the hit metadata for entity_name fields.

    Args:
        retrieved_hits: List of RetrievalHit instances from retrieval.
        expected_entities: List of entity names that should be surfaced.

    Returns:
        Coverage value between 0.0 and 1.0.
    """
    if not expected_entities:
        return 0.0

    found_entities: set[str] = set()
    expected_lower = {e.lower() for e in expected_entities}

    for hit in retrieved_hits:
        # Check hit ID (e.g. "graph:EntityName")
        hit_id = hit.id.lower()
        for entity in expected_entities:
            if entity.lower() in hit_id:
                found_entities.add(entity.lower())

        # Check metadata entity_name
        meta = hit.metadata or {}
        entity_name = meta.get("entity_name", "")
        if entity_name and entity_name.lower() in expected_lower:
            found_entities.add(entity_name.lower())

        # Check content for entity mentions
        content = (hit.content or "").lower()
        for entity in expected_entities:
            if entity.lower() in content:
                found_entities.add(entity.lower())

    return len(found_entities) / len(expected_entities)


# ---------------------------------------------------------------------------
# Evaluation result types
# ---------------------------------------------------------------------------

@dataclass
class QueryEvalResult:
    """Evaluation result for a single golden query.

    Attributes:
        query: The query string that was evaluated.
        recall_at_k: Recall@k score.
        precision_at_k: Precision@k score.
        mrr: Reciprocal rank score.
        entity_coverage: Entity coverage score (0.0 if no expected entities).
        num_retrieved: Number of documents retrieved.
        num_relevant: Number of relevant documents (ground truth size).
        retrieved_ids: List of retrieved document IDs (for debugging).
        elapsed_ms: Retrieval elapsed time in milliseconds.
        channel_contributions: Mapping of channel name → hit count for this query.
            Sprint 5.10: Populated from RetrievalTrace.channel_contributions.
    """

    query: str
    recall_at_k: float = 0.0
    precision_at_k: float = 0.0
    mrr: float = 0.0
    entity_coverage: float = 0.0
    num_retrieved: int = 0
    num_relevant: int = 0
    retrieved_ids: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    channel_contributions: dict[str, int] = field(default_factory=dict)


@dataclass
class EvalResult:
    """Aggregated evaluation result across all golden queries.

    Attributes:
        timestamp: When the evaluation was run.
        total_queries: Number of golden queries evaluated.
        mean_recall_at_k: Mean Recall@k across queries.
        mean_precision_at_k: Mean Precision@k across queries.
        mean_mrr: Mean Reciprocal Rank across queries.
        mean_entity_coverage: Mean entity coverage across queries.
        per_query_results: Individual results for each query.
        config_snapshot: Configuration used for the evaluation.
        channel_contribution_summary: Aggregated channel contributions across
            all queries (channel → total hit count).  Sprint 5.10.
        eval_harness_version: Version identifier for the harness.
    """

    timestamp: str = ""
    total_queries: int = 0
    mean_recall_at_k: float = 0.0
    mean_precision_at_k: float = 0.0
    mean_mrr: float = 0.0
    mean_entity_coverage: float = 0.0
    per_query_results: list[QueryEvalResult] = field(default_factory=list)
    config_snapshot: dict = field(default_factory=dict)
    channel_contribution_summary: dict[str, int] = field(default_factory=dict)
    eval_harness_version: str = "5.11"

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict."""
        return {
            "timestamp": self.timestamp,
            "total_queries": self.total_queries,
            "mean_recall_at_k": round(self.mean_recall_at_k, 4),
            "mean_precision_at_k": round(self.mean_precision_at_k, 4),
            "mean_mrr": round(self.mean_mrr, 4),
            "mean_entity_coverage": round(self.mean_entity_coverage, 4),
            "per_query_results": [
                {
                    "query": r.query,
                    "recall_at_k": round(r.recall_at_k, 4),
                    "precision_at_k": round(r.precision_at_k, 4),
                    "mrr": round(r.mrr, 4),
                    "entity_coverage": round(r.entity_coverage, 4),
                    "num_retrieved": r.num_retrieved,
                    "num_relevant": r.num_relevant,
                    "retrieved_ids": r.retrieved_ids[:20],
                    "elapsed_ms": round(r.elapsed_ms, 2),
                    "channel_contributions": r.channel_contributions,
                }
                for r in self.per_query_results
            ],
            "config_snapshot": self.config_snapshot,
            "channel_contribution_summary": self.channel_contribution_summary,
            "eval_harness_version": self.eval_harness_version,
        }

    def to_json(self, path: str) -> None:
        """Save results to a JSON file."""
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    def format_human_summary(self) -> str:
        """Format a human-readable summary of the evaluation results.

        Sprint 5.10: Provides a structured, readable summary suitable for
        console output or CI logs, including per-channel contribution data.

        Returns:
            Multi-line string with the evaluation summary.
        """
        lines = [
            "=" * 60,
            "  Retrieval Quality Evaluation Report",
            "=" * 60,
            f"  Timestamp:  {self.timestamp}",
            f"  Queries:    {self.total_queries}",
            f"  Version:    {self.eval_harness_version}",
            "-" * 60,
            "  Aggregate Metrics",
            "-" * 60,
            f"  Mean Recall@k:          {self.mean_recall_at_k:.4f}",
            f"  Mean Precision@k:       {self.mean_precision_at_k:.4f}",
            f"  Mean MRR:               {self.mean_mrr:.4f}",
            f"  Mean Entity Coverage:   {self.mean_entity_coverage:.4f}",
        ]

        # Channel contribution summary
        if self.channel_contribution_summary:
            lines.append("-" * 60)
            lines.append("  Channel Contribution Summary")
            lines.append("-" * 60)
            total_contrib = sum(self.channel_contribution_summary.values()) or 1
            for ch, count in sorted(
                self.channel_contribution_summary.items(),
                key=lambda x: x[1], reverse=True,
            ):
                pct = count / total_contrib * 100
                lines.append(f"    {ch:15s}  {count:4d} hits  ({pct:5.1f}%)")

        # Per-query details
        if self.per_query_results:
            lines.append("-" * 60)
            lines.append("  Per-Query Results")
            lines.append("-" * 60)
            for r in self.per_query_results:
                query_display = r.query[:50] + ("..." if len(r.query) > 50 else "")
                lines.append(f"  Q: {query_display}")
                lines.append(
                    f"     Recall={r.recall_at_k:.3f}  Prec={r.precision_at_k:.3f}  "
                    f"MRR={r.mrr:.3f}  EntityCov={r.entity_coverage:.3f}  "
                    f"Retrieved={r.num_retrieved}  Elapsed={r.elapsed_ms:.0f}ms"
                )

        lines.append("=" * 60)
        return "\n".join(lines)

    def save_with_timestamp(self, directory: str = "eval_results") -> str:
        """Save evaluation results with a timestamp-based filename.

        Creates the directory if it doesn't exist.  The filename format is
        ``eval_<timestamp>.json`` where timestamp uses a compact ISO format
        that is filesystem-safe and sorts chronologically.

        Args:
            directory: Directory to save results in.

        Returns:
            The path to the saved file.
        """
        os.makedirs(directory, exist_ok=True)
        # Compact timestamp: 20260607T143022Z
        ts_compact = self.timestamp.replace(":", "").replace("-", "").replace(".", "")[:16]
        filename = f"eval_{ts_compact}.json"
        path = os.path.join(directory, filename)
        self.to_json(path)
        return path


# ---------------------------------------------------------------------------
# Golden query loading
# ---------------------------------------------------------------------------

def load_golden_queries(path: str | None = None) -> list[GoldenQuery]:
    """Load golden queries from a JSON file.

    The JSON file should contain a list of objects with:
    - ``query`` (required): The query string.
    - ``relevant_ids`` (optional): List of relevant document IDs.
    - ``expected_entities`` (optional): List of expected entity names.
    - ``domain`` (optional): Domain hint.
    - ``tags`` (optional): Categorisation tags.

    Args:
        path: Path to the golden queries JSON file.  Defaults to
            ``tests/retrieval_goldens/golden_queries.json`` relative to
            the project root.

    Returns:
        List of GoldenQuery instances.
    """
    if path is None:
        # Default path relative to project root
        project_root = os.environ.get("AIP_PROJECT_ROOT", ".")
        path = os.path.join(project_root, "tests", "retrieval_goldens", "golden_queries.json")

    if not os.path.exists(path):
        logger.warning("Golden queries file not found: %s", path)
        return []

    with open(path) as f:
        data = json.load(f)

    queries: list[GoldenQuery] = []
    for item in data:
        if not isinstance(item, dict) or "query" not in item:
            continue
        queries.append(GoldenQuery(
            query=item["query"],
            relevant_ids=item.get("relevant_ids", []),
            expected_entities=item.get("expected_entities", []),
            domain=item.get("domain", ""),
            tags=item.get("tags", []),
        ))
    return queries


def create_default_golden_queries(path: str) -> None:
    """Create a default golden queries file with sample queries.

    Sprint 5.11: Expanded from 5 to 20 queries with coverage for:
    - Multi-hop queries (cross-channel references)
    - Entity-heavy queries (many named entities)
    - Procedural queries (how-to, steps, guides)
    - Cross-domain queries (multiple entities, relationships)
    - Queries with typos/variations
    - Cases where the Graph channel should dominate

    This is useful for bootstrapping the evaluation harness in a new
    project or CI environment.
    """
    default_queries = [
        {
            "query": "What is AIP?",
            "relevant_ids": ["doc:aip_overview", "doc:aip_architecture"],
            "expected_entities": ["AIP"],
            "domain": "aip",
            "tags": ["definitional", "core"],
        },
        {
            "query": "How do I configure the Knowledge Graph?",
            "relevant_ids": ["doc:kg_config", "doc:graph_setup"],
            "expected_entities": ["Knowledge Graph"],
            "domain": "configuration",
            "tags": ["procedural", "graph"],
        },
        {
            "query": "Explain the retrieval pipeline",
            "relevant_ids": ["doc:retrieval_pipeline", "doc:rrf_fusion"],
            "expected_entities": ["RetrievalOrchestrator", "RRF"],
            "domain": "architecture",
            "tags": ["definitional", "retrieval"],
        },
        {
            "query": "Steps to ingest a conversation file",
            "relevant_ids": ["doc:ingest_file", "doc:ingestion_pipeline"],
            "expected_entities": [],
            "domain": "ingestion",
            "tags": ["procedural", "ingestion"],
        },
        {
            "query": "What is SmartContextPacker and how does it work?",
            "relevant_ids": ["doc:smart_context_packer", "doc:budget_packing"],
            "expected_entities": ["SmartContextPacker"],
            "domain": "retrieval",
            "tags": ["definitional", "retrieval"],
        },
        {
            "query": "How does the Knowledge Graph connect to the retrieval pipeline?",
            "relevant_ids": ["doc:kg_config", "doc:retrieval_pipeline", "doc:graph_retrieval"],
            "expected_entities": ["Knowledge Graph", "RetrievalOrchestrator"],
            "domain": "architecture",
            "tags": ["cross-domain", "graph", "retrieval"],
        },
        {
            "query": "What are the differences between FTS and Vector search?",
            "relevant_ids": ["doc:fts_search", "doc:vector_search", "doc:retrieval_pipeline"],
            "expected_entities": ["FTS", "Vector"],
            "domain": "retrieval",
            "tags": ["cross-domain", "definitional", "retrieval"],
        },
        {
            "query": "Deploy AIP in a production environment",
            "relevant_ids": ["doc:deployment_guide", "doc:production_config"],
            "expected_entities": ["AIP"],
            "domain": "deployment",
            "tags": ["procedural", "deployment"],
        },
        {
            "query": "Guide to setting up the wiki channel",
            "relevant_ids": ["doc:wiki_setup", "doc:wiki_channel"],
            "expected_entities": [],
            "domain": "configuration",
            "tags": ["procedural", "wiki"],
        },
        {
            "query": "How does PersonalizedPageRank expand queries in the Graph channel?",
            "relevant_ids": ["doc:graph_retrieval", "doc:ppr_expansion"],
            "expected_entities": ["PersonalizedPageRank", "Graph"],
            "domain": "architecture",
            "tags": ["definitional", "graph", "entity-heavy"],
        },
        {
            "query": "What is RRF fusion and why does it matter?",
            "relevant_ids": ["doc:rrf_fusion", "doc:retrieval_pipeline"],
            "expected_entities": ["RRF"],
            "domain": "retrieval",
            "tags": ["definitional", "retrieval"],
        },
        {
            "query": "Configure the procedural guide channel for how-to articles",
            "relevant_ids": ["doc:procedural_config", "doc:procedural_channel"],
            "expected_entities": [],
            "domain": "configuration",
            "tags": ["procedural", "configuration"],
        },
        {
            "query": "How do I use EntityExtractor with LLM fallback?",
            "relevant_ids": ["doc:entity_extractor", "doc:llm_entity_extraction"],
            "expected_entities": ["EntityExtractor"],
            "domain": "configuration",
            "tags": ["procedural", "entity-heavy", "llm"],
        },
        {
            "query": "Explain the quality gate in the retrieval orchestrator",
            "relevant_ids": ["doc:quality_gate", "doc:retrieval_pipeline"],
            "expected_entities": [],
            "domain": "architecture",
            "tags": ["definitional", "retrieval"],
        },
        {
            "query": "What is the OrchestratorCache and when is it invalidated?",
            "relevant_ids": ["doc:orchestrator_cache", "doc:retrieval_pipeline"],
            "expected_entities": ["OrchestratorCache"],
            "domain": "architecture",
            "tags": ["definitional", "entity-heavy"],
        },
        {
            "query": "Ingest multiple conversation files at once",
            "relevant_ids": ["doc:ingest_batch", "doc:ingestion_pipeline"],
            "expected_entities": [],
            "domain": "ingestion",
            "tags": ["procedural", "ingestion"],
        },
        {
            "query": "how to setup aip",
            "relevant_ids": ["doc:aip_overview", "doc:deployment_guide"],
            "expected_entities": ["AIP"],
            "domain": "configuration",
            "tags": ["procedural", "typo-variation"],
        },
        {
            "query": "retrival pipeline explaination",
            "relevant_ids": ["doc:retrieval_pipeline", "doc:rrf_fusion"],
            "expected_entities": [],
            "domain": "retrieval",
            "tags": ["definitional", "typo-variation"],
        },
        {
            "query": "Walk through the SmartContextPacker budget algorithm",
            "relevant_ids": ["doc:smart_context_packer", "doc:budget_packing"],
            "expected_entities": ["SmartContextPacker"],
            "domain": "retrieval",
            "tags": ["procedural", "entity-heavy"],
        },
        {
            "query": "Describe how Sexton processes ingested conversations",
            "relevant_ids": ["doc:sexton_pipeline", "doc:ingestion_pipeline"],
            "expected_entities": ["Sexton"],
            "domain": "architecture",
            "tags": ["definitional", "entity-heavy"],
        },
    ]

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(default_queries, f, indent=2)


# ---------------------------------------------------------------------------
# Evaluation harness
# ---------------------------------------------------------------------------

class RetrievalEvalHarness:
    """Evaluation harness for retrieval quality.

    Runs golden queries against the retrieval pipeline and computes
    standard IR metrics.  The harness is decoupled from any specific
    retriever implementation — it accepts an async callable that
    takes a query string and returns a tuple of (hits, trace).

    Usage::

        harness = RetrievalEvalHarness(k=10)
        result = await harness.run(
            golden_queries=queries,
            retriever_fn=my_retrieve_function,
        )
        print(f"Mean Recall@10: {result.mean_recall_at_k:.4f}")
        result.to_json("eval_results.json")
    """

    def __init__(self, k: int = 10) -> None:
        """Initialise the evaluation harness.

        Args:
            k: The cutoff rank for Recall@k and Precision@k.
        """
        self._k = k

    async def run(
        self,
        golden_queries: list[GoldenQuery],
        retriever_fn: Any,  # async (query: str) -> tuple[list[RetrievalHit], Any]
    ) -> EvalResult:
        """Run evaluation over all golden queries.

        Args:
            golden_queries: List of GoldenQuery instances to evaluate.
            retriever_fn: Async callable that accepts a query string and
                returns ``(list[RetrievalHit], trace)``.  The trace is
                used for channel contribution tracking (Sprint 5.10).

        Returns:
            EvalResult with aggregated and per-query metrics.
        """
        import time as _time

        per_query: list[QueryEvalResult] = []
        recall_values: list[float] = []
        precision_values: list[float] = []
        mrr_values: list[float] = []
        entity_coverage_values: list[float] = []
        aggregated_channel_contributions: dict[str, int] = {}

        for gq in golden_queries:
            start = _time.monotonic()
            trace = None
            try:
                hits, trace = await retriever_fn(gq.query)
            except Exception as exc:
                logger.warning("Retrieval failed for golden query '%s': %s", gq.query, exc)
                hits = []
            elapsed = (_time.monotonic() - start) * 1000.0

            retrieved_ids = [h.id for h in hits]

            # Compute metrics
            recall = compute_recall_at_k(retrieved_ids, gq.relevant_ids, k=self._k)
            precision = compute_precision_at_k(retrieved_ids, gq.relevant_ids, k=self._k)
            mrr = compute_mrr(retrieved_ids, gq.relevant_ids)
            entity_cov = compute_entity_coverage(hits, gq.expected_entities)

            # Sprint 5.10: Extract channel contributions from trace
            ch_contrib: dict[str, int] = {}
            if trace is not None and hasattr(trace, "channel_contributions"):
                ch_contrib = dict(trace.channel_contributions)
                for ch, count in ch_contrib.items():
                    aggregated_channel_contributions[ch] = (
                        aggregated_channel_contributions.get(ch, 0) + count
                    )

            per_query.append(QueryEvalResult(
                query=gq.query,
                recall_at_k=recall,
                precision_at_k=precision,
                mrr=mrr,
                entity_coverage=entity_cov,
                num_retrieved=len(hits),
                num_relevant=len(gq.relevant_ids),
                retrieved_ids=retrieved_ids,
                elapsed_ms=elapsed,
                channel_contributions=ch_contrib,
            ))

            recall_values.append(recall)
            precision_values.append(precision)
            mrr_values.append(mrr)
            entity_coverage_values.append(entity_cov)

        # Compute means
        n = len(golden_queries)
        result = EvalResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_queries=n,
            mean_recall_at_k=sum(recall_values) / n if n > 0 else 0.0,
            mean_precision_at_k=sum(precision_values) / n if n > 0 else 0.0,
            mean_mrr=sum(mrr_values) / n if n > 0 else 0.0,
            mean_entity_coverage=sum(entity_coverage_values) / n if n > 0 else 0.0,
            per_query_results=per_query,
            channel_contribution_summary=aggregated_channel_contributions,
        )

        return result

    async def run_from_file(
        self,
        golden_path: str | None = None,
        retriever_fn: Any = None,
    ) -> EvalResult:
        """Load golden queries from a file and run evaluation.

        Args:
            golden_path: Path to the golden queries JSON file.
            retriever_fn: Async callable for retrieval.

        Returns:
            EvalResult with aggregated and per-query metrics.
        """
        queries = load_golden_queries(golden_path)
        if not queries:
            return EvalResult(
                timestamp=datetime.now(timezone.utc).isoformat(),
                total_queries=0,
            )
        return await self.run(queries, retriever_fn)


# ---------------------------------------------------------------------------
# Regression protection (Sprint 5.10)
# ---------------------------------------------------------------------------

@dataclass
class RegressionCheckResult:
    """Result of comparing current evaluation metrics against a baseline.

    Attributes:
        passed: Whether no significant regressions were detected.
        warnings: List of warning messages for minor regressions.
        failures: List of failure messages for major regressions.
        comparisons: Detailed comparison for each metric.
    """

    passed: bool = True
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    comparisons: list[dict] = field(default_factory=list)

    def format_report(self) -> str:
        """Format a human-readable regression check report."""
        lines = [
            "Regression Check Report",
            "=" * 50,
        ]
        if self.comparisons:
            lines.append("Metric Comparisons:")
            for c in self.comparisons:
                delta_str = f"+{c['delta']:.4f}" if c['delta'] >= 0 else f"{c['delta']:.4f}"
                status = "OK" if c.get("ok", True) else "REGRESSED"
                lines.append(
                    f"  {c['metric']:25s}  baseline={c['baseline']:.4f}  "
                    f"current={c['current']:.4f}  delta={delta_str}  [{status}]"
                )

        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  WARNING: {w}")

        if self.failures:
            lines.append("")
            lines.append("Failures:")
            for f in self.failures:
                lines.append(f"  FAIL: {f}")

        if self.passed and not self.warnings:
            lines.append("")
            lines.append("Result: ALL CLEAR — no regressions detected.")
        elif self.passed:
            lines.append("")
            lines.append("Result: PASSED with warnings — minor regressions noted.")
        else:
            lines.append("")
            lines.append("Result: FAILED — significant regressions detected!")
        return "\n".join(lines)


def compare_against_baseline(
    current: EvalResult,
    baseline_path: str,
    warn_threshold: float = 0.05,
    fail_threshold: float = 0.15,
) -> RegressionCheckResult:
    """Compare current evaluation results against a saved baseline.

    Sprint 5.10: Provides regression protection by comparing key metrics
    (mean Recall@k, mean MRR, mean Entity Coverage) against a previously
    saved baseline.  If a metric drops below the baseline by more than
    ``warn_threshold``, a warning is issued.  If it drops by more than
    ``fail_threshold``, the check fails.

    This is intended to be run as part of CI or as a pre-merge check
    to catch quality regressions early.

    Args:
        current: The current EvalResult to check.
        baseline_path: Path to the baseline JSON file.
        warn_threshold: Fractional drop that triggers a warning (default 5%).
        fail_threshold: Fractional drop that triggers a failure (default 15%).

    Returns:
        RegressionCheckResult with pass/fail status and detailed comparisons.
    """
    result = RegressionCheckResult()

    if not os.path.exists(baseline_path):
        result.warnings.append(f"Baseline file not found: {baseline_path}")
        return result

    try:
        with open(baseline_path) as f:
            baseline_data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        result.warnings.append(f"Could not load baseline: {exc}")
        return result

    # Metrics to compare: (metric_name, current_value, baseline_value)
    metrics_to_check = [
        ("mean_recall_at_k", current.mean_recall_at_k, baseline_data.get("mean_recall_at_k", 0)),
        ("mean_mrr", current.mean_mrr, baseline_data.get("mean_mrr", 0)),
        ("mean_precision_at_k", current.mean_precision_at_k, baseline_data.get("mean_precision_at_k", 0)),
        ("mean_entity_coverage", current.mean_entity_coverage, baseline_data.get("mean_entity_coverage", 0)),
    ]

    for metric_name, current_val, baseline_val in metrics_to_check:
        delta = current_val - baseline_val
        fractional_drop = -delta / baseline_val if baseline_val > 0 else 0.0

        comparison = {
            "metric": metric_name,
            "baseline": baseline_val,
            "current": current_val,
            "delta": delta,
            "fractional_drop": fractional_drop,
            "ok": True,
        }

        if delta < 0 and baseline_val > 0:
            if fractional_drop > fail_threshold:
                result.failures.append(
                    f"{metric_name} dropped by {fractional_drop:.1%} "
                    f"(baseline={baseline_val:.4f}, current={current_val:.4f})"
                )
                result.passed = False
                comparison["ok"] = False
            elif fractional_drop > warn_threshold:
                result.warnings.append(
                    f"{metric_name} dropped by {fractional_drop:.1%} "
                    f"(baseline={baseline_val:.4f}, current={current_val:.4f})"
                )

        result.comparisons.append(comparison)

    return result
