"""Retrieval Quality Evaluation Harness — data-driven quality evaluation.

Runs a set of golden queries against the retrieval pipeline and computes
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
            Populated from RetrievalTrace.channel_contributions.
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
            all queries (channel → total hit count).
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
    eval_harness_version: str = "6.4"

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
                key=lambda x: x[1],
                reverse=True,
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
    """Load golden queries from a JSON or YAML file.

    The file should contain a list of objects (JSON) or a dict with a
    ``questions`` key (YAML), each with:
    - ``query`` (required): The query string.
    - ``relevant_ids`` (optional): List of relevant document IDs.
    - ``expected_entities`` (optional): List of expected entity names.
    - ``domain`` (optional): Domain hint.
    - ``tags`` (optional): Categorisation tags.
    - ``diagnosis_hint`` (optional): Which pipeline stage to blame if
      this question fails (ingestion/embedding/retrieval/ranking/
      synthesis/missing).

    YAML files use the ``questions:`` key to hold the list of query
    objects, matching the format in ``docs/evals/aip_alpha_gold.yaml``.

    Args:
        path: Path to the golden queries file.  Defaults to
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

    # Detect format by extension
    if path.endswith((".yaml", ".yml")):
        return _load_golden_queries_yaml(path)
    else:
        return _load_golden_queries_json(path)


def _load_golden_queries_json(path: str) -> list[GoldenQuery]:
    """Load golden queries from a JSON file."""
    with open(path) as f:
        data = json.load(f)

    queries: list[GoldenQuery] = []
    for item in data:
        if not isinstance(item, dict) or "query" not in item:
            continue
        queries.append(
            GoldenQuery(
                query=item["query"],
                relevant_ids=item.get("relevant_ids", []),
                expected_entities=item.get("expected_entities", []),
                domain=item.get("domain", ""),
                tags=item.get("tags", []),
            )
        )
    return queries


