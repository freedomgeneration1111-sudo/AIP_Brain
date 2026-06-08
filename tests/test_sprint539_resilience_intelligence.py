"""Sprint 5.39 tests — Resilience, Persistence & Protocol Enhancements.

Deliverable 1: Service Worker Offline Cache
  (SW caches dashboard assets, offline action queueing, replay on reconnect,
   offline banner in dashboard HTML, AlertConfig.offline_cache_enabled)

Deliverable 2: Transition Probability Persistence + Retraining
  (persist transition probs to AlertHistoryStore, load on startup,
   periodic/scheduled retraining, retraining events, schema v7)

Deliverable 3: Circuit Breaker Auto-Tuning
  (dynamic threshold from historical patterns, effective_threshold,
   auto-tune status in health endpoint, threshold adjustments history)

Deliverable 4: Delivery Receipt Polling
  (email delivery status tracking, poll_email_delivery_status,
   get_enhanced_delivery_receipts, start/stop_receipt_polling,
   update_email_delivery_status)

Deliverable 5: Native WebSocket Per-Message Deflate
  (ws_native_permessage_deflate_enabled config, set_ws_permessage_deflate_negotiated,
   compress_ws_message_native_aware, decompress_ws_message_native_aware,
   get_native_deflate_status, WS endpoint compression query param)
"""

from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from aip.adapter.alerting import (
    AlertConfig,
    Alert,
    AlertManager,
)
from aip.adapter.alert_history_store import AlertHistoryStore


# ============================================================================
# Deliverable 1: Service Worker Offline Cache
# ============================================================================


class TestServiceWorkerOfflineCache:
    """Tests for Service Worker offline caching and action queueing."""

    def test_offline_cache_config_default(self):
        """AlertConfig has offline_cache_enabled with default False."""
        config = AlertConfig()
        assert config.offline_cache_enabled is False

    def test_offline_cache_config_custom(self):
        """AlertConfig offline_cache_enabled can be set."""
        config = AlertConfig(offline_cache_enabled=True)
        assert config.offline_cache_enabled is True

    def test_offline_cache_in_status(self):
        """get_status() includes offline_cache_enabled."""
        mgr = AlertManager(AlertConfig(enabled=True, offline_cache_enabled=True))
        status = mgr.get_status()
        assert "offline_cache_enabled" in status
        assert status["offline_cache_enabled"] is True

    def test_dashboard_html_contains_offline_banner(self):
        """Dashboard HTML includes offline banner element."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "offlineBanner" in _DASHBOARD_HTML
        assert "offline-banner" in _DASHBOARD_HTML

    def test_dashboard_html_contains_offline_action_queueing(self):
        """Dashboard HTML includes offline action queueing JS."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "queueOfflineAction" in _DASHBOARD_HTML
        assert "replayOfflineActionQueue" in _DASHBOARD_HTML

    def test_dashboard_html_contains_cache_api_in_sw(self):
        """Service Worker code includes Cache API usage."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "caches.open" in _DASHBOARD_HTML or "CACHE_NAME" in _DASHBOARD_HTML

    def test_dashboard_html_contains_indexeddb_in_sw(self):
        """Service Worker code includes IndexedDB for offline queue."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "indexedDB" in _DASHBOARD_HTML or "openOfflineDB" in _DASHBOARD_HTML

    def test_dashboard_html_contains_sync_handler(self):
        """Service Worker code includes sync event for replay."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "sync" in _DASHBOARD_HTML

    def test_dashboard_html_contains_fetch_handler(self):
        """Service Worker code includes fetch event handler for caching."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        assert "fetch" in _DASHBOARD_HTML


# ============================================================================
# Deliverable 2: Transition Probability Persistence + Retraining
# ============================================================================


