"""Persistent alert history store — SQLite-backed alert and delivery failure storage.

Sprint 5.29: Provides durable alert history that survives process restarts.
Mirrors the in-memory history pattern from AlertManager but persists to SQLite
so that historical alerts and delivery failures remain queryable across restarts.

Schema:
    alert_history
        id              INTEGER PRIMARY KEY AUTOINCREMENT
        alert_type      TEXT NOT NULL           -- quality_degradation, pool_adjustment, batch_reduction
        severity        TEXT NOT NULL           -- info, warning, critical
        subject         TEXT NOT NULL
        message         TEXT NOT NULL
        data            TEXT NOT NULL DEFAULT '{}'  -- JSON
        timestamp       TEXT NOT NULL           -- ISO 8601 UTC
        created_at      TEXT NOT NULL           -- Row insertion time

    alert_delivery_failures
        id              INTEGER PRIMARY KEY AUTOINCREMENT
        transport       TEXT NOT NULL           -- webhook, email
        alert_type      TEXT NOT NULL
        subject         TEXT NOT NULL
        error_message   TEXT NOT NULL
        timestamp       TEXT NOT NULL           -- ISO 8601 UTC
        retry_attempt   INTEGER NOT NULL DEFAULT 0
        final           INTEGER NOT NULL DEFAULT 1
        created_at      TEXT NOT NULL           -- Row insertion time

Design:
    - Lightweight SQLite table — no ORM, stdlib sqlite3
    - Auto-creates tables on first use
    - Graceful degradation: if DB operations fail, AlertManager falls back
      to in-memory history (existing Sprint 5.25 behavior)
    - Maximum practical history: configurable (default 5000 rows, auto-pruned)
    - WAL mode for concurrent read/write
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aip.logging import get_logger

logger = get_logger(__name__)

# Default maximum number of alert history rows to retain
_DEFAULT_MAX_ALERT_ROWS = 5000

# Default maximum number of delivery failure rows to retain
_DEFAULT_MAX_FAILURE_ROWS = 1000

# Schema version for migrations
_SCHEMA_VERSION = 1


class AlertHistoryStore:
    """SQLite-backed persistence for alert history and delivery failures.

    Sprint 5.29: Provides durable storage so that alert history survives
    process restarts. When an AlertManager is paired with this store,
    all sent alerts and delivery failures are written to SQLite and
    can be queried at any time, even after a restart.

    Usage::

        store = AlertHistoryStore(db_path="db/alert_history.db")
        store.initialize()

        # Record an alert
        store.record_alert({
            "alert_type": "batch_reduction",
            "severity": "warning",
            "subject": "graph_extraction",
            "message": "Batch size reduced",
            "data": {"old": 4, "new": 3},
            "timestamp": "2025-06-01T12:00:00Z",
        })

        # Query history
        alerts = store.get_alert_history(alert_type="batch_reduction", limit=50)
    """

    def __init__(
        self,
        db_path: str | Path,
        max_alert_rows: int = _DEFAULT_MAX_ALERT_ROWS,
        max_failure_rows: int = _DEFAULT_MAX_FAILURE_ROWS,
    ) -> None:
        self._db_path = str(db_path)
        self._max_alert_rows = max_alert_rows
        self._max_failure_rows = max_failure_rows
        self._initialized = False

    def initialize(self) -> None:
        """Create the alert history tables if they don't exist.

        Safe to call multiple times — uses IF NOT EXISTS.
        """
        if self._initialized:
            return

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")

                # Schema metadata table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS alert_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)

                # Alert history table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS alert_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        alert_type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        message TEXT NOT NULL,
                        data TEXT NOT NULL DEFAULT '{}',
                        timestamp TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)

                # Indexes for common queries
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_alert_history_type
                    ON alert_history (alert_type)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_alert_history_severity
                    ON alert_history (severity)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_alert_history_timestamp
                    ON alert_history (timestamp)
                """)

                # Delivery failures table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS alert_delivery_failures (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        transport TEXT NOT NULL,
                        alert_type TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        error_message TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        retry_attempt INTEGER NOT NULL DEFAULT 0,
                        final INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL
                    )
                """)

                # Indexes for delivery failures
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_alert_failures_transport
                    ON alert_delivery_failures (transport)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_alert_failures_timestamp
                    ON alert_delivery_failures (timestamp)
                """)

                # Record schema version
                conn.execute("""
                    INSERT OR REPLACE INTO alert_meta (key, value)
                    VALUES ('schema_version', ?)
                """, (str(_SCHEMA_VERSION),))

            self._initialized = True
            logger.info(
                "alert_history_store_initialized",
                db_path=self._db_path,
                schema_version=_SCHEMA_VERSION,
                max_alert_rows=self._max_alert_rows,
                max_failure_rows=self._max_failure_rows,
            )
        except Exception as exc:
            logger.warning(
                "alert_history_store_init_failed",
                db_path=self._db_path,
                error=str(exc),
            )

    def record_alert(self, alert_dict: dict) -> bool:
        """Record a sent alert to persistent storage.

        Parameters
        ----------
        alert_dict:
            Dict with alert fields: alert_type, severity, subject,
            message, data (optional), timestamp.

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            ts = alert_dict.get("timestamp", now)
            data_json = json.dumps(alert_dict.get("data", {}), default=str)

            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT INTO alert_history (alert_type, severity, subject, message, data, timestamp, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    alert_dict.get("alert_type", ""),
                    alert_dict.get("severity", ""),
                    alert_dict.get("subject", ""),
                    alert_dict.get("message", ""),
                    data_json,
                    ts,
                    now,
                ))

            # Auto-prune if needed
            self._prune_alerts_if_needed()
            return True
        except Exception as exc:
            logger.warning(
                "alert_history_store_record_failed",
                error=str(exc),
            )
            return False

    def record_delivery_failure(self, failure_dict: dict) -> bool:
        """Record a delivery failure to persistent storage.

        Parameters
        ----------
        failure_dict:
            Dict with failure fields: transport, alert_type, subject,
            error_message, timestamp (optional), retry_attempt (optional),
            final (optional).

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            ts = failure_dict.get("timestamp", now)

            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT INTO alert_delivery_failures (
                        transport, alert_type, subject, error_message,
                        timestamp, retry_attempt, final, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    failure_dict.get("transport", ""),
                    failure_dict.get("alert_type", ""),
                    failure_dict.get("subject", ""),
                    failure_dict.get("error_message", ""),
                    ts,
                    failure_dict.get("retry_attempt", 0),
                    1 if failure_dict.get("final", True) else 0,
                    now,
                ))

            # Auto-prune if needed
            self._prune_failures_if_needed()
            return True
        except Exception as exc:
            logger.warning(
                "alert_history_store_failure_record_failed",
                error=str(exc),
            )
            return False

    def get_alert_history(
        self,
        alert_type: str | None = None,
        severity: str | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return alert history with optional filtering.

        Parameters
        ----------
        alert_type:
            If provided, return only alerts of this type.
        severity:
            If provided, return only alerts with this severity.
        since:
            ISO 8601 datetime — only return alerts with timestamps after this.
        limit:
            Maximum number of alerts to return (most recent first).

        Returns a list of alert dicts, most recent first.
        """
        if not self._initialized:
            self.initialize()

        try:
            conditions: list[str] = []
            params: list[Any] = []

            if alert_type:
                conditions.append("alert_type = ?")
                params.append(alert_type)
            if severity:
                conditions.append("severity = ?")
                params.append(severity)
            if since:
                conditions.append("timestamp > ?")
                params.append(since)

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            query = f"""
                SELECT id, alert_type, severity, subject, message, data, timestamp
                FROM alert_history
                {where_clause}
                ORDER BY timestamp DESC
                LIMIT ?
            """
            params.append(limit)

            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()

            result = []
            for row in rows:
                try:
                    data = json.loads(row["data"]) if row["data"] else {}
                except (json.JSONDecodeError, TypeError):
                    data = {}

                result.append({
                    "id": row["id"],
                    "alert_type": row["alert_type"],
                    "severity": row["severity"],
                    "subject": row["subject"],
                    "message": row["message"],
                    "data": data,
                    "timestamp": row["timestamp"],
                })

            return result
        except Exception as exc:
            logger.warning(
                "alert_history_store_query_failed",
                error=str(exc),
            )
            return []

    def get_delivery_failures(
        self,
        transport: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Return delivery failure history, optionally filtered by transport.

        Parameters
        ----------
        transport:
            If provided, return only failures for this transport.
        limit:
            Maximum number of failures to return (most recent first).

        Returns a list of failure dicts, most recent first.
        """
        if not self._initialized:
            self.initialize()

        try:
            conditions: list[str] = []
            params: list[Any] = []

            if transport:
                conditions.append("transport = ?")
                params.append(transport)

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            query = f"""
                SELECT id, transport, alert_type, subject, error_message,
                       timestamp, retry_attempt, final
                FROM alert_delivery_failures
                {where_clause}
                ORDER BY timestamp DESC
                LIMIT ?
            """
            params.append(limit)

            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()

            result = []
            for row in rows:
                result.append({
                    "id": row["id"],
                    "transport": row["transport"],
                    "alert_type": row["alert_type"],
                    "subject": row["subject"],
                    "error_message": row["error_message"],
                    "timestamp": row["timestamp"],
                    "retry_attempt": row["retry_attempt"],
                    "final": bool(row["final"]),
                })

            return result
        except Exception as exc:
            logger.warning(
                "alert_history_store_failures_query_failed",
                error=str(exc),
            )
            return []

    def get_alert_count(self) -> int:
        """Return the total number of stored alert history rows."""
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM alert_history")
                return cursor.fetchone()[0]
        except Exception:
            return 0

    def get_failure_count(self) -> int:
        """Return the total number of stored delivery failure rows."""
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM alert_delivery_failures")
                return cursor.fetchone()[0]
        except Exception:
            return 0

    def get_status(self) -> dict:
        """Return status information about the alert history store."""
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                alert_count = conn.execute("SELECT COUNT(*) FROM alert_history").fetchone()[0]
                failure_count = conn.execute("SELECT COUNT(*) FROM alert_delivery_failures").fetchone()[0]

                oldest_alert = None
                newest_alert = None
                if alert_count > 0:
                    row = conn.execute(
                        "SELECT MIN(timestamp) as oldest, MAX(timestamp) as newest FROM alert_history"
                    ).fetchone()
                    oldest_alert = row[0]
                    newest_alert = row[1]

            return {
                "initialized": self._initialized,
                "db_path": self._db_path,
                "total_alerts": alert_count,
                "total_delivery_failures": failure_count,
                "oldest_alert_timestamp": oldest_alert,
                "newest_alert_timestamp": newest_alert,
                "max_alert_rows": self._max_alert_rows,
                "max_failure_rows": self._max_failure_rows,
            }
        except Exception as exc:
            return {
                "initialized": self._initialized,
                "db_path": self._db_path,
                "error": str(exc),
            }

    def _prune_alerts_if_needed(self) -> None:
        """Prune old alert history rows if exceeding max_alert_rows."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM alert_history").fetchone()[0]
                if count > self._max_alert_rows:
                    conn.execute("""
                        DELETE FROM alert_history
                        WHERE id NOT IN (
                            SELECT id FROM alert_history
                            ORDER BY timestamp DESC
                            LIMIT ?
                        )
                    """, (self._max_alert_rows,))
        except Exception as exc:
            logger.warning("alert_history_store_prune_failed", error=str(exc))

    def _prune_failures_if_needed(self) -> None:
        """Prune old delivery failure rows if exceeding max_failure_rows."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM alert_delivery_failures").fetchone()[0]
                if count > self._max_failure_rows:
                    conn.execute("""
                        DELETE FROM alert_delivery_failures
                        WHERE id NOT IN (
                            SELECT id FROM alert_delivery_failures
                            ORDER BY timestamp DESC
                            LIMIT ?
                        )
                    """, (self._max_failure_rows,))
        except Exception as exc:
            logger.warning("alert_failure_store_prune_failed", error=str(exc))
