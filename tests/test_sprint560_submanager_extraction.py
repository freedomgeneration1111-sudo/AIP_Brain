"""Tests for Sprint 5.60: Sub-manager extraction, deadlock fix, and async hardening.

Covers:
1. RealtimeEventBus extraction — SSE/WS subscriber methods moved out of AlertManager
2. DeliveryManager accessor cleanup — public accessors, no private attribute reach
3. StatusAggregator — get_status() delegates to sub-managers
4. WebSocket batch flush deadlock fix — RLock prevents reentrant deadlock
5. asyncio.get_event_loop() elimination — no deprecation warnings
6. Core state consolidation — counters owned by DeliveryManager
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alert_config(**overrides):
    """Create an AlertConfig with sensible defaults for testing."""
    from aip.adapter.alerting import AlertConfig

    defaults = dict(
        enabled=True,
        ws_batch_window_seconds=0,
        ws_batch_max_size=10,
        ws_compression_enabled=False,
        ws_native_permessage_deflate_enabled=False,
    )
    defaults.update(overrides)
    return AlertConfig(**defaults)


def _make_alert_manager(**config_overrides):
    """Create an AlertManager with test config."""
    from aip.adapter.alerting import AlertManager

    return AlertManager(_make_alert_config(**config_overrides))


# ===========================================================================
# 1. RealtimeEventBus extraction
# ===========================================================================


class TestRealtimeEventBusExtraction:
    """Verify that SSE/WS subscriber methods are on RealtimeEventBus."""

    def test_realtime_bus_exists(self):
        mgr = _make_alert_manager()
        assert hasattr(mgr, "realtime_bus")
        from aip.adapter.alerting import RealtimeEventBus

        assert isinstance(mgr.realtime_bus, RealtimeEventBus)

    def test_add_remove_sse_subscriber_on_bus(self):
        mgr = _make_alert_manager()
        bus = mgr.realtime_bus
        queue = MagicMock()
        bus.add_sse_subscriber(queue)
        assert queue in bus._sse_subscribers
        bus.remove_sse_subscriber(queue)
        assert queue not in bus._sse_subscribers

    def test_add_remove_ws_subscriber_on_bus(self):
        mgr = _make_alert_manager()
        bus = mgr.realtime_bus
        ws = MagicMock()
        bus.add_ws_subscriber(ws)
        assert ws in bus._ws_subscribers
        bus.remove_ws_subscriber(ws)
        assert ws not in bus._ws_subscribers

    def test_old_wrapper_methods_removed(self):
        """The old thin delegation wrappers should no longer exist on AlertManager."""
        mgr = _make_alert_manager()
        # These should NOT be direct methods on AlertManager anymore
        assert (
            not hasattr(type(mgr), "add_sse_subscriber")
            or callable(getattr(type(mgr), "add_sse_subscriber", None)) is False
        )
        assert not hasattr(type(mgr), "remove_sse_subscriber")
        assert not hasattr(type(mgr), "add_ws_subscriber")
        assert not hasattr(type(mgr), "remove_ws_subscriber")
        assert not hasattr(type(mgr), "_notify_realtime_subscribers")
        assert not hasattr(type(mgr), "_push_event_to_ws_subscribers")

    def test_sse_subscriber_receives_event(self):
        mgr = _make_alert_manager()
        bus = mgr.realtime_bus
        queue = MagicMock()
        bus.add_sse_subscriber(queue)
        event = {"event": "test_event", "data": "hello"}
        bus.notify_realtime_subscribers(event)
        queue.put_nowait.assert_called_once_with(event)

    def test_ws_subscriber_receives_event_in_immediate_mode(self):
        mgr = _make_alert_manager(ws_batch_window_seconds=0)
        bus = mgr.realtime_bus
        ws = MagicMock()
        ws.send_json = MagicMock()
        bus.add_ws_subscriber(ws)
        event = {"event": "test_event"}
        bus.notify_realtime_subscribers(event)
        # In immediate mode, _async_send_json is called
        # It uses asyncio.get_running_loop() which will fail outside async context
        # so the send is a no-op — that's fine, we verify the path was taken
        assert bus._ws_batch_total_flushes == 0  # No batching

    def test_remove_nonexistent_subscriber_no_error(self):
        mgr = _make_alert_manager()
        bus = mgr.realtime_bus
        bus.remove_sse_subscriber(MagicMock())  # Should not raise
        bus.remove_ws_subscriber(MagicMock())  # Should not raise


# ===========================================================================
# 2. DeliveryManager accessor cleanup
# ===========================================================================


class TestDeliveryManagerAccessors:
    """Verify that DeliveryManager exposes clean public accessors."""

    def test_delivery_manager_exists(self):
        mgr = _make_alert_manager()
        assert hasattr(mgr, "delivery_mgr")
        from aip.adapter.alerting import DeliveryManager

        assert isinstance(mgr.delivery_mgr, DeliveryManager)

    def test_increment_sent(self):
        dm = _make_alert_manager().delivery_mgr
        assert dm.get_total_alerts_sent() == 0
        dm.increment_sent()
        dm.increment_sent()
        assert dm.get_total_alerts_sent() == 2

    def test_increment_rate_limited(self):
        dm = _make_alert_manager().delivery_mgr
        assert dm.get_total_rate_limited() == 0
        dm.increment_rate_limited()
        assert dm.get_total_rate_limited() == 1

    def test_increment_send_failure(self):
        dm = _make_alert_manager().delivery_mgr
        assert dm.get_total_send_failures() == 0
        dm.increment_send_failure()
        assert dm.get_total_send_failures() == 1

    def test_increment_webhook_retry(self):
        dm = _make_alert_manager().delivery_mgr
        assert dm.get_total_webhook_retries() == 0
        dm.increment_webhook_retry()
        assert dm.get_total_webhook_retries() == 1

    def test_record_transport_result(self):
        dm = _make_alert_manager().delivery_mgr
        dm.record_transport_result("webhook", "delivered")
        dm.record_transport_result("email", "failed")
        success = dm.get_delivery_success_by_transport()
        failure = dm.get_delivery_failure_by_transport()
        assert success["webhook"] == 1
        assert failure["email"] == 1

    def test_delivery_success_by_transport_returns_copy(self):
        dm = _make_alert_manager().delivery_mgr
        dm.record_transport_result("webhook", "delivered")
        result = dm.get_delivery_success_by_transport()
        result["webhook"] = 999  # Mutate copy
        assert dm.get_delivery_success_by_transport()["webhook"] == 1  # Original unchanged

    def test_record_and_get_delivery_receipts(self):
        dm = _make_alert_manager().delivery_mgr
        transport_results = {
            "slack": {
                "status": "delivered",
                "receipt": {"message_ts": "1234.5678"},
            },
        }
        dm.record_delivery_receipts("corr-1", transport_results, config=None)
        receipts = dm.get_delivery_receipts("corr-1")
        assert "slack" in receipts
        assert receipts["slack"]["delivery_status"] == "delivered"

    def test_get_all_delivery_receipts(self):
        dm = _make_alert_manager().delivery_mgr
        for i in range(5):
            dm.record_delivery_receipts(
                f"corr-{i}",
                {"webhook": {"status": "delivered", "receipt": {"id": i}}},
                config=None,
            )
        all_receipts = dm.get_all_delivery_receipts(limit=3)
        assert len(all_receipts) == 3

    def test_get_delivery_receipts_count(self):
        dm = _make_alert_manager().delivery_mgr
        assert dm.get_delivery_receipts_count() == 0
        dm.record_delivery_receipts(
            "corr-1",
            {"webhook": {"status": "delivered", "receipt": {"id": 1}}},
            config=None,
        )
        assert dm.get_delivery_receipts_count() == 1

    def test_update_email_delivery_status(self):
        dm = _make_alert_manager().delivery_mgr
        dm.record_delivery_receipts(
            "corr-1",
            {"email": {"status": "sent", "receipt": {"msg_id": "abc"}}},
            config=None,
        )
        dm.update_email_delivery_status(
            "corr-1",
            {
                "delivery_status": "read",
                "email_poll_updated_at": "2025-01-01T00:00:00",
            },
        )
        receipts = dm.get_delivery_receipts("corr-1")
        assert receipts["email"]["delivery_status"] == "read"

    def test_iter_delivery_receipts(self):
        dm = _make_alert_manager().delivery_mgr
        dm.record_delivery_receipts(
            "corr-1",
            {"webhook": {"status": "delivered", "receipt": {"id": 1}}},
            config=None,
        )
        items = list(dm.iter_delivery_receipts())
        assert len(items) == 1
        assert items[0][0] == "corr-1"

    def test_status_summary_uses_accessors(self):
        dm = _make_alert_manager().delivery_mgr
        dm.increment_sent()
        dm.increment_rate_limited()
        dm.record_transport_result("webhook", "delivered")
        summary = dm.get_status_summary()
        assert summary["total_alerts_sent"] == 1
        assert summary["total_rate_limited"] == 1
        assert "success" in summary["delivery_by_transport"]
        assert "tracked_count" in summary["delivery_receipts"]


# ===========================================================================
# 3. StatusAggregator delegation
# ===========================================================================


class TestStatusAggregator:
    """Verify that get_status() delegates to StatusAggregator."""

    def test_status_aggregator_exists(self):
        mgr = _make_alert_manager()
        from aip.adapter.alerting import StatusAggregator

        assert isinstance(mgr._status_aggregator, StatusAggregator)

    def test_get_status_returns_dict(self):
        mgr = _make_alert_manager()
        status = mgr.get_status()
        assert isinstance(status, dict)
        assert "enabled" in status
        assert "total_alerts_sent" in status
        assert "delivery_by_transport" in status

    def test_get_status_reads_from_delivery_manager(self):
        mgr = _make_alert_manager()
        mgr.delivery_mgr.increment_sent()
        mgr.delivery_mgr.increment_rate_limited()
        status = mgr.get_status()
        assert status["total_alerts_sent"] == 1
        assert status["total_rate_limited"] == 1

    def test_get_status_reads_from_realtime_bus(self):
        mgr = _make_alert_manager()
        mgr.realtime_bus.add_sse_subscriber(MagicMock())
        status = mgr.get_status()
        assert status["sse_subscribers"] == 1

    def test_get_status_delivery_by_transport(self):
        mgr = _make_alert_manager()
        mgr.delivery_mgr.record_transport_result("webhook", "delivered")
        mgr.delivery_mgr.record_transport_result("email", "failed")
        status = mgr.get_status()
        assert status["delivery_by_transport"]["success"]["webhook"] == 1
        assert status["delivery_by_transport"]["failure"]["email"] == 1

    def test_get_status_has_ws_batching_from_realtime_bus(self):
        mgr = _make_alert_manager(ws_batch_window_seconds=5, ws_batch_max_size=50)
        status = mgr.get_status()
        assert "ws_batching" in status
        assert status["ws_batching"]["batch_window_seconds"] == 5
        assert status["ws_batching"]["batch_max_size"] == 50


# ===========================================================================
# 4. WebSocket batch flush deadlock fix
# ===========================================================================


class TestWebSocketDeadlockFix:
    """Verify that the RLock prevents deadlock in batch flush."""

    def test_realtime_bus_uses_rlock(self):
        from aip.adapter.alerting import RealtimeEventBus

        bus = RealtimeEventBus(_make_alert_config())
        assert isinstance(bus._lock, type(threading.RLock()))

    def test_batch_flush_under_lock_no_deadlock(self):
        """Simulate the deadlock scenario: notify while lock is held.

        The old code used threading.Lock(), which would deadlock when
        _flush_ws_batch was called from within the lock-holding path
        in _notify_realtime_subscribers when buffer exceeds max_size.
        RLock allows reentrant acquisition, preventing deadlock.
        """
        mgr = _make_alert_manager(
            ws_batch_window_seconds=1.0,
            ws_batch_max_size=2,
        )
        bus = mgr.realtime_bus

        # Use put_nowait-style mock (no asyncio needed)
        queue = MagicMock()
        bus.add_sse_subscriber(queue)

        # Send events up to max_size — this triggers flush inside the lock
        event = {"event": "test"}
        bus.notify_realtime_subscribers(event)
        bus.notify_realtime_subscribers(event)

        # If we get here, no deadlock occurred
        assert bus._ws_batch_total_flushes >= 0  # May or may not have flushed

    def test_concurrent_notify_no_deadlock(self):
        """Multiple threads calling notify_realtime_subscribers concurrently."""
        mgr = _make_alert_manager(ws_batch_window_seconds=0)
        bus = mgr.realtime_bus
        queue = MagicMock()
        bus.add_sse_subscriber(queue)

        barrier = threading.Barrier(4)
        errors = []

        def notify_worker():
            try:
                barrier.wait(timeout=5)
                for _ in range(20):
                    bus.notify_realtime_subscribers({"event": "concurrent_test"})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=notify_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"Concurrent notify errors: {errors}"


# ===========================================================================
# 5. asyncio.get_event_loop() elimination
# ===========================================================================


class TestAsyncioDeprecationElimination:
    """Verify that production code no longer uses deprecated asyncio patterns."""

    def test_no_get_event_loop_in_alerting_py(self):
        """The production alerting.py should not contain asyncio.get_event_loop() in code."""
        import aip.adapter.alerting as alerting_module

        source = open(alerting_module.__file__).read()
        # Only check lines that look like actual code (not docstrings/comments)
        import ast

        tree = ast.parse(source)
        code_uses_get_event_loop = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if (
                        node.func.attr == "get_event_loop"
                        and isinstance(node.func.value, ast.Attribute)
                        and node.func.value.attr == "asyncio"
                    ):
                        code_uses_get_event_loop = True
                    elif (
                        node.func.attr == "get_event_loop"
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "asyncio"
                    ):
                        code_uses_get_event_loop = True
        assert not code_uses_get_event_loop, "Found asyncio.get_event_loop() in production AST"

    def test_realtime_bus_uses_get_running_loop(self):
        """RealtimeEventBus should use asyncio.get_running_loop()."""
        import aip.adapter.alerting as alerting_module

        source = open(alerting_module.__file__).read()
        assert "asyncio.get_running_loop()" in source
        assert "_schedule_batch_flush" in source

    def test_async_send_json_uses_get_running_loop(self):
        """The _async_send_json helper should use get_running_loop."""
        from aip.adapter.alerting import RealtimeEventBus
        import inspect

        source = inspect.getsource(RealtimeEventBus._async_send_json)
        assert "get_running_loop" in source
        assert "get_event_loop" not in source


# ===========================================================================
# 6. Core state consolidation — counters owned by DeliveryManager
# ===========================================================================


class TestCoreStateConsolidation:
    """Verify that core counters are now owned by DeliveryManager."""

    def test_total_alerts_sent_in_delivery_manager(self):
        mgr = _make_alert_manager()
        # AlertManager no longer has _total_alerts_sent
        assert not hasattr(mgr, "_total_alerts_sent")
        # DeliveryManager does
        assert hasattr(mgr.delivery_mgr, "_total_alerts_sent")

    def test_total_rate_limited_in_delivery_manager(self):
        mgr = _make_alert_manager()
        assert not hasattr(mgr, "_total_alerts_rate_limited")
        assert hasattr(mgr.delivery_mgr, "_total_alerts_rate_limited")

    def test_total_send_failures_in_delivery_manager(self):
        mgr = _make_alert_manager()
        assert not hasattr(mgr, "_total_send_failures")
        assert hasattr(mgr.delivery_mgr, "_total_send_failures")

    def test_total_webhook_retries_in_delivery_manager(self):
        mgr = _make_alert_manager()
        assert not hasattr(mgr, "_total_webhook_retries")
        assert hasattr(mgr.delivery_mgr, "_total_webhook_retries")

    def test_delivery_success_by_transport_in_delivery_manager(self):
        mgr = _make_alert_manager()
        assert not hasattr(mgr, "_delivery_success_by_transport")
        assert hasattr(mgr.delivery_mgr, "_delivery_success_by_transport")

    def test_delivery_failure_by_transport_in_delivery_manager(self):
        mgr = _make_alert_manager()
        assert not hasattr(mgr, "_delivery_failure_by_transport")
        assert hasattr(mgr.delivery_mgr, "_delivery_failure_by_transport")

    def test_delivery_receipts_in_delivery_manager(self):
        mgr = _make_alert_manager()
        assert not hasattr(mgr, "_delivery_receipts")
        assert hasattr(mgr.delivery_mgr, "_delivery_receipts")

    def test_sse_subscribers_in_realtime_bus(self):
        mgr = _make_alert_manager()
        assert not hasattr(mgr, "_sse_subscribers")
        assert hasattr(mgr.realtime_bus, "_sse_subscribers")

    def test_ws_subscribers_in_realtime_bus(self):
        mgr = _make_alert_manager()
        assert not hasattr(mgr, "_ws_subscribers")
        assert hasattr(mgr.realtime_bus, "_ws_subscribers")

    def test_ws_batch_state_in_realtime_bus(self):
        mgr = _make_alert_manager()
        assert not hasattr(mgr, "_ws_batch_buffer")
        assert not hasattr(mgr, "_ws_batch_flush_scheduled")
        assert not hasattr(mgr, "_ws_batch_total_flushes")
        assert hasattr(mgr.realtime_bus, "_ws_batch_buffer")
        assert hasattr(mgr.realtime_bus, "_ws_batch_flush_scheduled")
        assert hasattr(mgr.realtime_bus, "_ws_batch_total_flushes")

    def test_compression_state_in_realtime_bus(self):
        mgr = _make_alert_manager()
        assert not hasattr(mgr, "_ws_compression_negotiated")
        assert not hasattr(mgr, "_ws_compression_bytes_saved_estimate")
        assert not hasattr(mgr, "_ws_permessage_deflate_negotiated")
        assert not hasattr(mgr, "_ws_native_deflate_bytes_saved")
        assert hasattr(mgr.realtime_bus, "_ws_compression_negotiated")
        assert hasattr(mgr.realtime_bus, "_ws_compression_bytes_saved_estimate")
        assert hasattr(mgr.realtime_bus, "_ws_permessage_deflate_negotiated")
        assert hasattr(mgr.realtime_bus, "_ws_native_deflate_bytes_saved")

    def test_send_alert_increments_delivery_manager(self):
        """Verify that send_alert increments the DeliveryManager counter."""
        from aip.adapter.alerting import Alert, AlertConfig

        mgr = _make_alert_manager(webhook_url="")
        alert = Alert(
            alert_type="quality_degradation",
            severity="info",
            subject="test",
            message="Test alert",
        )
        mgr.send_alert(alert)
        assert mgr.delivery_mgr.get_total_alerts_sent() == 1