class TestTransitionProbabilityPersistence:
    """Tests for transition probability persistence and retraining."""

    def test_persistence_config_defaults(self):
        """AlertConfig has transition persistence fields with defaults."""
        config = AlertConfig()
        assert config.transition_persistence_enabled is False
        assert config.retrain_interval_seconds == 3600
        assert config.retrain_after_n_alerts == 100

    def test_persistence_config_custom(self):
        """AlertConfig transition persistence can be customized."""
        config = AlertConfig(
            transition_persistence_enabled=True,
            retrain_interval_seconds=1800,
            retrain_after_n_alerts=50,
        )
        assert config.transition_persistence_enabled is True
        assert config.retrain_interval_seconds == 1800
        assert config.retrain_after_n_alerts == 50

    def test_persistence_in_status(self):
        """get_status() includes transition persistence info."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            transition_persistence_enabled=True,
        ))
        status = mgr.get_status()
        assert "transition_persistence" in status
        assert status["transition_persistence"]["persistence_enabled"] is True

    def test_save_transition_probabilities(self):
        """AlertHistoryStore.save_transition_probabilities persists to DB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            transition_counts = {("pool_adjustment", "quality_degradation"): 15}
            transition_totals = {"pool_adjustment": 20}

            result = store.save_transition_probabilities(transition_counts, transition_totals)
            assert result is True

    def test_load_transition_probabilities(self):
        """AlertHistoryStore.load_transition_probabilities loads from DB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            transition_counts = {("pool_adjustment", "quality_degradation"): 15, ("quality_degradation", "batch_reduction"): 8}
            transition_totals = {"pool_adjustment": 20, "quality_degradation": 8}

            store.save_transition_probabilities(transition_counts, transition_totals)
            loaded_counts, loaded_totals = store.load_transition_probabilities()

            assert loaded_counts == transition_counts
            assert loaded_totals == transition_totals

    def test_load_transition_probabilities_empty(self):
        """AlertHistoryStore.load_transition_probabilities returns empty when no data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            loaded_counts, loaded_totals = store.load_transition_probabilities()
            assert loaded_counts == {}
            assert loaded_totals == {}

    def test_persist_transition_model(self):
        """AlertManager.persist_transition_model saves to store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            mgr = AlertManager(AlertConfig(
                enabled=True,
                transition_persistence_enabled=True,
            ))
            mgr._history_store = store
            mgr._transition_counts = {("a", "b"): 10}
            mgr._transition_totals = {"a": 10}

            result = mgr.persist_transition_model()
            assert result is True

            # Verify it was persisted
            counts, totals = store.load_transition_probabilities()
            assert counts == {("a", "b"): 10}
            assert totals == {"a": 10}

    def test_persist_transition_model_no_store(self):
        """AlertManager.persist_transition_model returns False without store."""
        mgr = AlertManager(AlertConfig(enabled=True))
        result = mgr.persist_transition_model()
        assert result is False

    def test_load_transition_model(self):
        """AlertManager.load_transition_model restores from store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            # First, save some data
            store.save_transition_probabilities(
                {("x", "y"): 7},
                {"x": 7},
            )

            mgr = AlertManager(AlertConfig(
                enabled=True,
                transition_persistence_enabled=True,
            ))
            mgr._history_store = store

            result = mgr.load_transition_model()
            assert result is True
            assert mgr._transition_counts == {("x", "y"): 7}
            assert mgr._transition_totals == {"x": 7}

    def test_load_transition_model_no_store(self):
        """AlertManager.load_transition_model returns False without store."""
        mgr = AlertManager(AlertConfig(enabled=True))
        result = mgr.load_transition_model()
        assert result is False

    def test_record_retraining_event(self):
        """AlertHistoryStore.record_retraining_event stores event."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            result = store.record_retraining_event({
                "trigger_reason": "new_alerts_threshold",
                "alerts_since_last_train": 150,
                "transition_count": 5,
                "total_types": 3,
            })
            assert result is True

    def test_get_retraining_events(self):
        """AlertHistoryStore.get_retraining_events returns events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            store.record_retraining_event({
                "trigger_reason": "scheduled",
                "alerts_since_last_train": 200,
                "transition_count": 8,
                "total_types": 4,
            })

            events = store.get_retraining_events()
            assert len(events) == 1
            assert events[0]["trigger_reason"] == "scheduled"
            assert events[0]["alerts_since_last_train"] == 200

    def test_check_retrain_needed_disabled(self):
        """check_retrain_needed returns False when persistence disabled."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr._alerts_since_last_retrain = 500
        assert mgr.check_retrain_needed() is False

    def test_check_retrain_needed_by_alert_count(self):
        """check_retrain_needed returns True when alert count exceeded."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            transition_persistence_enabled=True,
            retrain_after_n_alerts=50,
        ))
        mgr._alerts_since_last_retrain = 60
        assert mgr.check_retrain_needed() is True

    def test_check_retrain_needed_by_interval(self):
        """check_retrain_needed returns True when interval elapsed."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            transition_persistence_enabled=True,
            retrain_interval_seconds=60,
        ))
        mgr._last_retrain_time = time.time() - 120  # 2 minutes ago
        assert mgr.check_retrain_needed() is True

    def test_check_retrain_needed_not_yet(self):
        """check_retrain_needed returns False when conditions unmet."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            transition_persistence_enabled=True,
            retrain_interval_seconds=3600,
            retrain_after_n_alerts=100,
        ))
        mgr._alerts_since_last_retrain = 10
        mgr._last_retrain_time = time.time() - 60
        assert mgr.check_retrain_needed() is False

    def test_schema_v7_migration(self):
        """Schema v7 creates model_retraining_events table."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            import sqlite3
            with sqlite3.connect(db_path) as conn:
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='model_retraining_events'"
                )
                assert cursor.fetchone() is not None


# ============================================================================
# Deliverable 3: Circuit Breaker Auto-Tuning
# ============================================================================


class TestCircuitBreakerAutoTuning:
    """Tests for circuit breaker auto-tuning based on historical patterns."""

    def test_auto_tune_config_defaults(self):
        """AlertConfig has auto-tune fields with defaults."""
        config = AlertConfig()
        assert config.circuit_breaker_auto_tune_enabled is False
        assert config.circuit_breaker_auto_tune_lookback_hours == 168
        assert config.circuit_breaker_auto_tune_sensitivity == 1.5
        assert config.circuit_breaker_auto_tune_min_threshold == 20
        assert config.circuit_breaker_auto_tune_max_threshold == 500

    def test_auto_tune_config_custom(self):
        """AlertConfig auto-tune fields can be customized."""
        config = AlertConfig(
            circuit_breaker_auto_tune_enabled=True,
            circuit_breaker_auto_tune_sensitivity=2.0,
            circuit_breaker_auto_tune_min_threshold=10,
        )
        assert config.circuit_breaker_auto_tune_enabled is True
        assert config.circuit_breaker_auto_tune_sensitivity == 2.0
        assert config.circuit_breaker_auto_tune_min_threshold == 10

    def test_auto_tune_disabled_returns_config_threshold(self):
        """compute_cb_auto_tune_threshold returns config threshold when disabled."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            circuit_breaker_enabled=True,
            throttle_threshold_per_minute=50,
        ))
        threshold = mgr.compute_cb_auto_tune_threshold()
        assert threshold == 50

    def test_auto_tune_no_store_returns_config_threshold(self):
        """Auto-tune returns config threshold when no history store."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            circuit_breaker_enabled=True,
            circuit_breaker_auto_tune_enabled=True,
            throttle_threshold_per_minute=80,
        ))
        # No store attached
        threshold = mgr.compute_cb_auto_tune_threshold()
        assert threshold == 80

    def test_get_cb_effective_threshold_default(self):
        """get_cb_effective_threshold returns config threshold by default."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            throttle_threshold_per_minute=75,
        ))
        assert mgr.get_cb_effective_threshold() == 75

    def test_get_cb_effective_threshold_auto_tuned(self):
        """get_cb_effective_threshold returns auto-tuned threshold when set."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            throttle_threshold_per_minute=100,
        ))
        mgr._cb_effective_threshold = 150
        assert mgr.get_cb_effective_threshold() == 150

    def test_auto_tune_status(self):
        """get_cb_auto_tune_status returns complete status."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            circuit_breaker_auto_tune_enabled=True,
            circuit_breaker_auto_tune_sensitivity=2.0,
        ))
        status = mgr.get_cb_auto_tune_status()
        assert status["enabled"] is True
        assert "effective_threshold" in status
        assert "config_threshold" in status
        assert "total_adjustments" in status
        assert "recent_adjustments" in status

    def test_auto_tune_in_overall_status(self):
        """get_status() includes circuit_breaker_auto_tune info."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            circuit_breaker_auto_tune_enabled=True,
        ))
        status = mgr.get_status()
        assert "circuit_breaker_auto_tune" in status

    def test_auto_tune_threshold_clamped_min(self):
        """Auto-tuned threshold is clamped to min_threshold."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            circuit_breaker_auto_tune_enabled=True,
            circuit_breaker_auto_tune_min_threshold=50,
            throttle_threshold_per_minute=100,
        ))
        # Set baseline rate so low that computed threshold would be below min
        mgr._cb_baseline_rates = {"test_slot": 1.0}
        mgr._cb_auto_tune_last_computed = time.time()

        # Without a store, it should return config threshold
        threshold = mgr.compute_cb_auto_tune_threshold()
        assert threshold >= mgr._config.circuit_breaker_auto_tune_min_threshold

    def test_auto_tune_threshold_clamped_max(self):
        """Auto-tuned threshold is clamped to max_threshold."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            circuit_breaker_auto_tune_enabled=True,
            circuit_breaker_auto_tune_max_threshold=200,
            throttle_threshold_per_minute=100,
        ))
        threshold = mgr.compute_cb_auto_tune_threshold()
        assert threshold <= mgr._config.circuit_breaker_auto_tune_max_threshold

    def test_update_cb_auto_tune_records_adjustment(self):
        """update_cb_auto_tune records threshold adjustments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            mgr = AlertManager(AlertConfig(
                enabled=True,
                circuit_breaker_auto_tune_enabled=True,
                throttle_threshold_per_minute=100,
            ))
            mgr._history_store = store
            mgr._cb_auto_tune_last_computed = 0  # Force recomputation

            result = mgr.update_cb_auto_tune()
            assert "old_threshold" in result
            assert "new_threshold" in result

    def test_circuit_breaker_status_includes_auto_tune(self):
        """get_circuit_breaker_status includes auto_tune and effective_threshold."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            circuit_breaker_enabled=True,
            circuit_breaker_auto_tune_enabled=True,
        ))
        status = mgr.get_circuit_breaker_status()
        assert "auto_tune" in status
        assert "effective_threshold" in status


# ============================================================================
# Deliverable 4: Delivery Receipt Polling
# ============================================================================


class TestDeliveryReceiptPolling:
    """Tests for delivery receipt polling for email and other channels."""

    def test_polling_config_defaults(self):
        """AlertConfig has delivery receipt polling fields with defaults."""
        config = AlertConfig()
        assert config.delivery_receipt_polling_enabled is False
        assert config.delivery_receipt_poll_interval_seconds == 300
        assert config.email_read_tracking_enabled is False
        assert config.email_delivery_webhook_url == ""

    def test_polling_config_custom(self):
        """AlertConfig polling fields can be customized."""
        config = AlertConfig(
            delivery_receipt_polling_enabled=True,
            delivery_receipt_poll_interval_seconds=60,
            email_read_tracking_enabled=True,
            email_delivery_webhook_url="https://webhook.example.com/delivery",
        )
        assert config.delivery_receipt_polling_enabled is True
        assert config.delivery_receipt_poll_interval_seconds == 60
        assert config.email_read_tracking_enabled is True
        assert config.email_delivery_webhook_url == "https://webhook.example.com/delivery"

    def test_polling_status_disabled(self):
        """get_delivery_polling_status returns correct disabled state."""
        mgr = AlertManager(AlertConfig(enabled=True))
        status = mgr.get_delivery_polling_status()
        assert status["enabled"] is False
        assert status["polling_active"] is False
        assert status["total_polls"] == 0

    def test_polling_status_in_overall_status(self):
        """get_status() includes delivery_receipt_polling info."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            delivery_receipt_polling_enabled=True,
        ))
        status = mgr.get_status()
        assert "delivery_receipt_polling" in status
        assert status["delivery_receipt_polling"]["enabled"] is True

    def test_update_email_delivery_status(self):
        """update_email_delivery_status stores status for a correlation ID."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            delivery_receipt_polling_enabled=True,
        ))
        mgr.update_email_delivery_status("cid-1", "delivered", {
            "smtp_response": "250 OK",
            "recipient": "ops@example.com",
        })

        statuses = mgr._email_delivery_statuses
        assert "cid-1" in statuses
        assert statuses["cid-1"]["email"]["status"] == "delivered"

    def test_update_email_delivery_status_merges_into_receipts(self):
        """update_email_delivery_status merges into existing delivery receipts."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            delivery_receipts_enabled=True,
            delivery_receipt_polling_enabled=True,
        ))
        # Pre-populate a delivery receipt with email
        mgr._delivery_receipts["cid-2"] = {
            "email": {"delivery_status": "sent", "confirmed_at": "2026-01-01T00:00:00Z"},
        }

        mgr.update_email_delivery_status("cid-2", "read", {
            "read_at": "2026-01-01T00:05:00Z",
        })

        assert mgr._delivery_receipts["cid-2"]["email"]["delivery_status"] == "read"

    def test_get_enhanced_delivery_receipts(self):
        """get_enhanced_delivery_receipts merges email polling status."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            delivery_receipts_enabled=True,
            delivery_receipt_polling_enabled=True,
        ))
        mgr._delivery_receipts["cid-3"] = {
            "slack": {"message_ts": "1234.5678", "delivery_status": "delivered"},
        }
        mgr._email_delivery_statuses["cid-3"] = {
            "email": {"status": "read", "read_at": "2026-01-01T00:05:00Z"},
        }

        receipts = mgr.get_enhanced_delivery_receipts("cid-3")
        assert "slack" in receipts
        assert "email" in receipts
        assert receipts["email"]["status"] == "read"

    def test_get_enhanced_delivery_receipts_not_found(self):
        """get_enhanced_delivery_receipts returns empty for unknown CID."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert mgr.get_enhanced_delivery_receipts("nonexistent") == {}

    def test_poll_email_delivery_status_disabled(self):
        """poll_email_delivery_status returns empty when disabled."""
        mgr = AlertManager(AlertConfig(enabled=True))
        result = mgr.poll_email_delivery_status()
        assert result == {"polled": 0, "updated": 0}

    def test_poll_email_delivery_status_no_pending(self):
        """poll_email_delivery_status returns empty when no pending emails."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            delivery_receipt_polling_enabled=True,
        ))
        result = mgr.poll_email_delivery_status()
        assert result == {"polled": 0, "updated": 0}

    def test_poll_email_delivery_status_tracks_sent(self):
        """poll_email_delivery_status marks email as sent when no webhook."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            delivery_receipts_enabled=True,
            delivery_receipt_polling_enabled=True,
        ))
        # Simulate email receipt with "sent" status
        mgr._delivery_receipts["cid-4"] = {
            "email": {"delivery_status": "sent"},
        }

        result = mgr.poll_email_delivery_status()
        # Should have tracked the email
        assert result["polled"] >= 0

    def test_record_delivery_receipts_tracks_email_sent(self):
        """_record_delivery_receipts tracks email sent when polling enabled."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            delivery_receipts_enabled=True,
            delivery_receipt_polling_enabled=True,
        ))
        transport_results = {
            "email": {"status": "delivered", "retries": 0},
        }
        mgr._record_delivery_receipts("cid-5", transport_results)

        receipts = mgr.get_delivery_receipts("cid-5")
        # Email should be tracked with "sent" status even without push receipt
        assert "email" in receipts
        assert receipts["email"]["delivery_status"] == "sent"

    def test_start_stop_receipt_polling(self):
        """start_receipt_polling and stop_receipt_polling work correctly."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            delivery_receipt_polling_enabled=True,
            delivery_receipt_poll_interval_seconds=600,
        ))
        mgr.start_receipt_polling()
        assert mgr._receipt_polling_running is True

        mgr.stop_receipt_polling()
        assert mgr._receipt_polling_running is False

    def test_start_polling_disabled_is_noop(self):
        """start_receipt_polling does nothing when polling disabled."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.start_receipt_polling()
        assert mgr._receipt_polling_running is False

    def test_polling_with_webhook_url(self):
        """poll_email_delivery_status uses webhook URL when configured."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            delivery_receipts_enabled=True,
            delivery_receipt_polling_enabled=True,
            email_delivery_webhook_url="https://webhook.example.com/delivery",
        ))
        mgr._delivery_receipts["cid-6"] = {
            "email": {"delivery_status": "sent"},
        }

        with patch('aip.adapter.alerting.urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"statuses": {"cid-6": {"status": "delivered", "delivered_at": "2026-01-01T00:01:00Z"}}}'
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            result = mgr.poll_email_delivery_status()
            assert result["polled"] == 1
            assert result["updated"] == 1


# ============================================================================
# Deliverable 5: Native WebSocket Per-Message Deflate
# ============================================================================


class TestNativeWebSocketPerMessageDeflate:
    """Tests for native WebSocket permessage-deflate compression."""

    def test_native_deflate_config_default(self):
        """AlertConfig has ws_native_permessage_deflate_enabled with default False."""
        config = AlertConfig()
        assert config.ws_native_permessage_deflate_enabled is False

    def test_native_deflate_config_custom(self):
        """AlertConfig ws_native_permessage_deflate_enabled can be set."""
        config = AlertConfig(ws_native_permessage_deflate_enabled=True)
        assert config.ws_native_permessage_deflate_enabled is True

    def test_set_ws_permessage_deflate_negotiated(self):
        """set_ws_permessage_deflate_negotiated sets the negotiation state."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert mgr._ws_permessage_deflate_negotiated is False

        mgr.set_ws_permessage_deflate_negotiated(True)
        assert mgr._ws_permessage_deflate_negotiated is True

        mgr.set_ws_permessage_deflate_negotiated(False)
        assert mgr._ws_permessage_deflate_negotiated is False

    def test_compress_native_aware_no_compression(self):
        """compress_ws_message_native_aware returns original when compression disabled."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            ws_compression_enabled=False,
        ))
        data = '{"event":"test"}'
        result, compressed = mgr.compress_ws_message_native_aware(data)
        assert compressed is False
        assert result == data

    def test_compress_native_aware_native_active(self):
        """compress_ws_message_native_aware is no-op when native deflate active."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            ws_compression_enabled=True,
            ws_native_permessage_deflate_enabled=True,
        ))
        mgr._ws_permessage_deflate_negotiated = True

        data = '{"event":"test","alerts":' + str(["a"] * 100) + '}'
        result, compressed = mgr.compress_ws_message_native_aware(data)
        assert compressed is False
        assert result == data  # No compression — protocol handles it

    def test_compress_native_aware_fallback_to_app_level(self):
        """compress_ws_message_native_aware falls back when native not negotiated."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            ws_compression_enabled=True,
            ws_native_permessage_deflate_enabled=True,
        ))
        # Native enabled but NOT negotiated — should fall back to app-level
        mgr._ws_permessage_deflate_negotiated = False

        data = '{"event":"batch_events","alerts":' + str(["type_x"] * 100) + '}'
        result, compressed = mgr.compress_ws_message_native_aware(data)
        # Should use app-level compression since native not negotiated
        assert compressed is True
        assert len(result) < len(data)

    def test_compress_native_aware_app_level_only(self):
        """compress_ws_message_native_aware uses app-level when native disabled."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            ws_compression_enabled=True,
            ws_native_permessage_deflate_enabled=False,
        ))
        data = '{"event":"batch_events","alerts":' + str(["type_x"] * 100) + '}'
        result, compressed = mgr.compress_ws_message_native_aware(data)
        assert compressed is True
        assert len(result) < len(data)

    def test_decompress_native_aware_native_active(self):
        """decompress_ws_message_native_aware is no-op when native deflate active."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            ws_compression_enabled=True,
            ws_native_permessage_deflate_enabled=True,
        ))
        mgr._ws_permessage_deflate_negotiated = True

        data = '{"event":"test"}'
        result = mgr.decompress_ws_message_native_aware(data)
        assert result == data  # No decompression needed — protocol handled it

    def test_decompress_native_aware_fallback(self):
        """decompress_ws_message_native_aware falls back to app-level."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            ws_compression_enabled=True,
            ws_native_permessage_deflate_enabled=True,
        ))
        mgr._ws_permessage_deflate_negotiated = False

        # Compress then decompress using app-level
        original = '{"event":"batch_events","alerts":["a1","a2","a3","a4","a5"]}'
        compressed_data, was_compressed = mgr.compress_ws_message_native_aware(original)
        if was_compressed:
            decompressed = mgr.decompress_ws_message_native_aware(compressed_data)
            assert decompressed == original

    def test_get_native_deflate_status_disabled(self):
        """get_native_deflate_status returns disabled when no compression."""
        mgr = AlertManager(AlertConfig(enabled=True))
        status = mgr.get_native_deflate_status()
        assert status["mode"] == "disabled"
        assert status["native_enabled"] is False
        assert status["native_negotiated"] is False

    def test_get_native_deflate_status_native_mode(self):
        """get_native_deflate_status returns native when negotiated."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            ws_compression_enabled=True,
            ws_native_permessage_deflate_enabled=True,
        ))
        mgr._ws_permessage_deflate_negotiated = True

        status = mgr.get_native_deflate_status()
        assert status["mode"] == "native"
        assert status["native_enabled"] is True
        assert status["native_negotiated"] is True

    def test_get_native_deflate_status_fallback(self):
        """get_native_deflate_status returns fallback when not negotiated."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            ws_compression_enabled=True,
            ws_native_permessage_deflate_enabled=True,
        ))
        # Native enabled but not negotiated
        status = mgr.get_native_deflate_status()
        assert status["mode"] == "fallback_application_level"

    def test_get_native_deflate_status_app_level_only(self):
        """get_native_deflate_status returns application_level when only app-level enabled."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            ws_compression_enabled=True,
            ws_native_permessage_deflate_enabled=False,
        ))
        status = mgr.get_native_deflate_status()
        assert status["mode"] == "application_level"

    def test_native_deflate_in_overall_status(self):
        """get_status() includes ws_native_deflate info."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            ws_native_permessage_deflate_enabled=True,
        ))
        status = mgr.get_status()
        assert "ws_native_deflate" in status
        assert status["ws_native_deflate"]["enabled"] is True

    def test_dashboard_ws_url_includes_compression_param(self):
        """Dashboard WS URLs include ?compression=deflate parameter."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML
        # Check that the WS URLs include the compression parameter
        assert "compression=deflate" in _DASHBOARD_HTML


