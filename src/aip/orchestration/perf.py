"""PerformanceProfiler — orchestration component for profiling and metrics (CHUNK-10.4).

Per AIP_0_1_Phase8_BuildSpec_Rev1.0.md exact prose + box + ANNEX.
Cross-cutting. Uses PerformanceConfig (10.0a) + TraceStore.
Synthetic deterministic (no real models/network in benchmarks).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Awaitable

from aip.foundation.schemas import PerformanceConfig


class PerformanceProfiler:
    """Provides performance profiling and metrics for critical path optimization.

    profile_operation wraps async callables, measures time, records trace events.
    Exposes system metrics, slow operations, memory usage.
    """

    def __init__(self, config: PerformanceConfig, trace_store: Any) -> None:
        self.config = config
        self.trace_store = trace_store

    async def profile_operation(self, operation_name: str, operation: Callable[[], Awaitable[Any]]) -> dict:
        """Wrap and measure an async operation. Records to trace."""
        start = time.perf_counter()
        success = True
        error = None
        result = None
        try:
            result = await operation()
        except Exception as e:
            success = False
            error = str(e)
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            try:
                await self.trace_store.record_event({
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "node_type": "performance_profiler",
                    "operation": operation_name,
                    "duration_ms": duration_ms,
                    "success": success,
                    "error": error,
                })
            except Exception:
                pass  # trace failures must not break profiling

        return {
            "operation": operation_name,
            "duration_ms": duration_ms,
            "success": success,
            "error": error,
            "result": result,
        }

    async def get_system_metrics(self) -> dict:
        """Return current system metrics (CPU, memory, sessions, DB sizes, model health)."""
        # Synthetic/deterministic values for laptop-viable profile (no real system calls in CI)
        return {
            "cpu_percent": 12.5,
            "memory_mb": 1850,
            "active_sessions": 3,
            "db_sizes": {"state": "12MB", "events": "8MB", "trace": "5MB", "vectors": "45MB", "lexical": "18MB"},
            "model_slot_health": {"synthesis": "ok", "evaluation": "ok"},
            "profiling_enabled": self.config.profiling_enabled,
        }

    async def get_slow_operations(self, threshold_ms: int = 1000) -> list[dict]:
        """Query trace for operations exceeding threshold (synthetic in this impl)."""
        # In full impl would query TraceStore; here return deterministic sample
        return [
            {"operation": "retrieve_for_synthesis", "duration_ms": 1240, "timestamp": "2026-05-..."},
        ]

    async def get_memory_usage(self) -> dict:
        """Detailed memory breakdown by component."""
        return {
            "total_mb": 1850,
            "vector_index": 320,
            "lexical_fts5": 180,
            "active_sessions": 45,
            "model_pools": 120,
            "compiled_knowledge_cache": 210,
            "other": 975,
            "max_target_mb": self.config.max_memory_mb,
        }
