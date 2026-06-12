"""Tests for Performance API contract.

Verifies that:
1. Performance endpoint with profiler disabled returns structured disabled/unavailable response.
2. Performance endpoint with test profiler returns real test metric.
3. Startup does not create half-initialized profiler state.
4. No fake metrics are returned when profiler is unavailable.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aip.adapter.api.performance import router
from aip.adapter.auth.dependencies import require_definer
from aip.foundation.schemas import PerformanceConfig


class FakeProfilerEnabled:
    """Fake profiler with profiling_enabled=True."""

    def __init__(self):
        self.config = PerformanceConfig(profiling_enabled=True, max_memory_mb=4096)

    async def get_system_metrics(self):
        return {
            "cpu_percent": 12.5,
            "memory_mb": 1850.0,
            "active_sessions": 0,
            "db_sizes": {},
            "model_slot_health": {},
            "profiling_enabled": True,
        }

    async def get_slow_operations(self, threshold_ms=1000):
        return [{"operation": "test_op", "duration_ms": 1500.0, "timestamp": "2026-01-01"}]

    async def get_memory_usage(self):
        return {
            "total_mb": 1850.0,
            "max_target_mb": 4096,
            "within_target": True,
            "components": {"vector_store": 462.5, "other": 1387.5},
        }


class FakeProfilerDisabled:
    """Fake profiler with profiling_enabled=False."""

    def __init__(self):
        self.config = PerformanceConfig(profiling_enabled=False, max_memory_mb=4096)

    async def get_system_metrics(self):
        return {"cpu_percent": 0, "memory_mb": 0, "profiling_enabled": False}

    async def get_slow_operations(self, threshold_ms=1000):
        return []

    async def get_memory_usage(self):
        return {"total_mb": 0, "max_target_mb": 4096}


def _make_app_with_profiler(profiler=None):
    """Create a test app with optional profiler."""
    app = FastAPI()
    app.include_router(router)

    class FakeContainer:
        performance_profiler = profiler

    app.state.container = FakeContainer()
    # Override auth — performance endpoints are read-only; tests verify
    # contract shape, not auth enforcement.
    app.dependency_overrides[require_definer] = lambda: {"identity": "test", "role": "definer"}
    return TestClient(app)


def _make_app_without_profiler():
    """Create a test app with no profiler at all."""
    app = FastAPI()
    app.include_router(router)

    class FakeContainer:
        performance_profiler = None

    app.state.container = FakeContainer()
    app.dependency_overrides[require_definer] = lambda: {"identity": "test", "role": "definer"}
    return TestClient(app)


# --- Test: profiler unavailable returns structured error ---


def test_metrics_unavailable_returns_structured_error():
    client = _make_app_without_profiler()
    response = client.get("/performance/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "BACKEND_UNAVAILABLE"
    assert "not configured" in data["error"]["message"].lower() or "not initialized" in data["error"]["message"].lower()


def test_slow_unavailable_returns_structured_error():
    client = _make_app_without_profiler()
    response = client.get("/performance/slow")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "BACKEND_UNAVAILABLE"


def test_memory_unavailable_returns_structured_error():
    client = _make_app_without_profiler()
    response = client.get("/performance/memory")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "BACKEND_UNAVAILABLE"


# --- Test: profiler disabled returns structured disabled response ---


def test_metrics_disabled_returns_disabled_response():
    client = _make_app_with_profiler(FakeProfilerDisabled())
    response = client.get("/performance/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "DISABLED"


def test_slow_disabled_returns_disabled_response():
    client = _make_app_with_profiler(FakeProfilerDisabled())
    response = client.get("/performance/slow")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "DISABLED"


def test_memory_disabled_returns_disabled_response():
    client = _make_app_with_profiler(FakeProfilerDisabled())
    response = client.get("/performance/memory")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "DISABLED"


# --- Test: enabled profiler returns real data ---


def test_metrics_enabled_returns_real_data():
    client = _make_app_with_profiler(FakeProfilerEnabled())
    response = client.get("/performance/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "cpu_percent" in data["data"]
    assert data["data"]["profiling_enabled"] is True


def test_slow_enabled_returns_real_data():
    client = _make_app_with_profiler(FakeProfilerEnabled())
    response = client.get("/performance/slow")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "slow" in data["data"]
    assert len(data["data"]["slow"]) > 0


def test_memory_enabled_returns_real_data():
    client = _make_app_with_profiler(FakeProfilerEnabled())
    response = client.get("/performance/memory")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "total_mb" in data["data"]
    assert data["data"]["within_target"] is True


# --- Test: no fake metrics on unavailable ---


def test_no_fake_metrics_on_unavailable():
    """When profiler is unavailable, no fake/preset metric values are returned."""
    client = _make_app_without_profiler()
    for endpoint in ["/performance/metrics", "/performance/slow", "/performance/memory"]:
        response = client.get(endpoint)
        data = response.json()
        # Must NOT return ok=True
        assert data["ok"] is False
        # Must NOT contain a 'data' key with fake metrics
        assert "data" not in data


# --- Test: profiler respects config profiling_enabled flag ---


def test_profiler_config_flag_honored():
    """A profiler with profiling_enabled=False must not return metrics."""
    client = _make_app_with_profiler(FakeProfilerDisabled())
    response = client.get("/performance/metrics")
    data = response.json()
    assert data["ok"] is False
    assert data["error"]["code"] == "DISABLED"


# --- Test: real PerformanceProfiler integration ---


async def test_real_profiler_returns_metrics():
    """Integration: real PerformanceProfiler returns actual metrics."""
    from aip.orchestration.perf import PerformanceProfiler

    class FakeTraceStore:
        async def write_event(self, **kwargs):
            pass

        async def query_events(self, **kwargs):
            return []

    config = PerformanceConfig(profiling_enabled=True, max_memory_mb=4096)
    profiler = PerformanceProfiler(config=config, trace_store=FakeTraceStore())
    metrics = await profiler.get_system_metrics()
    assert "cpu_percent" in metrics
    assert "memory_mb" in metrics
    assert metrics["profiling_enabled"] is True


async def test_real_profiler_slow_operations():
    """Integration: real PerformanceProfiler returns slow operations."""
    from aip.orchestration.perf import PerformanceProfiler

    class FakeTraceStore:
        async def write_event(self, **kwargs):
            pass

        async def query_events(self, **kwargs):
            return [
                {
                    "detail": "operation=slow_query, duration_ms=2500.0, error=None",
                    "session_id": "performance_profiler",
                },
            ]

    config = PerformanceConfig(profiling_enabled=True, max_memory_mb=4096)
    profiler = PerformanceProfiler(config=config, trace_store=FakeTraceStore())
    slow = await profiler.get_slow_operations(threshold_ms=1000)
    assert len(slow) == 1
    assert slow[0]["operation"] == "slow_query"
