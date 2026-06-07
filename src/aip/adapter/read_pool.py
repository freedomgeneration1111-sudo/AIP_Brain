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

Telemetry:
- Checkout count: total number of successful pool checkouts.
- Fallback count: times the pool was exhausted and the write conn was used.
- Exhaustion count: subset of fallback where all pool slots were occupied.
- Checkout latency: rolling average of time spent in _checkout_read_conn().

Interpreting exhaustion_rate:
- 0.0–0.1: Healthy. Pool is adequately sized for current load.
  Most checkouts are served from pool connections. No action needed.
- 0.1–0.3: Moderate. Pool handles most traffic but falls back
  occasionally under bursts. Acceptable for most workloads. Consider
  increasing pool_size if latency-sensitive reads are falling back.
- 0.3–0.6: High. A significant fraction of reads hit the write
  connection, which serializes through the single writer. This
  increases read latency and can block writes. **Increase pool_size
  by 1–2 connections** (e.g. from 3 to 5) and re-observe.
- >0.6: Critical. The pool is severely undersized. Most reads
  bypass the pool entirely, defeating its purpose. **Double the
  pool_size** and investigate whether read patterns have changed
  (e.g. new concurrent ask workloads, higher query complexity).

When to increase pool_size:
- If exhaustion_rate is consistently >0.3 on a store.
- If avg_checkout_latency_ms is high (>50ms) due to stale-connection
  recreation (which adds ~10ms per stale conn).
- If concurrent ask workloads are increasing (each ask touches 3–4
  stores, so 5 concurrent asks = ~15–20 simultaneous pool checkouts).

Note: Increasing pool_size increases memory usage (~1MB per SQLite
connection) and file descriptors. For a single-process app, 5–7 pool
connections per store is usually the practical maximum.

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
import time
from typing import Any, TypedDict

import aiosqlite

log = logging.getLogger(__name__)

# Default pool size — 3 is a good balance for a single-process async app:
# - 2 readers for parallel ask requests
# - 1 spare so a slow read doesn't immediately fall back to the write conn
_DEFAULT_POOL_SIZE = 3

# Rolling average window for checkout latency tracking
_LATENCY_WINDOW = 20



def resolve_pool_size(store_name: str, config: dict | None = None) -> int:
    """Resolve the read pool size for a given store from config.

    Resolution order:
      1. Per-store override: ``[read_pool.stores.<store_name>]`` pool_size
      2. Global default: ``[read_pool]`` pool_size
      3. Module default: 3

    Parameters
    ----------
    store_name:
        Identifier for the store (e.g. "lexical_store", "vector_store",
        "graph_store", "corpus_turn_store").  Used to look up per-store
        overrides in ``[read_pool.stores.<store_name>]``.
    config:
        Full TOML config dict.  If None, the module default is used.

    Returns
    -------
    int
        The resolved pool size, clamped to [1, 20].
    """
    if config is None:
        return _DEFAULT_POOL_SIZE

    read_pool_cfg = config.get("read_pool", {})

    # Per-store override
    stores_cfg = read_pool_cfg.get("stores", {})
    store_cfg = stores_cfg.get(store_name, {})
    if isinstance(store_cfg, dict) and "pool_size" in store_cfg:
        size = int(store_cfg["pool_size"])
        return max(1, min(20, size))

    # Global default
    if "pool_size" in read_pool_cfg:
        size = int(read_pool_cfg["pool_size"])
        return max(1, min(20, size))

    return _DEFAULT_POOL_SIZE


