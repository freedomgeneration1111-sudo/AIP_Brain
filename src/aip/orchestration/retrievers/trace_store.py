"""Trace Persistence — persist RetrievalTrace to SQLite for analysis.

Persists a summary of each RetrievalTrace to a SQLite table so that
retrieval quality can be analyzed over time, regressions detected,
and retrieval behavior audited.

The persisted data includes:
- Query text and trace ID
- Quality gate status and scores
- Per-retriever hit counts and timing
- Budget usage breakdown
- Entity detection and coverage data
- Retry information (Phase 5.6)

This is intentionally lightweight — we don't persist the full hit
content (which could be large), just the metadata needed for
quality analysis and regression detection.

Phase 5.5 deliverable: Trace Improvements.
Phase 5.6 enhancement: Retry tracking, analytics dashboard methods.

Layer: orchestration. Imports from foundation (schemas).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from aip.foundation.schemas.retrieval_trace import (
    RetrievalTrace,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trace persistence
# ---------------------------------------------------------------------------


class TraceStore:
    """SQLite-backed storage for retrieval trace summaries.

    Persists enough data from each RetrievalTrace to support:
    - Quality trend analysis over time
    - Regression detection (comparing across builds)
    - Audit trail (what was retrieved for each query)
    - Entity coverage tracking
    - Retry behavior analysis (Phase 5.6)
    - Per-retriever contribution stats (Phase 5.6)

    Phase 5.6 additions:
    - Retry tracking columns (retry_triggered, retry_reason, retry_round, etc.)
    - Analytics dashboard methods for common queries
    - Per-retriever contribution statistics
    - Most common fallback/retry reasons

    Design decisions:
    - Stores trace summaries, not full hit content (compact)
    - Uses the same database as the main AIP stores
    - Schema is append-only (no updates or deletes)
    - Queries are efficient (indexed by trace_id, created_at, domain)
    - Graceful degradation: if persistence fails, log and continue

    Usage:
        store = TraceStore(db_path)
        store.persist(trace)

        # Query for analysis
        recent = store.query_recent(limit=100)
        by_domain = store.query_by_domain("education")
        dashboard = store.get_dashboard_summary()
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create the retrieval_traces table if it doesn't exist.

        Phase 5.6: Added retry columns and index for retry_triggered.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS retrieval_traces (
                    trace_id TEXT PRIMARY KEY,
                    query_text TEXT NOT NULL,
                    normalized_query TEXT,
                    domain_filter TEXT,
                    intent_hint TEXT,
                    quality_status TEXT,
                    overall_quality REAL,
                    evidence_tokens INTEGER DEFAULT 0,
                    wiki_tokens INTEGER DEFAULT 0,
                    graph_tokens INTEGER DEFAULT 0,
                    procedural_tokens INTEGER DEFAULT 0,
                    total_estimated_tokens INTEGER DEFAULT 0,
                    hit_count INTEGER DEFAULT 0,
                    excluded_by_budget INTEGER DEFAULT 0,
                    detected_entities TEXT,
                    entity_count INTEGER DEFAULT 0,
                    entity_coverage REAL DEFAULT 0.0,
                    retriever_names TEXT,
                    retriever_count INTEGER DEFAULT 0,
                    query_expansions TEXT,
                    expansion_count INTEGER DEFAULT 0,
                    wiki_injected INTEGER DEFAULT 0,
                    procedural_injected INTEGER DEFAULT 0,
                    fallbacks_triggered TEXT,
                    total_elapsed_ms REAL DEFAULT 0.0,
                    quality_gate_ms REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    raw_scores_json TEXT,
                    -- Phase 5.6: Retry tracking columns
                    retry_triggered INTEGER DEFAULT 0,
                    retry_reason TEXT,
                    retry_round INTEGER DEFAULT 0,
                    retry_strategies_tried TEXT,
                    retry_quality_improved INTEGER DEFAULT 0,
                    retry_first_status TEXT
                )
            """)

            # Create indexes for common queries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_created_at
                ON retrieval_traces(created_at DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_quality_status
                ON retrieval_traces(quality_status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_domain
                ON retrieval_traces(domain_filter)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_retry_triggered
                ON retrieval_traces(retry_triggered)
            """)

            # Add Phase 5.6 columns to existing tables (safe ALTER)
            for col, coltype in [
                ("retry_triggered", "INTEGER DEFAULT 0"),
                ("retry_reason", "TEXT"),
                ("retry_round", "INTEGER DEFAULT 0"),
                ("retry_strategies_tried", "TEXT"),
                ("retry_quality_improved", "INTEGER DEFAULT 0"),
                ("retry_first_status", "TEXT"),
            ]:
                try:
                    conn.execute(
                        f"ALTER TABLE retrieval_traces ADD COLUMN {col} {coltype}"
                    )
                except sqlite3.OperationalError:
                    pass  # Column already exists

            conn.commit()
        finally:
            conn.close()

    def persist(self, trace: RetrievalTrace) -> bool:
        """Persist a retrieval trace summary to SQLite.

        Phase 5.6: Now includes retry tracking columns.

        Args:
            trace: The RetrievalTrace to persist.

        Returns:
            True if persisted successfully, False otherwise.
        """
        if not trace or not trace.trace_id:
            return False

        try:
            conn = sqlite3.connect(self._db_path)
            try:
                # Extract retriever names and counts
                retriever_names = [
                    rt.retriever_name for rt in trace.retriever_traces
                ]
                retriever_hit_counts = {
                    rt.retriever_name: rt.hit_count
                    for rt in trace.retriever_traces
                }

                # Build the summary row (including Phase 5.6 retry fields)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO retrieval_traces
                    (trace_id, query_text, normalized_query, domain_filter, intent_hint,
                     quality_status, overall_quality,
                     evidence_tokens, wiki_tokens, graph_tokens, procedural_tokens,
                     total_estimated_tokens, hit_count, excluded_by_budget,
                     detected_entities, entity_count, entity_coverage,
                     retriever_names, retriever_count,
                     query_expansions, expansion_count,
                     wiki_injected, procedural_injected,
                     fallbacks_triggered, total_elapsed_ms, quality_gate_ms,
                     created_at, raw_scores_json,
                     retry_triggered, retry_reason, retry_round,
                     retry_strategies_tried, retry_quality_improved, retry_first_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trace.trace_id,
                        trace.query.raw_query if trace.query else "",
                        trace.query.normalized_query if trace.query else "",
                        trace.query.domain_filter if trace.query else None,
                        trace.query.intent_hint if trace.query else None,
                        trace.context_quality_status or "unknown",
                        trace.context_quality_scores.get("overall_quality", 0.0),
                        trace.budget_usage.get("evidence_tokens", 0),
                        trace.budget_usage.get("wiki_tokens", 0),
                        trace.budget_usage.get("graph_tokens", 0),
                        trace.budget_usage.get("procedural_tokens", 0),
                        trace.budget_usage.get("total_estimated_tokens", 0),
                        trace.total_hits,
                        trace.excluded_due_to_budget,
                        json.dumps(trace.detected_entities[:20]),
                        len(trace.detected_entities),
                        trace.context_quality_scores.get("entity_coverage", 0.0),
                        json.dumps(retriever_names),
                        len(retriever_names),
                        json.dumps(trace.query_expansions[:10]),
                        len(trace.query_expansions),
                        1 if trace.wiki_injected else 0,
                        1 if trace.procedural_injected else 0,
                        json.dumps(trace.fallbacks_triggered[:10]),
                        # Elapsed: sum of retriever elapsed times
                        sum(rt.elapsed_ms for rt in trace.retriever_traces),
                        trace.quality_gate_elapsed_ms,
                        trace.created_at.isoformat() if trace.created_at else datetime.now(timezone.utc).isoformat(),
                        json.dumps({
                            "budget_usage": trace.budget_usage,
                            "quality_scores": trace.context_quality_scores,
                            "retriever_hit_counts": retriever_hit_counts,
                            "fusion_top_ids": [fid for fid, _, _ in trace.fusion_ranks[:10]],
                            "retry_first_scores": trace.retry_first_scores if trace.retry_triggered else None,
                        }),
                        # Phase 5.6: Retry fields
                        1 if trace.retry_triggered else 0,
                        trace.retry_reason or None,
                        trace.retry_round,
                        json.dumps(trace.retry_strategies_tried) if trace.retry_strategies_tried else None,
                        1 if trace.retry_quality_improved else 0,
                        trace.retry_first_status or None,
                    ),
                )
                conn.commit()
                return True
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("Failed to persist trace: %s", exc)
            return False

    def query_recent(self, limit: int = 100) -> list[dict]:
        """Query the most recent traces.

        Returns list of dicts with trace summary data.
        """
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT * FROM retrieval_traces
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def query_by_domain(self, domain: str, limit: int = 100) -> list[dict]:
        """Query traces for a specific domain."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT * FROM retrieval_traces
                WHERE domain_filter = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (domain, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def query_quality_summary(self, hours: int = 24) -> dict[str, Any]:
        """Query quality metrics summary for the last N hours.

        Returns aggregate statistics about retrieval quality.
        """
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        conn = sqlite3.connect(self._db_path)
        try:
            # Total queries
            total = conn.execute(
                "SELECT COUNT(*) FROM retrieval_traces WHERE created_at >= ?",
                (cutoff,),
            ).fetchone()[0]

            # Quality distribution
            status_counts = {}
            for row in conn.execute(
                """
                SELECT quality_status, COUNT(*) as cnt
                FROM retrieval_traces
                WHERE created_at >= ?
                GROUP BY quality_status
                """,
                (cutoff,),
            ).fetchall():
                status_counts[row[0]] = row[1]

            # Average quality
            avg_result = conn.execute(
                """
                SELECT AVG(overall_quality), AVG(entity_coverage), AVG(hit_count)
                FROM retrieval_traces
                WHERE created_at >= ?
                """,
                (cutoff,),
            ).fetchone()

            # Fallback rate
            fallback_count = conn.execute(
                """
                SELECT COUNT(*) FROM retrieval_traces
                WHERE created_at >= ? AND fallbacks_triggered != '[]'
                """,
                (cutoff,),
            ).fetchone()[0]

            return {
                "total_queries": total,
                "hours": hours,
                "quality_distribution": status_counts,
                "avg_quality": round(avg_result[0] or 0.0, 3),
                "avg_entity_coverage": round(avg_result[1] or 0.0, 3),
                "avg_hit_count": round(avg_result[2] or 0.0, 1),
                "fallback_rate": round(fallback_count / max(total, 1), 3),
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Phase 5.6: Analytics Dashboard Methods
    # ------------------------------------------------------------------

    def get_dashboard_summary(self, hours: int = 24) -> dict[str, Any]:
        """Get a comprehensive dashboard summary of retrieval quality.

        Combines multiple analytics queries into a single dashboard
        summary that covers quality status distribution, entity coverage,
        retry behavior, and per-retriever contribution stats.

        This is the primary entry point for the Trace Dashboard Foundation
        (Phase 5.6), providing everything a UI or CLI needs to display
        retrieval observability.

        Args:
            hours: Lookback window in hours. Default 24.

        Returns:
            Dict with keys: total_queries, quality_distribution,
            avg_entity_coverage, retry_stats, retriever_contribution,
            common_retry_reasons, fallback_rate.
        """
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        conn = sqlite3.connect(self._db_path)
        try:
            # Total queries
            total = conn.execute(
                "SELECT COUNT(*) FROM retrieval_traces WHERE created_at >= ?",
                (cutoff,),
            ).fetchone()[0]

            if total == 0:
                return {
                    "total_queries": 0,
                    "hours": hours,
                    "quality_distribution": {},
                    "avg_entity_coverage": 0.0,
                    "retry_stats": {"triggered": 0, "improved": 0, "rate": 0.0},
                    "retriever_contribution": {},
                    "common_retry_reasons": [],
                    "fallback_rate": 0.0,
                    "avg_quality": 0.0,
                }

            # Quality status distribution
            quality_dist = {}
            for row in conn.execute(
                """
                SELECT quality_status, COUNT(*) as cnt
                FROM retrieval_traces
                WHERE created_at >= ?
                GROUP BY quality_status
                """,
                (cutoff,),
            ).fetchall():
                quality_dist[row[0]] = row[1]

            # Average entity coverage
            avg_coverage = conn.execute(
                """
                SELECT AVG(entity_coverage)
                FROM retrieval_traces
                WHERE created_at >= ?
                """,
                (cutoff,),
            ).fetchone()[0] or 0.0

            # Average overall quality
            avg_quality = conn.execute(
                """
                SELECT AVG(overall_quality)
                FROM retrieval_traces
                WHERE created_at >= ?
                """,
                (cutoff,),
            ).fetchone()[0] or 0.0

            # Retry statistics
            retry_triggered = conn.execute(
                """
                SELECT COUNT(*) FROM retrieval_traces
                WHERE created_at >= ? AND retry_triggered = 1
                """,
                (cutoff,),
            ).fetchone()[0]
            retry_improved = conn.execute(
                """
                SELECT COUNT(*) FROM retrieval_traces
                WHERE created_at >= ? AND retry_triggered = 1 AND retry_quality_improved = 1
                """,
                (cutoff,),
            ).fetchone()[0]

            # Common retry reasons
            retry_reasons: dict[str, int] = {}
            for row in conn.execute(
                """
                SELECT retry_reason, COUNT(*) as cnt
                FROM retrieval_traces
                WHERE created_at >= ? AND retry_triggered = 1 AND retry_reason IS NOT NULL
                GROUP BY retry_reason
                ORDER BY cnt DESC
                LIMIT 10
                """,
                (cutoff,),
            ).fetchall():
                retry_reasons[row[0]] = row[1]

            # Per-retriever contribution stats
            # Parse the retriever_names JSON to count each retriever's appearances
            retriever_contribution: dict[str, dict[str, Any]] = {}
            rows = conn.execute(
                """
                SELECT retriever_names, raw_scores_json
                FROM retrieval_traces
                WHERE created_at >= ?
                """,
                (cutoff,),
            ).fetchall()
            for row in rows:
                try:
                    names = json.loads(row[0]) if row[0] else []
                    scores_json = json.loads(row[1]) if row[1] else {}
                    hit_counts = scores_json.get("retriever_hit_counts", {})
                    for name in names:
                        if name not in retriever_contribution:
                            retriever_contribution[name] = {
                                "appearances": 0,
                                "total_hits": 0,
                                "avg_hits": 0.0,
                            }
                        retriever_contribution[name]["appearances"] += 1
                        retriever_contribution[name]["total_hits"] += hit_counts.get(name, 0)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Compute averages for retriever contribution
            for name in retriever_contribution:
                appearances = retriever_contribution[name]["appearances"]
                if appearances > 0:
                    retriever_contribution[name]["avg_hits"] = round(
                        retriever_contribution[name]["total_hits"] / appearances, 1
                    )

            # Fallback rate
            fallback_count = conn.execute(
                """
                SELECT COUNT(*) FROM retrieval_traces
                WHERE created_at >= ? AND fallbacks_triggered != '[]'
                """,
                (cutoff,),
            ).fetchone()[0]

            return {
                "total_queries": total,
                "hours": hours,
                "quality_distribution": quality_dist,
                "avg_entity_coverage": round(avg_coverage, 3),
                "avg_quality": round(avg_quality, 3),
                "retry_stats": {
                    "triggered": retry_triggered,
                    "improved": retry_improved,
                    "rate": round(retry_triggered / max(total, 1), 3),
                    "improvement_rate": round(
                        retry_improved / max(retry_triggered, 1), 3
                    ),
                },
                "retriever_contribution": retriever_contribution,
                "common_retry_reasons": retry_reasons,
                "fallback_rate": round(fallback_count / max(total, 1), 3),
            }
        finally:
            conn.close()

    def query_retry_stats(self, hours: int = 168) -> dict[str, Any]:
        """Query retry behavior statistics.

        Provides detailed statistics about automatic retry behavior,
        including how often retries are triggered, which strategies
        are most common, and whether they improve results.

        Args:
            hours: Lookback window in hours. Default 168 (7 days).

        Returns:
            Dict with retry trigger count, improvement rate,
            strategy distribution, and reason distribution.
        """
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        conn = sqlite3.connect(self._db_path)
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM retrieval_traces WHERE created_at >= ?",
                (cutoff,),
            ).fetchone()[0]

            retry_count = conn.execute(
                """
                SELECT COUNT(*) FROM retrieval_traces
                WHERE created_at >= ? AND retry_triggered = 1
                """,
                (cutoff,),
            ).fetchone()[0]

            retry_improved = conn.execute(
                """
                SELECT COUNT(*) FROM retrieval_traces
                WHERE created_at >= ? AND retry_quality_improved = 1
                """,
                (cutoff,),
            ).fetchone()[0]

            # Retry reason distribution
            reason_dist = {}
            for row in conn.execute(
                """
                SELECT retry_reason, COUNT(*) as cnt
                FROM retrieval_traces
                WHERE created_at >= ? AND retry_triggered = 1 AND retry_reason IS NOT NULL
                GROUP BY retry_reason
                ORDER BY cnt DESC
                """,
                (cutoff,),
            ).fetchall():
                reason_dist[row[0]] = row[1]

            # Retry strategy distribution
            strategy_dist: dict[str, int] = {}
            rows = conn.execute(
                """
                SELECT retry_strategies_tried
                FROM retrieval_traces
                WHERE created_at >= ? AND retry_triggered = 1 AND retry_strategies_tried IS NOT NULL
                """,
                (cutoff,),
            ).fetchall()
            for row in rows:
                try:
                    strategies = json.loads(row[0])
                    for strategy in strategies:
                        strategy_dist[strategy] = strategy_dist.get(strategy, 0) + 1
                except (json.JSONDecodeError, TypeError):
                    pass

            return {
                "total_queries": total,
                "retry_count": retry_count,
                "retry_rate": round(retry_count / max(total, 1), 3),
                "improvement_rate": round(
                    retry_improved / max(retry_count, 1), 3
                ),
                "reason_distribution": reason_dist,
                "strategy_distribution": strategy_dist,
                "hours": hours,
            }
        finally:
            conn.close()

    def query_retriever_stats(self, hours: int = 168) -> dict[str, Any]:
        """Query per-retriever contribution statistics.

        Shows how often each retriever participates, how many hits
        it contributes on average, and its timing characteristics.

        Args:
            hours: Lookback window in hours. Default 168 (7 days).

        Returns:
            Dict mapping retriever_name → stats dict.
        """
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                """
                SELECT retriever_names, raw_scores_json, total_elapsed_ms
                FROM retrieval_traces
                WHERE created_at >= ?
                """,
                (cutoff,),
            ).fetchall()

            stats: dict[str, dict[str, Any]] = {}
            for row in rows:
                try:
                    names = json.loads(row[0]) if row[0] else []
                    scores_json = json.loads(row[1]) if row[1] else {}
                    hit_counts = scores_json.get("retriever_hit_counts", {})
                    for name in names:
                        if name not in stats:
                            stats[name] = {
                                "appearances": 0,
                                "total_hits": 0,
                                "zero_hit_count": 0,
                            }
                        stats[name]["appearances"] += 1
                        hits = hit_counts.get(name, 0)
                        stats[name]["total_hits"] += hits
                        if hits == 0:
                            stats[name]["zero_hit_count"] += 1
                except (json.JSONDecodeError, TypeError):
                    pass

            # Compute derived stats
            for name in stats:
                appearances = stats[name]["appearances"]
                stats[name]["avg_hits"] = round(
                    stats[name]["total_hits"] / max(appearances, 1), 1
                )
                stats[name]["zero_hit_rate"] = round(
                    stats[name]["zero_hit_count"] / max(appearances, 1), 3
                )

            return stats
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Retrieval quality metrics computation
# ---------------------------------------------------------------------------


def compute_retrieval_metrics(trace: RetrievalTrace) -> dict[str, float]:
    """Compute retrieval quality metrics from a trace.

    These metrics are useful for regression detection and quality
    monitoring. They are computed from the trace data without
    requiring any external resources.

    Phase 5.6: Added retry-related metrics.

    Returns:
        Dict of metric_name → float value.
    """
    metrics: dict[str, float] = {}

    if not trace.retriever_traces:
        return {"total_hits": 0.0, "retriever_count": 0.0}

    # Hit counts
    metrics["total_hits"] = float(trace.total_hits)
    metrics["retriever_count"] = float(len(trace.retriever_traces))
    metrics["excluded_by_budget"] = float(trace.excluded_due_to_budget)

    # Per-retriever hit counts
    for rt in trace.retriever_traces:
        metrics[f"{rt.retriever_name}_hits"] = float(rt.hit_count)
        metrics[f"{rt.retriever_name}_elapsed_ms"] = rt.elapsed_ms

    # Entity detection
    metrics["entities_detected"] = float(len(trace.detected_entities))
    metrics["entity_coverage"] = trace.context_quality_scores.get(
        "entity_coverage", 0.0
    )

    # Query expansion
    metrics["expansion_count"] = float(len(trace.query_expansions))

    # Wiki and procedural injection
    metrics["wiki_injected"] = 1.0 if trace.wiki_injected else 0.0
    metrics["procedural_injected"] = 1.0 if trace.procedural_injected else 0.0

    # Quality gate
    if trace.context_quality_status:
        metrics["quality_status_numeric"] = {
            "sufficient": 1.0,
            "marginal": 0.5,
            "needs_more_context": 0.2,
            "empty": 0.0,
        }.get(trace.context_quality_status, 0.0)

    # Budget usage
    total_est = trace.budget_usage.get("total_estimated_tokens", 0)
    budget_total = trace.budget_usage.get("budget_total_tokens", 8000)
    if budget_total > 0:
        metrics["budget_utilization"] = round(total_est / budget_total, 3)

    # Channel diversity
    metrics["channel_diversity"] = trace.context_quality_scores.get(
        "channel_diversity", 0.0
    )

    # Overall quality
    metrics["overall_quality"] = trace.context_quality_scores.get(
        "overall_quality", 0.0
    )

    # Phase 5.6: Retry metrics
    metrics["retry_triggered"] = 1.0 if trace.retry_triggered else 0.0
    metrics["retry_round"] = float(trace.retry_round)
    metrics["retry_quality_improved"] = 1.0 if trace.retry_quality_improved else 0.0

    # Quality improvement from retry (if applicable)
    if trace.retry_triggered and trace.retry_first_scores:
        first_quality = trace.retry_first_scores.get("overall_quality", 0.0)
        final_quality = trace.context_quality_scores.get("overall_quality", 0.0)
        metrics["retry_quality_delta"] = round(final_quality - first_quality, 3)

    return metrics


__all__ = [
    "TraceStore",
    "compute_retrieval_metrics",
]
