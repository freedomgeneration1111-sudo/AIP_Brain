"""Vigil quality history persistence — SQLite-backed cycle report storage.

Sprint 5.26: Persists Vigil's per-cycle quality reports to SQLite so that
quality data survives process restarts and supports longer time-range
queries from the ``/vigil/quality`` endpoint.

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

Design:
    - Lightweight SQLite table — no ORM, stdlib sqlite3
    - Auto-creates table on first use (no separate migration script needed)
    - Graceful degradation: if DB operations fail, Vigil falls back to
      in-memory history (Sprint 5.24 behavior)
    - Query methods support both ``last_n_cycles`` and ``since`` filtering
    - Maximum practical history: 10000 rows (auto-pruned on insert)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aip.logging import get_logger

logger = get_logger(__name__)

# Maximum number of history rows to retain (auto-prune on insert)
_MAX_HISTORY_ROWS = 10000

# Schema version for future migrations
_SCHEMA_VERSION = 1


class VigilQualityStore:
    """SQLite-backed persistence for Vigil quality cycle reports.

    Usage::

        store = VigilQualityStore(db_path="db/vigil_quality.db")
        store.initialize()

        # Record a cycle
        store.record_cycle({
            "timestamp": "2025-06-01T12:00:00Z",
            "avg_citation_rate": 0.85,
            "avg_grounding_rate": 0.90,
            "avg_llm_faithfulness": 0.88,
            "evaluated_count": 15,
            "flagged_count": 2,
            ...
        })

        # Query history
        cycles = store.get_cycles(last_n_cycles=50)
        cycles = store.get_cycles(since="2025-05-01T00:00:00Z")
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._initialized = False

    def initialize(self) -> None:
        """Create the quality history table if it doesn't exist.

        Safe to call multiple times — uses IF NOT EXISTS.
        """
        if self._initialized:
            return

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
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
                        cycle_report TEXT NOT NULL DEFAULT '{}'
                    )
                """)
                # Create index on timestamp for efficient range queries
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_vigil_quality_timestamp
                    ON vigil_quality_history (cycle_timestamp)
                """)
                # Schema metadata table for future migrations
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS vigil_quality_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
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

            # Auto-prune old records
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

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            query = f"""
                SELECT cycle_timestamp, avg_citation_rate, avg_grounding_rate,
                       avg_llm_faithfulness, evaluated_count, flagged_count,
                       hedging_detected_count, llm_eval_count, llm_hallucinations,
                       cycle_elapsed_seconds, trend_indicators, cycle_report
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

    def _prune_if_needed(self) -> None:
        """Prune old records if the table exceeds the maximum row count."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM vigil_quality_history")
                count = cursor.fetchone()[0]

                if count > _MAX_HISTORY_ROWS:
                    # Delete oldest rows, keeping the newest _MAX_HISTORY_ROWS
                    conn.execute("""
                        DELETE FROM vigil_quality_history
                        WHERE id NOT IN (
                            SELECT id FROM vigil_quality_history
                            ORDER BY cycle_timestamp DESC
                            LIMIT ?
                        )
                    """, (_MAX_HISTORY_ROWS,))
                    pruned = count - _MAX_HISTORY_ROWS
                    logger.info(
                        "vigil_quality_store_pruned",
                        pruned_rows=pruned,
                        remaining=_MAX_HISTORY_ROWS,
                    )
        except Exception as exc:
            logger.warning(
                "vigil_quality_store_prune_failed",
                error=str(exc),
            )
