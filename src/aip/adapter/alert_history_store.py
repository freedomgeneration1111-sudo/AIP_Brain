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

# Schema version for migrations (Sprint 5.45: v8 adds ab_experiments; Sprint 5.46: v9 adds rollback/recovery; Sprint 5.48: v10 adds stat results + timeseries; Sprint 5.49: v11 adds confidence_calibration + pre_promotion_snapshots; Sprint 5.50: v12 adds bandit_decision_log)
_SCHEMA_VERSION = 12


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

                # Sprint 5.45: Schema migration v7 -> v8
                # Add ab_experiments table for A/B experiment persistence
                if current_version < 8:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS ab_experiments (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT NOT NULL UNIQUE,
                            control_config TEXT NOT NULL DEFAULT '{}',
                            variant_config TEXT NOT NULL DEFAULT '{}',
                            status TEXT NOT NULL DEFAULT 'running',
                            started_at TEXT NOT NULL,
                            stopped_at TEXT NOT NULL DEFAULT '',
                            result TEXT NOT NULL DEFAULT '',
                            control_samples INTEGER NOT NULL DEFAULT 0,
                            variant_samples INTEGER NOT NULL DEFAULT 0,
                            control_accuracy REAL NOT NULL DEFAULT 0.0,
                            variant_accuracy REAL NOT NULL DEFAULT 0.0,
                            promoted_variant TEXT NOT NULL DEFAULT '',
                            promotion_timestamp TEXT NOT NULL DEFAULT '',
                            auto_promoted INTEGER NOT NULL DEFAULT 0,
                            metadata TEXT NOT NULL DEFAULT '{}',
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL DEFAULT ''
                        )
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_ab_experiments_name
                        ON ab_experiments (name)
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_ab_experiments_status
                        ON ab_experiments (status)
                    """)

                # Sprint 5.46: Schema migration v8 -> v9
                # Add ab_rollback_history and decay_recovery_history tables
                if current_version < 9:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS ab_rollback_history (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            experiment_name TEXT NOT NULL,
                            rolled_back_variant TEXT NOT NULL,
                            rolled_back_at TEXT NOT NULL,
                            control_accuracy REAL NOT NULL DEFAULT 0.0,
                            variant_accuracy REAL NOT NULL DEFAULT 0.0,
                            auto INTEGER NOT NULL DEFAULT 1,
                            created_at TEXT NOT NULL
                        )
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_ab_rollback_experiment
                        ON ab_rollback_history (experiment_name)
                    """)

                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS decay_recovery_history (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            subject TEXT NOT NULL,
                            decay_amount REAL NOT NULL DEFAULT 0.0,
                            current_confidence REAL NOT NULL DEFAULT 0.0,
                            actions_taken TEXT NOT NULL DEFAULT '[]',
                            created_at TEXT NOT NULL
                        )
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_decay_recovery_subject
                        ON decay_recovery_history (subject)
                    """)

                # Sprint 5.48: Schema migration v9 -> v10
                # Add statistical_test_results and ab_accuracy_timeseries tables
                if current_version < 10:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS statistical_test_results (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            experiment_name TEXT NOT NULL,
                            p_value REAL,
                            confidence_interval_lower REAL,
                            confidence_interval_upper REAL,
                            method TEXT NOT NULL DEFAULT '',
                            significant INTEGER NOT NULL DEFAULT 0,
                            statistic REAL,
                            control_mean REAL,
                            variant_mean REAL,
                            control_samples INTEGER,
                            variant_samples INTEGER,
                            reason TEXT NOT NULL DEFAULT '',
                            test_result_json TEXT NOT NULL DEFAULT '{}',
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL DEFAULT '',
                            UNIQUE(experiment_name)
                        )
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_stat_test_experiment
                        ON statistical_test_results (experiment_name)
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_stat_test_significant
                        ON statistical_test_results (significant)
                    """)

                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS ab_accuracy_timeseries (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            experiment_name TEXT NOT NULL,
                            timestamp TEXT NOT NULL,
                            control_accuracy REAL NOT NULL DEFAULT 0.0,
                            variant_accuracy REAL NOT NULL DEFAULT 0.0,
                            control_samples INTEGER NOT NULL DEFAULT 0,
                            variant_samples INTEGER NOT NULL DEFAULT 0,
                            status TEXT NOT NULL DEFAULT 'running',
                            created_at TEXT NOT NULL
                        )
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_accuracy_ts_experiment
                        ON ab_accuracy_timeseries (experiment_name)
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_accuracy_ts_timestamp
                        ON ab_accuracy_timeseries (timestamp)
                    """)

                # Sprint 5.49: Schema migration v10 -> v11
                # Add confidence_calibration and pre_promotion_snapshots tables
                if current_version < 11:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS confidence_calibration (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            subject TEXT NOT NULL UNIQUE,
                            calibration_factor REAL NOT NULL DEFAULT 1.0,
                            updated_at TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        )
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_confidence_calibration_subject
                        ON confidence_calibration (subject)
                    """)

                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS pre_promotion_snapshots (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            experiment_name TEXT NOT NULL UNIQUE,
                            snapshot_data TEXT NOT NULL DEFAULT '{}',
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL DEFAULT ''
                        )
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_pre_promotion_snapshot_name
                        ON pre_promotion_snapshots (experiment_name)
                    """)

                # Sprint 5.50: Schema migration v11 -> v12
                # Add bandit_decision_log table for logging every bandit allocation decision
                if current_version < 12:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS bandit_decision_log (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            experiment_name TEXT NOT NULL,
                            method TEXT NOT NULL DEFAULT '',
                            allocation TEXT NOT NULL DEFAULT '{}',
                            confidence REAL,
                            context_features TEXT NOT NULL DEFAULT '{}',
                            sample_sizes TEXT NOT NULL DEFAULT '{}',
                            timestamp TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        )
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_bandit_decision_experiment
                        ON bandit_decision_log (experiment_name)
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_bandit_decision_timestamp
                        ON bandit_decision_log (timestamp)
                    """)

                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_bandit_decision_method
                        ON bandit_decision_log (method)
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

    # -----------------------------------------------------------------------
    # Sprint 5.45: A/B Experiment persistence
    # -----------------------------------------------------------------------

    def record_ab_experiment(self, experiment: dict) -> bool:
        """Record or update an A/B experiment in persistent storage.

        Sprint 5.45: Uses INSERT OR REPLACE to handle both creation and updates.
        The experiment name is the unique key.

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT OR REPLACE INTO ab_experiments (
                        name, control_config, variant_config, status,
                        started_at, stopped_at, result,
                        control_samples, variant_samples,
                        control_accuracy, variant_accuracy,
                        promoted_variant, promotion_timestamp, auto_promoted,
                        metadata, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    experiment.get("name", ""),
                    json.dumps(experiment.get("control_config", {}), default=str),
                    json.dumps(experiment.get("variant_config", {}), default=str),
                    experiment.get("status", "running"),
                    experiment.get("started_at", now),
                    experiment.get("stopped_at", ""),
                    experiment.get("result", ""),
                    experiment.get("control_samples", 0),
                    experiment.get("variant_samples", 0),
                    experiment.get("control_accuracy", 0.0),
                    experiment.get("variant_accuracy", 0.0),
                    experiment.get("promoted_variant", ""),
                    experiment.get("promotion_timestamp", ""),
                    1 if experiment.get("auto_promoted", False) else 0,
                    json.dumps(experiment.get("metadata", {}), default=str),
                    experiment.get("created_at", now),
                    now,
                ))

            return True
        except Exception as exc:
            logger.warning(
                "ab_experiment_record_failed",
                error=str(exc),
            )
            return False

    def get_ab_experiments(self, status: str | None = None) -> list[dict]:
        """Return A/B experiments from persistent storage.

        Sprint 5.45: Used by AlertManager to restore experiment state on startup.

        Returns a list of experiment dicts.
        """
        if not self._initialized:
            self.initialize()

        try:
            conditions: list[str] = []
            params: list[Any] = []

            if status:
                conditions.append("status = ?")
                params.append(status)

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            query = f"""
                SELECT name, control_config, variant_config, status,
                       started_at, stopped_at, result,
                       control_samples, variant_samples,
                       control_accuracy, variant_accuracy,
                       promoted_variant, promotion_timestamp, auto_promoted,
                       metadata, created_at, updated_at
                FROM ab_experiments
                {where_clause}
                ORDER BY created_at DESC
            """

            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()

            result = []
            for row in rows:
                try:
                    control_config = json.loads(row["control_config"]) if row["control_config"] else {}
                except (json.JSONDecodeError, TypeError):
                    control_config = {}
                try:
                    variant_config = json.loads(row["variant_config"]) if row["variant_config"] else {}
                except (json.JSONDecodeError, TypeError):
                    variant_config = {}
                try:
                    metadata = json.loads(row["metadata"]) if row["metadata"] else {}
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

                result.append({
                    "name": row["name"],
                    "control_config": control_config,
                    "variant_config": variant_config,
                    "status": row["status"],
                    "started_at": row["started_at"],
                    "stopped_at": row["stopped_at"],
                    "result": row["result"],
                    "control_samples": row["control_samples"],
                    "variant_samples": row["variant_samples"],
                    "control_accuracy": row["control_accuracy"],
                    "variant_accuracy": row["variant_accuracy"],
                    "promoted_variant": row["promoted_variant"],
                    "promotion_timestamp": row["promotion_timestamp"],
                    "auto_promoted": bool(row["auto_promoted"]),
                    "metadata": metadata,
                    "created_at": row["created_at"],
                })

            return result
        except Exception as exc:
            logger.warning(
                "ab_experiments_query_failed",
                error=str(exc),
            )
            return []

    def delete_ab_experiment(self, name: str) -> bool:
        """Delete an A/B experiment by name.

        Sprint 5.46: Used by the cleanup checker to prune stopped experiments.

        Returns True if the experiment was deleted, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute("""
                    DELETE FROM ab_experiments WHERE name = ?
                """, (name,))
                deleted = cursor.rowcount > 0

            if deleted:
                logger.info("ab_experiment_deleted_from_store", name=name)
            return deleted
        except Exception as exc:
            logger.warning(
                "ab_experiment_delete_failed",
                name=name,
                error=str(exc),
            )
            return False

    def prune_stopped_ab_experiments(self, retention_hours: int) -> int:
        """Prune stopped A/B experiments older than the retention period.

        Sprint 5.46: Removes experiments that have been stopped for longer
        than the specified retention period.

        Returns the number of experiments pruned.
        """
        if not self._initialized:
            self.initialize()

        if retention_hours <= 0:
            return 0

        try:
            import time as _time
            cutoff = datetime.fromtimestamp(
                _time.time() - (retention_hours * 3600), tz=timezone.utc
            ).isoformat()

            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute("""
                    DELETE FROM ab_experiments
                    WHERE status != 'running' AND stopped_at != '' AND stopped_at < ?
                """, (cutoff,))
                pruned = cursor.rowcount

            if pruned > 0:
                logger.info(
                    "ab_experiments_pruned",
                    pruned=pruned,
                    retention_hours=retention_hours,
                )
            return pruned
        except Exception as exc:
            logger.warning(
                "ab_experiments_prune_failed",
                error=str(exc),
            )
            return 0

    # -----------------------------------------------------------------------
    # Sprint 5.46: Rollback and recovery history persistence
    # -----------------------------------------------------------------------

    def record_ab_rollback(self, rollback: dict) -> bool:
        """Record an A/B experiment rollback event.

        Sprint 5.46: Persists rollback events for audit trail.

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT INTO ab_rollback_history (
                        experiment_name, rolled_back_variant, rolled_back_at,
                        control_accuracy, variant_accuracy, auto, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    rollback.get("experiment_name", ""),
                    rollback.get("rolled_back_variant", ""),
                    rollback.get("rolled_back_at", now),
                    rollback.get("control_accuracy", 0.0),
                    rollback.get("variant_accuracy", 0.0),
                    1 if rollback.get("auto", True) else 0,
                    now,
                ))
            return True
        except Exception as exc:
            logger.warning("ab_rollback_record_failed", error=str(exc))
            return False

    def get_ab_rollback_history(self, limit: int = 50) -> list[dict]:
        """Return A/B experiment rollback history.

        Sprint 5.46: Returns rollback events, most recent first.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT experiment_name, rolled_back_variant, rolled_back_at,
                           control_accuracy, variant_accuracy, auto
                    FROM ab_rollback_history
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()

            return [
                {
                    "experiment_name": row["experiment_name"],
                    "rolled_back_variant": row["rolled_back_variant"],
                    "rolled_back_at": row["rolled_back_at"],
                    "control_accuracy": row["control_accuracy"],
                    "variant_accuracy": row["variant_accuracy"],
                    "auto": bool(row["auto"]),
                }
                for row in rows
            ]
        except Exception as exc:
            logger.warning("ab_rollback_history_query_failed", error=str(exc))
            return []

    def record_decay_recovery(self, recovery: dict) -> bool:
        """Record a decay recovery event.

        Sprint 5.46: Persists recovery events for audit trail.

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT INTO decay_recovery_history (
                        subject, decay_amount, current_confidence,
                        actions_taken, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    recovery.get("subject", ""),
                    recovery.get("decay_amount", 0.0),
                    recovery.get("current_confidence", 0.0),
                    json.dumps(recovery.get("actions_taken", []), default=str),
                    now,
                ))
            return True
        except Exception as exc:
            logger.warning("decay_recovery_record_failed", error=str(exc))
            return False

    def get_decay_recovery_history(self, limit: int = 50) -> list[dict]:
        """Return decay recovery history.

        Sprint 5.46: Returns recovery events, most recent first.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT subject, decay_amount, current_confidence,
                           actions_taken, created_at
                    FROM decay_recovery_history
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()

            result = []
            for row in rows:
                try:
                    actions = json.loads(row["actions_taken"]) if row["actions_taken"] else []
                except (json.JSONDecodeError, TypeError):
                    actions = []
                result.append({
                    "subject": row["subject"],
                    "decay_amount": row["decay_amount"],
                    "current_confidence": row["current_confidence"],
                    "actions_taken": actions,
                    "timestamp": row["created_at"],
                })

            return result
        except Exception as exc:
            logger.warning("decay_recovery_history_query_failed", error=str(exc))
            return []

    # -----------------------------------------------------------------------
    # Sprint 5.48: Statistical test results and accuracy timeseries persistence
    # -----------------------------------------------------------------------

    def record_statistical_test_result(self, experiment_name: str, result: dict) -> bool:
        """Record or update a statistical test result for an experiment.

        Sprint 5.48: Persists p-values, confidence intervals, and test method
        so that statistical test outcomes survive restarts and are visible
        in the dashboard and APIs.

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            ci = result.get("confidence_interval", [None, None])
            if ci is None:
                ci = [None, None]

            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT OR REPLACE INTO statistical_test_results (
                        experiment_name, p_value,
                        confidence_interval_lower, confidence_interval_upper,
                        method, significant, statistic,
                        control_mean, variant_mean,
                        control_samples, variant_samples,
                        reason, test_result_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    experiment_name,
                    result.get("p_value"),
                    ci[0] if len(ci) > 0 else None,
                    ci[1] if len(ci) > 1 else None,
                    result.get("method", ""),
                    1 if result.get("significant", False) else 0,
                    result.get("statistic"),
                    result.get("control_mean"),
                    result.get("variant_mean"),
                    result.get("control_samples"),
                    result.get("variant_samples"),
                    result.get("reason", ""),
                    json.dumps(result, default=str),
                    result.get("created_at", now),
                    now,
                ))

            return True
        except Exception as exc:
            logger.warning("statistical_test_result_record_failed", error=str(exc))
            return False

    def get_statistical_test_results(self, experiment_name: str | None = None) -> list[dict]:
        """Return statistical test results from persistent storage.

        Sprint 5.48: Used to restore statistical test state on startup
        and to surface test outcomes in the dashboard/APIs.

        Parameters
        ----------
        experiment_name:
            If provided, return results for this experiment only.

        Returns a list of statistical test result dicts.
        """
        if not self._initialized:
            self.initialize()

        try:
            conditions: list[str] = []
            params: list[Any] = []

            if experiment_name:
                conditions.append("experiment_name = ?")
                params.append(experiment_name)

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            query = f"""
                SELECT experiment_name, p_value,
                       confidence_interval_lower, confidence_interval_upper,
                       method, significant, statistic,
                       control_mean, variant_mean,
                       control_samples, variant_samples,
                       reason, test_result_json,
                       created_at, updated_at
                FROM statistical_test_results
                {where_clause}
                ORDER BY updated_at DESC
            """

            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()

            result = []
            for row in rows:
                # Try to reconstruct from full JSON first
                try:
                    full_result = json.loads(row["test_result_json"]) if row["test_result_json"] else {}
                except (json.JSONDecodeError, TypeError):
                    full_result = {}

                # Merge structured columns into the result
                full_result.update({
                    "experiment_name": row["experiment_name"],
                    "p_value": row["p_value"],
                    "method": row["method"],
                    "significant": bool(row["significant"]),
                    "statistic": row["statistic"],
                    "control_mean": row["control_mean"],
                    "variant_mean": row["variant_mean"],
                    "control_samples": row["control_samples"],
                    "variant_samples": row["variant_samples"],
                    "confidence_interval": [row["confidence_interval_lower"], row["confidence_interval_upper"]],
                    "reason": row["reason"],
                    "persisted_at": row["updated_at"],
                })
                result.append(full_result)

            return result
        except Exception as exc:
            logger.warning("statistical_test_results_query_failed", error=str(exc))
            return []

    def record_accuracy_timeseries(self, snapshot: dict) -> bool:
        """Record an accuracy timeseries snapshot for an experiment.

        Sprint 5.48: Persists per-variant accuracy snapshots over time
        for dashboard mini charts to render real historical data.

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT INTO ab_accuracy_timeseries (
                        experiment_name, timestamp,
                        control_accuracy, variant_accuracy,
                        control_samples, variant_samples,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    snapshot.get("experiment_name", ""),
                    snapshot.get("timestamp", now),
                    snapshot.get("control_accuracy", 0.0),
                    snapshot.get("variant_accuracy", 0.0),
                    snapshot.get("control_samples", 0),
                    snapshot.get("variant_samples", 0),
                    snapshot.get("status", "running"),
                    now,
                ))

            return True
        except Exception as exc:
            logger.warning("accuracy_timeseries_record_failed", error=str(exc))
            return False

    def get_accuracy_timeseries(self, experiment_name: str, limit: int = 200) -> list[dict]:
        """Return accuracy timeseries data for an experiment.

        Sprint 5.48: Used by dashboard mini charts to render real historical
        accuracy data instead of synthesized oscillation.

        Returns a list of snapshot dicts sorted by timestamp ascending.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT experiment_name, timestamp,
                           control_accuracy, variant_accuracy,
                           control_samples, variant_samples,
                           status, created_at
                    FROM ab_accuracy_timeseries
                    WHERE experiment_name = ?
                    ORDER BY timestamp ASC
                    LIMIT ?
                """, (experiment_name, limit))
                rows = cursor.fetchall()

            result = []
            for row in rows:
                result.append({
                    "experiment_name": row["experiment_name"],
                    "timestamp": row["timestamp"],
                    "control_accuracy": row["control_accuracy"],
                    "variant_accuracy": row["variant_accuracy"],
                    "control_samples": row["control_samples"],
                    "variant_samples": row["variant_samples"],
                    "status": row["status"],
                })

            return result
        except Exception as exc:
            logger.warning("accuracy_timeseries_query_failed", error=str(exc))
            return []

    def prune_accuracy_timeseries(self, experiment_name: str | None = None, max_age_hours: int = 168) -> int:
        """Prune old accuracy timeseries data.

        Sprint 5.48: Removes timeseries snapshots older than the specified
        retention period to prevent unbounded table growth.

        Returns the number of rows pruned.
        """
        if not self._initialized:
            self.initialize()

        if max_age_hours <= 0:
            return 0

        try:
            import time as _time
            cutoff = datetime.fromtimestamp(
                _time.time() - (max_age_hours * 3600), tz=timezone.utc
            ).isoformat()

            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                if experiment_name:
                    cursor = conn.execute("""
                        DELETE FROM ab_accuracy_timeseries
                        WHERE experiment_name = ? AND timestamp < ?
                    """, (experiment_name, cutoff))
                else:
                    cursor = conn.execute("""
                        DELETE FROM ab_accuracy_timeseries
                        WHERE timestamp < ?
                    """, (cutoff,))
                pruned = cursor.rowcount

            if pruned > 0:
                logger.info("accuracy_timeseries_pruned", pruned=pruned, max_age_hours=max_age_hours)
            return pruned
        except Exception as exc:
            logger.warning("accuracy_timeseries_prune_failed", error=str(exc))
            return 0

    # -----------------------------------------------------------------------
    # Sprint 5.49: Confidence calibration persistence
    # -----------------------------------------------------------------------

    def record_confidence_calibration(self, subject: str, calibration_factor: float, updated_at: str) -> bool:
        """Record or update a confidence calibration entry.

        Sprint 5.49: Persists the calibration factor for a subject so that
        calibrated confidence values survive restarts.

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT OR REPLACE INTO confidence_calibration (
                        subject, calibration_factor, updated_at, created_at
                    ) VALUES (?, ?, ?, COALESCE(
                        (SELECT created_at FROM confidence_calibration WHERE subject = ?),
                        ?
                    ))
                """, (subject, calibration_factor, updated_at, subject, now))

            return True
        except Exception as exc:
            logger.warning("confidence_calibration_record_failed", error=str(exc))
            return False

    def get_confidence_calibrations(self) -> list[dict]:
        """Return all confidence calibration entries.

        Sprint 5.49: Used by AlertManager to restore calibration state on startup.

        Returns a list of calibration dicts.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT subject, calibration_factor, updated_at, created_at
                    FROM confidence_calibration
                    ORDER BY updated_at DESC
                """)
                rows = cursor.fetchall()

            return [
                {
                    "subject": row["subject"],
                    "calibration_factor": row["calibration_factor"],
                    "updated_at": row["updated_at"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
        except Exception as exc:
            logger.warning("confidence_calibrations_query_failed", error=str(exc))
            return []

    # -----------------------------------------------------------------------
    # Sprint 5.49: Pre-promotion config snapshot persistence
    # -----------------------------------------------------------------------

    def record_pre_promotion_snapshot(self, experiment_name: str, snapshot: dict) -> bool:
        """Record or update a pre-promotion config snapshot.

        Sprint 5.49: Persists the snapshot so that config reversion can
        still function after a process restart.

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            snapshot_json = json.dumps(snapshot, default=str)
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT OR REPLACE INTO pre_promotion_snapshots (
                        experiment_name, snapshot_data, created_at, updated_at
                    ) VALUES (?, ?, COALESCE(
                        (SELECT created_at FROM pre_promotion_snapshots WHERE experiment_name = ?),
                        ?
                    ), ?)
                """, (experiment_name, snapshot_json, experiment_name, now, now))

            return True
        except Exception as exc:
            logger.warning("pre_promotion_snapshot_record_failed", error=str(exc))
            return False

    def get_pre_promotion_snapshots(self) -> list[dict]:
        """Return all pre-promotion config snapshots.

        Sprint 5.49: Used by AlertManager to restore snapshots on startup
        so rollback can revert to the correct pre-promotion state even
        after a process restart.

        Returns a list of snapshot dicts.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT experiment_name, snapshot_data, created_at, updated_at
                    FROM pre_promotion_snapshots
                    ORDER BY updated_at DESC
                """)
                rows = cursor.fetchall()

            result = []
            for row in rows:
                try:
                    snapshot_data = json.loads(row["snapshot_data"]) if row["snapshot_data"] else {}
                except (json.JSONDecodeError, TypeError):
                    snapshot_data = {}
                result.append({
                    "experiment_name": row["experiment_name"],
                    "snapshot_data": snapshot_data,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })

            return result
        except Exception as exc:
            logger.warning("pre_promotion_snapshots_query_failed", error=str(exc))
            return []

    def delete_pre_promotion_snapshot(self, experiment_name: str) -> bool:
        """Delete a pre-promotion config snapshot after rollback is complete.

        Sprint 5.49: Called after a successful config reversion to clean up
        the persisted snapshot.

        Returns True if the snapshot was deleted, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                cursor = conn.execute("""
                    DELETE FROM pre_promotion_snapshots WHERE experiment_name = ?
                """, (experiment_name,))
                return cursor.rowcount > 0
        except Exception as exc:
            logger.warning("pre_promotion_snapshot_delete_failed", error=str(exc))
            return False

    # -----------------------------------------------------------------------
    # Sprint 5.50: Bandit Decision Logging
    # -----------------------------------------------------------------------

    def record_bandit_decision(self, decision: dict) -> bool:
        """Record a bandit allocation decision to the log.

        Sprint 5.50: Persists every bandit allocation decision with full
        context (method, allocation, confidence, context features, sample
        sizes) for debugging, auditing, and replay.

        Parameters
        ----------
        decision:
            Dict with fields: experiment_name, method, allocation,
            confidence, context_features, sample_sizes, timestamp.

        Returns True if successfully recorded, False otherwise.
        """
        if not self._initialized:
            self.initialize()

        try:
            now = datetime.now(timezone.utc).isoformat()
            allocation_json = json.dumps(decision.get("allocation", {}), default=str)
            context_json = json.dumps(decision.get("context_features", {}), default=str)
            sample_sizes_json = json.dumps(decision.get("sample_sizes", {}), default=str)

            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    INSERT INTO bandit_decision_log (
                        experiment_name, method, allocation, confidence,
                        context_features, sample_sizes, timestamp, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    decision.get("experiment_name", ""),
                    decision.get("method", ""),
                    allocation_json,
                    decision.get("confidence"),
                    context_json,
                    sample_sizes_json,
                    decision.get("timestamp", now),
                    now,
                ))

            return True
        except Exception as exc:
            logger.warning("bandit_decision_log_record_failed", error=str(exc))
            return False

    def get_bandit_decisions(
        self,
        experiment_name: str | None = None,
        method: str | None = None,
        since: str | None = None,
        before: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query bandit decision log with optional filters.

        Sprint 5.50: Supports filtering by experiment name, method, and
        date range for debugging and auditing.

        Parameters
        ----------
        experiment_name:
            Filter by experiment name.
        method:
            Filter by bandit method (thompson, ucb, epsilon_greedy).
        since:
            ISO 8601 datetime — only return decisions after this time.
        before:
            ISO 8601 datetime — only return decisions before this time.
        limit:
            Maximum number of decisions to return (most recent first).

        Returns a list of decision dicts, most recent first.
        """
        if not self._initialized:
            self.initialize()

        try:
            conditions: list[str] = []
            params: list[Any] = []

            if experiment_name:
                conditions.append("experiment_name = ?")
                params.append(experiment_name)
            if method:
                conditions.append("method = ?")
                params.append(method)
            if since:
                conditions.append("timestamp > ?")
                params.append(since)
            if before:
                conditions.append("timestamp < ?")
                params.append(before)

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            query = f"""
                SELECT id, experiment_name, method, allocation, confidence,
                       context_features, sample_sizes, timestamp, created_at
                FROM bandit_decision_log
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
                    allocation = json.loads(row["allocation"]) if row["allocation"] else {}
                except (json.JSONDecodeError, TypeError):
                    allocation = {}
                try:
                    context_features = json.loads(row["context_features"]) if row["context_features"] else {}
                except (json.JSONDecodeError, TypeError):
                    context_features = {}
                try:
                    sample_sizes = json.loads(row["sample_sizes"]) if row["sample_sizes"] else {}
                except (json.JSONDecodeError, TypeError):
                    sample_sizes = {}

                result.append({
                    "id": row["id"],
                    "experiment_name": row["experiment_name"],
                    "method": row["method"],
                    "allocation": allocation,
                    "confidence": row["confidence"],
                    "context_features": context_features,
                    "sample_sizes": sample_sizes,
                    "timestamp": row["timestamp"],
                    "created_at": row["created_at"],
                })

            return result
        except Exception as exc:
            logger.warning("bandit_decisions_query_failed", error=str(exc))
            return []

    def get_experiment_event_timeline(
        self,
        experiment_name: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
        before: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Query a unified timeline of experimentation events.

        Sprint 5.50: Aggregates events from multiple tables (promotions,
        rollbacks, decay recovery, cleanup) into a single chronological
        timeline. This provides operators with a system-level view of all
        experimentation events instead of requiring them to check individual
        experiment panels.

        Parameters
        ----------
        experiment_name:
            Filter by experiment name (applies to all event types).
        event_type:
            Filter by event type: "promotion", "rollback", "decay_recovery",
            "cleanup", "bandit_decision". None returns all types.
        since:
            ISO 8601 datetime — only return events after this time.
        before:
            ISO 8601 datetime — only return events before this time.
        limit:
            Maximum number of events to return (most recent first).

        Returns a list of event dicts sorted by timestamp descending.
        """
        if not self._initialized:
            self.initialize()

        events: list[dict] = []

        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row

                # Promotion events from ab_experiments
                if event_type is None or event_type == "promotion":
                    conditions = ["promoted_variant != ''", "promotion_timestamp != ''"]
                    params: list[Any] = []
                    if experiment_name:
                        conditions.append("name = ?")
                        params.append(experiment_name)
                    if since:
                        conditions.append("promotion_timestamp > ?")
                        params.append(since)
                    if before:
                        conditions.append("promotion_timestamp < ?")
                        params.append(before)
                    where = "WHERE " + " AND ".join(conditions)
                    cursor = conn.execute(f"""
                        SELECT name, promoted_variant, promotion_timestamp,
                               control_accuracy, variant_accuracy, auto_promoted
                        FROM ab_experiments {where}
                        ORDER BY promotion_timestamp DESC LIMIT ?
                    """, params + [limit])
                    for row in cursor.fetchall():
                        events.append({
                            "event_type": "promotion",
                            "experiment_name": row["name"],
                            "timestamp": row["promotion_timestamp"],
                            "variant": row["promoted_variant"],
                            "auto_promoted": bool(row["auto_promoted"]),
                            "control_accuracy": row["control_accuracy"],
                            "variant_accuracy": row["variant_accuracy"],
                        })

                # Rollback events
                if event_type is None or event_type == "rollback":
                    conditions = []
                    params = []
                    if experiment_name:
                        conditions.append("experiment_name = ?")
                        params.append(experiment_name)
                    if since:
                        conditions.append("rolled_back_at > ?")
                        params.append(since)
                    if before:
                        conditions.append("rolled_back_at < ?")
                        params.append(before)
                    where = "WHERE " + " AND ".join(conditions) if conditions else ""
                    cursor = conn.execute(f"""
                        SELECT experiment_name, rolled_back_variant, rolled_back_at,
                               control_accuracy, variant_accuracy, auto
                        FROM ab_rollback_history {where}
                        ORDER BY rolled_back_at DESC LIMIT ?
                    """, params + [limit])
                    for row in cursor.fetchall():
                        events.append({
                            "event_type": "rollback",
                            "experiment_name": row["experiment_name"],
                            "timestamp": row["rolled_back_at"],
                            "rolled_back_variant": row["rolled_back_variant"],
                            "control_accuracy": row["control_accuracy"],
                            "variant_accuracy": row["variant_accuracy"],
                            "auto": bool(row["auto"]),
                        })

                # Decay recovery events
                if event_type is None or event_type == "decay_recovery":
                    conditions = []
                    params = []
                    if experiment_name:
                        conditions.append("subject = ?")
                        params.append(experiment_name)
                    if since:
                        conditions.append("created_at > ?")
                        params.append(since)
                    if before:
                        conditions.append("created_at < ?")
                        params.append(before)
                    where = "WHERE " + " AND ".join(conditions) if conditions else ""
                    cursor = conn.execute(f"""
                        SELECT subject, decay_amount, current_confidence, actions_taken, created_at
                        FROM decay_recovery_history {where}
                        ORDER BY created_at DESC LIMIT ?
                    """, params + [limit])
                    for row in cursor.fetchall():
                        events.append({
                            "event_type": "decay_recovery",
                            "experiment_name": row["subject"],
                            "timestamp": row["created_at"],
                            "decay_amount": row["decay_amount"],
                            "current_confidence": row["current_confidence"],
                            "actions_taken": json.loads(row["actions_taken"]) if row["actions_taken"] else [],
                        })

                # Bandit decision events (most recent per experiment)
                if event_type is None or event_type == "bandit_decision":
                    conditions = []
                    params = []
                    if experiment_name:
                        conditions.append("experiment_name = ?")
                        params.append(experiment_name)
                    if since:
                        conditions.append("timestamp > ?")
                        params.append(since)
                    if before:
                        conditions.append("timestamp < ?")
                        params.append(before)
                    where = "WHERE " + " AND ".join(conditions) if conditions else ""
                    cursor = conn.execute(f"""
                        SELECT experiment_name, method, allocation, confidence, timestamp
                        FROM bandit_decision_log {where}
                        ORDER BY timestamp DESC LIMIT ?
                    """, params + [limit])
                    for row in cursor.fetchall():
                        events.append({
                            "event_type": "bandit_decision",
                            "experiment_name": row["experiment_name"],
                            "timestamp": row["timestamp"],
                            "method": row["method"],
                            "allocation": json.loads(row["allocation"]) if row["allocation"] else {},
                            "confidence": row["confidence"],
                        })

            # Sort all events by timestamp descending
            events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
            return events[:limit]
        except Exception as exc:
            logger.warning("experiment_event_timeline_query_failed", error=str(exc))
            return []

    def cleanup_stale_pre_promotion_snapshots(
        self,
        active_experiment_names: set[str] | None = None,
        max_age_hours: int = 72,
    ) -> int:
        """Remove stale pre-promotion snapshots.

        Sprint 5.50: Cleans up snapshots for experiments that have been
        stopped, rolled back, or whose promotion window has expired.
        Snapshots for experiments still in the active set are retained.

        Parameters
        ----------
        active_experiment_names:
            Set of currently active (running or promoted) experiment names.
            Snapshots for experiments NOT in this set are candidates for
            removal. If None, only age-based cleanup is performed.
        max_age_hours:
            Maximum age in hours for snapshots. Snapshots older than this
            are removed regardless of active status. Default 72 hours.

        Returns the number of snapshots removed.
        """
        if not self._initialized:
            self.initialize()

        try:
            now_epoch = datetime.now(timezone.utc).timestamp()
            cutoff_epoch = now_epoch - (max_age_hours * 3600)

            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.row_factory = sqlite3.Row

                # Get all snapshots
                cursor = conn.execute("""
                    SELECT experiment_name, created_at FROM pre_promotion_snapshots
                """)
                rows = cursor.fetchall()

                removed = 0
                for row in rows:
                    exp_name = row["experiment_name"]
                    created_at = row["created_at"]

                    # Check if experiment is still active
                    if active_experiment_names is not None and exp_name in active_experiment_names:
                        continue  # Keep snapshots for active experiments

                    # Check age-based expiry
                    try:
                        created_dt = datetime.fromisoformat(created_at)
                        if created_dt.timestamp() < cutoff_epoch:
                            conn.execute(
                                "DELETE FROM pre_promotion_snapshots WHERE experiment_name = ?",
                                (exp_name,),
                            )
                            removed += 1
                    except (ValueError, TypeError):
                        # If we can't parse the date and the experiment isn't active, remove it
                        if active_experiment_names is None or exp_name not in active_experiment_names:
                            conn.execute(
                                "DELETE FROM pre_promotion_snapshots WHERE experiment_name = ?",
                                (exp_name,),
                            )
                            removed += 1

                # Also remove snapshots for inactive experiments (not in active set)
                if active_experiment_names is not None:
                    cursor2 = conn.execute("SELECT experiment_name FROM pre_promotion_snapshots")
                    for row in cursor2.fetchall():
                        if row["experiment_name"] not in active_experiment_names:
                            conn.execute(
                                "DELETE FROM pre_promotion_snapshots WHERE experiment_name = ?",
                                (row["experiment_name"],),
                            )
                            removed += 1

            if removed > 0:
                logger.info("stale_snapshots_cleaned_up", removed=removed)

            return removed
        except Exception as exc:
            logger.warning("stale_snapshot_cleanup_failed", error=str(exc))
            return 0
