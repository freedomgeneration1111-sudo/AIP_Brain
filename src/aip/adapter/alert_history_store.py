"""Persistent alert history store — SQLite-backed alert and delivery failure storage.

Sprint 5.29: Provides durable alert history that survives process restarts.
Mirrors the in-memory history pattern from AlertManager but persists to SQLite
so that historical alerts and delivery failures remain queryable across restarts.

Sprint 5.30: Added alert acknowledgment/dismissal support with schema v2.
Alerts can be acknowledged or dismissed via API, and the status persists
across restarts. Added correlation_id for async dispatch tracking.

Sprint 5.31: Added mute rules table for persistent alert silencing.
Added delivery status tracking table for per-correlation-ID transport outcomes.
Schema v3 adds alert_mute_rules and alert_delivery_status tables.

Sprint 5.32: Added update_delivery_status() for updating records on completion.
Added get_recent_delivery_statuses() for rebuilding in-memory state on restart.
Added get_alert_by_correlation_id() for bulk group operations.
Schema v4 adds retry_count and group_key columns to delivery status.

Schema:
    alert_history
        id              INTEGER PRIMARY KEY AUTOINCREMENT
        alert_type      TEXT NOT NULL
        severity        TEXT NOT NULL
        subject         TEXT NOT NULL
        message         TEXT NOT NULL
        data            TEXT NOT NULL DEFAULT '{}'  -- JSON
        timestamp       TEXT NOT NULL           -- ISO 8601 UTC
        created_at      TEXT NOT NULL           -- Row insertion time
        correlation_id  TEXT NOT NULL DEFAULT ''  -- Sprint 5.30: correlation ID
        acknowledged    INTEGER NOT NULL DEFAULT 0  -- Sprint 5.30: 0=open, 1=acknowledged, 2=dismissed
        acknowledged_at TEXT NOT NULL DEFAULT ''     -- Sprint 5.30
        acknowledged_by TEXT NOT NULL DEFAULT ''     -- Sprint 5.30

    alert_delivery_failures
        id              INTEGER PRIMARY KEY AUTOINCREMENT
        transport       TEXT NOT NULL
        alert_type      TEXT NOT NULL
        subject         TEXT NOT NULL
        error_message   TEXT NOT NULL
        timestamp       TEXT NOT NULL
        retry_attempt   INTEGER NOT NULL DEFAULT 0
        final           INTEGER NOT NULL DEFAULT 1
        created_at      TEXT NOT NULL

    alert_mute_rules (Sprint 5.31)
        id              INTEGER PRIMARY KEY AUTOINCREMENT
        alert_type      TEXT NOT NULL
        subject         TEXT NOT NULL
        muted_at        TEXT NOT NULL
        muted_by        TEXT NOT NULL DEFAULT 'operator'
        duration_seconds INTEGER NOT NULL DEFAULT 3600
        expires_at      REAL NOT NULL DEFAULT 0     -- epoch float, 0=indefinite
        auto_mute_on_ack INTEGER NOT NULL DEFAULT 0
        created_at      TEXT NOT NULL

    alert_delivery_status (Sprint 5.31)
        id              INTEGER PRIMARY KEY AUTOINCREMENT
        correlation_id  TEXT NOT NULL
        status          TEXT NOT NULL           -- dispatching, delivered, partial, failed, buffered_for_digest
        alert_type      TEXT NOT NULL
        severity        TEXT NOT NULL
        subject         TEXT NOT NULL
        transports      TEXT NOT NULL DEFAULT '[]'  -- JSON list
        transport_results TEXT NOT NULL DEFAULT '{}' -- JSON dict
        dispatched_at   TEXT NOT NULL
        completed_at    TEXT NOT NULL DEFAULT ''
        created_at      TEXT NOT NULL

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

# Default maximum number of delivery status rows to retain (Sprint 5.33)
_DEFAULT_MAX_DELIVERY_STATUS_ROWS = 2000

# Schema version for migrations (Sprint 5.39: v7 adds model_retraining_events table)
_SCHEMA_VERSION = 7


class AlertHistoryStore:
    """SQLite-backed persistence for alert history and delivery failures.

    Sprint 5.29: Provides durable storage so that alert history survives
    process restarts. When an AlertManager is paired with this store,
    all sent alerts and delivery failures are written to SQLite and
    can be queried at any time, even after a restart.

    Sprint 5.30: Added acknowledgment/dismissal support and correlation ID
    tracking. Alerts can be acknowledged (operator has seen and accepted)
    or dismissed (operator has resolved the underlying issue). Both states
    persist across restarts and are visible in the dashboard and API.

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

        # Acknowledge an alert (Sprint 5.30)
        store.acknowledge_alert(alert_id=1, acknowledged_by="operator")
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

        Sprint 5.30: Runs schema migration from v1 to v2 if needed,
        adding correlation_id, acknowledged, acknowledged_at, and
        acknowledged_by columns.
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

                # Sprint 5.30: Schema migration v1 -> v2
                # Add correlation_id, acknowledged, acknowledged_at, acknowledged_by columns
                current_version = 0
                try:
                    cursor = conn.execute(
                        "SELECT value FROM alert_meta WHERE key = 'schema_version'"
                    )
                    row = cursor.fetchone()
                    if row:
                        current_version = int(row[0])
                except Exception:
                    pass

                if current_version < 2:
                    for col_spec in [
                        "ALTER TABLE alert_history ADD COLUMN correlation_id TEXT NOT NULL DEFAULT ''",
                        "ALTER TABLE alert_history ADD COLUMN acknowledged INTEGER NOT NULL DEFAULT 0",
                        "ALTER TABLE alert_history ADD COLUMN acknowledged_at TEXT NOT NULL DEFAULT ''",
                        "ALTER TABLE alert_history ADD COLUMN acknowledged_by TEXT NOT NULL DEFAULT ''",
                    ]:
                        try:
                            conn.execute(col_spec)
                        except sqlite3.OperationalError:
                            pass  # Column already exists

                # Index for acknowledged queries
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_alert_history_acknowledged
                    ON alert_history (acknowledged)
                """)

                # Index for correlation_id lookups
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_alert_history_correlation_id
                    ON alert_history (correlation_id)
                """)

                # Record schema version
                conn.execute("""
                    INSERT OR REPLACE INTO alert_meta (key, value)
                    VALUES ('schema_version', ?)
                """, (str(_SCHEMA_VERSION),))

                # Sprint 5.31: Schema migration v2 -> v3
                # Add alert_mute_rules and alert_delivery_status tables
                if current_version < 3:
                    # Mute rules table
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS alert_mute_rules (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            alert_type TEXT NOT NULL,
                            subject TEXT NOT NULL,
                            muted_at TEXT NOT NULL,
                            muted_by TEXT NOT NULL DEFAULT 'operator',
                            duration_seconds INTEGER NOT NULL DEFAULT 3600,
                            expires_at REAL NOT NULL DEFAULT 0,
                            auto_mute_on_ack INTEGER NOT NULL DEFAULT 0,
                            created_at TEXT NOT NULL
                        )
                    """)

                    # Index for mute rule lookups
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_mute_rules_type_subject
                        ON alert_mute_rules (alert_type, subject)
                    """)

                    # Delivery status tracking table
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS alert_delivery_status (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            correlation_id TEXT NOT NULL,
                            status TEXT NOT NULL,
                            alert_type TEXT NOT NULL,
                            severity TEXT NOT NULL,
                            subject TEXT NOT NULL,
                            transports TEXT NOT NULL DEFAULT '[]',
                            transport_results TEXT NOT NULL DEFAULT '{}',
                            dispatched_at TEXT NOT NULL,
                            completed_at TEXT NOT NULL DEFAULT '',
                            created_at TEXT NOT NULL
                        )
                    """)

                    # Index for correlation_id lookups
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_delivery_status_correlation_id
                        ON alert_delivery_status (correlation_id)
                    """)

                # Sprint 5.33: Schema migration v4 -> v5
                # Add alert_groups table for persistent alert grouping
                if current_version < 5:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS alert_groups (
                            group_key TEXT NOT NULL,
                            correlation_id TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            PRIMARY KEY (group_key, correlation_id)
                        )
                    """)

                    # Index for group_key lookups
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_alert_groups_group_key
                        ON alert_groups (group_key)
                    """)

                # Sprint 5.38: Schema migration v5 -> v6
                # Add transition_probabilities table for learned prediction model persistence
                if current_version < 6:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS transition_probabilities (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            from_type TEXT NOT NULL,
                            to_type TEXT NOT NULL,
                            count INTEGER NOT NULL DEFAULT 0,
                            total_from INTEGER NOT NULL DEFAULT 0,
                            updated_at TEXT NOT NULL,
                            UNIQUE(from_type, to_type)
                        )
                    """)

                    # Index for from_type lookups
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_transition_from_type
                        ON transition_probabilities (from_type)
                    """)

                    # Add delivery_receipts table for multi-channel receipt tracking
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS delivery_receipts (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            correlation_id TEXT NOT NULL,
                            channel TEXT NOT NULL,
                            receipt_data TEXT NOT NULL DEFAULT '{}',
                            confirmed_at TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        )
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_delivery_receipts_cid
                        ON delivery_receipts (correlation_id)
                    """)

                # Sprint 5.39: Schema migration v6 -> v7
                # Add model_retraining_events table for tracking model retraining
                if current_version < 7:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS model_retraining_events (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            trigger_reason TEXT NOT NULL,
                            alerts_since_last_train INTEGER NOT NULL DEFAULT 0,
                            transition_count INTEGER NOT NULL DEFAULT 0,
                            total_types INTEGER NOT NULL DEFAULT 0,
                            trained_at TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        )
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_retraining_events_trained_at
                        ON model_retraining_events (trained_at)
                    """)

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
            message, data (optional), timestamp, correlation_id (optional).

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            ts = alert_dict.get("timestamp", now)
            data_json = json.dumps(alert_dict.get("data", {}), default=str)
            correlation_id = alert_dict.get("correlation_id", "")

            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT INTO alert_history (
                        alert_type, severity, subject, message, data,
                        timestamp, created_at, correlation_id,
                        acknowledged, acknowledged_at, acknowledged_by
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, '', '')
                """, (
                    alert_dict.get("alert_type", ""),
                    alert_dict.get("severity", ""),
                    alert_dict.get("subject", ""),
                    alert_dict.get("message", ""),
                    data_json,
                    ts,
                    now,
                    correlation_id,
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
                SELECT id, alert_type, severity, subject, message, data, timestamp,
                       correlation_id, acknowledged, acknowledged_at, acknowledged_by
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

                alert_dict = {
                    "id": row["id"],
                    "alert_type": row["alert_type"],
                    "severity": row["severity"],
                    "subject": row["subject"],
                    "message": row["message"],
                    "data": data,
                    "timestamp": row["timestamp"],
                }

                # Sprint 5.30: Include acknowledgment fields if available
                try:
                    alert_dict["correlation_id"] = row["correlation_id"] if "correlation_id" in row.keys() else ""
                    alert_dict["acknowledged"] = row["acknowledged"] if "acknowledged" in row.keys() else 0
                    alert_dict["acknowledged_at"] = row["acknowledged_at"] if "acknowledged_at" in row.keys() else ""
                    alert_dict["acknowledged_by"] = row["acknowledged_by"] if "acknowledged_by" in row.keys() else ""
                except Exception:
                    alert_dict["correlation_id"] = ""
                    alert_dict["acknowledged"] = 0
                    alert_dict["acknowledged_at"] = ""
                    alert_dict["acknowledged_by"] = ""

                result.append(alert_dict)

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

    # -----------------------------------------------------------------------
    # Sprint 5.30: Acknowledgment / Dismissal
    # -----------------------------------------------------------------------

    def acknowledge_alert(self, alert_id: int, acknowledged_by: str = "operator") -> bool:
        """Mark an alert as acknowledged.

        Sprint 5.30: Sets the acknowledged status to 1 (acknowledged),
        records who acknowledged it and when.

        Parameters
        ----------
        alert_id:
            The database ID of the alert to acknowledge.
        acknowledged_by:
            Identifier for who acknowledged the alert (default "operator").

        Returns True if the alert was found and updated, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute("""
                    UPDATE alert_history
                    SET acknowledged = 1, acknowledged_at = ?, acknowledged_by = ?
                    WHERE id = ?
                """, (now, acknowledged_by, alert_id))
                if cursor.rowcount == 0:
                    return False

            logger.info(
                "alert_acknowledged",
                alert_id=alert_id,
                acknowledged_by=acknowledged_by,
            )
            return True
        except Exception as exc:
            logger.warning(
                "alert_acknowledge_failed",
                alert_id=alert_id,
                error=str(exc),
            )
            return False

    def dismiss_alert(self, alert_id: int, dismissed_by: str = "operator") -> bool:
        """Mark an alert as dismissed.

        Sprint 5.30: Sets the acknowledged status to 2 (dismissed),
        indicating the operator has resolved the underlying issue or
        explicitly dismissed the alert. Records who dismissed it and when.

        Parameters
        ----------
        alert_id:
            The database ID of the alert to dismiss.
        dismissed_by:
            Identifier for who dismissed the alert (default "operator").

        Returns True if the alert was found and updated, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute("""
                    UPDATE alert_history
                    SET acknowledged = 2, acknowledged_at = ?, acknowledged_by = ?
                    WHERE id = ?
                """, (now, dismissed_by, alert_id))
                if cursor.rowcount == 0:
                    return False

            logger.info(
                "alert_dismissed",
                alert_id=alert_id,
                dismissed_by=dismissed_by,
            )
            return True
        except Exception as exc:
            logger.warning(
                "alert_dismiss_failed",
                alert_id=alert_id,
                error=str(exc),
            )
            return False

    def get_alert_by_id(self, alert_id: int) -> dict | None:
        """Return a single alert by its database ID.

        Sprint 5.30: Used by the acknowledge/dismiss endpoints to verify
        that an alert exists before updating its status.

        Parameters
        ----------
        alert_id:
            The database ID of the alert.

        Returns the alert dict or None if not found.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT id, alert_type, severity, subject, message, data, timestamp,
                           correlation_id, acknowledged, acknowledged_at, acknowledged_by
                    FROM alert_history
                    WHERE id = ?
                """, (alert_id,))
                row = cursor.fetchone()

            if row is None:
                return None

            try:
                data = json.loads(row["data"]) if row["data"] else {}
            except (json.JSONDecodeError, TypeError):
                data = {}

            return {
                "id": row["id"],
                "alert_type": row["alert_type"],
                "severity": row["severity"],
                "subject": row["subject"],
                "message": row["message"],
                "data": data,
                "timestamp": row["timestamp"],
                "correlation_id": row["correlation_id"],
                "acknowledged": row["acknowledged"],
                "acknowledged_at": row["acknowledged_at"],
                "acknowledged_by": row["acknowledged_by"],
            }
        except Exception as exc:
            logger.warning(
                "alert_get_by_id_failed",
                alert_id=alert_id,
                error=str(exc),
            )
            return None

    def get_recent_alerts_for_dedup(self, window_seconds: int) -> dict[tuple[str, str], float]:
        """Return recent alerts within the rate-limit window for deduplication.

        Sprint 5.30: Used by AlertManager to rebuild its in-memory
        rate-limiting state (_last_alert_time) from the persistent store
        after a process restart. This prevents duplicate alert storms
        immediately after restart.

        Parameters
        ----------
        window_seconds:
            The rate-limit window in seconds. Only alerts within this
            window are returned.

        Returns a dict mapping (alert_type, subject) -> timestamp (epoch float).
        """
        if not self._initialized:
            self.initialize()

        try:
            import time as _time
            cutoff = datetime.fromtimestamp(
                _time.time() - window_seconds, tz=timezone.utc
            ).isoformat()

            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT alert_type, subject, MAX(timestamp) as latest_ts
                    FROM alert_history
                    WHERE timestamp > ?
                    GROUP BY alert_type, subject
                """, (cutoff,))
                rows = cursor.fetchall()

            result: dict[tuple[str, str], float] = {}
            for row in rows:
                # Parse ISO 8601 timestamp back to epoch float
                try:
                    ts_str = row["latest_ts"]
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    epoch = dt.timestamp()
                    result[(row["alert_type"], row["subject"])] = epoch
                except (ValueError, TypeError):
                    continue

            return result
        except Exception as exc:
            logger.warning(
                "alert_history_store_dedup_query_failed",
                error=str(exc),
            )
            return {}

    # -----------------------------------------------------------------------
    # Sprint 5.31: Mute rules
    # -----------------------------------------------------------------------

    def record_mute_rule(self, rule: dict) -> int:
        """Record a mute rule to persistent storage.

        Sprint 5.31: Persists mute rules so they survive process restarts.

        Returns the database ID of the created rule.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute("""
                    INSERT INTO alert_mute_rules (
                        alert_type, subject, muted_at, muted_by,
                        duration_seconds, expires_at, auto_mute_on_ack, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rule.get("alert_type", ""),
                    rule.get("subject", ""),
                    rule.get("muted_at", now),
                    rule.get("muted_by", "operator"),
                    rule.get("duration_seconds", 3600),
                    rule.get("expires_at", 0),
                    1 if rule.get("auto_mute_on_ack", False) else 0,
                    now,
                ))
                rule_id = cursor.lastrowid

            logger.info(
                "alert_mute_rule_recorded",
                rule_id=rule_id,
                alert_type=rule.get("alert_type", ""),
                subject=rule.get("subject", ""),
            )
            return rule_id or 0
        except Exception as exc:
            logger.warning(
                "alert_mute_rule_record_failed",
                error=str(exc),
            )
            return 0

    def delete_mute_rule(self, alert_type: str, subject: str) -> bool:
        """Delete a mute rule by alert_type and subject.

        Sprint 5.31: Removes the mute rule from persistent storage.

        Returns True if a rule was deleted, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute("""
                    DELETE FROM alert_mute_rules
                    WHERE alert_type = ? AND subject = ?
                """, (alert_type, subject))
                return cursor.rowcount > 0
        except Exception as exc:
            logger.warning(
                "alert_mute_rule_delete_failed",
                error=str(exc),
            )
            return False

    def get_active_mute_rules(self) -> list[dict]:
        """Return all mute rules (active and possibly expired).

        Sprint 5.31: Used by AlertManager to rebuild in-memory mute
        rules on startup. Expired rules are filtered out by the
        AlertManager after reading.

        Returns a list of mute rule dicts.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT id, alert_type, subject, muted_at, muted_by,
                           duration_seconds, expires_at, auto_mute_on_ack
                    FROM alert_mute_rules
                    ORDER BY created_at DESC
                """)
                rows = cursor.fetchall()

            result = []
            for row in rows:
                result.append({
                    "id": row["id"],
                    "alert_type": row["alert_type"],
                    "subject": row["subject"],
                    "muted_at": row["muted_at"],
                    "muted_by": row["muted_by"],
                    "duration_seconds": row["duration_seconds"],
                    "expires_at": row["expires_at"],
                    "auto_mute_on_ack": bool(row["auto_mute_on_ack"]),
                })

            return result
        except Exception as exc:
            logger.warning(
                "alert_mute_rules_query_failed",
                error=str(exc),
            )
            return []

    # -----------------------------------------------------------------------
    # Sprint 5.31: Delivery status tracking
    # -----------------------------------------------------------------------

    def record_delivery_status(self, status_dict: dict) -> bool:
        """Record delivery status for a correlation ID.

        Sprint 5.31: Persists the per-transport delivery outcomes so
        operators can query the status of any alert by correlation ID,
        even after a process restart.

        Sprint 5.33: Auto-prunes delivery status after recording.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT INTO alert_delivery_status (
                        correlation_id, status, alert_type, severity, subject,
                        transports, transport_results, dispatched_at,
                        completed_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    status_dict.get("correlation_id", ""),
                    status_dict.get("status", ""),
                    status_dict.get("alert_type", ""),
                    status_dict.get("severity", ""),
                    status_dict.get("subject", ""),
                    json.dumps(status_dict.get("transports", [])),
                    json.dumps(status_dict.get("transport_results", {})),
                    status_dict.get("dispatched_at", now),
                    status_dict.get("completed_at", ""),
                    now,
                ))

            # Sprint 5.33: Auto-prune delivery status after recording
            self.prune_delivery_status()

            return True
        except Exception as exc:
            logger.warning(
                "alert_delivery_status_record_failed",
                error=str(exc),
            )
            return False

    def get_delivery_status_by_correlation_id(self, correlation_id: str) -> dict | None:
        """Return delivery status for a given correlation ID.

        Sprint 5.31: Queries the persistent store for delivery status
        information associated with a specific correlation ID.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT correlation_id, status, alert_type, severity, subject,
                           transports, transport_results, dispatched_at, completed_at
                    FROM alert_delivery_status
                    WHERE correlation_id = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (correlation_id,))
                row = cursor.fetchone()

            if row is None:
                return None

            return {
                "correlation_id": row["correlation_id"],
                "status": row["status"],
                "alert_type": row["alert_type"],
                "severity": row["severity"],
                "subject": row["subject"],
                "transports": json.loads(row["transports"]) if row["transports"] else [],
                "transport_results": json.loads(row["transport_results"]) if row["transport_results"] else {},
                "dispatched_at": row["dispatched_at"],
                "completed_at": row["completed_at"],
            }
        except Exception as exc:
            logger.warning(
                "alert_delivery_status_query_failed",
                correlation_id=correlation_id,
                error=str(exc),
            )
            return None

    # -----------------------------------------------------------------------
    # Sprint 5.32: Delivery status persistence enhancements
    # -----------------------------------------------------------------------

    def update_delivery_status(
        self,
        correlation_id: str,
        status: str,
        completed_at: str,
    ) -> bool:
        """Update an existing delivery status record.

        Sprint 5.32: Called when delivery completes to update the
        persistent record with the final status and completion time.
        This ensures delivery status survives process restarts.

        Returns True if the record was updated, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute("""
                    UPDATE alert_delivery_status
                    SET status = ?, completed_at = ?
                    WHERE correlation_id = ?
                """, (status, completed_at, correlation_id))
                return cursor.rowcount > 0
        except Exception as exc:
            logger.warning(
                "alert_delivery_status_update_failed",
                correlation_id=correlation_id,
                error=str(exc),
            )
            return False

    def get_recent_delivery_statuses(self, limit: int = 100) -> list[dict]:
        """Return recent delivery status records.

        Sprint 5.32: Used by AlertManager to rebuild its in-memory
        delivery status cache on startup, ensuring that delivery status
        is queryable after a restart.

        Returns a list of delivery status dicts, most recent first.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT correlation_id, status, alert_type, severity, subject,
                           transports, transport_results, dispatched_at, completed_at
                    FROM alert_delivery_status
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()

            result = []
            for row in rows:
                result.append({
                    "correlation_id": row["correlation_id"],
                    "status": row["status"],
                    "alert_type": row["alert_type"],
                    "severity": row["severity"],
                    "subject": row["subject"],
                    "transports": json.loads(row["transports"]) if row["transports"] else [],
                    "transport_results": json.loads(row["transport_results"]) if row["transport_results"] else {},
                    "dispatched_at": row["dispatched_at"],
                    "completed_at": row["completed_at"],
                })

            return result
        except Exception as exc:
            logger.warning(
                "alert_recent_delivery_statuses_query_failed",
                error=str(exc),
            )
            return []

    def get_alert_by_correlation_id(self, correlation_id: str) -> dict | None:
        """Return a single alert by its correlation ID.

        Sprint 5.32: Used by bulk acknowledge/dismiss operations to
        look up alerts by their correlation ID instead of database ID.

        Returns the alert dict or None if not found.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT id, alert_type, severity, subject, message, data, timestamp,
                           correlation_id, acknowledged, acknowledged_at, acknowledged_by
                    FROM alert_history
                    WHERE correlation_id = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (correlation_id,))
                row = cursor.fetchone()

            if row is None:
                return None

            try:
                data = json.loads(row["data"]) if row["data"] else {}
            except (json.JSONDecodeError, TypeError):
                data = {}

            return {
                "id": row["id"],
                "alert_type": row["alert_type"],
                "severity": row["severity"],
                "subject": row["subject"],
                "message": row["message"],
                "data": data,
                "timestamp": row["timestamp"],
                "correlation_id": row["correlation_id"],
                "acknowledged": row["acknowledged"],
                "acknowledged_at": row["acknowledged_at"],
                "acknowledged_by": row["acknowledged_by"],
            }
        except Exception as exc:
            logger.warning(
                "alert_get_by_correlation_id_failed",
                correlation_id=correlation_id,
                error=str(exc),
            )
            return None

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

    # -----------------------------------------------------------------------
    # Sprint 5.33: Alert group persistence
    # -----------------------------------------------------------------------

    def record_alert_group(self, group_key: str, correlation_id: str) -> bool:
        """Persist an alert group membership to SQLite.

        Sprint 5.33: Records that a correlation_id belongs to a group_key
        so alert groups survive process restarts.

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT OR IGNORE INTO alert_groups (group_key, correlation_id, created_at)
                    VALUES (?, ?, ?)
                """, (group_key, correlation_id, now))
            return True
        except Exception as exc:
            logger.warning(
                "alert_group_record_failed",
                group_key=group_key,
                correlation_id=correlation_id,
                error=str(exc),
            )
            return False

    def get_alert_groups(self) -> dict[str, list[str]]:
        """Load all alert groups from SQLite.

        Sprint 5.33: Rebuilds the in-memory _alert_groups dict from
        persistent storage on startup.

        Returns a dict mapping group_key -> list of correlation_ids.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT group_key, correlation_id
                    FROM alert_groups
                    ORDER BY created_at ASC
                """)
                rows = cursor.fetchall()

            result: dict[str, list[str]] = {}
            for row in rows:
                gk = row["group_key"]
                cid = row["correlation_id"]
                if gk not in result:
                    result[gk] = []
                result[gk].append(cid)

            return result
        except Exception as exc:
            logger.warning("alert_groups_load_failed", error=str(exc))
            return {}

    def delete_alert_group(self, group_key: str) -> bool:
        """Delete a group from persistent storage.

        Sprint 5.33: Used for cleanup when a group is removed.

        Returns True if any rows were deleted, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute("""
                    DELETE FROM alert_groups WHERE group_key = ?
                """, (group_key,))
                return cursor.rowcount > 0
        except Exception as exc:
            logger.warning(
                "alert_group_delete_failed",
                group_key=group_key,
                error=str(exc),
            )
            return False

    # -----------------------------------------------------------------------
    # Sprint 5.33: Delivery status auto-pruning
    # -----------------------------------------------------------------------

    def prune_delivery_status(
        self,
        max_rows: int = _DEFAULT_MAX_DELIVERY_STATUS_ROWS,
        max_age_days: int = 30,
    ) -> int:
        """Prune delivery status records by age and count.

        Sprint 5.33: Deletes records older than max_age_days days,
        then if still over max_rows, deletes oldest records keeping
        only max_rows.

        Returns the number of deleted records.
        """
        if not self._initialized:
            self.initialize()

        deleted = 0
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")

                # Delete records older than max_age_days
                if max_age_days > 0:
                    import time as _time
                    cutoff = datetime.fromtimestamp(
                        _time.time() - (max_age_days * 86400),
                        tz=timezone.utc,
                    ).isoformat()
                    cursor = conn.execute("""
                        DELETE FROM alert_delivery_status
                        WHERE created_at < ?
                    """, (cutoff,))
                    deleted += cursor.rowcount

                # If still over max_rows, delete oldest records
                count = conn.execute("SELECT COUNT(*) FROM alert_delivery_status").fetchone()[0]
                if count > max_rows:
                    cursor = conn.execute("""
                        DELETE FROM alert_delivery_status
                        WHERE id NOT IN (
                            SELECT id FROM alert_delivery_status
                            ORDER BY created_at DESC
                            LIMIT ?
                        )
                    """, (max_rows,))
                    deleted += cursor.rowcount

            if deleted > 0:
                logger.info(
                    "delivery_status_pruned",
                    deleted=deleted,
                    max_rows=max_rows,
                    max_age_days=max_age_days,
                )

            return deleted
        except Exception as exc:
            logger.warning("delivery_status_prune_failed", error=str(exc))
            return 0

    def get_delivery_status_count(self) -> int:
        """Return the total number of delivery status rows.

        Sprint 5.33: Used by the stats endpoint to report row counts.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM alert_delivery_status")
                return cursor.fetchone()[0]
        except Exception:
            return 0

    # -----------------------------------------------------------------------
    # Sprint 5.39: Transition probability persistence
    # -----------------------------------------------------------------------

    def save_transition_probabilities(
        self,
        transition_counts: dict[tuple[str, str], int],
        transition_totals: dict[str, int],
    ) -> bool:
        """Upsert transition probabilities to the transition_probabilities table.

        Sprint 5.39: Persists the learned transition probability model
        so it survives process restarts. Uses INSERT OR REPLACE to update
        existing entries.

        Parameters
        ----------
        transition_counts:
            Dict mapping (from_type, to_type) -> count of observed transitions.
        transition_totals:
            Dict mapping from_type -> total outgoing transitions.

        Returns True if successfully saved, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                for (from_type, to_type), count in transition_counts.items():
                    total_from = transition_totals.get(from_type, 0)
                    conn.execute("""
                        INSERT OR REPLACE INTO transition_probabilities
                            (from_type, to_type, count, total_from, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (from_type, to_type, count, total_from, now))

            logger.info(
                "transition_probabilities_saved",
                transition_pairs=len(transition_counts),
                total_types=len(transition_totals),
            )
            return True
        except Exception as exc:
            logger.warning(
                "transition_probabilities_save_failed",
                error=str(exc),
            )
            return False

    def load_transition_probabilities(
        self,
    ) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
        """Load all transition probabilities from the table.

        Sprint 5.39: Returns (transition_counts, transition_totals) for
        rebuilding the in-memory learned prediction model on startup.

        Returns
        -------
        transition_counts : dict[tuple[str, str], int]
            Dict mapping (from_type, to_type) -> count.
        transition_totals : dict[str, int]
            Dict mapping from_type -> total outgoing transitions.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT from_type, to_type, count, total_from
                    FROM transition_probabilities
                """)
                rows = cursor.fetchall()

            transition_counts: dict[tuple[str, str], int] = {}
            transition_totals: dict[str, int] = {}
            for row in rows:
                from_type = row["from_type"]
                to_type = row["to_type"]
                count = row["count"]
                total_from = row["total_from"]
                transition_counts[(from_type, to_type)] = count
                # Use the latest total_from for each from_type
                transition_totals[from_type] = max(
                    transition_totals.get(from_type, 0), total_from
                )

            logger.info(
                "transition_probabilities_loaded",
                transition_pairs=len(transition_counts),
                total_types=len(transition_totals),
            )
            return transition_counts, transition_totals
        except Exception as exc:
            logger.warning(
                "transition_probabilities_load_failed",
                error=str(exc),
            )
            return {}, {}

    def record_retraining_event(self, event: dict) -> bool:
        """Record when a model retraining occurred.

        Sprint 5.39: Persists a retraining event to the
        model_retraining_events table so that operators can see
        when and why the model was retrained.

        Parameters
        ----------
        event:
            Dict with fields: trigger_reason, alerts_since_last_train,
            transition_count, total_types, trained_at.

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT INTO model_retraining_events (
                        trigger_reason, alerts_since_last_train,
                        transition_count, total_types, trained_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    event.get("trigger_reason", "unknown"),
                    event.get("alerts_since_last_train", 0),
                    event.get("transition_count", 0),
                    event.get("total_types", 0),
                    event.get("trained_at", now),
                    now,
                ))

            logger.info(
                "retraining_event_recorded",
                trigger_reason=event.get("trigger_reason", "unknown"),
                alerts_since_last_train=event.get("alerts_since_last_train", 0),
            )
            return True
        except Exception as exc:
            logger.warning(
                "retraining_event_record_failed",
                error=str(exc),
            )
            return False

    def get_retraining_events(self, limit: int = 20) -> list[dict]:
        """Get recent retraining events.

        Sprint 5.39: Returns the most recent model retraining events
        from the persistent store, ordered by created_at descending.

        Parameters
        ----------
        limit:
            Maximum number of events to return.

        Returns a list of retraining event dicts, most recent first.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT id, trigger_reason, alerts_since_last_train,
                           transition_count, total_types, trained_at, created_at
                    FROM model_retraining_events
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()

            result = []
            for row in rows:
                result.append({
                    "id": row["id"],
                    "trigger_reason": row["trigger_reason"],
                    "alerts_since_last_train": row["alerts_since_last_train"],
                    "transition_count": row["transition_count"],
                    "total_types": row["total_types"],
                    "trained_at": row["trained_at"],
                    "created_at": row["created_at"],
                })

            return result
        except Exception as exc:
            logger.warning(
                "retraining_events_query_failed",
                error=str(exc),
            )
            return []
