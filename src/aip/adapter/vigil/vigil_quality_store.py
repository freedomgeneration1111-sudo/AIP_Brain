"""Vigil quality history persistence — SQLite-backed cycle report storage.

Sprint 5.26: Persists Vigil's per-cycle quality reports to SQLite so that
quality data survives process restarts and supports longer time-range
queries from the ``/vigil/quality`` endpoint.

Sprint 5.27: Added configurable retention policy and daily/weekly rollup
aggregation.  Older data is aggregated into summary rows to keep the
table from growing indefinitely while preserving long-term trends.

Schema:
    vigil_quality_history
        id              INTEGER PRIMARY KEY AUTOINCREMENT
        cycle_timestamp TEXT NOT NULL           -- ISO 8601 UTC timestamp
        avg_citation_rate REAL NOT NULL
        avg_grounding_rate REAL NOT NULL
        avg_llm_faithfulness REAL NOT NULL
        evaluated_count INTEGER NOT NULL
        flagged_count INTEGER NOT NULL
        hedging_detected_count INTEGER NOT NULL DEFAULT 0
        llm_eval_count INTEGER NOT NULL DEFAULT 0
        llm_hallucinations INTEGER NOT NULL DEFAULT 0
        cycle_elapsed_seconds REAL NOT NULL DEFAULT 0.0
        trend_indicators TEXT NOT NULL DEFAULT '{}'  -- JSON
        cycle_report TEXT NOT NULL DEFAULT '{}'       -- Full report JSON
        is_rollup INTEGER NOT NULL DEFAULT 0          -- Sprint 5.27: 1=aggregated row
        rollup_period TEXT NOT NULL DEFAULT ''         -- Sprint 5.27: 'daily'|'weekly'
        rollup_count INTEGER NOT NULL DEFAULT 0        -- Sprint 5.27: # of original rows

Design:
    - Lightweight SQLite table — no ORM, stdlib sqlite3
    - Auto-creates table on first use (no separate migration script needed)
    - Graceful degradation: if DB operations fail, Vigil falls back to
      in-memory history (Sprint 5.24 behavior)
    - Query methods support both ``last_n_cycles`` and ``since`` filtering
    - Maximum practical history: configurable (default 10000 rows, auto-pruned)
    - Sprint 5.27: Retention policy (max_days) and rollup aggregation
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aip.logging import get_logger

logger = get_logger(__name__)

# Default maximum number of history rows to retain (auto-prune on insert)
_DEFAULT_MAX_HISTORY_ROWS = 10000

# Default retention period in days (0 = unlimited, keep all)
_DEFAULT_RETENTION_DAYS = 90

# Default age in days before data is eligible for rollup
_DEFAULT_ROLLUP_AGE_DAYS = 7

# Schema version for migrations
_SCHEMA_VERSION = 2


class VigilQualityStore:
    """SQLite-backed persistence for Vigil quality cycle reports.

    Sprint 5.27 additions:
    - Configurable retention policy (max_rows, retention_days)
    - Daily/weekly rollup aggregation for older data
    - Rollup rows preserve trend data while reducing row count

    Usage::

        store = VigilQualityStore(
            db_path="db/vigil_quality.db",
            max_history_rows=10000,
            retention_days=90,
            rollup_age_days=7,
        )
        store.initialize()

        # Record a cycle
        store.record_cycle({...})

        # Query history
        cycles = store.get_cycles(last_n_cycles=50)

        # Run rollup (call periodically, e.g., once per day)
        store.run_rollup()
    """

    def __init__(
        self,
        db_path: str | Path,
        max_history_rows: int = _DEFAULT_MAX_HISTORY_ROWS,
        retention_days: int = _DEFAULT_RETENTION_DAYS,
        rollup_age_days: int = _DEFAULT_ROLLUP_AGE_DAYS,
    ) -> None:
        self._db_path = str(db_path)
        self._max_history_rows = max_history_rows
        self._retention_days = retention_days
        self._rollup_age_days = rollup_age_days
        self._initialized = False

    def initialize(self) -> None:
        """Create the quality history table if it doesn't exist.

        Safe to call multiple times — uses IF NOT EXISTS.

        Sprint 5.27: Runs schema migration from v1 to v2 if needed,
        adding is_rollup, rollup_period, and rollup_count columns.
        """
        if self._initialized:
            return

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")

                # Schema metadata table for future migrations (must exist before version check)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS vigil_quality_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)

                # Sprint 5.27: Check current schema version BEFORE creating/modifying table
                current_version = 0
                try:
                    cursor = conn.execute(
                        "SELECT value FROM vigil_quality_meta WHERE key = 'schema_version'"
                    )
                    row = cursor.fetchone()
                    if row:
                        current_version = int(row[0])
                except Exception:
                    pass

                if current_version < 2:
                    # V1→V2 migration: add rollup columns to existing table
                    for col_spec in [
                        "ALTER TABLE vigil_quality_history ADD COLUMN is_rollup INTEGER NOT NULL DEFAULT 0",
                        "ALTER TABLE vigil_quality_history ADD COLUMN rollup_period TEXT NOT NULL DEFAULT ''",
                        "ALTER TABLE vigil_quality_history ADD COLUMN rollup_count INTEGER NOT NULL DEFAULT 0",
                    ]:
                        try:
                            conn.execute(col_spec)
                        except sqlite3.OperationalError:
                            pass  # Column already exists or table doesn't exist yet

                # Create the table (IF NOT EXISTS — only creates if missing)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS vigil_quality_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        cycle_timestamp TEXT NOT NULL,
                        avg_citation_rate REAL NOT NULL,
                        avg_grounding_rate REAL NOT NULL,
                        avg_llm_faithfulness REAL NOT NULL,
                        evaluated_count INTEGER NOT NULL,
                        flagged_count INTEGER NOT NULL,
                        hedging_detected_count INTEGER NOT NULL DEFAULT 0,
                        llm_eval_count INTEGER NOT NULL DEFAULT 0,
                        llm_hallucinations INTEGER NOT NULL DEFAULT 0,
                        cycle_elapsed_seconds REAL NOT NULL DEFAULT 0.0,
                        trend_indicators TEXT NOT NULL DEFAULT '{}',
                        cycle_report TEXT NOT NULL DEFAULT '{}',
                        is_rollup INTEGER NOT NULL DEFAULT 0,
                        rollup_period TEXT NOT NULL DEFAULT '',
                        rollup_count INTEGER NOT NULL DEFAULT 0
                    )
                """)
                # Create index on timestamp for efficient range queries
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_vigil_quality_timestamp
                    ON vigil_quality_history (cycle_timestamp)
                """)
                # Index for rollup queries
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_vigil_quality_rollup
                    ON vigil_quality_history (is_rollup, cycle_timestamp)
                """)

                # Record schema version
                conn.execute("""
                    INSERT OR REPLACE INTO vigil_quality_meta (key, value)
                    VALUES ('schema_version', ?)
                """, (str(_SCHEMA_VERSION),))

            self._initialized = True
            logger.info(
                "vigil_quality_store_initialized",
                db_path=self._db_path,
                schema_version=_SCHEMA_VERSION,
                max_history_rows=self._max_history_rows,
                retention_days=self._retention_days,
                rollup_age_days=self._rollup_age_days,
            )
        except Exception as exc:
            logger.warning(
                "vigil_quality_store_init_failed",
                db_path=self._db_path,
                error=str(exc),
            )

    def record_cycle(self, cycle_report: dict) -> bool:
        """Record a Vigil quality evaluation cycle to persistent storage.

        Parameters
        ----------
        cycle_report:
            Dict with the per-cycle quality metrics.  Expected keys:
            timestamp, avg_citation_rate, avg_grounding_rate,
            avg_llm_faithfulness, evaluated_count, flagged_count,
            plus optional: hedging_detected_count, llm_eval_count,
            llm_hallucinations, cycle_elapsed_seconds, trend_indicators.

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            ts = cycle_report.get("timestamp", datetime.now(timezone.utc).isoformat())
            trend_json = json.dumps(cycle_report.get("trend_indicators", {}))
            # Store the full report as JSON for flexibility
            full_report_json = json.dumps(cycle_report, default=str)

            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT INTO vigil_quality_history (
                        cycle_timestamp, avg_citation_rate, avg_grounding_rate,
                        avg_llm_faithfulness, evaluated_count, flagged_count,
                        hedging_detected_count, llm_eval_count, llm_hallucinations,
                        cycle_elapsed_seconds, trend_indicators, cycle_report
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ts,
                    cycle_report.get("avg_citation_rate", 0.0),
                    cycle_report.get("avg_grounding_rate", 0.0),
                    cycle_report.get("avg_llm_faithfulness", 0.0),
                    cycle_report.get("evaluated_count", 0),
                    cycle_report.get("flagged_count", 0),
                    cycle_report.get("hedging_detected_count", 0),
                    cycle_report.get("llm_eval_count", 0),
                    cycle_report.get("llm_hallucinations", 0),
                    cycle_report.get("cycle_elapsed_seconds", 0.0),
                    trend_json,
                    full_report_json,
                ))

            # Auto-prune old records (Sprint 5.27: uses configurable max)
            self._prune_if_needed()

            return True
        except Exception as exc:
            logger.warning(
                "vigil_quality_store_record_failed",
                error=str(exc),
            )
            return False

    def get_cycles(
        self,
        last_n_cycles: int | None = None,
        since: str | None = None,
        limit: int = 500,
        include_rollups: bool = True,
    ) -> list[dict]:
        """Query quality cycle history.

        Parameters
        ----------
        last_n_cycles:
            Return the most recent N cycles.  If None, returns all
            matching cycles up to ``limit``.
        since:
            ISO 8601 datetime string — only return cycles with timestamps
            after this value.
        limit:
            Maximum number of rows to return.  Default 500.
        include_rollups:
            If True (default), include rollup summary rows in results.
            If False, only return original (non-rollup) cycle data.

        Returns a list of cycle report dicts, oldest first.
        """
        if not self._initialized:
            self.initialize()

        try:
            conditions: list[str] = []
            params: list[Any] = []

            if since:
                conditions.append("cycle_timestamp > ?")
                params.append(since)

            # Sprint 5.27: Optionally exclude rollup rows
            if not include_rollups:
                conditions.append("is_rollup = 0")

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            query = f"""
                SELECT cycle_timestamp, avg_citation_rate, avg_grounding_rate,
                       avg_llm_faithfulness, evaluated_count, flagged_count,
                       hedging_detected_count, llm_eval_count, llm_hallucinations,
                       cycle_elapsed_seconds, trend_indicators, cycle_report,
                       is_rollup, rollup_period, rollup_count
                FROM vigil_quality_history
                {where_clause}
                ORDER BY cycle_timestamp ASC
                LIMIT ?
            """
            params.append(limit)

            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()

            cycles = []
            for row in rows:
                try:
                    trend = json.loads(row["trend_indicators"]) if row["trend_indicators"] else {}
                    full_report = json.loads(row["cycle_report"]) if row["cycle_report"] else {}
                except (json.JSONDecodeError, TypeError):
                    trend = {}
                    full_report = {}

                cycle_dict = {
                    "timestamp": row["cycle_timestamp"],
                    "avg_citation_rate": row["avg_citation_rate"],
                    "avg_grounding_rate": row["avg_grounding_rate"],
                    "avg_llm_faithfulness": row["avg_llm_faithfulness"],
                    "evaluated_count": row["evaluated_count"],
                    "flagged_count": row["flagged_count"],
                    "hedging_detected_count": row["hedging_detected_count"],
                    "llm_eval_count": row["llm_eval_count"],
                    "llm_hallucinations": row["llm_hallucinations"],
                    "cycle_elapsed_seconds": row["cycle_elapsed_seconds"],
                    "trend_indicators": trend,
                }
                # Merge any extra fields from the full report
                for key in ("flag_rate", "cycle_elapsed"):
                    if key in full_report and key not in cycle_dict:
                        cycle_dict[key] = full_report[key]

                # Sprint 5.27: Include rollup metadata if present
                if row["is_rollup"]:
                    cycle_dict["is_rollup"] = True
                    cycle_dict["rollup_period"] = row["rollup_period"]
                    cycle_dict["rollup_count"] = row["rollup_count"]

                cycles.append(cycle_dict)

            # Apply last_n_cycles filter (from the end, most recent)
            if last_n_cycles is not None and last_n_cycles > 0:
                cycles = cycles[-last_n_cycles:]

            return cycles
        except Exception as exc:
            logger.warning(
                "vigil_quality_store_query_failed",
                error=str(exc),
            )
            return []

    def get_cycle_count(self) -> int:
        """Return the total number of stored quality cycles."""
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM vigil_quality_history")
                return cursor.fetchone()[0]
        except Exception:
            return 0

    def get_schema_version(self) -> int:
        """Return the schema version of the quality history table."""
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute(
                    "SELECT value FROM vigil_quality_meta WHERE key = 'schema_version'"
                )
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        except Exception:
            return 0

    def get_retention_status(self) -> dict:
        """Return retention and rollup status for monitoring.

        Sprint 5.27: Provides operators with visibility into data
        retention and rollup activity.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                total = conn.execute("SELECT COUNT(*) FROM vigil_quality_history").fetchone()[0]
                rollups = conn.execute(
                    "SELECT COUNT(*) FROM vigil_quality_history WHERE is_rollup = 1"
                ).fetchone()[0]
                originals = total - rollups

                oldest_row = conn.execute(
                    "SELECT MIN(cycle_timestamp) FROM vigil_quality_history"
                ).fetchone()[0]
                newest_row = conn.execute(
                    "SELECT MAX(cycle_timestamp) FROM vigil_quality_history"
                ).fetchone()[0]

            return {
                "total_rows": total,
                "original_rows": originals,
                "rollup_rows": rollups,
                "oldest_timestamp": oldest_row,
                "newest_timestamp": newest_row,
                "max_history_rows": self._max_history_rows,
                "retention_days": self._retention_days,
                "rollup_age_days": self._rollup_age_days,
            }
        except Exception as exc:
            return {
                "error": str(exc),
                "max_history_rows": self._max_history_rows,
                "retention_days": self._retention_days,
                "rollup_age_days": self._rollup_age_days,
            }

    def run_rollup(self) -> dict:
        """Run daily rollup aggregation for older data.

        Sprint 5.27: Aggregates individual cycle rows that are older than
        ``rollup_age_days`` into daily summary rows.  This reduces the
        table size while preserving long-term trends.

        The rollup process:
        1. Finds all non-rollup rows older than rollup_age_days
        2. Groups them by day (UTC date)
        3. Computes averages for rate columns and sums for count columns
        4. Inserts a single rollup row per day
        5. Deletes the original rows that were rolled up

        Returns a dict with rollup statistics.
        """
        if not self._initialized:
            self.initialize()

        cutoff = (datetime.now(timezone.utc) - timedelta(days=self._rollup_age_days)).isoformat()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")

                # Find eligible non-rollup rows grouped by day
                cursor = conn.execute("""
                    SELECT
                        DATE(cycle_timestamp) as day,
                        COUNT(*) as cnt,
                        AVG(avg_citation_rate) as avg_citation,
                        AVG(avg_grounding_rate) as avg_grounding,
                        AVG(avg_llm_faithfulness) as avg_faithfulness,
                        SUM(evaluated_count) as sum_evaluated,
                        SUM(flagged_count) as sum_flagged,
                        SUM(hedging_detected_count) as sum_hedging,
                        SUM(llm_eval_count) as sum_llm_eval,
                        SUM(llm_hallucinations) as sum_hallucinations,
                        SUM(cycle_elapsed_seconds) as sum_elapsed
                    FROM vigil_quality_history
                    WHERE is_rollup = 0
                      AND cycle_timestamp < ?
                    GROUP BY DATE(cycle_timestamp)
                    HAVING cnt > 1
                    ORDER BY day ASC
                """, (cutoff,))

                rows = cursor.fetchall()

                if not rows:
                    return {"rolled_up_days": 0, "rows_aggregated": 0, "rows_deleted": 0}

                total_aggregated = 0
                total_deleted = 0
                days_rolled = 0

                for row in rows:
                    day, cnt, avg_citation, avg_grounding, avg_faithfulness, \
                        sum_evaluated, sum_flagged, sum_hedging, sum_llm_eval, \
                        sum_hallucinations, sum_elapsed = row

                    # Use noon UTC on that day as the rollup timestamp
                    rollup_ts = f"{day}T12:00:00Z"

                    # Insert rollup row
                    conn.execute("""
                        INSERT INTO vigil_quality_history (
                            cycle_timestamp, avg_citation_rate, avg_grounding_rate,
                            avg_llm_faithfulness, evaluated_count, flagged_count,
                            hedging_detected_count, llm_eval_count, llm_hallucinations,
                            cycle_elapsed_seconds, trend_indicators, cycle_report,
                            is_rollup, rollup_period, rollup_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        rollup_ts,
                        avg_citation,
                        avg_grounding,
                        avg_faithfulness,
                        sum_evaluated,
                        sum_flagged,
                        sum_hedging,
                        sum_llm_eval,
                        sum_hallucinations,
                        sum_elapsed,
                        json.dumps({"rollup_source": "daily", "original_day": day}),
                        json.dumps({"rollup": True, "period": "daily", "aggregated_count": cnt}),
                        1,  # is_rollup
                        "daily",
                        cnt,
                    ))

                    # Delete original rows for this day
                    delete_cursor = conn.execute("""
                        DELETE FROM vigil_quality_history
                        WHERE is_rollup = 0
                          AND DATE(cycle_timestamp) = ?
                    """, (day,))
                    total_deleted += delete_cursor.rowcount
                    total_aggregated += cnt
                    days_rolled += 1

                logger.info(
                    "vigil_quality_store_rollup",
                    rolled_up_days=days_rolled,
                    rows_aggregated=total_aggregated,
                    rows_deleted=total_deleted,
                )

                return {
                    "rolled_up_days": days_rolled,
                    "rows_aggregated": total_aggregated,
                    "rows_deleted": total_deleted,
                }

        except Exception as exc:
            logger.warning(
                "vigil_quality_store_rollup_failed",
                error=str(exc),
            )
            return {"error": str(exc), "rolled_up_days": 0}

    def _prune_if_needed(self) -> None:
        """Prune old records based on retention policy.

        Sprint 5.27: Two pruning strategies:
        1. Time-based: Delete rows older than retention_days (if configured)
        2. Count-based: Delete oldest rows if total exceeds max_history_rows
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                # Strategy 1: Time-based retention
                if self._retention_days > 0:
                    cutoff = (
                        datetime.now(timezone.utc) - timedelta(days=self._retention_days)
                    ).isoformat()
                    cursor = conn.execute("""
                        DELETE FROM vigil_quality_history
                        WHERE cycle_timestamp < ?
                    """, (cutoff,))
                    if cursor.rowcount > 0:
                        logger.info(
                            "vigil_quality_store_retention_prune",
                            pruned_rows=cursor.rowcount,
                            retention_days=self._retention_days,
                        )

                # Strategy 2: Count-based pruning
                cursor = conn.execute("SELECT COUNT(*) FROM vigil_quality_history")
                count = cursor.fetchone()[0]

                if count > self._max_history_rows:
                    # Delete oldest rows, keeping the newest max_history_rows
                    conn.execute("""
                        DELETE FROM vigil_quality_history
                        WHERE id NOT IN (
                            SELECT id FROM vigil_quality_history
                            ORDER BY cycle_timestamp DESC
                            LIMIT ?
                        )
                    """, (self._max_history_rows,))
                    pruned = count - self._max_history_rows
                    logger.info(
                        "vigil_quality_store_pruned",
                        pruned_rows=pruned,
                        remaining=self._max_history_rows,
                    )
        except Exception as exc:
            logger.warning(
                "vigil_quality_store_prune_failed",
                error=str(exc),
            )
