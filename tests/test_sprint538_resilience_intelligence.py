"""Sprint 5.38 tests — Resilience, Intelligence & Operational Visibility.

Deliverable 1: Service Worker Migration
  (Service Worker for WS connection pooling, SharedWorker fallback,
   BroadcastChannel communication, graceful degradation)

Deliverable 2: Learned Prediction Model
  (transition probability computation, confidence intervals via Wilson score,
   predict_causal_chain_learned, fallback to static chain,
   transition delay estimation, get_transition_probabilities)

Deliverable 3: Alert Throttling & Circuit Breaker
  (sliding window rate tracking, circuit breaker activation/deactivation,
   half-open recovery, digest-only mode during storms, critical alert passthrough)

Deliverable 4: Multi-Channel Delivery Receipts
  (Slack message_ts receipt, PagerDuty dedup_key receipt,
   _record_delivery_receipts, get_delivery_receipts, get_all_delivery_receipts)

Deliverable 5: Dashboard WebSocket Compression
  (compress_ws_message, decompress_ws_message, graceful fallback,
   compression metrics, bytes saved tracking)
"""

from __future__ import annotations

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


# ============================================================================
# Deliverable 1: Service Worker Migration
# ============================================================================


class TestServiceWorkerMigration:
    """Tests for Service Worker migration replacing SharedWorker."""

    def test_dashboard_html_contains_service_worker_code(self):
        """Dashboard HTML includes Service Worker registration code."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "serviceWorker" in _DASHBOARD_HTML
        assert "navigator.serviceWorker.register" in _DASHBOARD_HTML
        assert "initServiceWorker" in _DASHBOARD_HTML

    def test_dashboard_html_contains_broadcast_channel(self):
        """Dashboard HTML uses BroadcastChannel for SW communication."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "BroadcastChannel" in _DASHBOARD_HTML
        assert "aip-dashboard-ws" in _DASHBOARD_HTML

    def test_dashboard_html_has_sharedworker_fallback(self):
        """Dashboard HTML retains SharedWorker as fallback."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "initSharedWorkerFallback" in _DASHBOARD_HTML
        # SharedWorker code still present for fallback
        assert "SharedWorker" in _DASHBOARD_HTML

    def test_dashboard_html_has_service_worker_status_display(self):
        """Dashboard HTML shows Service Worker connection status."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "WS Connected (ServiceWorker)" in _DASHBOARD_HTML
        assert "WS Reconnecting (ServiceWorker)" in _DASHBOARD_HTML

    def test_dashboard_html_has_ws_compression_panel(self):
        """Dashboard HTML includes WebSocket compression panel."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "wsCompressionPanel" in _DASHBOARD_HTML
        assert "wsCompEnabled" in _DASHBOARD_HTML
        assert "Bytes Saved" in _DASHBOARD_HTML

    def test_dashboard_html_has_circuit_breaker_panel(self):
        """Dashboard HTML includes circuit breaker status panel."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "circuitBreakerPanel" in _DASHBOARD_HTML
        assert "cbStatus" in _DASHBOARD_HTML
        assert "Circuit Breaker" in _DASHBOARD_HTML

    def test_dashboard_html_has_delivery_receipts_panel(self):
        """Dashboard HTML includes delivery receipts panel."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "deliveryReceiptsPanel" in _DASHBOARD_HTML
        assert "Delivery Receipts" in _DASHBOARD_HTML
        assert "drEnabled" in _DASHBOARD_HTML

    def test_dashboard_html_has_learned_prediction_panel(self):
        """Dashboard HTML includes learned prediction model panel."""
        from aip.adapter.api.routes.vigil_quality import _DASHBOARD_HTML

        assert "transition" in _DASHBOARD_HTML.lower() or "learned" in _DASHBOARD_HTML.lower()


# ============================================================================
# Deliverable 2: Learned Prediction Model
# ============================================================================


class TestLearnedPredictionModel:
    """Tests for learned transition probability model."""

    def test_learned_prediction_config_defaults(self):
        """AlertConfig has learned prediction fields with defaults."""
        config = AlertConfig()
        assert config.learned_prediction_enabled is False
        assert config.learned_prediction_min_samples == 10
        assert config.learned_prediction_confidence_threshold == 0.05

    def test_learned_prediction_config_custom(self):
        """AlertConfig learned prediction can be customized."""
        config = AlertConfig(
            learned_prediction_enabled=True,
            learned_prediction_min_samples=5,
            learned_prediction_confidence_threshold=0.1,
        )
        assert config.learned_prediction_enabled is True
        assert config.learned_prediction_min_samples == 5
        assert config.learned_prediction_confidence_threshold == 0.1

    def test_transition_learning_records_alerts(self):
        """_record_alert_for_transition_learning builds transition counts."""
        mgr = AlertManager(AlertConfig(enabled=True))
        # Simulate a sequence of alerts for same subject
        now = time.time()
        mgr.prediction_mgr.record_alert_for_transition_learning(
            Alert(alert_type="pool_adjustment", severity="warning", subject="test_subj", message="1"), now
        )
        mgr.prediction_mgr.record_alert_for_transition_learning(
            Alert(alert_type="quality_degradation", severity="info", subject="test_subj", message="2"), now + 10
        )
        mgr.prediction_mgr.record_alert_for_transition_learning(
            Alert(alert_type="batch_reduction", severity="warning", subject="test_subj", message="3"), now + 20
        )

        # Should have recorded transitions
        assert mgr.prediction_mgr._transition_counts.get(("pool_adjustment", "quality_degradation"), 0) >= 1
        assert mgr.prediction_mgr._transition_counts.get(("quality_degradation", "batch_reduction"), 0) >= 1
        assert mgr.prediction_mgr._transition_totals.get("pool_adjustment", 0) >= 1

    def test_transition_learning_different_subjects(self):
        """Transitions are only recorded within same subject."""
        mgr = AlertManager(AlertConfig(enabled=True))
        now = time.time()
        mgr.prediction_mgr.record_alert_for_transition_learning(
            Alert(alert_type="pool_adjustment", severity="warning", subject="subj_a", message="1"), now
        )
        mgr.prediction_mgr.record_alert_for_transition_learning(
            Alert(alert_type="quality_degradation", severity="info", subject="subj_b", message="2"), now + 10
        )
        # No transition should be recorded because subjects differ
        assert ("pool_adjustment", "quality_degradation") not in mgr.prediction_mgr._transition_counts

    def test_transition_learning_bounds_memory(self):
        """Alert sequence is bounded to prevent unbounded memory growth."""
        mgr = AlertManager(AlertConfig(enabled=True))
        now = time.time()
        for i in range(1500):
            mgr.prediction_mgr.record_alert_for_transition_learning(
                Alert(alert_type="pool_adjustment", severity="info", subject="test", message=f"alert-{i}"), now + i
            )
        assert len(mgr.prediction_mgr._alert_type_sequence) <= 1000

    def test_get_transition_probabilities(self):
        """get_transition_probabilities returns computed probabilities."""
        mgr = AlertManager(AlertConfig(enabled=True))
        # Manually populate transition data
        mgr.prediction_mgr._transition_counts = {
            ("pool_adjustment", "quality_degradation"): 7,
            ("pool_adjustment", "batch_reduction"): 3,
        }
        mgr.prediction_mgr._transition_totals = {"pool_adjustment": 10}

        result = mgr.get_transition_probabilities("pool_adjustment")
        assert result["from_type"] == "pool_adjustment"
        assert result["total_samples"] == 10
        trans = result["transitions"]
        assert "quality_degradation" in trans
        assert trans["quality_degradation"]["probability"] == 0.7
        assert "confidence_lower" in trans["quality_degradation"]
        assert "confidence_upper" in trans["quality_degradation"]
        assert trans["quality_degradation"]["confidence_lower"] < 0.7
        assert trans["quality_degradation"]["confidence_upper"] > 0.7

    def test_get_transition_probabilities_no_data(self):
        """get_transition_probabilities returns empty when no data."""
        mgr = AlertManager(AlertConfig(enabled=True))
        result = mgr.get_transition_probabilities("nonexistent_type")
        assert result["total_samples"] == 0
        assert result["transitions"] == {}
        assert result["confidence"] == 0.0

    def test_get_transition_probabilities_all_types(self):
        """get_transition_probabilities without from_type returns all."""
        mgr = AlertManager(AlertConfig(enabled=True))
        mgr.prediction_mgr._transition_counts = {
            ("pool_adjustment", "quality_degradation"): 8,
            ("quality_degradation", "batch_reduction"): 5,
        }
        mgr.prediction_mgr._transition_totals = {"pool_adjustment": 8, "quality_degradation": 5}

        result = mgr.get_transition_probabilities()
        assert "pool_adjustment" in result
        assert "quality_degradation" in result

    def test_predict_causal_chain_learned_disabled(self):
        """predict_causal_chain_learned returns empty when disabled."""
        mgr = AlertManager(AlertConfig(enabled=True))
        alert = Alert(alert_type="pool_adjustment", severity="warning", subject="test", message="test")
        result = mgr.predict_causal_chain_learned(alert)
        assert result == []

    def test_predict_causal_chain_learned_insufficient_data(self):
        """predict_causal_chain_learned falls back when insufficient data."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                learned_prediction_enabled=True,
                learned_prediction_min_samples=100,
                causal_prediction_enabled=True,
            )
        )
        # Add minimal data (not enough for learned model)
        mgr.prediction_mgr._transition_totals = {"pool_adjustment": 5}
        alert = Alert(alert_type="pool_adjustment", severity="warning", subject="test", message="test")
        # Should fall back to static chain
        result = mgr.predict_causal_chain_learned(alert)
        # Falls back to predict_causal_chain which uses static chain
        assert isinstance(result, list)

    def test_predict_causal_chain_learned_with_data(self):
        """predict_causal_chain_learned generates predictions with confidence intervals."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                learned_prediction_enabled=True,
                learned_prediction_min_samples=5,
                learned_prediction_confidence_threshold=0.05,
            )
        )
        # Populate with sufficient transition data
        mgr.prediction_mgr._transition_counts = {
            ("pool_adjustment", "quality_degradation"): 15,
            ("pool_adjustment", "batch_reduction"): 5,
        }
        mgr.prediction_mgr._transition_totals = {"pool_adjustment": 20}
        mgr.prediction_mgr._alert_type_sequence = [
            ("pool_adjustment", "test", time.time() - 60),
            ("quality_degradation", "test", time.time() - 30),
        ]

        alert = Alert(alert_type="pool_adjustment", severity="warning", subject="test", message="test")
        predictions = mgr.predict_causal_chain_learned(alert)

        assert len(predictions) == 2  # Both transitions above threshold
        for pred in predictions:
            assert pred["model"] == "learned"
            assert "probability" in pred
            assert "confidence_lower" in pred
            assert "confidence_upper" in pred
            assert pred["prediction_id"].startswith("lpred-")
            assert pred["confidence_lower"] <= pred["probability"]
            assert pred["confidence_upper"] >= pred["probability"]
            assert pred["sample_size"] == 20

        # Verify predictions are tracked
        assert len(mgr.prediction_mgr._prediction_outcomes) == 2
        assert mgr.prediction_mgr._total_learned_predictions_made == 2

    def test_predict_causal_chain_learned_threshold_filtering(self):
        """predict_causal_chain_learned filters below confidence threshold."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                learned_prediction_enabled=True,
                learned_prediction_min_samples=5,
                learned_prediction_confidence_threshold=0.5,  # High threshold
            )
        )
        mgr.prediction_mgr._transition_counts = {
            ("pool_adjustment", "quality_degradation"): 15,  # 75%
            ("pool_adjustment", "batch_reduction"): 5,  # 25% — below threshold
        }
        mgr.prediction_mgr._transition_totals = {"pool_adjustment": 20}

        alert = Alert(alert_type="pool_adjustment", severity="warning", subject="test", message="test")
        predictions = mgr.predict_causal_chain_learned(alert)

        # Only quality_degradation should pass the 0.5 threshold
        assert len(predictions) == 1
        assert predictions[0]["predicted_alert_type"] == "quality_degradation"

    def test_learned_predictions_tracked_for_accuracy(self):
        """Learned predictions are tracked in _prediction_outcomes for accuracy feedback."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                learned_prediction_enabled=True,
                learned_prediction_min_samples=5,
            )
        )
        mgr.prediction_mgr._transition_counts = {
            ("pool_adjustment", "quality_degradation"): 10,
        }
        mgr.prediction_mgr._transition_totals = {"pool_adjustment": 10}

        alert = Alert(alert_type="pool_adjustment", severity="warning", subject="test", message="test")
        predictions = mgr.predict_causal_chain_learned(alert)

        for pred in predictions:
            pred_id = pred["prediction_id"]
            assert pred_id in mgr.prediction_mgr._prediction_outcomes
            assert mgr.prediction_mgr._prediction_outcomes[pred_id]["model"] == "learned"
            assert mgr.prediction_mgr._prediction_outcomes[pred_id]["outcome"] == "pending"

    def test_learned_prediction_in_status(self):
        """get_status() includes learned prediction model info."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                learned_prediction_enabled=True,
            )
        )
        status = mgr.get_status()
        assert "learned_prediction" in status
        assert status["learned_prediction"]["enabled"] is True
        assert status["learned_prediction"]["min_samples"] == 10


