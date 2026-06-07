"""Connection lifecycle observability for SQLite adapter stores.

Provides a lightweight mixin that tracks connection health metrics:
- Time since last connection reset
- Number of connection resets
- Connection age
- Tables ready status

Stores that use the async init + persistent connection pattern can
inherit from this mixin to expose health data through the /health endpoint.
"""

from __future__ import annotations

import time
from typing import Any


class StoreHealthMixin:
    """Mixin for SQLite stores to track and expose connection health metrics.

    Expects the inheriting class to have:
    - _conn: aiosqlite.Connection | None
    - _tables_ready: bool
    - _db_path: str

    Call _health_track_connect() when creating a new persistent connection.
    Call _health_track_reset() when resetting a connection due to error.
    """

    # Tracked internally — initialized lazily to avoid requiring __init__ changes
    _health_conn_created_at: float
    _health_last_reset_at: float
    _health_reset_count: int

    def _health_ensure_init(self) -> None:
        """Lazily initialize health tracking fields."""
        if not hasattr(self, "_health_conn_created_at"):
            self._health_conn_created_at = 0.0
        if not hasattr(self, "_health_last_reset_at"):
            self._health_last_reset_at = 0.0
        if not hasattr(self, "_health_reset_count"):
            self._health_reset_count = 0

    def _health_track_connect(self) -> None:
        """Record that a new persistent connection was created."""
        self._health_ensure_init()
        self._health_conn_created_at = time.monotonic()

    def _health_track_reset(self) -> None:
        """Record that the connection was reset (error recovery)."""
        self._health_ensure_init()
        self._health_last_reset_at = time.monotonic()
        self._health_reset_count += 1

    def connection_health(self) -> dict[str, Any]:
        """Return connection health metrics for this store.

        Returns a dict with:
        - store_type: class name
        - connected: whether a persistent connection exists
        - tables_ready: whether tables have been created
        - connection_age_seconds: age of the current connection (0 if not connected)
        - resets: number of connection resets since startup
        - seconds_since_last_reset: time since last reset (0 if never reset)
        - db_path: the database file path
        """
        self._health_ensure_init()
        now = time.monotonic()

        conn = getattr(self, "_conn", None)
        connected = conn is not None
        tables_ready = getattr(self, "_tables_ready", False)
        db_path = getattr(self, "_db_path", "unknown")

        conn_age = (now - self._health_conn_created_at) if connected and self._health_conn_created_at > 0 else 0.0
        secs_since_reset = (now - self._health_last_reset_at) if self._health_last_reset_at > 0 else 0.0

        return {
            "store_type": self.__class__.__name__,
            "connected": connected,
            "tables_ready": tables_ready,
            "connection_age_seconds": round(conn_age, 1),
            "resets": self._health_reset_count,
            "seconds_since_last_reset": round(secs_since_reset, 1),
            "db_path": db_path,
        }