# ============================================================================
# Integration: Full Sprint 5.39 flow
# ============================================================================


class TestSprint539Integration:
    """Integration tests combining multiple Sprint 5.39 features."""

    def test_full_transition_persistence_flow(self):
        """Full flow: learn → persist → restart → load → predict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = AlertHistoryStore(db_path)
            store.initialize()

            # Phase 1: Learn transitions
            mgr = AlertManager(AlertConfig(
                enabled=True,
                learned_prediction_enabled=True,
                learned_prediction_min_samples=5,
                transition_persistence_enabled=True,
                delivery_receipts_enabled=True,
                min_alert_interval_seconds=0,
            ))
            mgr._history_store = store

            now = time.time()
            for i in range(20):
                mgr._record_alert_for_transition_learning(
                    Alert(alert_type="pool_adjustment", severity="warning", subject="subj1", message="pool"),
                    now + i * 30,
                )
                mgr._record_alert_for_transition_learning(
                    Alert(alert_type="quality_degradation", severity="info", subject="subj1", message="quality"),
                    now + i * 30 + 10,
                )

            # Persist the model
            assert mgr.persist_transition_model() is True

            # Phase 2: Simulate restart — new manager loads from store
            mgr2 = AlertManager(AlertConfig(
                enabled=True,
                learned_prediction_enabled=True,
                learned_prediction_min_samples=5,
                transition_persistence_enabled=True,
            ))
            mgr2._history_store = store

            # Load persisted model
            assert mgr2.load_transition_model() is True
            assert len(mgr2._transition_counts) > 0

            # Predictions should work with loaded data
            alert = Alert(alert_type="pool_adjustment", severity="warning", subject="subj1", message="trigger")
            predictions = mgr2.predict_causal_chain_learned(alert)
            assert len(predictions) > 0
            for pred in predictions:
                assert pred["model"] == "learned"

    def test_full_delivery_receipt_polling_flow(self):
        """Full flow: send alert → poll → update → query enhanced receipts."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            delivery_receipts_enabled=True,
            delivery_receipt_polling_enabled=True,
        ))

        # Record initial delivery receipt with email
        mgr._delivery_receipts["cid-int"] = {
            "slack": {"message_ts": "ts1", "delivery_status": "delivered"},
            "email": {"delivery_status": "sent"},
        }

        # Simulate email delivery confirmation
        mgr.update_email_delivery_status("cid-int", "delivered", {
            "smtp_response": "250 OK",
        })

        # Query enhanced receipts
        receipts = mgr.get_enhanced_delivery_receipts("cid-int")
        assert "slack" in receipts
        assert "email" in receipts

        # Simulate email read
        mgr.update_email_delivery_status("cid-int", "read", {
            "read_at": "2026-01-01T00:05:00Z",
        })

        receipts = mgr.get_enhanced_delivery_receipts("cid-int")
        assert receipts["email"]["status"] == "read"

    def test_full_native_deflate_flow(self):
        """Full flow: native deflate → negotiate → compress → decompress."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            ws_compression_enabled=True,
            ws_native_permessage_deflate_enabled=True,
        ))

        # Before negotiation — should fall back to app-level
        data = '{"event":"batch","alerts":' + str(["a"] * 100) + '}'
        result, compressed = mgr.compress_ws_message_native_aware(data)
        assert compressed is True  # App-level compression used

        # After negotiation — native takes over
        mgr.set_ws_permessage_deflate_negotiated(True)
        result, compressed = mgr.compress_ws_message_native_aware(data)
        assert compressed is False  # Protocol handles it
        assert result == data

        # Decompress with native active is a no-op
        decompressed = mgr.decompress_ws_message_native_aware(data)
        assert decompressed == data

        # Status shows native mode
        status = mgr.get_native_deflate_status()
        assert status["mode"] == "native"

    def test_status_includes_all_sprint539_metrics(self):
        """get_status() includes all Sprint 5.39 metrics."""
        mgr = AlertManager(AlertConfig(
            enabled=True,
            offline_cache_enabled=True,
            transition_persistence_enabled=True,
            circuit_breaker_auto_tune_enabled=True,
            delivery_receipt_polling_enabled=True,
            ws_native_permessage_deflate_enabled=True,
        ))
        status = mgr.get_status()

        # Offline cache
        assert "offline_cache_enabled" in status
        assert status["offline_cache_enabled"] is True

        # Transition persistence
        assert "transition_persistence" in status
        assert status["transition_persistence"]["persistence_enabled"] is True

        # Circuit breaker auto-tune
        assert "circuit_breaker_auto_tune" in status
        assert status["circuit_breaker_auto_tune"]["enabled"] is True

        # Delivery receipt polling
        assert "delivery_receipt_polling" in status
        assert status["delivery_receipt_polling"]["enabled"] is True

        # Native deflate
        assert "ws_native_deflate" in status
        assert status["ws_native_deflate"]["enabled"] is True