class ReadPoolHealth(TypedDict):
    """Structured type for read pool telemetry metrics.

    Key metrics for production observability:
    - checkout_count: total successful checkouts (pool + fallback)
    - fallback_count: times write conn was used (pool exhausted or stale)
    - exhaustion_count: subset of fallback where all pool slots were occupied
    - exhaustion_rate: exhaustion_count / checkout_count (utilization indicator)
    - avg_checkout_latency_ms: rolling average (window=20)
    - p95_checkout_latency_ms: 95th percentile of recent checkout latencies
    - recommendation: actionable guidance when exhaustion_rate > 0.3
    """

    pool_size: int
    pool_active: int
    checkout_count: int
    fallback_count: int
    exhaustion_count: int
    exhaustion_rate: float
    avg_checkout_latency_ms: float
    p95_checkout_latency_ms: float
    recommendation: str


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

    # Telemetry counters
    _pool_checkout_count: int
    _pool_fallback_count: int
    _pool_exhaustion_count: int
    _pool_checkout_latencies: list[float]

    def _init_read_pool(self, pool_size: int = _DEFAULT_POOL_SIZE) -> None:
        """Initialize pool bookkeeping (call from __init__)."""
        self._read_pool = []
        self._read_pool_available = []
        self._read_pool_size = pool_size
        self._read_pool_initialized = False
        # Telemetry
        self._pool_checkout_count = 0
        self._pool_fallback_count = 0
        self._pool_exhaustion_count = 0
        self._pool_checkout_latencies = []

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
        t0 = time.monotonic()
        await self._ensure_read_pool()

        for i, available in enumerate(self._read_pool_available):
            if available and i < len(self._read_pool):
                self._read_pool_available[i] = False
                conn = self._read_pool[i]
                # Verify connection is still alive
                try:
                    await conn.execute("SELECT 1")
                    self._record_checkout(t0, fallback=False)
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
                    self._record_checkout(t0, fallback=False)
                    return new_conn

        # All pool connections in use — fall back to write connection
        self._pool_exhaustion_count += 1
        log.debug(
            "read_pool_exhausted store=%s — falling back to write conn",
            self.__class__.__name__,
        )
        self._record_checkout(t0, fallback=True)
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

    def _record_checkout(self, t0: float, fallback: bool) -> None:
        """Record checkout telemetry."""
        elapsed = time.monotonic() - t0
        self._pool_checkout_count += 1
        if fallback:
            self._pool_fallback_count += 1
        self._pool_checkout_latencies.append(elapsed)
        if len(self._pool_checkout_latencies) > _LATENCY_WINDOW:
            self._pool_checkout_latencies = self._pool_checkout_latencies[-_LATENCY_WINDOW:]

    def read_pool_health(self) -> ReadPoolHealth:
        """Return read pool telemetry metrics.

        Only meaningful for stores that have ReadPoolMixin. Callers
        should check ``hasattr(store, "read_pool_health")`` before
        invoking.

        Includes utilization indicators:
        - exhaustion_rate: fraction of checkouts that hit pool exhaustion.
          Interpretation:
            0.0–0.1  Healthy — pool is adequately sized.
            0.1–0.3  Moderate — occasional fallbacks, usually fine.
            0.3–0.6  High — increase pool_size by 1–2 connections.
            >0.6     Critical — double pool_size and investigate.
        - p95_checkout_latency_ms: 95th percentile of recent checkout
          latencies, useful for spotting tail latency from stale connections.
        - recommendation: when exhaustion_rate > 0.3, suggests increasing
          pool_size; otherwise empty string.
        """
        pool_active = sum(
            1 for avail in getattr(self, "_read_pool_available", []) if not avail
        )
        latencies = getattr(self, "_pool_checkout_latencies", [])
        avg_latency_ms = 0.0
        p95_latency_ms = 0.0
        if latencies:
            avg_latency_ms = (sum(latencies) / len(latencies)) * 1000
            sorted_lat = sorted(latencies)
            p95_idx = max(0, int(len(sorted_lat) * 0.95) - 1)
            p95_latency_ms = sorted_lat[p95_idx] * 1000

        checkout_count = getattr(self, "_pool_checkout_count", 0)
        exhaustion_count = getattr(self, "_pool_exhaustion_count", 0)
        exhaustion_rate = (exhaustion_count / checkout_count) if checkout_count > 0 else 0.0

        # Generate recommendation when exhaustion rate is high
        pool_size = getattr(self, "_read_pool_size", 0)
        recommendation = ""
        if exhaustion_rate > 0.6:
            recommendation = (
                f"Critical: exhaustion_rate={exhaustion_rate:.2%}. "
                f"Double pool_size from {pool_size} to {pool_size * 2} and investigate read patterns."
            )
        elif exhaustion_rate > 0.3:
            recommendation = (
                f"High: exhaustion_rate={exhaustion_rate:.2%}. "
                f"Consider increasing pool_size from {pool_size} to {pool_size + 2}."
            )

        return {
            "pool_size": pool_size,
            "pool_active": pool_active,
            "checkout_count": checkout_count,
            "fallback_count": getattr(self, "_pool_fallback_count", 0),
            "exhaustion_count": exhaustion_count,
            "exhaustion_rate": round(exhaustion_rate, 4),
            "avg_checkout_latency_ms": round(avg_latency_ms, 3),
            "p95_checkout_latency_ms": round(p95_latency_ms, 3),
            "recommendation": recommendation,
        }

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


