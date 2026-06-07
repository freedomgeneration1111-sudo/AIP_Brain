"""Connection lifecycle observability for SQLite adapter stores.

Provides a lightweight mixin that tracks connection health metrics:
- Time since last connection reset
- Number of connection resets
- Connection age
- Tables ready status
- Last operation timestamp
- Rolling average operation latency

Stores that use the async init + persistent connection pattern can
inherit from this mixin to expose health data through the /health endpoint.
"""

from __future__ import annotations

import time
from typing import TypedDict

# Rolling average window for operation latency tracking
_LATENCY_WINDOW = 20


class ConnectionHealth(TypedDict):
    """Structured type for connection health metrics returned by StoreHealthMixin."""

    store_type: str
    connected: bool
    tables_ready: bool
    connection_age_seconds: float
    resets: int
    seconds_since_last_reset: float
    seconds_since_last_op: float
    total_ops: int
    avg_op_latency_ms: float
    db_path: str


class StoreHealthMixin:
    """Mixin for SQLite stores to track and expose connection health metrics.

    Expects the inheriting class to have:
    - _conn: aiosqlite.Connection | None
    - _tables_ready: bool
    - _db_path: str

    Call _health_track_connect() when creating a new persistent connection.
    Call _health_track_reset() when resetting a connection due to error.
    Call _health_track_operation() after each DB operation to update latency.
    """

    # Tracked internally — initialized lazily to avoid requiring __init__ changes
    _health_conn_created_at: float
    _health_last_reset_at: float
    _health_reset_count: int
    _health_last_op_at: float
    _health_op_count: int
    _health_latency_samples: list[float]

    def _health_ensure_init(self) -> None:
        """Lazily initialize health tracking fields."""
        if not hasattr(self, "_health_conn_created_at"):
            self._health_conn_created_at = 0.0
        if not hasattr(self, "_health_last_reset_at"):
            self._health_last_reset_at = 0.0
        if not hasattr(self, "_health_reset_count"):
            self._health_reset_count = 0
        if not hasattr(self, "_health_last_op_at"):
            self._health_last_op_at = 0.0
        if not hasattr(self, "_health_op_count"):
            self._health_op_count = 0
        if not hasattr(self, "_health_latency_samples"):
            self._health_latency_samples = []

    def _health_track_connect(self) -> None:
        """Record that a new persistent connection was created."""
        self._health_ensure_init()
        self._health_conn_created_at = time.monotonic()

    def _health_track_reset(self) -> None:
        """Record that the connection was reset (error recovery)."""
        self._health_ensure_init()
        self._health_last_reset_at = time.monotonic()
        self._health_reset_count += 1

    def _health_track_operation(self, elapsed: float) -> None:
        """Record a completed DB operation with its elapsed time in seconds.

        Maintains a rolling window of the last _LATENCY_WINDOW samples
        for computing average latency without unbounded memory growth.
        """
        self._health_ensure_init()
        self._health_last_op_at = time.monotonic()
        self._health_op_count += 1
        self._health_latency_samples.append(elapsed)
        if len(self._health_latency_samples) > _LATENCY_WINDOW:
            self._health_latency_samples = self._health_latency_samples[-_LATENCY_WINDOW:]

    def connection_health(self) -> ConnectionHealth:
        """Return connection health metrics for this store.

        Returns a dict with:
        - store_type: class name
        - connected: whether a persistent connection exists
        - tables_ready: whether tables have been created
        - connection_age_seconds: age of the current connection (0 if not connected)
        - resets: number of connection resets since startup
        - seconds_since_last_reset: time since last reset (0 if never reset)
        - seconds_since_last_op: time since last tracked operation (0 if none)
        - total_ops: total number of tracked operations since startup
        - avg_op_latency_ms: rolling average of recent operation latencies in ms
        - db_path: the database file path
        """
        self._health_ensure_init()
        now = time.monotonic()

        conn = getattr(self, "_conn", None)
        # Consider connected if either the write connection or any read pool
        # connection is active (read-heavy stores may only use pool connections)
        read_pool_active = bool(getattr(self, "_read_pool_initialized", False))
        connected = conn is not None or read_pool_active
        tables_ready = getattr(self, "_tables_ready", False)
        db_path = getattr(self, "_db_path", "unknown")

        conn_age = (now - self._health_conn_created_at) if connected and self._health_conn_created_at > 0 else 0.0
        secs_since_reset = (now - self._health_last_reset_at) if self._health_last_reset_at > 0 else 0.0
        secs_since_op = (now - self._health_last_op_at) if self._health_last_op_at > 0 else 0.0

        avg_latency_ms = 0.0
        if self._health_latency_samples:
            avg_latency_ms = (sum(self._health_latency_samples) / len(self._health_latency_samples)) * 1000

        return {
            "store_type": self.__class__.__name__,
            "connected": connected,
            "tables_ready": tables_ready,
            "connection_age_seconds": round(conn_age, 1),
            "resets": self._health_reset_count,
            "seconds_since_last_reset": round(secs_since_reset, 1),
            "seconds_since_last_op": round(secs_since_op, 1),
            "total_ops": self._health_op_count,
            "avg_op_latency_ms": round(avg_latency_ms, 2),
            "db_path": db_path,
        }