def _load_golden_queries_yaml(path: str) -> list[GoldenQuery]:
    """Load golden queries from a YAML file.

    YAML format expects a top-level ``questions`` key containing a list
    of query objects.  This matches the format in
    ``docs/evals/aip_alpha_gold.yaml``.
    """
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed; cannot load YAML golden queries from %s", path)
        return []

    with open(path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "questions" not in data:
        logger.warning("YAML golden queries file must have a 'questions' key: %s", path)
        return []

    queries: list[GoldenQuery] = []
    for item in data["questions"]:
        if not isinstance(item, dict) or "query" not in item:
            continue
        queries.append(
            GoldenQuery(
                query=item["query"],
                relevant_ids=item.get("relevant_ids", []),
                expected_entities=item.get("expected_entities", []),
                domain=item.get("domain", ""),
                tags=item.get("tags", []),
            )
        )
    return queries


def create_default_golden_queries(path: str) -> None:
    """Create a default golden queries file with sample queries.

    Includes 20 queries with coverage for:
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
                used for channel contribution tracking.

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

            # Extract channel contributions from trace
            ch_contrib: dict[str, int] = {}
            if trace is not None and hasattr(trace, "channel_contributions"):
                ch_contrib = dict(trace.channel_contributions)
                for ch, count in ch_contrib.items():
                    aggregated_channel_contributions[ch] = aggregated_channel_contributions.get(ch, 0) + count

            per_query.append(
                QueryEvalResult(
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
                )
            )

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
# Regression protection
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
                delta_str = f"+{c['delta']:.4f}" if c["delta"] >= 0 else f"{c['delta']:.4f}"
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

    Compares key metrics (mean Recall@k, mean MRR, mean Entity Coverage)
    against a previously saved baseline.  If a metric drops below the
    baseline by more than ``warn_threshold``, a warning is issued.  If it
    drops by more than ``fail_threshold``, the check fails.

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


# ---------------------------------------------------------------------------
# A/B Evaluation Comparison
# ---------------------------------------------------------------------------


@dataclass
class ABComparisonResult:
    """Result of comparing two evaluation runs (config A vs config B).

    Compares two EvalResult instances side-by-side, computing delta metrics
    and identifying queries where one configuration significantly outperforms
    the other.

    Attributes:
        config_a_label: Label for configuration A (e.g. "baseline" or file path).
        config_b_label: Label for configuration B.
        result_a: The EvalResult for configuration A.
        result_b: The EvalResult for configuration B.
        metric_deltas: Dict of metric_name -> {a, b, delta, pct_change}.
        per_query_deltas: Per-query delta breakdown.
        winner: Overall winner ("A", "B", or "tie").
        channel_delta: Channel contribution delta between configs.
    """

    config_a_label: str = "A"
    config_b_label: str = "B"
    result_a: EvalResult | None = None
    result_b: EvalResult | None = None
    metric_deltas: dict = field(default_factory=dict)
    per_query_deltas: list[dict] = field(default_factory=list)
    winner: str = "tie"
    channel_delta: dict = field(default_factory=dict)

    def format_report(self) -> str:
        """Format a human-readable A/B comparison report."""
        lines = [
            "=" * 70,
            "  A/B Retrieval Evaluation Comparison",
            "=" * 70,
            f"  Config A:  {self.config_a_label}",
            f"  Config B:  {self.config_b_label}",
            "-" * 70,
            "  Metric Deltas (B - A, positive = B wins)",
            "-" * 70,
        ]

        for metric_name, delta_info in sorted(self.metric_deltas.items()):
            a_val = delta_info.get("a", 0)
            b_val = delta_info.get("b", 0)
            delta = delta_info.get("delta", 0)
            pct = delta_info.get("pct_change", 0)
            delta_str = f"+{delta:.4f}" if delta >= 0 else f"{delta:.4f}"
            pct_str = f"+{pct:.1%}" if pct >= 0 else f"{pct:.1%}"
            indicator = "  << B" if delta > 0 else ("  << A" if delta < 0 else "  (tie)")
            lines.append(
                f"  {metric_name:25s}  A={a_val:.4f}  B={b_val:.4f}  delta={delta_str}  ({pct_str}){indicator}"
            )

        # Channel contribution delta
        if self.channel_delta:
            lines.append("-" * 70)
            lines.append("  Channel Contribution Delta (B - A)")
            lines.append("-" * 70)
            for ch, delta in sorted(self.channel_delta.items(), key=lambda x: x[1], reverse=True):
                delta_str = f"+{delta}" if delta >= 0 else f"{delta}"
                lines.append(f"    {ch:15s}  {delta_str}")

        # Per-query highlights
        if self.per_query_deltas:
            lines.append("-" * 70)
            lines.append("  Per-Query Highlights (largest improvements/regressions)")
            lines.append("-" * 70)
            sorted_queries = sorted(
                self.per_query_deltas,
                key=lambda q: abs(q.get("recall_delta", 0)),
                reverse=True,
            )
            for qd in sorted_queries[:10]:
                query_display = qd.get("query", "")[:45] + ("..." if len(qd.get("query", "")) > 45 else "")
                recall_d = qd.get("recall_delta", 0)
                mrr_d = qd.get("mrr_delta", 0)
                rd_str = f"+{recall_d:.3f}" if recall_d >= 0 else f"{recall_d:.3f}"
                md_str = f"+{mrr_d:.3f}" if mrr_d >= 0 else f"{mrr_d:.3f}"
                lines.append(f"  Q: {query_display}")
                lines.append(f"     Recall delta={rd_str}  MRR delta={md_str}")

        lines.append("-" * 70)
        lines.append(f"  Overall Winner: {self.winner.upper()}")
        lines.append("=" * 70)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict."""
        return {
            "config_a_label": self.config_a_label,
            "config_b_label": self.config_b_label,
            "winner": self.winner,
            "metric_deltas": self.metric_deltas,
            "channel_delta": self.channel_delta,
            "per_query_deltas": self.per_query_deltas[:20],
        }

    def to_json(self, path: str) -> None:
        """Save A/B comparison results to a JSON file."""
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


def compare_eval_results(
    result_a: EvalResult,
    result_b: EvalResult,
    config_a_label: str = "A",
    config_b_label: str = "B",
) -> ABComparisonResult:
    """Compare two EvalResult instances and produce an A/B comparison.

    Compares metrics from two evaluation runs, computing deltas and
    identifying which configuration performs better overall.  This is the
    core logic behind the ``aip eval retrieval-ab`` CLI command.

    Args:
        result_a: EvalResult from configuration A.
        result_b: EvalResult from configuration B.
        config_a_label: Human-readable label for config A.
        config_b_label: Human-readable label for config B.

    Returns:
        ABComparisonResult with detailed delta analysis.
    """
    comparison = ABComparisonResult(
        config_a_label=config_a_label,
        config_b_label=config_b_label,
        result_a=result_a,
        result_b=result_b,
    )

    # Compare aggregate metrics
    metrics = [
        ("mean_recall_at_k", result_a.mean_recall_at_k, result_b.mean_recall_at_k),
        ("mean_precision_at_k", result_a.mean_precision_at_k, result_b.mean_precision_at_k),
        ("mean_mrr", result_a.mean_mrr, result_b.mean_mrr),
        ("mean_entity_coverage", result_a.mean_entity_coverage, result_b.mean_entity_coverage),
    ]

    wins_a = 0
    wins_b = 0

    for metric_name, a_val, b_val in metrics:
        delta = b_val - a_val
        pct_change = (delta / a_val) if a_val > 0 else (1.0 if b_val > 0 else 0.0)
        comparison.metric_deltas[metric_name] = {
            "a": round(a_val, 4),
            "b": round(b_val, 4),
            "delta": round(delta, 4),
            "pct_change": round(pct_change, 4),
        }
        if delta > 0.001:
            wins_b += 1
        elif delta < -0.001:
            wins_a += 1

    # Channel contribution delta
    all_channels = set(result_a.channel_contribution_summary.keys()) | set(result_b.channel_contribution_summary.keys())
    for ch in all_channels:
        a_count = result_a.channel_contribution_summary.get(ch, 0)
        b_count = result_b.channel_contribution_summary.get(ch, 0)
        comparison.channel_delta[ch] = b_count - a_count

    # Per-query deltas
    a_by_query = {r.query: r for r in result_a.per_query_results}
    b_by_query = {r.query: r for r in result_b.per_query_results}
    all_queries = set(a_by_query.keys()) | set(b_by_query.keys())

    for query in all_queries:
        a_q = a_by_query.get(query)
        b_q = b_by_query.get(query)
        if a_q is None or b_q is None:
            continue
        recall_delta = b_q.recall_at_k - a_q.recall_at_k
        mrr_delta = b_q.mrr - a_q.mrr
        comparison.per_query_deltas.append(
            {
                "query": query,
                "recall_a": round(a_q.recall_at_k, 4),
                "recall_b": round(b_q.recall_at_k, 4),
                "recall_delta": round(recall_delta, 4),
                "mrr_a": round(a_q.mrr, 4),
                "mrr_b": round(b_q.mrr, 4),
                "mrr_delta": round(mrr_delta, 4),
            }
        )

    # Determine overall winner
    if wins_b > wins_a:
        comparison.winner = "B"
    elif wins_a > wins_b:
        comparison.winner = "A"
    else:
        # Tie-break on mean_recall_at_k
        if result_b.mean_recall_at_k > result_a.mean_recall_at_k:
            comparison.winner = "B"
        elif result_a.mean_recall_at_k > result_b.mean_recall_at_k:
            comparison.winner = "A"
        else:
            comparison.winner = "tie"

    return comparison