# ---------------------------------------------------------------------------
# Read Pool Auto-Sizing (Sprint 5.23)
# ---------------------------------------------------------------------------

# How many consecutive high-exhaustion observations before suggesting
# a pool size increase.  Prevents transient spikes from triggering
# suggestions.
_AUTO_SIZE_CONSECUTIVE_THRESHOLD = 3

# Maximum suggested pool size — conservative upper bound for a
# single-process app with SQLite WAL mode.
_AUTO_SIZE_MAX_POOL = 10


class PoolSizeSuggestion:
    """A single pool-size suggestion for a store.

    Generated by ``ReadPoolAutoSizer`` when a store's exhaustion rate
    has been consistently high over multiple observations.  This is a
    *suggestion only* — it is logged and exposed via the health
    endpoint but NOT automatically applied.  Operators must update
    ``aip.config.toml`` or the environment to apply the change.

    Attributes
    ----------
    store_name:
        The store identifier (e.g. "graph_store").
    current_pool_size:
        The pool size at the time of the suggestion.
    suggested_pool_size:
        The recommended new pool size.
    exhaustion_rate:
        The exhaustion rate that triggered the suggestion.
    reason:
        Human-readable explanation.
    created_at:
        ISO 8601 timestamp of when the suggestion was generated.
    """

    def __init__(
        self,
        store_name: str,
        current_pool_size: int,
        suggested_pool_size: int,
        exhaustion_rate: float,
        reason: str,
    ) -> None:
        self.store_name = store_name
        self.current_pool_size = current_pool_size
        self.suggested_pool_size = suggested_pool_size
        self.exhaustion_rate = exhaustion_rate
        self.reason = reason
        self.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def to_dict(self) -> dict:
        return {
            "store_name": self.store_name,
            "current_pool_size": self.current_pool_size,
            "suggested_pool_size": self.suggested_pool_size,
            "exhaustion_rate": round(self.exhaustion_rate, 4),
            "reason": self.reason,
            "created_at": self.created_at,
        }


