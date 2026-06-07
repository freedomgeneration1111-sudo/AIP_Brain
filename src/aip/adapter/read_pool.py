"""Read connection pool for SQLite stores in WAL mode.

SQLite WAL mode supports concurrent readers alongside a single writer.
This module provides a lightweight pool of read-only connections that
read-heavy stores (lexical, vector, graph, corpus turns) can use
during concurrent ask workloads to avoid serialising all reads
through a single persistent connection.

Pool semantics:
- Fixed size (default 3 connections), created lazily on first checkout.
- Connections are checked out, used, and returned (no timeout-based eviction).
- Read connections use PRAGMA query_only = ON to guarantee no accidental writes.
- If all connections are in use, the caller falls back to the store's
  existing persistent connection (graceful degradation, never blocks).

Usage::

    class MyStore(StoreHealthMixin, ReadPoolMixin):
        ...

        async def search(self, query, ...):
            conn = await self._checkout_read_conn()
            try:
                cursor = await conn.execute(...)
                return [self._row_to_item(r) for r in await cursor.fetchall()]
            finally:
                self._return_read_conn(conn)
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

# Default pool size — 3 is a good balance for a single-process async app:
# - 2 readers for parallel ask requests
# - 1 spare so a slow read doesn't immediately fall back to the write conn
_DEFAULT_POOL_SIZE = 3


class ReadPoolMixin:
    """Mixin that adds a small read connection pool to a SQLite store.

    Expects the inheriting class to have:
    - _db_path: str
    - _get_conn(): async method returning the write connection (fallback)

    The pool is created lazily on first checkout. Connections use
    WAL mode and PRAGMA query_only = ON for safety.
    """

    _read_pool: list[aiosqlite.Connection]
    _read_pool_available: list[bool]
    _read_pool_size: int
    _read_pool_initialized: bool

    def _init_read_pool(self, pool_size: int = _DEFAULT_POOL_SIZE) -> None:
        """Initialize pool bookkeeping (call from __init__)."""
        self._read_pool = []
        self._read_pool_available = []
        self._read_pool_size = pool_size
        self._read_pool_initialized = False

    async def _ensure_read_pool(self) -> None:
        """Create pool connections lazily on first use."""
        if self._read_pool_initialized:
            return
        db_path = getattr(self, "_db_path", None)
        if not db_path:
            return
        try:
            for _ in range(self._read_pool_size):
                conn = await aiosqlite.connect(db_path)
                conn.row_factory = sqlite3.Row
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA query_only = ON")
                self._read_pool.append(conn)
                self._read_pool_available.append(True)
            self._read_pool_initialized = True
            # Track connection health for the read pool
            if hasattr(self, "_health_track_connect"):
                self._health_track_connect()
            log.debug(
                "read_pool_created store=%s size=%d",
                self.__class__.__name__, self._read_pool_size,
            )
        except Exception as exc:
            log.warning(
                "read_pool_init_failed store=%s error=%s — will fall back to write conn",
                self.__class__.__name__, exc,
            )
            # Clean up any partially created connections
            await self._close_read_pool()
            self._read_pool_initialized = False

    async def _checkout_read_conn(self) -> aiosqlite.Connection:
        """Check out a read connection from the pool.

        Returns a read-only connection if available, otherwise falls back
        to the store's existing persistent (write) connection.  Never blocks.
        """
        await self._ensure_read_pool()

        for i, available in enumerate(self._read_pool_available):
            if available and i < len(self._read_pool):
                self._read_pool_available[i] = False
                conn = self._read_pool[i]
                # Verify connection is still alive
                try:
                    await conn.execute("SELECT 1")
                    return conn
                except Exception:
                    # Connection is stale — recreate it
                    try:
                        await conn.close()
                    except Exception:
                        pass
                    db_path = getattr(self, "_db_path", "")
                    new_conn = await aiosqlite.connect(db_path)
                    new_conn.row_factory = sqlite3.Row
                    await new_conn.execute("PRAGMA journal_mode=WAL")
                    await new_conn.execute("PRAGMA query_only = ON")
                    self._read_pool[i] = new_conn
                    return new_conn

        # All pool connections in use — fall back to write connection
        log.debug(
            "read_pool_exhausted store=%s — falling back to write conn",
            self.__class__.__name__,
        )
        return await self._get_conn()  # type: ignore[attr-defined]

    def _return_read_conn(self, conn: aiosqlite.Connection) -> None:
        """Return a read connection to the pool.

        If the connection is a pool member, marks it as available.
        If it's the write connection (fallback), this is a no-op.
        """
        for i, pool_conn in enumerate(self._read_pool):
            if pool_conn is conn:
                self._read_pool_available[i] = True
                return
        # Not a pool connection (was a write-conn fallback) — no action needed

    async def _close_read_pool(self) -> None:
        """Close all read pool connections."""
        for conn in self._read_pool:
            try:
                await conn.close()
            except Exception:
                pass
        self._read_pool = []
        self._read_pool_available = []
        self._read_pool_initialized = False