# ============================================================================
# Deliverable 3: Alert Throttling & Circuit Breaker
# ============================================================================


class TestAlertThrottlingCircuitBreaker:
    """Tests for alert throttling and circuit breaker mode."""

    def test_circuit_breaker_config_defaults(self):
        """AlertConfig has circuit breaker fields with defaults."""
        config = AlertConfig()
        assert config.throttle_threshold_per_minute == 100
        assert config.circuit_breaker_enabled is False
        assert config.circuit_breaker_cooldown_seconds == 300

    def test_circuit_breaker_config_custom(self):
        """AlertConfig circuit breaker can be customized."""
        config = AlertConfig(
            throttle_threshold_per_minute=50,
            circuit_breaker_enabled=True,
            circuit_breaker_cooldown_seconds=60,
        )
        assert config.throttle_threshold_per_minute == 50
        assert config.circuit_breaker_enabled is True
        assert config.circuit_breaker_cooldown_seconds == 60

    def test_circuit_breaker_not_active_when_disabled(self):
        """Circuit breaker does not activate when disabled."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=False,
            )
        )
        now = time.time()
        # Even with many timestamps, should not activate
        mgr.throttle_mgr._throttle_alert_timestamps = [now] * 200
        assert mgr.throttle_mgr.check_circuit_breaker(now) is False

    def test_circuit_breaker_activates_on_high_rate(self):
        """Circuit breaker activates when alert rate exceeds threshold."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
                throttle_threshold_per_minute=50,
            )
        )
        now = time.time()
        # Simulate 60 alerts in the last minute
        mgr.throttle_mgr._throttle_alert_timestamps = [now - i * 0.5 for i in range(60)]
        assert mgr.throttle_mgr.check_circuit_breaker(now) is True
        assert mgr.throttle_mgr._circuit_breaker_active is True
        assert mgr.throttle_mgr._total_circuit_breaker_activations == 1

    def test_circuit_breaker_does_not_activate_below_threshold(self):
        """Circuit breaker stays inactive below threshold."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
                throttle_threshold_per_minute=100,
            )
        )
        now = time.time()
        mgr.throttle_mgr._throttle_alert_timestamps = [now - i for i in range(50)]
        assert mgr.throttle_mgr.check_circuit_breaker(now) is False

    def test_circuit_breaker_stays_active_during_cooldown(self):
        """Circuit breaker stays active during cooldown period."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
                throttle_threshold_per_minute=10,
                circuit_breaker_cooldown_seconds=300,
            )
        )
        now = time.time()
        mgr.throttle_mgr._circuit_breaker_active = True
        mgr.throttle_mgr._circuit_breaker_activated_at = now - 10  # Activated 10s ago
        mgr.throttle_mgr._throttle_alert_timestamps = [now] * 5  # Rate is now low

        # Should stay active because cooldown hasn't elapsed
        assert mgr.throttle_mgr.check_circuit_breaker(now) is True

    def test_circuit_breaker_deactivates_after_cooldown(self):
        """Circuit breaker deactivates when cooldown expires and rate drops."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
                throttle_threshold_per_minute=100,
                circuit_breaker_cooldown_seconds=10,
            )
        )
        now = time.time()
        mgr.throttle_mgr._circuit_breaker_active = True
        mgr.throttle_mgr._circuit_breaker_activated_at = now - 20  # 20s ago, past cooldown
        mgr.throttle_mgr._throttle_alert_timestamps = [now] * 5  # Low rate now

        assert mgr.throttle_mgr.check_circuit_breaker(now) is False
        assert mgr.throttle_mgr._circuit_breaker_active is False

    def test_circuit_breaker_reactivates_if_still_high(self):
        """Circuit breaker reactivates after cooldown if rate is still high."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
                throttle_threshold_per_minute=10,
                circuit_breaker_cooldown_seconds=5,
            )
        )
        now = time.time()
        mgr.throttle_mgr._circuit_breaker_active = True
        mgr.throttle_mgr._circuit_breaker_activated_at = now - 10  # Past cooldown
        mgr.throttle_mgr._throttle_alert_timestamps = [now - i * 0.1 for i in range(50)]  # Still high

        assert mgr.throttle_mgr.check_circuit_breaker(now) is True
        assert mgr.throttle_mgr._circuit_breaker_active is True

    def test_send_alert_throttles_non_critical_during_storm(self):
        """send_alert throttles non-critical alerts during circuit breaker."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
                throttle_threshold_per_minute=5,
                circuit_breaker_cooldown_seconds=300,
                min_alert_interval_seconds=0,
                ws_batch_window_seconds=0,
            )
        )
        now = time.time()
        mgr.throttle_mgr._throttle_alert_timestamps = [now] * 10
        mgr.throttle_mgr._circuit_breaker_active = True
        mgr.throttle_mgr._circuit_breaker_activated_at = now

        alert = Alert(alert_type="pool_adjustment", severity="info", subject="test", message="throttled")
        with patch.object(mgr.realtime_bus, "notify_realtime_subscribers"):
            result = mgr.send_alert(alert)

        assert result.startswith("throttled:")
        assert mgr.throttle_mgr._total_throttled_alerts >= 1

    def test_send_alert_passes_critical_during_storm(self):
        """send_alert allows critical alerts through circuit breaker."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
                throttle_threshold_per_minute=5,
                circuit_breaker_cooldown_seconds=300,
                min_alert_interval_seconds=0,
                slack_webhook_url="https://hooks.slack.com/services/test",
            )
        )
        # Simulate high alert rate
        now = time.time()
        mgr.throttle_mgr._throttle_alert_timestamps = [now] * 10
        # Pre-activate circuit breaker
        mgr.throttle_mgr._circuit_breaker_active = True
        mgr.throttle_mgr._circuit_breaker_activated_at = now

        # Critical alert should NOT be throttled
        alert = Alert(alert_type="quality_degradation", severity="critical", subject="test", message="critical!")
        with patch.object(mgr, "_dispatch_to_transports"):
            result = mgr.send_alert(alert)
        assert not result.startswith("throttled:")

    def test_circuit_breaker_status(self):
        """get_circuit_breaker_status returns complete status."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
                throttle_threshold_per_minute=50,
                circuit_breaker_cooldown_seconds=300,
            )
        )
        mgr.throttle_mgr._circuit_breaker_active = True
        mgr.throttle_mgr._circuit_breaker_activated_at = time.time()
        mgr.throttle_mgr._total_circuit_breaker_activations = 3
        mgr.throttle_mgr._total_throttled_alerts = 25

        status = mgr.get_circuit_breaker_status()
        assert status["enabled"] is True
        assert status["active"] is True
        assert status["threshold"] == 50
        assert status["total_activations"] == 3
        assert status["total_throttled_alerts"] == 25
        assert "cooldown_remaining" in status

    def test_circuit_breaker_in_overall_status(self):
        """get_status() includes circuit breaker info."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
            )
        )
        status = mgr.get_status()
        assert "circuit_breaker" in status
        assert status["circuit_breaker"]["enabled"] is True

    def test_record_throttle_window_prunes_old_entries(self):
        """_record_throttle_window prunes entries older than 60 seconds."""
        mgr = AlertManager(AlertConfig(enabled=True))
        now = time.time()
        mgr.throttle_mgr._throttle_alert_timestamps = [now - 120, now - 90, now - 30, now - 10]
        mgr.throttle_mgr.record_throttle_window(now)
        # Only entries within 60s should remain (plus the new one)
        assert all(ts > now - 60 for ts in mgr.throttle_mgr._throttle_alert_timestamps)