class ReadPoolAutoSizer:
    """Monitors read pool exhaustion and generates pool-size suggestions.

    This is a lightweight observer that does NOT automatically resize
    pools.  It tracks exhaustion_rate per store over time and emits
    suggestions (via logging and the health endpoint) when the rate
    stays consistently high.

    Usage::

        auto_sizer = ReadPoolAutoSizer()
        # Call after each health check or cycle
        auto_sizer.observe("graph_store", pool_health)
        # Get current suggestions
        suggestions = auto_sizer.get_suggestions()
    """

    def __init__(self, consecutive_threshold: int = _AUTO_SIZE_CONSECUTIVE_THRESHOLD) -> None:
        self._consecutive_threshold = consecutive_threshold
        # store_name -> list of recent exhaustion_rate observations
        self._observations: dict[str, list[float]] = {}
        # store_name -> current pool size (from last observation)
        self._current_pool_sizes: dict[str, int] = {}
        # Active suggestions
        self._suggestions: list[PoolSizeSuggestion] = []

    def observe(self, store_name: str, pool_health: ReadPoolHealth) -> PoolSizeSuggestion | None:
        """Record a pool health observation and check for sustained high exhaustion.

        Call this periodically (e.g., on each health check or Sexton cycle)
        with the current pool health for each store.

        Returns a PoolSizeSuggestion if one was generated, or None.
        """
        exhaustion_rate = pool_health.get("exhaustion_rate", 0.0)
        pool_size = pool_health.get("pool_size", 3)

        self._current_pool_sizes[store_name] = pool_size

        # Track observations (keep last N for sliding window)
        if store_name not in self._observations:
            self._observations[store_name] = []
        self._observations[store_name].append(exhaustion_rate)
        # Keep only the last 10 observations
        if len(self._observations[store_name]) > 10:
            self._observations[store_name] = self._observations[store_name][-10:]

        # Check for sustained high exhaustion
        obs = self._observations[store_name]
        if len(obs) >= self._consecutive_threshold:
            # Check if the last N observations are all above 0.3
            recent = obs[-self._consecutive_threshold:]
            if all(rate > 0.3 for rate in recent):
                # Sustained high exhaustion — generate a suggestion
                avg_rate = sum(recent) / len(recent)
                suggested_size = self._compute_suggested_size(pool_size, avg_rate)

                if suggested_size > pool_size:
                    suggestion = PoolSizeSuggestion(
                        store_name=store_name,
                        current_pool_size=pool_size,
                        suggested_pool_size=suggested_size,
                        exhaustion_rate=avg_rate,
                        reason=(
                            f"Exhaustion rate has been > 30% for "
                            f"{self._consecutive_threshold} consecutive observations "
                            f"(avg={avg_rate:.2%}). "
                            f"Consider increasing pool_size from {pool_size} to "
                            f"{suggested_size} in [read_pool.stores.{store_name}] "
                            f"or [read_pool] pool_size."
                        ),
                    )

                    # Replace any existing suggestion for this store
                    self._suggestions = [
                        s for s in self._suggestions if s.store_name != store_name
                    ]
                    self._suggestions.append(suggestion)

                    log.info(
                        "read_pool_auto_size_suggestion",
                        store=store_name,
                        current_size=pool_size,
                        suggested_size=suggested_size,
                        exhaustion_rate=round(avg_rate, 4),
                    )

                    return suggestion

        # If exhaustion has recovered, clear the suggestion for this store
        if exhaustion_rate <= 0.3:
            prev_suggestions = len(self._suggestions)
            self._suggestions = [
                s for s in self._suggestions if s.store_name != store_name
            ]
            if len(self._suggestions) < prev_suggestions:
                log.info(
                    "read_pool_auto_size_cleared",
                    store=store_name,
                    note="exhaustion_rate recovered to <= 0.3",
                )

        return None

    @staticmethod
    def _compute_suggested_size(current_size: int, exhaustion_rate: float) -> int:
        """Compute a suggested pool size based on current size and exhaustion rate.

        Conservative strategy:
        - If exhaustion > 0.6 (critical): double the pool size
        - If exhaustion > 0.3 (high): add 2 connections
        - Never exceed _AUTO_SIZE_MAX_POOL (10)
        - Never decrease (only suggest increases)
        """
        if exhaustion_rate > 0.6:
            suggested = current_size * 2
        else:
            suggested = current_size + 2

        return min(suggested, _AUTO_SIZE_MAX_POOL)

    def get_suggestions(self) -> list[dict]:
        """Return all active suggestions as dicts.

        Suitable for inclusion in the /health endpoint response.
        """
        return [s.to_dict() for s in self._suggestions]

    def clear_suggestion(self, store_name: str) -> None:
        """Manually clear a suggestion for a store (e.g., after operator acts on it)."""
        self._suggestions = [s for s in self._suggestions if s.store_name != store_name]