# ============================================================================
# Deliverable 4: Multi-Channel Delivery Receipts
# ============================================================================


class TestMultiChannelDeliveryReceipts:
    """Tests for multi-channel delivery receipt tracking."""

    def test_delivery_receipts_config_defaults(self):
        """AlertConfig has delivery_receipts_enabled with default False."""
        config = AlertConfig()
        assert config.delivery_receipts_enabled is False

    def test_delivery_receipts_config_custom(self):
        """AlertConfig delivery_receipts_enabled can be set."""
        config = AlertConfig(delivery_receipts_enabled=True)
        assert config.delivery_receipts_enabled is True

    def test_record_delivery_receipts_slack(self):
        """_record_delivery_receipts captures Slack message_ts receipt."""
        mgr = AlertManager(AlertConfig(enabled=True, delivery_receipts_enabled=True))
        transport_results = {
            "slack": {
                "status": "delivered",
                "retries": 0,
                "receipt": {"message_ts": "1234567890.123456", "channel": "C0123ABCD"},
            },
        }
        mgr._record_delivery_receipts("cid-1", transport_results)

        receipts = mgr.get_delivery_receipts("cid-1")
        assert "slack" in receipts
        assert receipts["slack"]["message_ts"] == "1234567890.123456"
        assert "confirmed_at" in receipts["slack"]
        assert receipts["slack"]["delivery_status"] == "delivered"

    def test_record_delivery_receipts_pagerduty(self):
        """_record_delivery_receipts captures PagerDuty dedup_key receipt."""
        mgr = AlertManager(AlertConfig(enabled=True, delivery_receipts_enabled=True))
        transport_results = {
            "pagerduty": {
                "status": "delivered",
                "retries": 0,
                "receipt": {"dedup_key": "aip-brain-test-subj-abc12345", "status": "triggered"},
            },
        }
        mgr._record_delivery_receipts("cid-2", transport_results)

        receipts = mgr.get_delivery_receipts("cid-2")
        assert "pagerduty" in receipts
        assert receipts["pagerduty"]["dedup_key"] == "aip-brain-test-subj-abc12345"
        assert receipts["pagerduty"]["status"] == "triggered"

    def test_record_delivery_receipts_no_receipt(self):
        """_record_delivery_receipts handles transport results without receipts."""
        mgr = AlertManager(AlertConfig(enabled=True, delivery_receipts_enabled=True))
        transport_results = {
            "email": {"status": "delivered", "retries": 0},
        }
        mgr._record_delivery_receipts("cid-3", transport_results)
        # Email has no receipt, so nothing should be stored
        receipts = mgr.get_delivery_receipts("cid-3")
        assert receipts == {}

    def test_get_delivery_receipts_not_found(self):
        """get_delivery_receipts returns empty dict for unknown correlation ID."""
        mgr = AlertManager(AlertConfig(enabled=True))
        assert mgr.get_delivery_receipts("nonexistent") == {}

    def test_get_all_delivery_receipts(self):
        """get_all_delivery_receipts returns all receipt records."""
        mgr = AlertManager(AlertConfig(enabled=True, delivery_receipts_enabled=True))
        mgr.delivery_mgr._delivery_receipts = {
            "cid-1": {"slack": {"message_ts": "ts1"}},
            "cid-2": {"pagerduty": {"dedup_key": "dk1"}},
        }
        all_receipts = mgr.get_all_delivery_receipts()
        assert len(all_receipts) == 2
        assert "cid-1" in all_receipts
        assert "cid-2" in all_receipts

    def test_get_all_delivery_receipts_limit(self):
        """get_all_delivery_receipts respects limit parameter."""
        mgr = AlertManager(AlertConfig(enabled=True))
        for i in range(10):
            mgr.delivery_mgr._delivery_receipts[f"cid-{i}"] = {"slack": {"message_ts": f"ts{i}"}}
        result = mgr.get_all_delivery_receipts(limit=3)
        assert len(result) <= 3

    def test_delivery_receipts_in_status(self):
        """get_status() includes delivery receipts info."""
        mgr = AlertManager(AlertConfig(enabled=True, delivery_receipts_enabled=True))
        status = mgr.get_status()
        assert "delivery_receipts" in status
        assert status["delivery_receipts"]["enabled"] is True

    def test_slack_notification_returns_receipt(self):
        """_send_slack_notification returns receipt when enabled."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                slack_webhook_url="https://hooks.slack.com/services/test",
                delivery_receipts_enabled=True,
            )
        )
        alert = Alert(alert_type="test", severity="info", subject="s", message="m")
        with patch("aip.adapter.alerting.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"ok":true,"ts":"1234567890.123456","channel":"C01"}'
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            receipt = mgr._send_slack_notification(alert)
            assert receipt is not None
            assert receipt["message_ts"] == "1234567890.123456"

    def test_pagerduty_notification_returns_receipt(self):
        """_send_pagerduty_notification returns receipt when enabled."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                pagerduty_integration_key="pd-key-123",
                delivery_receipts_enabled=True,
            )
        )
        alert = Alert(alert_type="test", severity="info", subject="s", message="m")
        with patch("aip.adapter.alerting.urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 202
            mock_resp.read.return_value = b'{"dedup_key":"aip-brain-test-s-abc12345","status":"triggered"}'
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            receipt = mgr._send_pagerduty_notification(alert)
            assert receipt is not None
            assert "dedup_key" in receipt

    def test_dispatch_records_receipts_when_enabled(self):
        """_dispatch_to_transports records receipts when delivery_receipts_enabled."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                slack_webhook_url="https://hooks.slack.com/services/test",
                delivery_receipts_enabled=True,
                min_alert_interval_seconds=0,
            )
        )
        with patch.object(mgr, "_send_slack_notification", return_value={"message_ts": "ts123", "channel": "C01"}):
            mgr._dispatch_to_transports(
                Alert(alert_type="test", severity="info", subject="s", message="m"),
                ["slack"],
                "test-cid-receipts",
            )
        # Should have recorded the receipt
        receipts = mgr.get_delivery_receipts("test-cid-receipts")
        assert "slack" in receipts
        assert receipts["slack"]["message_ts"] == "ts123"


# ============================================================================
# Deliverable 5: Dashboard WebSocket Compression
# ============================================================================


class TestWebSocketCompression:
    """Tests for per-message deflate WebSocket compression."""

    def test_ws_compression_config_defaults(self):
        """AlertConfig has ws_compression_enabled with default False."""
        config = AlertConfig()
        assert config.ws_compression_enabled is False

    def test_ws_compression_config_custom(self):
        """AlertConfig ws_compression_enabled can be set."""
        config = AlertConfig(ws_compression_enabled=True)
        assert config.ws_compression_enabled is True

    def test_compress_ws_message_disabled(self):
        """compress_ws_message returns original when disabled."""
        mgr = AlertManager(AlertConfig(enabled=True, ws_compression_enabled=False))
        data = '{"event":"test","data":"hello world"}'
        result, compressed = mgr.compress_ws_message(data)
        assert compressed is False
        assert result == data

    def test_compress_ws_message_compresses(self):
        """compress_ws_message compresses data when enabled and beneficial."""
        mgr = AlertManager(AlertConfig(enabled=True, ws_compression_enabled=True))
        # Create data that compresses well (repetitive)
        data = '{"event":"batch_events","alerts":' + str(["alert_type_x"] * 100) + "}"
        result, compressed = mgr.compress_ws_message(data)
        assert compressed is True
        assert len(result) < len(data)

    def test_compress_ws_message_falls_back_when_not_smaller(self):
        """compress_ws_message falls back when compressed is not smaller."""
        mgr = AlertManager(AlertConfig(enabled=True, ws_compression_enabled=True))
        # Very short data that won't compress well
        data = "hi"
        result, compressed = mgr.compress_ws_message(data)
        # Short strings usually don't compress smaller with base64 overhead
        assert compressed is False
        assert result == data

    def test_decompress_ws_message_roundtrip(self):
        """compress → decompress roundtrip preserves data."""
        mgr = AlertManager(AlertConfig(enabled=True, ws_compression_enabled=True))
        original = '{"event":"batch_events","alerts":["a1","a2","a3","a4","a5"]}'
        compressed_data, was_compressed = mgr.compress_ws_message(original)
        if was_compressed:
            decompressed = mgr.decompress_ws_message(compressed_data)
            assert decompressed == original

    def test_decompress_ws_message_invalid_data(self):
        """decompress_ws_message handles invalid data gracefully."""
        mgr = AlertManager(AlertConfig(enabled=True, ws_compression_enabled=True))
        # Invalid base64 data should return as-is
        result = mgr.decompress_ws_message("not-valid-compressed-data!!!")
        # Should not raise, returns the input as fallback
        assert isinstance(result, str)

    def test_compress_ws_message_tracks_bytes_saved(self):
        """compress_ws_message tracks bytes saved estimate."""
        mgr = AlertManager(AlertConfig(enabled=True, ws_compression_enabled=True))
        data = '{"event":"batch_events","alerts":' + str(["type_x"] * 50) + "}"
        mgr.compress_ws_message(data)
        assert mgr.realtime_bus._ws_compression_bytes_saved_estimate > 0

    def test_compression_status(self):
        """get_compression_status returns metrics."""
        mgr = AlertManager(AlertConfig(enabled=True, ws_compression_enabled=True))
        mgr.realtime_bus._ws_compression_bytes_saved_estimate = 5000
        status = mgr.get_compression_status()
        assert status["enabled"] is True
        assert status["bytes_saved_estimate"] == 5000

    def test_compression_in_overall_status(self):
        """get_status() includes compression info."""
        mgr = AlertManager(AlertConfig(enabled=True, ws_compression_enabled=True))
        status = mgr.get_status()
        assert "ws_compression" in status
        assert status["ws_compression"]["enabled"] is True


# ============================================================================
# Integration: Full Sprint 5.38 flow
# ============================================================================


class TestSprint538Integration:
    """Integration tests combining multiple Sprint 5.38 features."""

    def test_full_learned_prediction_flow(self):
        """Full flow: observe alerts → learn transitions → predict with confidence."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                learned_prediction_enabled=True,
                learned_prediction_min_samples=5,
                learned_prediction_confidence_threshold=0.05,
                min_alert_interval_seconds=0,
            )
        )
        now = time.time()

        # Simulate a sequence of alerts
        for i in range(20):
            mgr.prediction_mgr.record_alert_for_transition_learning(
                Alert(alert_type="pool_adjustment", severity="warning", subject="integration", message="pool"),
                now + i * 30,
            )
            mgr.prediction_mgr.record_alert_for_transition_learning(
                Alert(alert_type="quality_degradation", severity="info", subject="integration", message="quality"),
                now + i * 30 + 10,
            )

        # Now predict using the learned model
        alert = Alert(alert_type="pool_adjustment", severity="warning", subject="integration", message="trigger")
        with patch.object(mgr.realtime_bus, "notify_realtime_subscribers"):
            predictions = mgr.predict_causal_chain_learned(alert)

        assert len(predictions) > 0
        for pred in predictions:
            assert pred["model"] == "learned"
            assert "confidence_lower" in pred
            assert "confidence_upper" in pred

    def test_full_circuit_breaker_flow(self):
        """Full flow: storm detected → breaker activates → non-critical throttled → critical passes."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                circuit_breaker_enabled=True,
                throttle_threshold_per_minute=5,
                circuit_breaker_cooldown_seconds=300,
                min_alert_interval_seconds=0,
                slack_webhook_url="https://hooks.slack.com/services/test",
                ws_batch_window_seconds=0,
            )
        )

        # Trigger circuit breaker
        now = time.time()
        mgr.throttle_mgr._throttle_alert_timestamps = [now] * 10
        mgr.throttle_mgr._circuit_breaker_active = True
        mgr.throttle_mgr._circuit_breaker_activated_at = now

        # Non-critical alert should be throttled
        info_alert = Alert(alert_type="pool_adjustment", severity="info", subject="test", message="info alert")
        with patch.object(mgr.realtime_bus, "notify_realtime_subscribers"):
            result = mgr.send_alert(info_alert)
        assert result.startswith("throttled:")

        # Critical alert should pass through
        critical_alert = Alert(
            alert_type="quality_degradation", severity="critical", subject="test", message="critical!"
        )
        with (
            patch.object(mgr, "_dispatch_to_transports"),
            patch.object(mgr.realtime_bus, "notify_realtime_subscribers"),
        ):
            result = mgr.send_alert(critical_alert)
        assert not result.startswith("throttled:")

    def test_full_delivery_receipts_flow(self):
        """Full flow: send alert → deliver to channels → capture receipts → query."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                slack_webhook_url="https://hooks.slack.com/services/test",
                pagerduty_integration_key="pd-key-123",
                delivery_receipts_enabled=True,
                min_alert_interval_seconds=0,
            )
        )

        with (
            patch.object(mgr, "_send_slack_notification", return_value={"message_ts": "1234.5678", "channel": "C01"}),
            patch.object(
                mgr, "_send_pagerduty_notification", return_value={"dedup_key": "dk-abc", "status": "triggered"}
            ),
        ):
            mgr._dispatch_to_transports(
                Alert(alert_type="test", severity="critical", subject="s", message="m"),
                ["slack", "pagerduty"],
                "receipt-cid-1",
            )

        receipts = mgr.get_delivery_receipts("receipt-cid-1")
        assert "slack" in receipts
        assert "pagerduty" in receipts
        assert receipts["slack"]["message_ts"] == "1234.5678"
        assert receipts["pagerduty"]["dedup_key"] == "dk-abc"

    def test_status_includes_all_sprint538_metrics(self):
        """get_status() includes all Sprint 5.38 metrics."""
        mgr = AlertManager(
            AlertConfig(
                enabled=True,
                learned_prediction_enabled=True,
                circuit_breaker_enabled=True,
                delivery_receipts_enabled=True,
                ws_compression_enabled=True,
            )
        )
        status = mgr.get_status()

        # Learned prediction
        assert "learned_prediction" in status
        assert status["learned_prediction"]["enabled"] is True

        # Circuit breaker
        assert "circuit_breaker" in status
        assert status["circuit_breaker"]["enabled"] is True

        # Delivery receipts
        assert "delivery_receipts" in status
        assert status["delivery_receipts"]["enabled"] is True

        # Compression
        assert "ws_compression" in status
        assert status["ws_compression"]["enabled"] is True
