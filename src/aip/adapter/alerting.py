"""Operator alerting — lightweight webhook/email notification system.

Sprint 5.25: Provides configurable notifications for significant events
in the self-tuning system:

- Vigil quality degradation (faithfulness score dropping over multiple cycles)
- Read pool auto-sizing adjustments (increases AND rollbacks)
- Graph extraction batch size reductions due to high parse failure rate

Sprint 5.26: Transport hardening improvements:

- Retry with exponential backoff for webhook delivery
- Webhook URL validation at startup / first use
- SMTP authentication support (username/password/TLS control)
- Delivery failure history tracking with detailed error records

Sprint 5.28: Admin visibility improvements:

- get_alert_history() method with filtering by type, severity, and time range
- Enables the /vigil/quality/alerts endpoint for operator visibility
- Full alerting configuration status exposed via API

Sprint 5.29: Durability and routing improvements:

- AlertHistoryStore integration for SQLite-backed persistent alert history
- Alert history survives process restarts when a persistent store is attached
- Config-driven alert routing: map alert types to specific transports
- get_alert_history() queries persistent store when available

Sprint 5.30: Responsiveness, interactivity, and operational control:

- Async alert dispatch: send_alert() is non-blocking, returns correlation ID
- Alert acknowledgment/dismissal with persistent state in AlertHistoryStore
- Configurable alert severity escalation based on occurrence count
- Rate-limiting state rebuilt from persistent store on restart (no alert storms)
- Delivery happens in background threads; failures recorded asynchronously

Sprint 5.31: Noise reduction, delivery visibility, and real-time updates:

- Delivery status tracking: per-correlation-ID tracking of transport outcomes
- Alert silencing/muting rules: temporarily mute (alert_type, subject) pairs
- Alert aggregation/digest: batch low-severity alerts into periodic summaries

Sprint 5.32: Durability, real-time interaction, and operational visibility:

- Delivery status persistence: delivery status records survive restarts via SQLite
- WebSocket dashboard channel: bidirectional WS for real-time updates + commands
- Alert correlation & grouping: group related alerts, bulk acknowledge/dismiss
- Digest customization per alert type: per-type intervals via TOML config
- Rate-limit & mute metrics in health endpoint

Design principles:
- Lightweight and opt-in — alerting is disabled by default
- Multiple transport mechanisms (webhook, email)
- Non-blocking — alerts are fire-and-forget; failures are logged but never
  interrupt the calling code
- Resilient — webhook retries with exponential backoff; transport errors
  are recorded with full context for operator debugging
- Configurable via ``[alerting]`` section in ``aip.config.toml``

Configuration example::

    [alerting]
    enabled = true
    webhook_url = "https://hooks.slack.com/services/..."
    email_to = "ops@example.com"
    email_from = "aip-brain@example.com"
    smtp_host = "smtp.example.com"
    smtp_port = 587
    smtp_username = "aip-brain"
    smtp_password = ""  # Set via AIP_SMTP_PASSWORD env var
    smtp_use_tls = true
    alert_on_quality_degradation = true
    alert_on_pool_adjustment = true
    alert_on_batch_reduction = true
    min_alert_interval_seconds = 300   # Rate-limit: don't re-alert for 5 min
    webhook_max_retries = 3            # Sprint 5.26: retry count
    webhook_retry_base_delay_seconds = 1.0  # Sprint 5.26: exponential backoff base

    # Sprint 5.30: Escalation configuration
    # escalation_threshold = 3            # Alert N times before escalating
    # escalation_window_seconds = 3600    # Count alerts within this window
    # escalation_severity = "critical"    # Severity to escalate to
    # escalation_additional_transports = []  # Extra transports on escalation
"""

from __future__ import annotations

import json
import math
import random
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from aip.logging import get_logger

# Optional asyncio import for SSE/WebSocket notifications
try:
    import asyncio
except ImportError:
    asyncio = None  # type: ignore[assignment]

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Alert configuration
# ---------------------------------------------------------------------------


@dataclass
class AlertConfig:
    """Configuration for operator alerting.

    All alerting is opt-in.  By default, alerting is disabled and no
    notifications are sent.  Operators must explicitly enable alerting
    and configure at least one transport (webhook_url or email_to).

    Sprint 5.26 additions:
    - smtp_username / smtp_password: SMTP authentication
    - smtp_use_tls: Explicit TLS control (default True for port 587)
    - webhook_max_retries: Retry count for failed webhook deliveries
    - webhook_retry_base_delay_seconds: Base delay for exponential backoff

    Sprint 5.30 additions:
    - escalation_threshold: Number of occurrences before escalating severity
    - escalation_window_seconds: Time window for counting occurrences
    - escalation_severity: Severity level to escalate to
    - escalation_additional_transports: Extra transports to use on escalation

    Attributes
    ----------
    enabled:
        Master switch for all alerting.  When False, no alerts are sent.
    webhook_url:
        HTTP(S) URL to POST alert payloads to (JSON body).
        Suitable for Slack webhooks, generic webhook receivers, etc.
    email_to:
        Comma-separated email addresses for alert recipients.
    email_from:
        Sender email address for alert emails.
    smtp_host:
        SMTP server hostname for sending emails.
    smtp_port:
        SMTP server port (default 587 for TLS).
    smtp_username:
        Username for SMTP authentication.  If empty, no authentication
        is attempted (backwards compatible with Sprint 5.25).
    smtp_password:
        Password for SMTP authentication.  Prefer the AIP_SMTP_PASSWORD
        environment variable over storing it in the config file.
    smtp_use_tls:
        Whether to use TLS for the SMTP connection.  Default True for
        port 587.  Set to False for port 25 / no-TLS relays.
    alert_on_quality_degradation:
        Alert when Vigil detects a degrading quality trend across cycles.
    alert_on_pool_adjustment:
        Alert when read pool auto-sizing makes a significant adjustment
        (increase or rollback).
    alert_on_batch_reduction:
        Alert when graph extraction batch size is reduced due to high
        parse failure rate.
    min_alert_interval_seconds:
        Minimum time between identical alert types for the same subject.
        Prevents alert storms.  Default 300 (5 minutes).
    webhook_max_retries:
        Maximum number of retry attempts for failed webhook deliveries.
        Default 3.  Each retry uses exponential backoff.
    webhook_retry_base_delay_seconds:
        Base delay in seconds for exponential backoff on webhook retries.
        The Nth retry waits (base_delay * 2^N) seconds.
        Default 1.0 (1s, 2s, 4s for retries 1-3).
    escalation_threshold:
        Number of times the same (alert_type, subject) must occur within
        the escalation window before severity is automatically escalated.
        Default 3.  Set to 0 to disable escalation.
    escalation_window_seconds:
        Time window in seconds for counting occurrences toward escalation.
        Default 3600 (1 hour).
    escalation_severity:
        The severity level to escalate to.  Default "critical".
    escalation_additional_transports:
        Additional transports to use when an alert is escalated.
        For example, if normal routing sends batch_reduction to webhook
        only, escalation could add email: ["email"].
    """

    enabled: bool = False
    webhook_url: str = ""
    email_to: str = ""
    email_from: str = "aip-brain@localhost"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    alert_on_quality_degradation: bool = True
    alert_on_pool_adjustment: bool = True
    alert_on_batch_reduction: bool = True
    min_alert_interval_seconds: int = 300
    # Sprint 5.26: Webhook retry configuration
    webhook_max_retries: int = 3
    webhook_retry_base_delay_seconds: float = 1.0
    # Sprint 5.29: Config-driven alert routing
    # Maps alert_type to list of transports (e.g., {"batch_reduction": ["webhook"]})
    # When empty, all enabled transports are used for all alert types (default).
    routes: dict[str, list[str]] = field(default_factory=dict)
    # Sprint 5.30: Escalation configuration
    escalation_threshold: int = 3
    escalation_window_seconds: int = 3600
    escalation_severity: str = "critical"
    escalation_additional_transports: list[str] = field(default_factory=list)
    # Sprint 5.31: Alert digest/aggregation configuration
    digest_enabled: bool = False
    digest_interval_minutes: int = 15
    digest_min_alerts: int = 3
    # Sprint 5.32: Per-alert-type digest overrides
    # Maps alert_type to {"interval_minutes": N, "min_alerts": N}
    # When set, these override the global digest settings for specific alert types.
    # Example: {"batch_reduction": {"interval_minutes": 5, "min_alerts": 2}}
    digest_overrides: dict[str, dict[str, int]] = field(default_factory=dict)
    # Sprint 5.33: Delivery status auto-pruning max age
    delivery_status_max_age_days: int = 30
    # Sprint 5.33: WebSocket authentication and rate limiting
    ws_auth_token: str = ""
    ws_rate_limit_per_minute: int = 60
    # Sprint 5.33: Causal alert grouping
    causal_grouping_enabled: bool = False
    causal_grouping_window_seconds: int = 300
    # Sprint 5.34: Delivery status max rows for pruning
    delivery_status_max_rows: int = 2000
    # Sprint 5.34: Alert group TTL (hours, 0=disabled)
    alert_group_ttl_hours: int = 24
    # Sprint 5.35: WebSocket heartbeat interval in seconds
    ws_heartbeat_interval_seconds: int = 30
    # Sprint 5.35: WebSocket heartbeat timeout — missed heartbeats before session cleanup
    ws_heartbeat_missed_limit: int = 3
    # Sprint 5.35: Delivery status pruning scheduler interval in seconds (0=disabled)
    delivery_status_prune_interval_seconds: int = 0
    # Sprint 5.36: WebSocket message batching window in seconds (0=disabled, send immediately)
    ws_batch_window_seconds: float = 0.5
    # Sprint 5.36: WebSocket message batching max batch size
    ws_batch_max_size: int = 20
    # Sprint 5.36: Auto-merge suggestion window in seconds (0=disabled)
    auto_merge_window_seconds: int = 600
    # Sprint 5.36: Auto-merge similarity threshold (0.0-1.0, subject overlap)
    auto_merge_similarity_threshold: float = 0.6
    # Sprint 5.36: Causal chain prediction enabled
    causal_prediction_enabled: bool = False
    # Sprint 5.36: Pruning history retention (number of past runs to keep)
    pruning_history_size: int = 20
    # Sprint 5.37: Auto-merge policy engine
    auto_merge_mode: str = "suggest"  # "suggest" or "auto"
    auto_merge_cooldown_seconds: int = 300  # Cooldown between auto-merges
    auto_merge_type_thresholds: dict[str, float] = field(default_factory=dict)  # Per-type similarity thresholds
    # Sprint 5.37: Notification channel diversification
    slack_webhook_url: str = ""
    pagerduty_integration_key: str = ""
    notification_routes: dict[str, list[str]] = field(default_factory=dict)  # severity/type -> channels
    # Sprint 5.37: Causal prediction accuracy
    prediction_accuracy_window_seconds: int = 600  # Time window to check if prediction materialized
    # Sprint 5.38: Learned prediction model
    learned_prediction_enabled: bool = False  # Use learned transition probabilities instead of static chain
    learned_prediction_min_samples: int = 10  # Minimum alert pairs before learned model is used
    learned_prediction_confidence_threshold: float = 0.05  # Minimum transition probability to predict
    # Sprint 5.38: Alert throttling & circuit breaker
    throttle_threshold_per_minute: int = 100  # Alert rate threshold to trigger throttling
    circuit_breaker_enabled: bool = False  # Enable circuit breaker mode
    circuit_breaker_cooldown_seconds: int = 300  # Duration of digest-only mode during storm
    # Sprint 5.38: Multi-channel delivery receipts
    delivery_receipts_enabled: bool = False  # Track delivery confirmations per channel
    # Sprint 5.38: WebSocket compression
    ws_compression_enabled: bool = False  # Enable per-message deflate compression for WS
    # Sprint 5.39: Offline cache for dashboard assets
    offline_cache_enabled: bool = False  # Enable Service Worker offline cache and action queueing
    # Sprint 5.39: Transition probability persistence and retraining
    transition_persistence_enabled: bool = False  # Persist learned transition model to DB
    retrain_interval_seconds: int = 3600  # Periodic retraining interval (0 = disabled)
    retrain_after_n_alerts: int = 100  # Retrain after N new alerts since last train
    # Sprint 5.39: Circuit breaker auto-tuning
    circuit_breaker_auto_tune_enabled: bool = False  # Enable dynamic threshold adjustment
    circuit_breaker_auto_tune_lookback_hours: int = 168  # Look back 7 days for pattern learning
    circuit_breaker_auto_tune_sensitivity: float = 1.5  # Multiplier above baseline for threshold
    circuit_breaker_auto_tune_min_threshold: int = 20  # Minimum allowed threshold
    circuit_breaker_auto_tune_max_threshold: int = 500  # Maximum allowed threshold
    # Sprint 5.39: Delivery receipt polling
    delivery_receipt_polling_enabled: bool = False  # Enable polling for email delivery status
    delivery_receipt_poll_interval_seconds: int = 300  # Poll every 5 minutes
    email_read_tracking_enabled: bool = False  # Track email open/read via webhook or pixel
    email_delivery_webhook_url: str = ""  # Webhook URL for email delivery callbacks
    # Sprint 5.39: Native WebSocket permessage-deflate
    ws_native_permessage_deflate_enabled: bool = False  # Use protocol-level permessage-deflate instead of app-level
    # Sprint 5.45: A/B experiment configuration
    ab_experiment_enabled: bool = False  # Enable A/B experiment tracking
    ab_auto_promote_interval_seconds: int = 300  # Auto-promotion check interval (0=disabled)
    ab_auto_promote_confidence_threshold: float = 0.95  # Confidence threshold for auto-promotion
    ab_auto_promote_min_samples: int = 50  # Minimum samples before auto-promotion
    # Sprint 5.46: Experiment expiry/cleanup configuration
    ab_experiment_ttl_hours: int = 168  # Max running time for experiments (0=disabled, default 7 days)
    ab_stopped_experiment_retention_hours: int = 72  # Retain stopped experiments for N hours (0=prune immediately)
    ab_cleanup_interval_seconds: int = 3600  # Cleanup checker interval (0=disabled)
    # Sprint 5.46: Promotion rollback configuration
    ab_rollback_enabled: bool = False  # Enable automatic rollback on accuracy degradation
    ab_rollback_observation_window_seconds: int = 1800  # Window to observe after promotion
    ab_rollback_accuracy_drop_threshold: float = 0.05  # Accuracy drop threshold to trigger rollback
    # Sprint 5.46: Decay recovery configuration
    decay_recovery_enabled: bool = False  # Enable automatic decay recovery
    decay_recovery_threshold: float = 0.15  # Confidence decay threshold to trigger recovery
    decay_recovery_actions: list[str] = field(
        default_factory=lambda: ["rerun_calibration"]
    )  # Actions: rerun_calibration, restart_experiment
    # Sprint 5.47: Rollback + live config reversion
    ab_rollback_revert_live_config: bool = True  # Automatically revert live model config on rollback
    # Sprint 5.47: Statistical significance testing for promotions
    ab_statistical_significance_enabled: bool = False  # Require statistical significance before promotion
    ab_statistical_significance_p_value: float = 0.05  # P-value threshold for significance (default 0.05)
    ab_statistical_significance_method: str = "z_test"  # Method: "z_test", "t_test", "bootstrap"
    ab_statistical_significance_min_samples: int = 30  # Minimum samples per variant for stat testing
    # Sprint 5.47: Cleanup alerting
    ab_cleanup_alert_on_ttl_expiry: bool = True  # Send alert when experiment expired due to TTL
    # Sprint 5.47: Confidence calibration from A/B results
    ab_confidence_calibration_enabled: bool = False  # Use A/B results for confidence calibration
    # Sprint 5.48: Rollback dry-run mode
    ab_rollback_dry_run: bool = False  # Evaluate rollback conditions without actually reverting
    # Sprint 5.48: Multi-armed bandit support
    ab_bandit_enabled: bool = False  # Enable bandit-based traffic allocation (replaces fixed 50/50)
    ab_bandit_method: str = "thompson"  # Method: "thompson", "ucb", or "epsilon_greedy"
    ab_bandit_explore_rate: float = 0.1  # Exploration rate for UCB; epsilon for epsilon-greedy; ignored for Thompson
    # Sprint 5.49: Contextual bandit support
    ab_bandit_contextual_enabled: bool = False  # Enable contextual features in bandit allocation
    ab_bandit_contextual_features: list[str] = field(
        default_factory=lambda: ["alert_type", "subject"]
    )  # Context features to consider
    ab_bandit_accuracy_snapshot_interval_seconds: int = (
        60  # Interval for recording accuracy snapshots in promotion checker (0=disabled)
    )
    # Sprint 5.50: Bandit decision logging
    ab_bandit_decision_logging_enabled: bool = False  # Log every bandit allocation decision to SQLite
    # Sprint 5.50: Adaptive bandit method selection
    ab_bandit_adaptive_method_enabled: bool = (
        False  # Allow automatic method selection based on experiment characteristics
    )
    # Sprint 5.50: Snapshot garbage collection
    ab_snapshot_gc_enabled: bool = False  # Enable automatic cleanup of stale pre-promotion snapshots
    ab_snapshot_gc_max_age_hours: int = 72  # Maximum age in hours for snapshots (default 72)
    ab_snapshot_gc_interval_seconds: int = 3600  # Interval for running snapshot GC (0=disabled)
    # Sprint 5.50: Calibration drift detection
    ab_calibration_drift_threshold: float = 0.20  # Alert when calibration factor deviates >20% from 1.0
    ab_calibration_drift_check_enabled: bool = False  # Enable calibration drift monitoring


# ---------------------------------------------------------------------------
# Alert types
# ---------------------------------------------------------------------------


@dataclass
class Alert:
    """A single alert event.

    Attributes
    ----------
    alert_type:
        Category: ``quality_degradation``, ``pool_adjustment``,
        ``batch_reduction``.
    severity:
        ``info``, ``warning``, or ``critical``.
    subject:
        Human-readable identifier (e.g. store name, metric name).
    message:
        Detailed human-readable message.
    data:
        Arbitrary structured data for programmatic consumers.
    timestamp:
        ISO 8601 timestamp of when the alert was generated.
    """

    alert_type: str
    severity: str  # info, warning, critical
    subject: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "alert_type": self.alert_type,
            "severity": self.severity,
            "subject": self.subject,
            "message": self.message,
            "data": self.data,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Delivery failure record (Sprint 5.26)
# ---------------------------------------------------------------------------


@dataclass
class DeliveryFailure:
    """Record of a failed alert delivery attempt.

    Tracks the transport, error details, and retry context so operators
    can diagnose persistent delivery problems.

    Attributes
    ----------
    transport:
        ``webhook`` or ``email``.
    alert_type:
        The alert type that failed delivery.
    subject:
        The alert subject.
    error_message:
        The exception or error string.
    timestamp:
        ISO 8601 timestamp of the failure.
    retry_attempt:
        Which retry attempt this failure represents (0 = initial attempt).
    final:
        Whether this was the final attempt (no more retries).
    """

    transport: str
    alert_type: str
    subject: str
    error_message: str
    timestamp: str = ""
    retry_attempt: int = 0
    final: bool = True

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "transport": self.transport,
            "alert_type": self.alert_type,
            "subject": self.subject,
            "error_message": self.error_message,
            "timestamp": self.timestamp,
            "retry_attempt": self.retry_attempt,
            "final": self.final,
        }


# ---------------------------------------------------------------------------
# Webhook URL validation
# ---------------------------------------------------------------------------

# Valid webhook URL schemes
_WEBHOOK_VALID_SCHEMES = frozenset({"http", "https"})

# Regex for basic URL structure validation
_WEBHOOK_URL_PATTERN = re.compile(
    r"^https?://[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*"
    r"(:[0-9]{1,5})?(/.*)?$"
)


def validate_webhook_url(url: str) -> tuple[bool, str]:
    """Validate a webhook URL for basic correctness.

    Checks that the URL:
    1. Uses http or https scheme
    2. Has a valid hostname structure
    3. Is not empty or whitespace

    Returns (is_valid, reason) tuple.  If valid, reason is empty string.
    """
    if not url or not url.strip():
        return False, "Webhook URL is empty"

    url = url.strip()

    # Check scheme
    scheme_sep = url.find("://")
    if scheme_sep == -1:
        return False, "Webhook URL must include a scheme (http:// or https://)"

    scheme = url[:scheme_sep].lower()
    if scheme not in _WEBHOOK_VALID_SCHEMES:
        return False, f"Webhook URL scheme must be http or https, got '{scheme}'"

    # Basic structural validation
    if not _WEBHOOK_URL_PATTERN.match(url):
        return False, "Webhook URL has invalid hostname structure"

    return True, ""


# ---------------------------------------------------------------------------
# Sprint 5.60: Sub-managers extracted from AlertManager
# ---------------------------------------------------------------------------


class _WSConnectionPool:
    """Shared worker pool for high-concurrency WebSocket delivery.

    Sprint 5.63: When the number of WebSocket subscribers exceeds
    ``_pool_activation_threshold`` (default 50), the pool activates
    and groups subscribers into fixed-size "worker groups". Each
    group shares a single delivery task, reducing per-connection
    overhead and improving throughput under high concurrency.

    Below the threshold, direct delivery is used (no pooling).
    """

    _pool_activation_threshold: int = 50
    _group_size: int = 10  # subscribers per worker group

    def __init__(self) -> None:
        self._active: bool = False
        self._total_pool_deliveries: int = 0
        self._total_direct_deliveries: int = 0
        self._total_pool_saves: int = 0  # deliveries avoided via pooling

    @property
    def active(self) -> bool:
        return self._active

    def maybe_activate(self, subscriber_count: int) -> bool:
        """Activate pool if subscriber count exceeds threshold."""
        if not self._active and subscriber_count >= self._pool_activation_threshold:
            self._active = True
            logger.info(
                "ws_connection_pool_activated",
                subscriber_count=subscriber_count,
                threshold=self._pool_activation_threshold,
            )
        return self._active

    def maybe_deactivate(self, subscriber_count: int) -> None:
        """Deactivate pool if subscriber count drops below half threshold."""
        if self._active and subscriber_count < self._pool_activation_threshold // 2:
            self._active = False
            logger.info(
                "ws_connection_pool_deactivated",
                subscriber_count=subscriber_count,
            )

    def group_subscribers(self, subscribers: list[Any]) -> list[list[Any]]:
        """Split subscribers into groups of ``_group_size``."""
        groups = []
        for i in range(0, len(subscribers), self._group_size):
            groups.append(subscribers[i : i + self._group_size])
        return groups

    def get_status(self) -> dict[str, Any]:
        """Return connection pool status for monitoring."""
        return {
            "active": self._active,
            "activation_threshold": self._pool_activation_threshold,
            "group_size": self._group_size,
            "total_pool_deliveries": self._total_pool_deliveries,
            "total_direct_deliveries": self._total_direct_deliveries,
            "total_pool_saves": self._total_pool_saves,
        }


class RealtimeEventBus:
    """Owns all SSE/WebSocket subscriber state and real-time event distribution.

    Sprint 5.60: Extracted from AlertManager to eliminate thin delegation
    wrappers and centralize real-time event distribution logic.  Uses an
    ``RLock`` internally to prevent the reentrant deadlock that existed
    when ``_flush_ws_batch`` was called while the batch-buffer lock was
    already held.

    Sprint 5.63: Adds ``_WSConnectionPool`` for scenarios with >50
    concurrent WebSocket connections. When active, subscribers are
    grouped into worker groups of 10, reducing per-connection
    overhead and improving fan-out throughput. Pooling is transparent
    — it only activates when the subscriber count exceeds the
    threshold and deactivates when load drops.

    All ``asyncio.get_event_loop()`` calls have been replaced with
    ``asyncio.get_running_loop()`` + graceful fallback, eliminating
    Python 3.10+ deprecation warnings.
    """

    def __init__(self, config: AlertConfig) -> None:
        self._config = config
        # SSE subscriber queues
        self._sse_subscribers: list[Any] = []
        # WebSocket subscriber connections
        self._ws_subscribers: list[Any] = []
        # WebSocket batching state
        self._ws_batch_buffer: list[dict] = []
        self._ws_batch_flush_scheduled: bool = False
        self._ws_batch_total_flushes: int = 0
        self._ws_batch_total_events_sent: int = 0
        # WebSocket compression metrics
        self._ws_compression_negotiated: bool = False
        self._ws_compression_bytes_saved_estimate: int = 0
        self._ws_permessage_deflate_negotiated: bool = False
        self._ws_native_deflate_bytes_saved: int = 0
        # Sprint 5.63: Connection pool for high-concurrency WS delivery
        self._ws_pool = _WSConnectionPool()
        # RLock prevents deadlock when _flush_ws_batch is called from
        # within a code path that already holds the lock.
        self._lock = threading.RLock()

    # -- SSE subscriber management ----------------------------------------

    def add_sse_subscriber(self, queue: Any) -> None:
        """Add an asyncio.Queue as an SSE subscriber."""
        with self._lock:
            self._sse_subscribers.append(queue)

    def remove_sse_subscriber(self, queue: Any) -> None:
        """Remove an SSE subscriber queue."""
        with self._lock:
            try:
                self._sse_subscribers.remove(queue)
            except ValueError:
                pass

    def _notify_sse_subscribers(self, event: dict) -> None:
        """Push an event to all SSE subscriber queues (immediate)."""
        with self._lock:
            subscribers = list(self._sse_subscribers)

        for queue in subscribers:
            try:
                if asyncio is not None and hasattr(queue, "put_nowait"):
                    queue.put_nowait(event)
            except Exception:
                pass  # Subscriber queue may be full or closed

    # -- WebSocket subscriber management ----------------------------------

    def add_ws_subscriber(self, websocket: Any) -> None:
        """Add a WebSocket connection as a subscriber.

        Sprint 5.63: Triggers connection pool activation check.
        """
        with self._lock:
            self._ws_subscribers.append(websocket)
        self._ws_pool.maybe_activate(len(self._ws_subscribers))

    def remove_ws_subscriber(self, websocket: Any) -> None:
        """Remove a WebSocket subscriber.

        Sprint 5.63: Triggers connection pool deactivation check.
        """
        with self._lock:
            try:
                self._ws_subscribers.remove(websocket)
            except ValueError:
                pass
        self._ws_pool.maybe_deactivate(len(self._ws_subscribers))

    def _push_event_to_ws_subscribers(self, event: dict) -> None:
        """Push a single event to all WebSocket subscribers (immediate mode).

        Sprint 5.62: Optimized for high subscriber counts using
        ``asyncio.gather()`` when inside a running event loop.  This
        enables concurrent delivery to all subscribers rather than
        sequential iteration, significantly reducing fan-out latency
        for >20 subscribers.

        Sprint 5.63: When subscriber count exceeds the connection pool
        threshold (50), subscribers are grouped into worker groups and
        delivered concurrently per group, reducing per-connection
        scheduling overhead.
        """
        with self._lock:
            ws_subs = list(self._ws_subscribers)

        if not ws_subs:
            return

        # Sprint 5.63: Check connection pool activation
        pool_active = self._ws_pool.maybe_activate(len(ws_subs))

        # Sprint 5.62: Use asyncio.gather for concurrent fan-out when
        # inside a running event loop (typical for FastAPI WebSocket handlers).
        if asyncio is not None:
            try:
                asyncio.get_running_loop()

                # Inside a running loop — schedule concurrent fan-out
                async def _concurrent_fan_out() -> None:
                    if pool_active:
                        # Sprint 5.63: Pooled delivery — group subscribers
                        # and deliver concurrently per group
                        groups = self._ws_pool.group_subscribers(ws_subs)
                        group_tasks = []
                        for group in groups:
                            group_tasks.append(self._pooled_group_send(group, event))
                        await asyncio.gather(*group_tasks, return_exceptions=True)
                        self._ws_pool._total_pool_deliveries += 1
                    else:
                        tasks = []
                        for ws in ws_subs:
                            if hasattr(ws, "send_json"):
                                tasks.append(self._safe_send_json(ws, event))
                            elif hasattr(ws, "put_nowait"):
                                try:
                                    ws.put_nowait(event)
                                except Exception:
                                    pass
                        if tasks:
                            await asyncio.gather(*tasks, return_exceptions=True)
                        self._ws_pool._total_direct_deliveries += 1

                asyncio.ensure_future(_concurrent_fan_out())
                return
            except RuntimeError:
                pass  # No running loop — fall through to sync path

        # Fallback: synchronous iteration (when no event loop is running)
        for ws in ws_subs:
            try:
                if hasattr(ws, "send_json"):
                    self._async_send_json(ws, event)
                elif hasattr(ws, "put_nowait"):
                    ws.put_nowait(event)
            except Exception:
                pass  # WebSocket may be closed
        self._ws_pool._total_direct_deliveries += 1

    # -- Unified real-time notification -----------------------------------

    def notify_realtime_subscribers(self, event: dict) -> None:
        """Push an event to all SSE and WebSocket subscribers.

        SSE subscribers always receive events immediately.  When
        WebSocket batching is enabled (``ws_batch_window_seconds > 0``),
        WS events are buffered and flushed as a batch after the window
        elapses.  If the buffer exceeds ``ws_batch_max_size``, it is
        flushed immediately.
        """
        # SSE — always immediate
        self._notify_sse_subscribers(event)

        batch_window = self._config.ws_batch_window_seconds
        if batch_window > 0:
            with self._lock:
                self._ws_batch_buffer.append(event)
                if not self._ws_batch_flush_scheduled:
                    self._ws_batch_flush_scheduled = True
                    self._schedule_batch_flush(batch_window)
                # If buffer exceeds max batch size, flush immediately.
                # Safe because we use RLock — the reentrant acquire in
                # _flush_ws_batch will succeed.
                if len(self._ws_batch_buffer) >= self._config.ws_batch_max_size:
                    self._flush_ws_batch()
        else:
            self._push_event_to_ws_subscribers(event)

    def _schedule_batch_flush(self, delay: float) -> None:
        """Schedule a flush after the batch window (must be called with _lock held)."""
        if asyncio is not None:
            try:
                asyncio.get_running_loop()
                # We are inside a running loop — schedule async flush
                asyncio.ensure_future(self._flush_ws_batch_later(delay))
            except RuntimeError:
                # No running loop — fall back to threading.Timer
                threading.Timer(delay, self._flush_ws_batch).start()
        else:
            threading.Timer(delay, self._flush_ws_batch).start()

    async def _flush_ws_batch_later(self, delay: float) -> None:
        """Async coroutine that flushes the WebSocket batch after a delay."""
        await asyncio.sleep(delay)
        self._flush_ws_batch()

    def _flush_ws_batch(self) -> None:
        """Flush buffered WebSocket events as a single batch message.

        Because this is called from within a lock-holding code path
        (``notify_realtime_subscribers`` when buffer exceeds max size),
        we use RLock so the reentrant acquire succeeds.
        """
        with self._lock:
            if not self._ws_batch_buffer:
                self._ws_batch_flush_scheduled = False
                return
            events = list(self._ws_batch_buffer)
            self._ws_batch_buffer = []
            self._ws_batch_flush_scheduled = False

        batch_msg = {
            "event": "batch_events",
            "events": events,
            "count": len(events),
        }

        with self._lock:
            ws_subs = list(self._ws_subscribers)

        # Sprint 5.62: Use concurrent fan-out for batch delivery too
        if asyncio is not None:
            try:
                asyncio.get_running_loop()

                async def _concurrent_batch_fan_out() -> None:
                    tasks = []
                    for ws in ws_subs:
                        if hasattr(ws, "send_json"):
                            tasks.append(self._safe_send_json(ws, batch_msg))
                        elif hasattr(ws, "put_nowait"):
                            try:
                                ws.put_nowait(batch_msg)
                            except Exception:
                                pass
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)

                asyncio.ensure_future(_concurrent_batch_fan_out())
            except RuntimeError:
                # No running loop — fall back to sequential delivery
                for ws in ws_subs:
                    try:
                        if hasattr(ws, "send_json"):
                            self._async_send_json(ws, batch_msg)
                        elif hasattr(ws, "put_nowait"):
                            ws.put_nowait(batch_msg)
                    except Exception:
                        pass
        else:
            for ws in ws_subs:
                try:
                    if hasattr(ws, "send_json"):
                        self._async_send_json(ws, batch_msg)
                    elif hasattr(ws, "put_nowait"):
                        ws.put_nowait(batch_msg)
                except Exception:
                    pass

        with self._lock:
            self._ws_batch_total_flushes += 1
            self._ws_batch_total_events_sent += len(events)

        logger.debug(
            "ws_batch_flushed",
            total_flushes=self._ws_batch_total_flushes,
            events_in_batch=len(events),
        )

    # -- Async helper -----------------------------------------------------

    @staticmethod
    async def _safe_send_json(ws: Any, data: dict) -> None:
        """Safely send JSON to a WebSocket, catching all exceptions.

        Sprint 5.62: New async helper for use with ``asyncio.gather()``
        in concurrent fan-out.  Returns None on any error so that one
        failed subscriber does not block delivery to others.
        """
        try:
            await ws.send_json(data)
        except Exception:
            pass  # WebSocket may be closed or broken

    async def _pooled_group_send(self, group: list[Any], data: dict) -> None:
        """Deliver *data* to all WebSocket subscribers in a pool group.

        Sprint 5.63: Sends to each subscriber in the group concurrently
        via ``asyncio.gather()``.  One failed connection does not block
        the rest of the group.
        """
        tasks = []
        for ws in group:
            if hasattr(ws, "send_json"):
                tasks.append(self._safe_send_json(ws, data))
            elif hasattr(ws, "put_nowait"):
                try:
                    ws.put_nowait(data)
                except Exception:
                    pass
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _async_send_json(ws: Any, data: dict) -> None:
        """Send JSON to a WebSocket, handling both async and sync contexts.

        Uses ``asyncio.get_running_loop()`` (Python 3.7+) to detect
        whether we are inside a running event loop.  Falls back
        gracefully when no loop is running.
        """
        if asyncio is None:
            return
        try:
            asyncio.get_running_loop()
            # Inside a running loop — schedule the send as a task
            asyncio.ensure_future(ws.send_json(data))
        except RuntimeError:
            # No running loop — nothing to do (can't call run_until_complete
            # from a non-async context safely)
            pass

    # -- Compression methods ----------------------------------------------

    def compress_ws_message(self, data: str) -> tuple[str, bool]:
        """Compress a WebSocket message using zlib deflate."""
        if not self._config.ws_compression_enabled:
            return data, False
        try:
            import base64
            import zlib

            raw_bytes = data.encode("utf-8")
            compressed = zlib.compress(raw_bytes, level=6)
            compressed_b64 = base64.b64encode(compressed).decode("ascii")

            if len(compressed_b64) < len(data):
                saved = len(data) - len(compressed_b64)
                with self._lock:
                    self._ws_compression_bytes_saved_estimate += saved
                return compressed_b64, True
            return data, False
        except Exception as exc:
            logger.debug("ws_compression_failed", error=str(exc))
            return data, False

    def decompress_ws_message(self, compressed_b64: str) -> str:
        """Decompress a base64-encoded zlib-compressed WebSocket message."""
        try:
            import base64
            import zlib

            compressed = base64.b64decode(compressed_b64)
            decompressed = zlib.decompress(compressed)
            return decompressed.decode("utf-8")
        except Exception as exc:
            logger.debug("ws_decompression_failed", error=str(exc))
            return compressed_b64

    def get_compression_status(self) -> dict[str, Any]:
        """Return WebSocket compression status and metrics."""
        with self._lock:
            return {
                "enabled": self._config.ws_compression_enabled,
                "negotiated": self._ws_compression_negotiated,
                "bytes_saved_estimate": self._ws_compression_bytes_saved_estimate,
            }

    def set_ws_permessage_deflate_negotiated(self, negotiated: bool) -> None:
        """Set whether native permessage-deflate was negotiated."""
        self._ws_permessage_deflate_negotiated = negotiated
        if negotiated:
            logger.info("ws_native_permessage_deflate_negotiated", mode="native")

    def compress_ws_message_native_aware(self, data: str) -> tuple[str, bool]:
        """Compress with native deflate awareness (no-op if native active)."""
        if not self._config.ws_compression_enabled:
            return data, False
        if self._config.ws_native_permessage_deflate_enabled and self._ws_permessage_deflate_negotiated:
            return data, False
        return self.compress_ws_message(data)

    def decompress_ws_message_native_aware(self, data: str) -> str:
        """Decompress with native deflate awareness (no-op if native active)."""
        if self._config.ws_native_permessage_deflate_enabled and self._ws_permessage_deflate_negotiated:
            return data
        return self.decompress_ws_message(data)

    def get_native_deflate_status(self) -> dict[str, Any]:
        """Return native permessage-deflate negotiation status."""
        with self._lock:
            return {
                "enabled": self._config.ws_native_permessage_deflate_enabled,
                "native_negotiated": self._ws_permessage_deflate_negotiated,
                "bytes_saved_estimate": self._ws_compression_bytes_saved_estimate,
            }

    # -- Status summary ---------------------------------------------------

    def get_status_summary(self) -> dict[str, Any]:
        """Return a status dict for the StatusAggregator."""
        with self._lock:
            return {
                "sse_subscribers": len(self._sse_subscribers),
                "ws_subscribers": len(self._ws_subscribers),
                "ws_batching": {
                    "batch_window_seconds": self._config.ws_batch_window_seconds,
                    "batch_max_size": self._config.ws_batch_max_size,
                    "total_flushes": self._ws_batch_total_flushes,
                    "total_events_sent": self._ws_batch_total_events_sent,
                    "buffered_count": len(self._ws_batch_buffer),
                },
                "ws_compression": self.get_compression_status(),
                "ws_native_deflate": {
                    "enabled": self._config.ws_native_permessage_deflate_enabled,
                    "negotiated": self._ws_permessage_deflate_negotiated,
                    "mode": "native" if self._ws_permessage_deflate_negotiated else "application_level",
                },
                # Sprint 5.63: Connection pool status
                "ws_connection_pool": self._ws_pool.get_status(),
            }


class DeliveryManager:
    """Owns delivery counters, per-transport success/failure tracking, and
    multi-channel delivery receipts.

    Sprint 5.60: Extracted from AlertManager to provide clean public
    accessors used by ``StatusAggregator`` instead of reaching into
    private attributes.
    """

    def __init__(self) -> None:
        self._total_alerts_sent: int = 0
        self._total_alerts_rate_limited: int = 0
        self._total_send_failures: int = 0
        self._total_webhook_retries: int = 0
        self._delivery_success_by_transport: dict[str, int] = {}
        self._delivery_failure_by_transport: dict[str, int] = {}
        # correlation_id -> {channel -> {receipt_key, receipt_value, confirmed_at}}
        self._delivery_receipts: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    # -- Counter mutators -------------------------------------------------

    def increment_sent(self) -> None:
        with self._lock:
            self._total_alerts_sent += 1

    def increment_rate_limited(self) -> None:
        with self._lock:
            self._total_alerts_rate_limited += 1

    def increment_send_failure(self) -> None:
        with self._lock:
            self._total_send_failures += 1

    def increment_webhook_retry(self) -> None:
        with self._lock:
            self._total_webhook_retries += 1

    # -- Transport success/failure tracking --------------------------------

    def record_delivery_success(self, transport: str) -> None:
        with self._lock:
            self._delivery_success_by_transport[transport] = self._delivery_success_by_transport.get(transport, 0) + 1

    def record_delivery_failure(self, transport: str) -> None:
        with self._lock:
            self._delivery_failure_by_transport[transport] = self._delivery_failure_by_transport.get(transport, 0) + 1

    def record_transport_result(self, transport_name: str, status: str) -> None:
        """Record a transport result as either success or failure."""
        if status == "delivered":
            self.record_delivery_success(transport_name)
        else:
            self.record_delivery_failure(transport_name)

    # -- Public accessors --------------------------------------------------

    def get_total_alerts_sent(self) -> int:
        with self._lock:
            return self._total_alerts_sent

    def get_total_rate_limited(self) -> int:
        with self._lock:
            return self._total_alerts_rate_limited

    def get_total_send_failures(self) -> int:
        with self._lock:
            return self._total_send_failures

    def get_total_webhook_retries(self) -> int:
        with self._lock:
            return self._total_webhook_retries

    def get_delivery_success_by_transport(self) -> dict[str, int]:
        """Return a copy of per-transport success counts."""
        with self._lock:
            return dict(self._delivery_success_by_transport)

    def get_delivery_failure_by_transport(self) -> dict[str, int]:
        """Return a copy of per-transport failure counts."""
        with self._lock:
            return dict(self._delivery_failure_by_transport)

    # -- Delivery receipts -------------------------------------------------

    def record_delivery_receipts(
        self,
        correlation_id: str,
        transport_results: dict[str, dict[str, Any]],
        config: AlertConfig | None = None,
    ) -> None:
        """Record delivery receipts from transport results.

        Extracts receipt data from each transport's result and stores
        it per correlation ID.  When ``delivery_receipt_polling_enabled``
        is set in *config*, also tracks email "sent" status even without
        push receipts.
        """
        receipts: dict[str, dict[str, Any]] = {}
        now_iso = datetime.now(timezone.utc).isoformat()

        for channel, result in transport_results.items():
            receipt = result.get("receipt")
            if receipt:
                receipts[channel] = {
                    **receipt,
                    "confirmed_at": now_iso,
                    "delivery_status": result.get("status", "unknown"),
                }
            elif config is not None and config.delivery_receipt_polling_enabled and channel == "email":
                receipts[channel] = {
                    "delivery_status": "sent",
                    "confirmed_at": now_iso,
                    "polling_enabled": True,
                }

        if receipts:
            with self._lock:
                self._delivery_receipts[correlation_id] = receipts

    def get_delivery_receipts(self, correlation_id: str) -> dict[str, Any]:
        """Return delivery receipts for a given correlation ID."""
        with self._lock:
            return self._delivery_receipts.get(correlation_id, {})

    def get_all_delivery_receipts(self, limit: int = 50) -> dict[str, dict[str, Any]]:
        """Return all delivery receipts, limited to most recent."""
        with self._lock:
            items = list(self._delivery_receipts.items())
        if limit > 0:
            items = items[-limit:]
        return dict(items)

    def get_enhanced_delivery_receipts(self, correlation_id: str) -> dict[str, Any]:
        """Like get_delivery_receipts but includes email polling status."""
        with self._lock:
            receipts = dict(self._delivery_receipts.get(correlation_id, {}))
        return receipts

    def get_delivery_receipts_count(self) -> int:
        """Return the number of tracked delivery receipts."""
        with self._lock:
            return len(self._delivery_receipts)

    def update_email_delivery_status(
        self,
        correlation_id: str,
        email_status: dict[str, Any],
    ) -> None:
        """Update email delivery status within an existing receipt record."""
        with self._lock:
            if correlation_id in self._delivery_receipts:
                if "email" in self._delivery_receipts[correlation_id]:
                    self._delivery_receipts[correlation_id]["email"].update(email_status)
                else:
                    self._delivery_receipts[correlation_id]["email"] = email_status

    def iter_delivery_receipts(self) -> Iterator[tuple[str, dict[str, dict[str, Any]]]]:
        """Iterate over all delivery receipt entries (for polling)."""
        with self._lock:
            yield from list(self._delivery_receipts.items())

    # -- Status summary ---------------------------------------------------

    def get_status_summary(self) -> dict[str, Any]:
        """Return a status dict for the StatusAggregator."""
        with self._lock:
            return {
                "total_alerts_sent": self._total_alerts_sent,
                "total_rate_limited": self._total_alerts_rate_limited,
                "total_send_failures": self._total_send_failures,
                "total_webhook_retries": self._total_webhook_retries,
                "delivery_by_transport": {
                    "success": dict(self._delivery_success_by_transport),
                    "failure": dict(self._delivery_failure_by_transport),
                },
                "delivery_receipts": {
                    "tracked_count": len(self._delivery_receipts),
                },
            }


class ThrottleManager:
    """Owns all throttle and circuit-breaker state and logic.

    Sprint 5.61: Extracted from AlertManager to centralize rate-based
    throttling decisions and circuit-breaker state management.
    """

    def __init__(self, config: AlertConfig, history_store: Any = None) -> None:
        self._config = config
        self._history_store = history_store
        self._lock = threading.Lock()
        # Sprint 5.38: Alert throttling & circuit breaker
        self._throttle_alert_timestamps: list[float] = []
        self._circuit_breaker_active: bool = False
        self._circuit_breaker_activated_at: float = 0.0
        self._total_throttled_alerts: int = 0
        self._total_circuit_breaker_activations: int = 0
        # Sprint 5.39: Circuit breaker auto-tuning
        self._cb_effective_threshold: int = 0
        self._cb_baseline_rates: dict[str, float] = {}
        self._cb_auto_tune_last_computed: float = 0.0
        self._cb_auto_tune_compute_interval: int = 300
        self._cb_threshold_adjustments: list[dict] = []
        self._total_auto_tune_adjustments: int = 0
        # Sprint 5.63: Circuit breaker status cache (~100ms TTL)
        # Avoids repeated lock acquisition on the hot path during high
        # alert volume.  The cache stores the result of
        # get_circuit_breaker_status() and is invalidated after
        # ``_cb_cache_ttl_seconds``.
        self._cb_cache_ttl_seconds: float = 0.1  # 100ms
        self._cb_cache: dict[str, Any] | None = None
        self._cb_cache_timestamp: float = 0.0

    # -----------------------------------------------------------------------
    # Sprint 5.38: Alert throttling & circuit breaker
    # -----------------------------------------------------------------------

    def record_throttle_window(self, now: float) -> None:
        """Record an alert timestamp in the sliding throttle window.

        Sprint 5.38: Maintains a sliding 60-second window of alert
        timestamps for rate-based throttling decisions.
        """
        with self._lock:
            self._throttle_alert_timestamps.append(now)
            # Prune entries older than 60 seconds
            cutoff = now - 60
            self._throttle_alert_timestamps = [ts for ts in self._throttle_alert_timestamps if ts > cutoff]

    def check_circuit_breaker(self, now: float) -> bool:
        """Check if the circuit breaker should be active.

        Sprint 5.38: Returns True if the alert rate exceeds
        throttle_threshold_per_minute and circuit_breaker_enabled
        is True. Once activated, the circuit breaker stays active
        for circuit_breaker_cooldown_seconds, then automatically
        resets for the next evaluation cycle.

        The circuit breaker uses a "half-open" approach: after the
        cooldown expires, the next check evaluates the current rate.
        If still high, the breaker re-activates; if low, it deactivates.

        Sprint 5.39: If auto-tune is enabled, the threshold is
        dynamically adjusted based on historical alert rate patterns.
        """
        if not self._config.circuit_breaker_enabled:
            return False

        # Sprint 5.39: Update auto-tune threshold if enabled
        if self._config.circuit_breaker_auto_tune_enabled:
            self.update_cb_auto_tune()

        # Sprint 5.39: Use effective threshold (may be auto-tuned)
        threshold = self.get_cb_effective_threshold()

        with self._lock:
            # If circuit breaker is active, check cooldown
            if self._circuit_breaker_active:
                elapsed = now - self._circuit_breaker_activated_at
                if elapsed < self._config.circuit_breaker_cooldown_seconds:
                    return True
                else:
                    # Cooldown expired — check if rate is still high
                    rate = len(self._throttle_alert_timestamps)
                    if rate >= threshold:
                        # Still high — re-activate
                        self._circuit_breaker_activated_at = now
                        self._total_circuit_breaker_activations += 1
                        self.invalidate_cb_cache()  # Sprint 5.63
                        logger.warning(
                            "circuit_breaker_reactivated",
                            alert_rate=rate,
                            threshold=threshold,
                        )
                        return True
                    else:
                        # Rate has dropped — deactivate
                        self._circuit_breaker_active = False
                        self.invalidate_cb_cache()  # Sprint 5.63
                        logger.info(
                            "circuit_breaker_deactivated",
                            alert_rate=rate,
                        )
                        return False

            # Not active — check if we should activate
            rate = len(self._throttle_alert_timestamps)
            if rate >= threshold:
                self._circuit_breaker_active = True
                self._circuit_breaker_activated_at = now
                self._total_circuit_breaker_activations += 1
                self.invalidate_cb_cache()  # Sprint 5.63
                logger.warning(
                    "circuit_breaker_activated",
                    alert_rate=rate,
                    threshold=threshold,
                )
                return True

            return False

    def get_circuit_breaker_status(self) -> dict[str, Any]:
        """Return current circuit breaker status and metrics.

        Sprint 5.38: Provides visibility into the circuit breaker
        state for the dashboard and API endpoints.

        Sprint 5.39: Includes auto-tune info and effective threshold.

        Sprint 5.63: Uses a lightweight TTL cache (~100ms) to avoid
        repeated lock acquisition on the hot path during high alert
        volume.  The cache is invalidated whenever the circuit breaker
        state changes (activation/deactivation) or after the TTL
        expires.
        """
        now = time.time()

        # Sprint 5.63: Return cached result if still valid
        if self._cb_cache is not None:
            cache_age = now - self._cb_cache_timestamp
            if cache_age < self._cb_cache_ttl_seconds:
                # Update cache_age_ms on cache hit for observability
                result = dict(self._cb_cache)
                result["cache_age_ms"] = round(cache_age * 1000, 1)
                return result

        with self._lock:
            rate = len(self._throttle_alert_timestamps)
            result = {
                "enabled": self._config.circuit_breaker_enabled,
                "active": self._circuit_breaker_active,
                "current_rate_per_minute": rate,
                "threshold": self._config.throttle_threshold_per_minute,
                "effective_threshold": self.get_cb_effective_threshold(),
                "cooldown_seconds": self._config.circuit_breaker_cooldown_seconds,
                "total_activations": self._total_circuit_breaker_activations,
                "total_throttled_alerts": self._total_throttled_alerts,
                "activated_at": self._circuit_breaker_activated_at,
                "cooldown_remaining": max(
                    0, self._config.circuit_breaker_cooldown_seconds - (now - self._circuit_breaker_activated_at)
                )
                if self._circuit_breaker_active
                else 0,
                "auto_tune": self.get_cb_auto_tune_status(),
                # Sprint 5.63: Cache metadata for observability
                "cache_age_ms": round((now - self._cb_cache_timestamp) * 1000, 1)
                if self._cb_cache is not None
                else None,
            }

        # Sprint 5.63: Update cache
        self._cb_cache = result
        self._cb_cache_timestamp = now
        return result

    def invalidate_cb_cache(self) -> None:
        """Invalidate the circuit breaker status cache.

        Sprint 5.63: Called when circuit breaker state changes
        (activation, deactivation, auto-tune update) to ensure
        the next ``get_circuit_breaker_status()`` call returns
        fresh data.
        """
        self._cb_cache = None

    # -----------------------------------------------------------------------
    # Sprint 5.39: Circuit breaker auto-tuning
    # -----------------------------------------------------------------------

    def compute_cb_auto_tune_threshold(self) -> int:
        """Compute the auto-tuned circuit breaker threshold.

        Sprint 5.39: Queries alert history from the store to compute
        baseline rates by (hour_of_day, day_of_week).  The effective
        threshold is baseline_rate * sensitivity, clamped to
        [min_threshold, max_threshold].

        If auto-tune is disabled, returns the static config threshold.
        If no history store is attached, falls back to config threshold.
        """
        if not self._config.circuit_breaker_auto_tune_enabled:
            return self._config.throttle_threshold_per_minute

        # Need a history store for pattern learning
        if self._history_store is None:
            logger.debug(
                "cb_auto_tune_no_store",
                reason="no AlertHistoryStore attached",
            )
            return self._config.throttle_threshold_per_minute

        # Determine the lookback window
        lookback_hours = self._config.circuit_breaker_auto_tune_lookback_hours
        cutoff = time.time() - (lookback_hours * 3600)

        # Query alert history from the store
        try:
            if hasattr(self._history_store, "get_alert_history_since"):
                alerts = self._history_store.get_alert_history_since(since=cutoff)
            elif hasattr(self._history_store, "query_alerts"):
                alerts = self._history_store.query_alerts(since_epoch=cutoff)
            else:
                # Fallback: try the standard get_alerts method
                since_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
                alerts = (
                    self._history_store.get_alerts(since=since_iso)
                    if hasattr(self._history_store, "get_alerts")
                    else []
                )
        except Exception as exc:
            logger.warning(
                "cb_auto_tune_store_query_failed",
                error=str(exc),
            )
            return self._config.throttle_threshold_per_minute

        if not alerts:
            logger.debug(
                "cb_auto_tune_no_history",
                reason="no alerts found in lookback window",
            )
            return self._config.throttle_threshold_per_minute

        # Build per-timeslot rates: (hour_of_day, day_of_week) -> list of counts per minute
        # We bucket alerts into hourly slots and compute the rate per minute for each slot
        timeslot_counts: dict[str, list[int]] = {}
        for alert_record in alerts:
            ts = alert_record.get("timestamp", "")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                hour = dt.hour
                dow = dt.weekday()  # 0=Monday
                key = f"{hour}:{dow}"
                if key not in timeslot_counts:
                    timeslot_counts[key] = []
                # We count 1 per alert in this timeslot
                timeslot_counts[key].append(1)
            except (ValueError, TypeError):
                continue

        if not timeslot_counts:
            return self._config.throttle_threshold_per_minute

        # Compute average rate per minute for each timeslot
        # Each bucket represents the alerts in that hour across multiple weeks
        # Rate = total_alerts_in_slot / (number_of_weeks * 60)  [alerts per minute]
        baseline_rates: dict[str, float] = {}
        num_weeks = max(1, lookback_hours / 168)
        for key, counts in timeslot_counts.items():
            # Rate per minute for this timeslot
            rate_per_minute = len(counts) / (num_weeks * 60)
            baseline_rates[key] = rate_per_minute

        self._cb_baseline_rates = baseline_rates

        # Find the current timeslot
        now = time.time()
        now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
        current_key = f"{now_dt.hour}:{now_dt.weekday()}"

        # Get the baseline rate for the current timeslot, or average across all
        baseline_rate = baseline_rates.get(current_key)
        if baseline_rate is None:
            # No data for this exact timeslot — use the average of all rates
            if baseline_rates:
                baseline_rate = sum(baseline_rates.values()) / len(baseline_rates)
            else:
                return self._config.throttle_threshold_per_minute

        # Compute effective threshold: baseline * sensitivity, clamped
        sensitivity = self._config.circuit_breaker_auto_tune_sensitivity
        min_threshold = self._config.circuit_breaker_auto_tune_min_threshold
        max_threshold = self._config.circuit_breaker_auto_tune_max_threshold

        effective = int(baseline_rate * sensitivity)
        effective = max(min_threshold, min(max_threshold, effective))

        logger.info(
            "cb_auto_tune_computed",
            current_timeslot=current_key,
            baseline_rate=round(baseline_rate, 3),
            sensitivity=sensitivity,
            raw_threshold=int(baseline_rate * sensitivity),
            effective_threshold=effective,
            baseline_rates_count=len(baseline_rates),
        )

        return effective

    def get_cb_effective_threshold(self) -> int:
        """Return the effective circuit breaker threshold.

        Sprint 5.39: Returns the auto-tuned threshold if auto-tune
        is enabled and a threshold has been computed. Otherwise
        returns the static config threshold.
        """
        if self._cb_effective_threshold > 0:
            return self._cb_effective_threshold
        return self._config.throttle_threshold_per_minute

    def update_cb_auto_tune(self) -> dict:
        """Recompute the auto-tuned threshold if enough time has elapsed.

        Sprint 5.39: Compares the old and new threshold, records
        adjustments for audit trail when the threshold changes.
        Returns an info dict with the computation details.
        """
        result: dict[str, Any] = {
            "old_threshold": self.get_cb_effective_threshold(),
            "new_threshold": self.get_cb_effective_threshold(),
            "baseline_rate": 0.0,
            "reason": "no_change",
        }

        if not self._config.circuit_breaker_auto_tune_enabled:
            result["reason"] = "auto_tune_disabled"
            return result

        now = time.time()
        elapsed = now - self._cb_auto_tune_last_computed

        if elapsed < self._cb_auto_tune_compute_interval:
            result["reason"] = "interval_not_elapsed"
            result["seconds_remaining"] = int(self._cb_auto_tune_compute_interval - elapsed)
            return result

        # Compute new threshold
        old_threshold = self.get_cb_effective_threshold()
        new_threshold = self.compute_cb_auto_tune_threshold()

        # Determine current baseline rate for reporting
        now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
        current_key = f"{now_dt.hour}:{now_dt.weekday()}"
        baseline_rate = self._cb_baseline_rates.get(current_key, 0.0)

        result["old_threshold"] = old_threshold
        result["new_threshold"] = new_threshold
        result["baseline_rate"] = round(baseline_rate, 3)

        if new_threshold != old_threshold:
            # Record the adjustment
            adjustment = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "old_threshold": old_threshold,
                "new_threshold": new_threshold,
                "baseline_rate": round(baseline_rate, 3),
                "timeslot": current_key,
                "sensitivity": self._config.circuit_breaker_auto_tune_sensitivity,
            }
            self._cb_threshold_adjustments.append(adjustment)
            # Keep only the last 50 adjustments
            if len(self._cb_threshold_adjustments) > 50:
                self._cb_threshold_adjustments = self._cb_threshold_adjustments[-50:]
            self._total_auto_tune_adjustments += 1

            result["reason"] = "threshold_adjusted"

            logger.info(
                "cb_auto_tune_adjusted",
                old_threshold=old_threshold,
                new_threshold=new_threshold,
                baseline_rate=round(baseline_rate, 3),
                timeslot=current_key,
            )
        else:
            result["reason"] = "threshold_unchanged"

        # Update the effective threshold
        self._cb_effective_threshold = new_threshold
        self._cb_auto_tune_last_computed = now

        return result

    def get_cb_auto_tune_status(self) -> dict:
        """Return the circuit breaker auto-tuning status.

        Sprint 5.39: Provides visibility into the auto-tuning state
        for the dashboard and API endpoints.
        """
        return {
            "enabled": self._config.circuit_breaker_auto_tune_enabled,
            "effective_threshold": self.get_cb_effective_threshold(),
            "config_threshold": self._config.throttle_threshold_per_minute,
            "baseline_rates_count": len(self._cb_baseline_rates),
            "last_computed": self._cb_auto_tune_last_computed,
            "recent_adjustments": self._cb_threshold_adjustments[-5:],
            "total_adjustments": self._total_auto_tune_adjustments,
        }

    def increment_throttled_alerts(self) -> None:
        """Increment the count of throttled alerts."""
        with self._lock:
            self._total_throttled_alerts += 1

    def get_status_summary(self) -> dict[str, Any]:
        """Return a status dict for the StatusAggregator."""
        return {
            "circuit_breaker": self.get_circuit_breaker_status(),
            "circuit_breaker_auto_tune": self.get_cb_auto_tune_status(),
        }


class PredictionManager:
    """Owns all prediction, causal chain, and transition learning state.

    Sprint 5.61: Extracted from AlertManager to centralize causal chain
    prediction, learned transition probability models, and prediction
    accuracy tracking.
    """

    _CAUSAL_PREDICTION_CHAIN = {
        "pool_adjustment": ["quality_degradation", "batch_reduction"],
        "quality_degradation": ["batch_reduction"],
        "batch_reduction": [],
    }

    def __init__(
        self, config: AlertConfig, history_store: Any = None, realtime_bus: RealtimeEventBus | None = None
    ) -> None:
        self._config = config
        self._history_store = history_store
        self._realtime_bus = realtime_bus
        self._lock = threading.Lock()
        # Sprint 5.36: Causal chain prediction state
        self._causal_predictions: dict[str, list[dict]] = {}  # subject → predicted alerts
        self._total_predictions_made: int = 0
        # Sprint 5.37: Prediction accuracy tracking
        self._prediction_outcomes: dict[str, dict] = {}  # prediction_id -> outcome record
        self._prediction_accuracy_hits: int = 0
        self._prediction_accuracy_misses: int = 0
        # Sprint 5.38: Learned transition probability model
        self._transition_counts: dict[tuple[str, str], int] = {}
        self._transition_totals: dict[str, int] = {}
        self._alert_type_sequence: list[tuple[str, str, float]] = []
        self._learned_model_last_trained: float = 0.0
        self._total_learned_predictions_made: int = 0
        # Sprint 5.39: Transition probability persistence and retraining
        self._transition_persistence_enabled: bool = config.transition_persistence_enabled
        self._retrain_interval_seconds: int = config.retrain_interval_seconds
        self._retrain_after_n_alerts: int = config.retrain_after_n_alerts
        self._alerts_since_last_retrain: int = 0
        self._last_retrain_time: float = 0.0
        self._total_retraining_events: int = 0
        # Sprint 5.47: Confidence calibration callback (set by AlertManager)
        self._calibration_callback: Any = None
        # Sprint 5.63: Background tracking for prediction accuracy and
        # transition learning updates.  Instead of running accuracy checks
        # and retraining synchronously on the hot path (send_alert), these
        # operations are queued and processed by a background thread,
        # reducing latency for the main alert dispatch path.
        self._bg_tracking_enabled: bool = True
        self._bg_tracking_thread: threading.Thread | None = None
        self._bg_tracking_running: bool = False
        self._bg_pending_accuracy_checks: list[Alert] = []  # alerts to check for prediction outcomes
        self._bg_pending_transitions: list[tuple[Alert, float]] = []  # (alert, timestamp) for transition learning
        self._bg_lock = threading.Lock()
        self._bg_total_accuracy_checks: int = 0
        self._bg_total_transition_learnings: int = 0

    # -- Calibration callback -----------------------------------------------

    def set_calibration_callback(self, callback: Any) -> None:
        """Set the calibration callback for learned predictions.

        Sprint 5.61: The callback receives (subject, raw_confidence) and
        returns a calibrated probability float.  When None (default),
        raw probability is used as-is.
        """
        self._calibration_callback = callback

    # -- Sprint 5.63: Background tracking ------------------------------------

    def start_bg_tracking(self) -> None:
        """Start the background prediction tracking thread.

        Sprint 5.63: Processes queued prediction accuracy checks and
        transition learning updates off the hot path.  This reduces
        send_alert() latency by deferring accuracy matching and model
        retraining to a background worker.
        """
        if not self._bg_tracking_enabled:
            return
        if self._bg_tracking_running:
            return

        self._bg_tracking_running = True

        def _tracking_loop() -> None:
            logger.info("prediction_bg_tracking_started")
            while self._bg_tracking_running:
                try:
                    self._process_bg_queue()
                except Exception as exc:
                    logger.debug("prediction_bg_tracking_error", error=str(exc))
                time.sleep(0.05)  # 50ms poll interval for low latency
            logger.info("prediction_bg_tracking_stopped")

        self._bg_tracking_thread = threading.Thread(
            target=_tracking_loop,
            daemon=True,
            name="aip-prediction-bg-tracking",
        )
        self._bg_tracking_thread.start()

    def stop_bg_tracking(self) -> None:
        """Stop the background prediction tracking thread."""
        self._bg_tracking_running = False
        if self._bg_tracking_thread is not None:
            self._bg_tracking_thread.join(timeout=2)
            self._bg_tracking_thread = None

    def enqueue_accuracy_check(self, alert: Alert) -> None:
        """Enqueue an alert for background prediction accuracy checking.

        Sprint 5.63: Instead of calling record_prediction_outcome()
        synchronously in send_alert(), the alert is queued for the
        background thread to process.  This removes O(n) prediction
        matching from the hot path.
        """
        with self._bg_lock:
            self._bg_pending_accuracy_checks.append(alert)
            # Bound queue size to prevent unbounded memory growth
            if len(self._bg_pending_accuracy_checks) > 500:
                self._bg_pending_accuracy_checks = self._bg_pending_accuracy_checks[-500:]

    def enqueue_transition_learning(self, alert: Alert, now: float) -> None:
        """Enqueue an alert for background transition learning.

        Sprint 5.63: Instead of calling record_alert_for_transition_learning()
        synchronously in send_alert(), the alert is queued for the
        background thread to process.  This removes O(n) sequence
        scanning and retraining checks from the hot path.
        """
        with self._bg_lock:
            self._bg_pending_transitions.append((alert, now))
            if len(self._bg_pending_transitions) > 500:
                self._bg_pending_transitions = self._bg_pending_transitions[-500:]

    def _process_bg_queue(self) -> None:
        """Process pending accuracy checks and transition learnings.

        Sprint 5.63: Called by the background thread. Drains the queues
        and delegates to the existing synchronous methods.
        """
        # Drain queues under lock (fast)
        with self._bg_lock:
            accuracy_items = list(self._bg_pending_accuracy_checks)
            self._bg_pending_accuracy_checks = []
            transition_items = list(self._bg_pending_transitions)
            self._bg_pending_transitions = []

        # Process accuracy checks (outside bg_lock, uses self._lock internally)
        for alert in accuracy_items:
            self.record_prediction_outcome(alert)
            self._bg_total_accuracy_checks += 1

        # Process transition learning (outside bg_lock, uses self._lock internally)
        for alert, ts in transition_items:
            self._record_transition_learning_internal(alert, ts)
            self._bg_total_transition_learnings += 1

    def _record_transition_learning_internal(self, alert: Alert, now: float) -> None:
        """Internal transition learning — same logic as record_alert_for_transition_learning
        but without the retraining check (handled by separate background retrain logic).

        Sprint 5.63: Extracted from record_alert_for_transition_learning()
        to allow background processing without retraining side-effects.
        Retraining is triggered separately by check_retrain_needed().
        """
        with self._lock:
            self._alert_type_sequence.append((alert.alert_type, alert.subject, now))
            if len(self._alert_type_sequence) > 1000:
                self._alert_type_sequence = self._alert_type_sequence[-1000:]

            for i in range(len(self._alert_type_sequence) - 2, -1, -1):
                prev_type, prev_subject, prev_ts = self._alert_type_sequence[i]
                if prev_subject == alert.subject:
                    pair = (prev_type, alert.alert_type)
                    self._transition_counts[pair] = self._transition_counts.get(pair, 0) + 1
                    self._transition_totals[prev_type] = self._transition_totals.get(prev_type, 0) + 1
                    break

            self._alerts_since_last_retrain += 1

    def get_bg_tracking_status(self) -> dict[str, Any]:
        """Return background tracking status for monitoring."""
        with self._bg_lock:
            return {
                "enabled": self._bg_tracking_enabled,
                "running": self._bg_tracking_running,
                "pending_accuracy_checks": len(self._bg_pending_accuracy_checks),
                "pending_transitions": len(self._bg_pending_transitions),
                "total_accuracy_checks": self._bg_total_accuracy_checks,
                "total_transition_learnings": self._bg_total_transition_learnings,
            }

    # -- Public property accessors ------------------------------------------

    @property
    def total_predictions_made(self) -> int:
        with self._lock:
            return self._total_predictions_made

    @property
    def causal_predictions_count(self) -> int:
        with self._lock:
            return len(self._causal_predictions)

    @property
    def total_learned_predictions_made(self) -> int:
        with self._lock:
            return self._total_learned_predictions_made

    @property
    def learned_model_last_trained(self) -> float:
        with self._lock:
            return self._learned_model_last_trained

    @property
    def transition_types_count(self) -> int:
        with self._lock:
            return len(self._transition_totals)

    @property
    def transition_persistence_enabled(self) -> bool:
        return self._transition_persistence_enabled

    @property
    def last_retrain_time(self) -> float:
        with self._lock:
            return self._last_retrain_time

    @property
    def alerts_since_last_retrain(self) -> int:
        with self._lock:
            return self._alerts_since_last_retrain

    @property
    def total_retraining_events(self) -> int:
        with self._lock:
            return self._total_retraining_events

    @property
    def retrain_interval_seconds(self) -> int:
        return self._retrain_interval_seconds

    @property
    def retrain_after_n_alerts(self) -> int:
        return self._retrain_after_n_alerts

    # -- Causal chain prediction (Sprint 5.36) -----------------------------

    def predict_causal_chain(self, alert: Alert) -> list[dict]:
        """Predict the likely next alerts in a causal chain.

        Sprint 5.36: When a causal-chain alert arrives (e.g., pool_adjustment),
        predicts what subsequent alerts are likely based on the configured
        causal chain rules and historical patterns. Returns a list of
        predicted alert dicts with alert_type, estimated delay, and confidence.

        Sprint 5.37: Each prediction now includes a prediction_id and is
        tracked for accuracy feedback. The record_prediction_outcome()
        method is called to check if this alert matches a previous prediction.
        """
        if not self._config.causal_prediction_enabled:
            return []

        # Sprint 5.37: Check if this alert matches a previous prediction
        self.record_prediction_outcome(alert)

        # Sprint 5.37: Also expire stale prediction outcomes
        self._expire_prediction_outcomes()

        if alert.alert_type not in self._CAUSAL_PREDICTION_CHAIN:
            return []

        predicted_types = self._CAUSAL_PREDICTION_CHAIN[alert.alert_type]
        if not predicted_types:
            return []

        predictions = []
        now = time.time()
        for i, pred_type in enumerate(predicted_types):
            # Estimate delay based on position in chain (rough heuristic)
            estimated_delay_seconds = (i + 1) * self._config.causal_grouping_window_seconds // 2
            # Sprint 5.37: Adjust confidence based on historical accuracy
            accuracy = self.get_prediction_accuracy()
            accuracy_factor = accuracy.get("hit_rate", 0.0)
            # Base confidence decreases with chain depth; adjusted by accuracy
            base_confidence = 1.0 - (i * 0.25)
            confidence = (
                round(base_confidence * (0.5 + 0.5 * accuracy_factor), 2)
                if accuracy_factor > 0
                else round(base_confidence, 2)
            )
            confidence = max(0.1, min(1.0, confidence))

            # Sprint 5.37: Generate prediction_id for accuracy tracking
            prediction_id = f"pred-{uuid.uuid4().hex[:8]}"

            prediction = {
                "prediction_id": prediction_id,
                "predicted_alert_type": pred_type,
                "subject": alert.subject,
                "estimated_delay_seconds": estimated_delay_seconds,
                "confidence": confidence,
                "triggered_by": alert.alert_type,
                "triggered_at": alert.timestamp,
                "predicted_at": datetime.now(timezone.utc).isoformat(),
            }
            predictions.append(prediction)

            # Sprint 5.37: Track this prediction for accuracy feedback
            with self._lock:
                self._prediction_outcomes[prediction_id] = {
                    "prediction_id": prediction_id,
                    "predicted_alert_type": pred_type,
                    "subject": alert.subject,
                    "triggered_by": alert.alert_type,
                    "predicted_at_epoch": now,
                    "predicted_at": prediction["predicted_at"],
                    "estimated_delay_seconds": estimated_delay_seconds,
                    "confidence": confidence,
                    "outcome": "pending",
                }

        with self._lock:
            subject = alert.subject
            if subject not in self._causal_predictions:
                self._causal_predictions[subject] = []
            self._causal_predictions[subject].extend(predictions)
            self._total_predictions_made += len(predictions)

        # Notify dashboard subscribers about predictions
        if self._realtime_bus is not None:
            self._realtime_bus.notify_realtime_subscribers(
                {
                    "event": "causal_predictions",
                    "subject": alert.subject,
                    "predictions": predictions,
                    "triggered_by": alert.alert_type,
                }
            )

        logger.info(
            "causal_predictions_generated",
            triggered_by=alert.alert_type,
            subject=alert.subject,
            prediction_count=len(predictions),
        )

        return predictions

    def get_causal_predictions(self, subject: str | None = None) -> dict[str, list[dict]] | list[dict]:
        """Return current causal predictions, optionally filtered by subject.

        Sprint 5.36: If subject is provided, returns predictions for that
        subject only. Otherwise returns all predictions.
        """
        with self._lock:
            if subject:
                return self._causal_predictions.get(subject, [])
            return dict(self._causal_predictions)

    # -- Prediction accuracy feedback loop (Sprint 5.37) -------------------

    def record_prediction_outcome(self, alert: Alert) -> None:
        """Check if an incoming alert matches any prediction and mark it as hit.

        Sprint 5.37: When an alert arrives, checks if it matches any
        pending prediction (same alert_type and subject). If so, marks
        the prediction as a hit. Also checks for expired predictions
        and marks them as misses.
        """
        now = time.time()
        window = self._config.prediction_accuracy_window_seconds

        with self._lock:
            # Check for expired predictions (misses)
            expired_ids = []
            for pred_id, record in self._prediction_outcomes.items():
                if record.get("outcome") == "pending":
                    predicted_at = record.get("predicted_at_epoch", 0)
                    if now - predicted_at > window:
                        record["outcome"] = "miss"
                        record["resolved_at"] = datetime.now(timezone.utc).isoformat()
                        self._prediction_accuracy_misses += 1
                        expired_ids.append(pred_id)
                        logger.info(
                            "prediction_miss",
                            prediction_id=pred_id,
                            predicted_type=record.get("predicted_alert_type", ""),
                            subject=record.get("subject", ""),
                        )

            # Check if this alert matches any pending prediction
            for pred_id, record in self._prediction_outcomes.items():
                if record.get("outcome") != "pending":
                    continue
                if record.get("predicted_alert_type") == alert.alert_type and record.get("subject") == alert.subject:
                    record["outcome"] = "hit"
                    record["resolved_at"] = datetime.now(timezone.utc).isoformat()
                    record["actual_alert_timestamp"] = alert.timestamp
                    self._prediction_accuracy_hits += 1
                    logger.info(
                        "prediction_hit",
                        prediction_id=pred_id,
                        predicted_type=alert.alert_type,
                        subject=alert.subject,
                    )
                    break  # Only match first prediction

    def get_prediction_accuracy(self) -> dict[str, Any]:
        """Return prediction accuracy metrics.

        Sprint 5.37: Computes precision, recall, hit rate, and total
        predictions tracked. Returns a dict with all accuracy metrics.
        """
        with self._lock:
            total = self._prediction_accuracy_hits + self._prediction_accuracy_misses
            pending = sum(1 for r in self._prediction_outcomes.values() if r.get("outcome") == "pending")
            hit_rate = self._prediction_accuracy_hits / total if total > 0 else 0.0
            # Precision: of resolved predictions, what fraction were hits
            precision = self._prediction_accuracy_hits / total if total > 0 else 0.0
            # Recall approximation: hits / (hits + misses)
            recall = self._prediction_accuracy_hits / total if total > 0 else 0.0

            return {
                "total_predictions_tracked": len(self._prediction_outcomes),
                "hits": self._prediction_accuracy_hits,
                "misses": self._prediction_accuracy_misses,
                "pending": pending,
                "hit_rate": round(hit_rate, 4),
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "accuracy_window_seconds": self._config.prediction_accuracy_window_seconds,
            }

    def _expire_prediction_outcomes(self) -> None:
        """Check and expire prediction outcomes whose time window has elapsed.

        Sprint 5.37: Called periodically to mark predictions as misses
        when the configured accuracy window has passed.
        """
        now = time.time()
        window = self._config.prediction_accuracy_window_seconds
        with self._lock:
            for pred_id, record in self._prediction_outcomes.items():
                if record.get("outcome") == "pending":
                    predicted_at = record.get("predicted_at_epoch", 0)
                    if now - predicted_at > window:
                        record["outcome"] = "miss"
                        record["resolved_at"] = datetime.now(timezone.utc).isoformat()
                        self._prediction_accuracy_misses += 1

    # -- Transition learning (Sprint 5.38) ----------------------------------

    def record_alert_for_transition_learning(self, alert: Alert, now: float) -> None:
        """Record alert for building transition probability model.

        Sprint 5.38: Appends the alert to the internal sequence and
        updates transition counts between consecutive alert types
        for the same subject. This data is used by the learned
        prediction model instead of the static _CAUSAL_PREDICTION_CHAIN.

        Sprint 5.39: Increments _alerts_since_last_retrain and
        checks if retraining is needed.
        """
        with self._lock:
            self._alert_type_sequence.append((alert.alert_type, alert.subject, now))
            # Keep only last 1000 entries to bound memory
            if len(self._alert_type_sequence) > 1000:
                self._alert_type_sequence = self._alert_type_sequence[-1000:]

            # Find the previous alert for the same subject to build transition
            for i in range(len(self._alert_type_sequence) - 2, -1, -1):
                prev_type, prev_subject, prev_ts = self._alert_type_sequence[i]
                if prev_subject == alert.subject:
                    # Record transition
                    pair = (prev_type, alert.alert_type)
                    self._transition_counts[pair] = self._transition_counts.get(pair, 0) + 1
                    self._transition_totals[prev_type] = self._transition_totals.get(prev_type, 0) + 1
                    break

            # Sprint 5.39: Track alerts since last retrain and check if retrain needed
            self._alerts_since_last_retrain += 1

        # Sprint 5.39: Check if retraining is needed (outside lock to avoid deadlock)
        if self._transition_persistence_enabled and self.check_retrain_needed():
            self.retrain_transition_model()

    def get_transition_probabilities(self, from_type: str | None = None) -> dict[str, Any]:
        """Return computed transition probabilities from the learned model.

        Sprint 5.38: Returns a dict mapping from_type to a dict of
        (to_type -> probability). If from_type is specified, returns
        only transitions from that type. Probabilities are computed
        as count / total for each from_type.

        Returns confidence information based on sample size.
        """
        with self._lock:
            if from_type:
                total = self._transition_totals.get(from_type, 0)
                if total == 0:
                    return {"from_type": from_type, "transitions": {}, "total_samples": 0, "confidence": 0.0}
                transitions = {}
                for (ft, tt), count in self._transition_counts.items():
                    if ft == from_type:
                        prob = count / total
                        # Wilson score interval approximation for confidence
                        z = 1.96  # 95% CI
                        n = total
                        p_hat = prob
                        denominator = 1 + z**2 / n
                        center = (p_hat + z**2 / (2 * n)) / denominator
                        margin = z * ((p_hat * (1 - p_hat) / n + z**2 / (4 * n**2)) ** 0.5) / denominator
                        transitions[tt] = {
                            "probability": round(prob, 4),
                            "count": count,
                            "confidence_lower": round(max(0, center - margin), 4),
                            "confidence_upper": round(min(1, center + margin), 4),
                        }
                return {
                    "from_type": from_type,
                    "transitions": transitions,
                    "total_samples": total,
                    "confidence": round(min(1.0, total / self._config.learned_prediction_min_samples), 4),
                }
            else:
                result = {}
                for ft in self._transition_totals:
                    total = self._transition_totals[ft]
                    transitions = {}
                    for (f, t), count in self._transition_counts.items():
                        if f == ft:
                            prob = count / total
                            transitions[t] = round(prob, 4)
                    result[ft] = {
                        "transitions": transitions,
                        "total_samples": total,
                    }
                return result

    def predict_causal_chain_learned(self, alert: Alert) -> list[dict]:
        """Predict next alerts using learned transition probabilities.

        Sprint 5.38: Instead of using the static _CAUSAL_PREDICTION_CHAIN,
        this method queries the transition probability model built from
        actual alert sequences. Returns predictions with confidence
        intervals derived from sample size and transition probability.

        Falls back to the static chain if the learned model has
        insufficient data (< learned_prediction_min_samples).
        """
        if not self._config.learned_prediction_enabled:
            return []

        # Check if this alert matches a previous prediction
        if self._config.causal_prediction_enabled:
            self.record_prediction_outcome(alert)
            self._expire_prediction_outcomes()

        now = time.time()
        from_type = alert.alert_type

        with self._lock:
            total = self._transition_totals.get(from_type, 0)

        # If insufficient data, fall back to static chain
        if total < self._config.learned_prediction_min_samples:
            logger.debug(
                "learned_prediction_insufficient_data",
                from_type=from_type,
                total_samples=total,
                min_required=self._config.learned_prediction_min_samples,
            )
            # Fall back to static chain if enabled
            if self._config.causal_prediction_enabled:
                return self.predict_causal_chain(alert)
            return []

        # Build predictions from learned probabilities
        with self._lock:
            transitions = {}
            for (ft, tt), count in self._transition_counts.items():
                if ft == from_type:
                    transitions[tt] = count / total

        predictions = []
        for to_type, probability in sorted(transitions.items(), key=lambda x: -x[1]):
            if probability < self._config.learned_prediction_confidence_threshold:
                continue

            prediction_id = f"lpred-{uuid.uuid4().hex[:8]}"

            # Sprint 5.47: Apply confidence calibration from A/B results
            calibrated_probability = probability
            if self._calibration_callback is not None:
                calibrated_probability = self._calibration_callback(alert.subject, probability)

            # Confidence interval using Wilson score
            z = 1.96
            n = total
            p_hat = probability
            denominator = 1 + z**2 / n
            center = (p_hat + z**2 / (2 * n)) / denominator
            margin = z * ((p_hat * (1 - p_hat) / n + z**2 / (4 * n**2)) ** 0.5) / denominator

            # Estimate delay based on average observed delay
            estimated_delay = self.estimate_transition_delay(from_type, to_type, alert.subject)

            prediction = {
                "prediction_id": prediction_id,
                "predicted_alert_type": to_type,
                "subject": alert.subject,
                "probability": round(probability, 4),
                "calibrated_probability": round(calibrated_probability, 4),
                "confidence_lower": round(max(0, center - margin), 4),
                "confidence_upper": round(min(1, center + margin), 4),
                "estimated_delay_seconds": estimated_delay,
                "triggered_by": from_type,
                "triggered_at": alert.timestamp,
                "predicted_at": datetime.now(timezone.utc).isoformat(),
                "model": "learned",
                "sample_size": total,
            }
            predictions.append(prediction)

            # Track this prediction for accuracy feedback
            with self._lock:
                self._prediction_outcomes[prediction_id] = {
                    "prediction_id": prediction_id,
                    "predicted_alert_type": to_type,
                    "subject": alert.subject,
                    "triggered_by": from_type,
                    "predicted_at_epoch": now,
                    "predicted_at": prediction["predicted_at"],
                    "estimated_delay_seconds": estimated_delay,
                    "confidence": probability,
                    "outcome": "pending",
                    "model": "learned",
                }

        with self._lock:
            subject = alert.subject
            if subject not in self._causal_predictions:
                self._causal_predictions[subject] = []
            self._causal_predictions[subject].extend(predictions)
            self._total_predictions_made += len(predictions)
            self._total_learned_predictions_made += len(predictions)
            self._learned_model_last_trained = now

        # Notify dashboard subscribers
        if self._realtime_bus is not None:
            self._realtime_bus.notify_realtime_subscribers(
                {
                    "event": "causal_predictions",
                    "subject": alert.subject,
                    "predictions": predictions,
                    "triggered_by": from_type,
                    "model": "learned",
                }
            )

        logger.info(
            "learned_predictions_generated",
            triggered_by=from_type,
            subject=alert.subject,
            prediction_count=len(predictions),
            sample_size=total,
        )

        return predictions

    def estimate_transition_delay(self, from_type: str, to_type: str, subject: str) -> int:
        """Estimate the average delay between two alert types for a subject.

        Sprint 5.38: Scans the alert sequence to compute average
        time between from_type and to_type occurrences for the same subject.
        """
        delays = []
        with self._lock:
            seq = self._alert_type_sequence
            # Find pairs of consecutive (from_type, to_type) for this subject
            last_from_ts = None
            for atype, asubject, ats in seq:
                if asubject != subject:
                    continue
                if atype == from_type:
                    last_from_ts = ats
                elif atype == to_type and last_from_ts is not None:
                    delays.append(ats - last_from_ts)
                    last_from_ts = None

        if delays:
            return int(sum(delays) / len(delays))
        # Default: causal grouping window / 2
        return self._config.causal_grouping_window_seconds // 2

    # -- Transition persistence & retraining (Sprint 5.39) -----------------

    def persist_transition_model(self) -> bool:
        """Save current transition_counts and transition_totals to DB.

        Sprint 5.39: Persists the in-memory transition probability model
        to the AlertHistoryStore's transition_probabilities table.
        Should be called periodically (e.g., after retraining or on shutdown).

        Returns True if successfully persisted, False otherwise.
        """
        if self._history_store is None:
            logger.warning(
                "transition_model_persist_no_store",
                message="No history store attached — cannot persist transition model",
            )
            return False

        with self._lock:
            counts_copy = dict(self._transition_counts)
            totals_copy = dict(self._transition_totals)

        if not counts_copy:
            logger.debug(
                "transition_model_persist_empty",
                message="No transition data to persist",
            )
            return True

        try:
            result = self._history_store.save_transition_probabilities(
                transition_counts=counts_copy,
                transition_totals=totals_copy,
            )
            if result:
                logger.info(
                    "transition_model_persisted",
                    transition_pairs=len(counts_copy),
                    total_types=len(totals_copy),
                )
            return result
        except Exception as exc:
            logger.warning(
                "transition_model_persist_failed",
                error=str(exc),
            )
            return False

    def load_transition_model(self) -> bool:
        """Load transition probabilities from the store into memory.

        Sprint 5.39: Loads the persisted transition probability model
        from the AlertHistoryStore on startup. Replaces the current
        in-memory _transition_counts and _transition_totals.

        Returns True if successfully loaded, False otherwise.
        """
        if self._history_store is None:
            logger.warning(
                "transition_model_load_no_store",
                message="No history store attached — cannot load transition model",
            )
            return False

        try:
            counts, totals = self._history_store.load_transition_probabilities()
            with self._lock:
                if counts:
                    self._transition_counts = counts
                    self._transition_totals = totals
                    self._learned_model_last_trained = time.time()

            logger.info(
                "transition_model_loaded",
                transition_pairs=len(counts),
                total_types=len(totals),
            )
            return True
        except Exception as exc:
            logger.warning(
                "transition_model_load_failed",
                error=str(exc),
            )
            return False

    def retrain_transition_model(self) -> dict:
        """Full retrain from the alert_history in the store.

        Sprint 5.39: Computes transitions from stored alert sequences
        in the AlertHistoryStore. Records a retraining event. Resets
        _alerts_since_last_retrain. Persists the new model.

        Returns a dict with retraining results.
        """
        now = time.time()
        trigger_reason = "scheduled"

        # Determine trigger reason
        if self._alerts_since_last_retrain >= self._retrain_after_n_alerts > 0:
            trigger_reason = "new_alerts_threshold"

        # Load all alert history from the store to recompute transitions
        new_counts: dict[tuple[str, str], int] = {}
        new_totals: dict[str, int] = {}

        if self._history_store is not None:
            try:
                # Get all alerts ordered by timestamp for sequence building
                all_alerts = self._history_store.get_alert_history(limit=5000)
                # Group by subject and compute transitions
                subject_last: dict[str, str] = {}
                for alert in reversed(all_alerts):  # oldest first
                    subject = alert.get("subject", "")
                    alert_type = alert.get("alert_type", "")
                    if subject in subject_last:
                        prev_type = subject_last[subject]
                        pair = (prev_type, alert_type)
                        new_counts[pair] = new_counts.get(pair, 0) + 1
                        new_totals[prev_type] = new_totals.get(prev_type, 0) + 1
                    subject_last[subject] = alert_type
            except Exception as exc:
                logger.warning(
                    "transition_retrain_history_load_failed",
                    error=str(exc),
                )
                # Fall back to current in-memory data
                with self._lock:
                    new_counts = dict(self._transition_counts)
                    new_totals = dict(self._transition_totals)

        with self._lock:
            self._transition_counts = new_counts
            self._transition_totals = new_totals
            self._alerts_since_last_retrain = 0
            self._last_retrain_time = now
            self._learned_model_last_trained = now
            self._total_retraining_events += 1
            transition_count = len(self._transition_counts)
            total_types = len(self._transition_totals)

        # Record retraining event in persistent store
        if self._history_store is not None:
            try:
                self._history_store.record_retraining_event(
                    {
                        "trigger_reason": trigger_reason,
                        "alerts_since_last_train": self._alerts_since_last_retrain,
                        "transition_count": transition_count,
                        "total_types": total_types,
                        "trained_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception as exc:
                logger.warning(
                    "transition_retrain_event_record_failed",
                    error=str(exc),
                )

        # Persist the retrained model
        self.persist_transition_model()

        result = {
            "trigger_reason": trigger_reason,
            "transition_count": transition_count,
            "total_types": total_types,
            "alerts_since_last_retrain": 0,
            "retrained_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "transition_model_retrained",
            trigger_reason=trigger_reason,
            transition_count=transition_count,
            total_types=total_types,
        )

        return result

    def check_retrain_needed(self) -> bool:
        """Check if the transition model should be retrained.

        Sprint 5.39: Returns True if either:
        - retrain_interval_seconds has elapsed since last retrain, or
        - _alerts_since_last_retrain >= retrain_after_n_alerts

        Returns False if transition persistence is disabled or if
        both conditions are unmet.
        """
        if not self._transition_persistence_enabled:
            return False

        now = time.time()

        # Check interval-based retraining
        if self._retrain_interval_seconds > 0 and self._last_retrain_time > 0:
            elapsed = now - self._last_retrain_time
            if elapsed >= self._retrain_interval_seconds:
                return True

        # Check alert-count-based retraining
        if self._retrain_after_n_alerts > 0 and self._alerts_since_last_retrain >= self._retrain_after_n_alerts:
            return True

        return False

    # -- Status summary -----------------------------------------------------

    def get_status_summary(self) -> dict[str, Any]:
        """Return a status dict for the StatusAggregator."""
        return {
            "causal_prediction": {
                "enabled": self._config.causal_prediction_enabled,
                "total_predictions": self._total_predictions_made,
                "subjects_with_predictions": len(self._causal_predictions),
            },
            "prediction_accuracy": self.get_prediction_accuracy(),
            "learned_prediction": {
                "enabled": self._config.learned_prediction_enabled,
                "min_samples": self._config.learned_prediction_min_samples,
                "confidence_threshold": self._config.learned_prediction_confidence_threshold,
                "total_learned_predictions": self._total_learned_predictions_made,
                "last_trained": self._learned_model_last_trained,
                "transition_types_known": len(self._transition_totals),
            },
            "transition_persistence": {
                "persistence_enabled": self._transition_persistence_enabled,
                "last_retrain_time": self._last_retrain_time,
                "alerts_since_last_retrain": self._alerts_since_last_retrain,
                "total_retraining_events": self._total_retraining_events,
                "retrain_interval_seconds": self._retrain_interval_seconds,
                "retrain_after_n_alerts": self._retrain_after_n_alerts,
            },
        }


class DigestManager:
    """Owns all digest buffering and flushing state.

    Sprint 5.61: Extracted from AlertManager to centralize digest
    buffering logic and flush scheduling.

    The DigestManager owns the buffer state and decides **when** to
    flush.  When a flush is needed, it either returns the buffered
    items (via ``flush_digest()``) or calls a flush callback that
    AlertManager provides (via ``check_digest_flush()``).  The
    callback pattern keeps transport dispatch and history recording
    in AlertManager's domain, while DigestManager focuses solely on
    buffer management and flush timing.
    """

    def __init__(self, config: AlertConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._digest_buffer: list[dict] = []
        self._digest_last_flush: float = time.time()
        self._total_digest_flushes: int = 0
        # Callback set by AlertManager for actual dispatch
        self._flush_callback: Any = None

    def set_flush_callback(self, callback: Any) -> None:
        """Set the callback that handles actual digest dispatch.

        The callback receives ``buffered_alerts`` (a list of alert
        dicts) and is responsible for creating the digest Alert,
        recording it in history, and dispatching to transports.
        """
        self._flush_callback = callback

    def buffer_alert(self, alert_dict: dict) -> None:
        """Add an alert dict to the digest buffer."""
        with self._lock:
            self._digest_buffer.append(alert_dict)

    def should_flush(self, alert_type: str | None = None) -> bool:
        """Check if the digest buffer should be flushed.

        Returns True if either:
        - The buffer has enough alerts (>= min_alerts for the type)
        - Enough time has elapsed since last flush
        """
        if not self._config.digest_enabled:
            return False
        with self._lock:
            if not self._digest_buffer:
                return False
            elapsed = time.time() - self._digest_last_flush
            # Get settings for the specific alert type or global
            if alert_type:
                interval, min_alerts = self.get_digest_settings(alert_type)
            else:
                interval = self._config.digest_interval_minutes
                min_alerts = self._config.digest_min_alerts
                # Use shortest interval from overrides
                for override in self._config.digest_overrides.values():
                    override_interval = override.get("interval_minutes", interval)
                    if override_interval < interval:
                        interval = override_interval
            interval_secs = interval * 60
            return len(self._digest_buffer) >= min_alerts or elapsed >= interval_secs

    def flush_digest(self) -> list[dict]:
        """Flush the digest buffer and return the buffered items.

        The caller (AlertManager) is responsible for creating the
        digest Alert, dispatching to transports, and recording in
        history.
        """
        with self._lock:
            if not self._digest_buffer:
                return []
            buffered = list(self._digest_buffer)
            self._digest_buffer.clear()
            self._digest_last_flush = time.time()
            self._total_digest_flushes += 1
            return buffered

    def check_digest_flush(self) -> None:
        """Check if the digest buffer should be flushed based on time.

        If a flush is needed, calls the flush_callback if set.
        The lock is released before invoking the callback to
        prevent deadlock.
        """
        if not self._config.digest_enabled:
            return
        now = time.time()
        with self._lock:
            if not self._digest_buffer:
                return
            elapsed = now - self._digest_last_flush
            # Use shortest interval from overrides or global
            min_interval = self._config.digest_interval_minutes
            for override in self._config.digest_overrides.values():
                override_interval = override.get("interval_minutes", min_interval)
                if override_interval < min_interval:
                    min_interval = override_interval
            interval_secs = min_interval * 60
            if elapsed >= interval_secs:
                # Perform the flush
                if self._flush_callback is not None:
                    buffered = list(self._digest_buffer)
                    self._digest_buffer.clear()
                    self._digest_last_flush = time.time()
                    self._total_digest_flushes += 1
                    # Release lock before calling callback to avoid deadlock
                    self._lock.release()
                    try:
                        self._flush_callback(buffered)
                    finally:
                        self._lock.acquire()

    def get_digest_settings(self, alert_type: str) -> tuple[int, int]:
        """Get digest interval and min_alerts for a specific alert type."""
        override = self._config.digest_overrides.get(alert_type)
        if override:
            interval = override.get("interval_minutes", self._config.digest_interval_minutes)
            min_alerts = override.get("min_alerts", self._config.digest_min_alerts)
            return (interval, min_alerts)
        return (self._config.digest_interval_minutes, self._config.digest_min_alerts)

    def get_status_summary(self) -> dict[str, Any]:
        """Return a status dict for the StatusAggregator."""
        with self._lock:
            return {
                "enabled": self._config.digest_enabled,
                "interval_minutes": self._config.digest_interval_minutes,
                "min_alerts": self._config.digest_min_alerts,
                "buffered_count": len(self._digest_buffer),
                "overrides": self._config.digest_overrides,
                "total_flushes": self._total_digest_flushes,
            }

    # Public read-only accessors
    @property
    def buffered_count(self) -> int:
        with self._lock:
            return len(self._digest_buffer)

    @property
    def total_flushes(self) -> int:
        with self._lock:
            return self._total_digest_flushes


class ABExperimentManager:
    """Owns all A/B experiment state, bandit allocation, calibration,
    rollback, cleanup, and statistical testing logic.

    Sprint 5.61: Extracted from AlertManager to centralize the ~40+
    A/B experiment-related state variables and 46+ methods.
    """

    def __init__(self, config: AlertConfig, history_store: Any = None, realtime_bus: Any = None) -> None:
        self._config = config
        self._history_store = history_store
        self._realtime_bus = realtime_bus
        self._lock = threading.Lock()
        # Alert sender callback (set by AlertManager wiring)
        self._alert_sender: Any = None
        # Sprint 5.45: A/B experiment tracking
        self._ab_experiments: dict[str, dict[str, Any]] = {}
        self._ab_promotion_checker_thread: threading.Thread | None = None
        self._ab_promotion_checker_running: bool = False
        self._total_ab_promotions: int = 0
        self._total_ab_auto_promotions: int = 0
        # Sprint 5.45: Decay event tracking
        self._decay_events: list[dict] = []
        self._total_decay_events: int = 0
        # Sprint 5.46: Cleanup checker state
        self._ab_cleanup_checker_thread: threading.Thread | None = None
        self._ab_cleanup_checker_running: bool = False
        self._total_ab_cleanups: int = 0
        self._last_ab_cleanup_run: float = 0.0
        # Sprint 5.46: Rollback tracking
        self._ab_rollback_history: list[dict] = []
        self._total_ab_rollbacks: int = 0
        # Sprint 5.46: Decay recovery tracking
        self._decay_recovery_history: list[dict] = []
        self._total_decay_recoveries: int = 0
        # Sprint 5.47: Pre-promotion config snapshot for live config reversion
        self._pre_promotion_config_snapshots: dict[str, dict[str, Any]] = {}
        self._total_config_reversions: int = 0
        # Sprint 5.47: Live config reverter callback (set by app wiring)
        self._live_config_reverter: Any = None
        # Sprint 5.47: Auto-tuning policy reverter callback (set by app wiring)
        self._auto_tuning_reverter: Any = None
        # Sprint 5.47: Statistical significance test results cache
        self._statistical_test_results: dict[str, dict[str, Any]] = {}
        self._total_statistical_tests_run: int = 0
        self._total_promotions_blocked_by_stats: int = 0
        # Sprint 5.47: Cleanup metrics for health endpoint
        self._total_expired_by_ttl: int = 0
        self._total_pruned_stopped: int = 0
        # Sprint 5.47: Confidence calibration data from A/B results
        self._confidence_calibration_map: dict[str, float] = {}
        self._total_calibration_updates: int = 0
        # Sprint 5.49: Calibration persistence tracking
        self._last_calibration_update_time: str | None = None
        self._last_calibration_persist_time: float = 0.0
        # Sprint 5.48: Rollback dry-run tracking
        self._total_dry_run_evaluations: int = 0
        self._total_dry_run_would_rollback: int = 0
        # Sprint 5.48: Multi-armed bandit state
        self._bandit_state: dict[str, dict[str, Any]] = {}
        self._total_bandit_allocations: int = 0
        # Sprint 5.49: Bandit context tracking for contextual bandits
        self._bandit_context_rewards: dict[str, dict[str, list[float]]] = {}
        # Sprint 5.49: Accuracy snapshot timing
        self._last_accuracy_snapshot_time: float = 0.0
        # Sprint 5.50: Bandit decision logging counter
        self._total_bandit_decisions_logged: int = 0
        # Sprint 5.50: Adaptive bandit method tracking
        self._adaptive_method_history: dict[str, str] = {}
        self._total_adaptive_method_switches: int = 0
        # Sprint 5.50: Snapshot GC tracking
        self._snapshot_gc_thread: threading.Thread | None = None
        self._snapshot_gc_running: bool = False
        self._total_snapshot_gc_runs: int = 0
        self._total_snapshots_cleaned: int = 0
        self._last_snapshot_gc_run: float = 0.0
        # Sprint 5.50: Calibration drift detection tracking
        self._calibration_drift_alerts: list[dict] = []
        self._total_calibration_drift_alerts: int = 0

    def set_alert_sender(self, sender: Any) -> None:
        """Set the callback for sending alerts from AB events."""
        self._alert_sender = sender

    def get_status_summary(self) -> dict[str, Any]:
        """Return a status dict for the StatusAggregator."""
        return {
            "ab_experiments_total": len(self._ab_experiments),
            "ab_experiments_running": len([e for e in self._ab_experiments.values() if e.get("status") == "running"]),
            "ab_promotion_checker_running": self._ab_promotion_checker_running,
            "ab_total_promotions": self._total_ab_promotions,
            "ab_total_auto_promotions": self._total_ab_auto_promotions,
            "ab_total_decay_events": self._total_decay_events,
            "ab_cleanup_checker_running": self._ab_cleanup_checker_running,
            "ab_total_cleanups": self._total_ab_cleanups,
            "ab_total_rollbacks": self._total_ab_rollbacks,
            "ab_total_decay_recoveries": self._total_decay_recoveries,
            "ab_statistical_significance": self.get_statistical_significance_status(),
            "ab_config_reversion": self.get_config_reversion_status(),
            "ab_confidence_calibration": self.get_confidence_calibration_status(),
            "ab_cleanup_metrics": self.get_cleanup_metrics(),
            "ab_bandit": self.get_bandit_status(),
            "ab_rollback_dry_run": self.get_rollback_dry_run_status(),
        }

    def start_ab_experiment(
        self,
        name: str,
        control_config: dict[str, Any],
        variant_config: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a new A/B experiment.

        Sprint 5.45: Creates an experiment with control and variant configurations.
        The experiment runs until manually stopped, auto-promoted, or expired by TTL.

        Parameters
        ----------
        name:
            Unique experiment name.
        control_config:
            Configuration dict for the control (baseline) variant.
        variant_config:
            Configuration dict for the experimental variant.
        metadata:
            Optional metadata dict for the experiment.

        Returns the experiment dict.
        """
        time.time()
        now_iso = datetime.now(timezone.utc).isoformat()

        if name in self._ab_experiments:
            existing = self._ab_experiments[name]
            if existing.get("status") == "running":
                logger.warning("ab_experiment_already_running", name=name)
                return existing

        experiment = {
            "name": name,
            "control_config": control_config,
            "variant_config": variant_config,
            "status": "running",
            "started_at": now_iso,
            "stopped_at": None,
            "result": None,
            "control_samples": 0,
            "variant_samples": 0,
            "control_accuracy": 0.0,
            "variant_accuracy": 0.0,
            "promoted_variant": None,
            "promotion_timestamp": None,
            "metadata": metadata or {},
            "created_at": now_iso,
        }

        self._ab_experiments[name] = experiment

        # Persist to store
        self.persist_ab_experiment(experiment)

        logger.info(
            "ab_experiment_started",
            name=name,
            control_config=control_config,
            variant_config=variant_config,
        )

        return experiment

    def stop_ab_experiment(self, name: str, result: str | None = None) -> dict[str, Any] | None:
        """Stop a running A/B experiment.

        Sprint 5.45: Marks the experiment as stopped with an optional result.
        The experiment record is retained for the configured retention period.

        Parameters
        ----------
        name:
            The experiment name to stop.
        result:
            Optional result string (e.g., "variant_wins", "control_wins", "inconclusive").

        Returns the updated experiment dict, or None if not found.
        """
        if name not in self._ab_experiments:
            logger.warning("ab_experiment_not_found", name=name)
            return None

        experiment = self._ab_experiments[name]
        if experiment.get("status") != "running":
            logger.warning("ab_experiment_not_running", name=name, status=experiment.get("status"))
            return experiment

        now_iso = datetime.now(timezone.utc).isoformat()
        experiment["status"] = "stopped"
        experiment["stopped_at"] = now_iso
        experiment["result"] = result

        # Persist to store
        self.persist_ab_experiment(experiment)

        logger.info(
            "ab_experiment_stopped",
            name=name,
            result=result,
        )

        return experiment

    def record_ab_result(
        self,
        name: str,
        variant: str,
        accuracy: float,
        samples: int = 1,
    ) -> dict[str, Any] | None:
        """Record a result for an A/B experiment variant.

        Sprint 5.45: Accumulates accuracy and sample count for a variant.
        Accuracy is tracked as a running average weighted by samples.

        Parameters
        ----------
        name:
            The experiment name.
        variant:
            Either "control" or "variant".
        accuracy:
            The accuracy measurement for this sample batch.
        samples:
            Number of samples in this batch (default 1).

        Returns the updated experiment dict, or None if not found.
        """
        if name not in self._ab_experiments:
            logger.warning("ab_experiment_not_found", name=name)
            return None

        experiment = self._ab_experiments[name]
        if experiment.get("status") != "running":
            logger.warning("ab_experiment_not_running", name=name)
            return experiment

        if variant == "control":
            old_samples = experiment["control_samples"]
            new_samples = old_samples + samples
            if new_samples > 0:
                experiment["control_accuracy"] = (
                    experiment["control_accuracy"] * old_samples + accuracy * samples
                ) / new_samples
            experiment["control_samples"] = new_samples
        elif variant == "variant":
            old_samples = experiment["variant_samples"]
            new_samples = old_samples + samples
            if new_samples > 0:
                experiment["variant_accuracy"] = (
                    experiment["variant_accuracy"] * old_samples + accuracy * samples
                ) / new_samples
            experiment["variant_samples"] = new_samples
        else:
            logger.warning("ab_experiment_invalid_variant", name=name, variant=variant)
            return experiment

        # Persist to store
        self.persist_ab_experiment(experiment)

        logger.info(
            "ab_result_recorded",
            name=name,
            variant=variant,
            accuracy=accuracy,
            samples=samples,
        )

        return experiment

    def promote_variant(self, name: str, variant: str = "variant") -> dict[str, Any] | None:
        """Promote a variant in an A/B experiment.

        Sprint 5.45: Marks the specified variant as promoted, records the
        promotion timestamp, and sends an alert notification.

        Sprint 5.47: Saves a pre-promotion config snapshot for potential
        rollback reversion. Also enforces statistical significance testing
        when enabled — promotion is blocked if the result is not statistically
        significant.

        Parameters
        ----------
        name:
            The experiment name.
        variant:
            The variant to promote ("control" or "variant", default "variant").

        Returns the updated experiment dict, or None if not found or blocked.
        """
        if name not in self._ab_experiments:
            logger.warning("ab_experiment_not_found", name=name)
            return None

        experiment = self._ab_experiments[name]

        # Sprint 5.47: Statistical significance gate
        if self._config.ab_statistical_significance_enabled:
            stat_result = self.compute_statistical_significance(name)
            if stat_result is not None:
                experiment["statistical_test_result"] = stat_result
                if not stat_result.get("significant", False):
                    self._total_promotions_blocked_by_stats += 1
                    logger.warning(
                        "ab_promotion_blocked_not_significant",
                        name=name,
                        p_value=stat_result.get("p_value"),
                        method=stat_result.get("method"),
                    )
                    # Return the experiment without promoting
                    return None

        # Sprint 5.47: Save pre-promotion config snapshot for rollback reversion
        baseline_config = (
            experiment.get("control_config", {}) if variant == "variant" else experiment.get("variant_config", {})
        )
        self._pre_promotion_config_snapshots[name] = {
            "control_config": dict(experiment.get("control_config", {})),
            "variant_config": dict(experiment.get("variant_config", {})),
            "promoted_variant": variant,
            "baseline_config": dict(baseline_config),
            "auto_tuning_snapshot": self.get_auto_tuning_snapshot(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        now_iso = datetime.now(timezone.utc).isoformat()

        experiment["promoted_variant"] = variant
        experiment["promotion_timestamp"] = now_iso
        experiment["status"] = "promoted"

        self._total_ab_promotions += 1

        # Persist to store
        self.persist_ab_experiment(experiment)

        # Build alert data with stat results if available
        alert_data = {
            "experiment_name": name,
            "variant": variant,
            "control_accuracy": experiment["control_accuracy"],
            "variant_accuracy": experiment["variant_accuracy"],
            "control_samples": experiment["control_samples"],
            "variant_samples": experiment["variant_samples"],
            "auto": False,
        }
        if "statistical_test_result" in experiment:
            alert_data["statistical_test_result"] = experiment["statistical_test_result"]

        # Send notification alert
        self._alert_sender(
            Alert(
                alert_type="ab_experiment_promotion",
                severity="info",
                subject=f"experiment:{name}",
                message=f"A/B experiment '{name}' promoted variant '{variant}'",
                data=alert_data,
            )
        )

        logger.info(
            "ab_variant_promoted",
            name=name,
            variant=variant,
        )

        return experiment

    def get_ab_experiment(self, name: str) -> dict[str, Any] | None:
        """Return a single A/B experiment by name.

        Sprint 5.45: Returns the experiment dict or None if not found.
        """
        return self._ab_experiments.get(name)

    def get_ab_experiments(self, status: str | None = None) -> list[dict[str, Any]]:
        """Return all A/B experiments, optionally filtered by status.

        Sprint 5.45: Returns a list of experiment dicts.
        """
        experiments = list(self._ab_experiments.values())
        if status:
            experiments = [e for e in experiments if e.get("status") == status]
        return experiments

    def start_ab_promotion_checker(self) -> None:
        """Start the auto-promotion checker background thread.

        Sprint 5.45: Periodically checks running experiments to see if any
        variant meets the auto-promotion criteria (sufficient samples and
        confidence threshold).
        """
        interval = self._config.ab_auto_promote_interval_seconds
        if interval <= 0:
            logger.info("ab_promotion_checker_disabled")
            return

        if self._ab_promotion_checker_running:
            logger.warning("ab_promotion_checker_already_running")
            return

        self._ab_promotion_checker_running = True

        def _checker_loop():
            logger.info("ab_promotion_checker_started", interval_seconds=interval)
            while self._ab_promotion_checker_running:
                try:
                    self.check_auto_promotion()
                except Exception as exc:
                    logger.warning("ab_promotion_checker_error", error=str(exc))
                time.sleep(interval)

        self._ab_promotion_checker_thread = threading.Thread(
            target=_checker_loop,
            daemon=True,
            name="ab-promotion-checker",
        )
        self._ab_promotion_checker_thread.start()

    def stop_ab_promotion_checker(self) -> None:
        """Stop the auto-promotion checker background thread."""
        self._ab_promotion_checker_running = False
        logger.info("ab_promotion_checker_stopped")

    def check_auto_promotion(self) -> None:
        """Check all running experiments for auto-promotion eligibility.

        Sprint 5.45: An experiment is eligible for auto-promotion when:
        1. Both variants have >= ab_auto_promote_min_samples
        2. One variant's accuracy exceeds the other by >= ab_auto_promote_confidence_threshold

        Sprint 5.49: Bandit-managed experiments use dynamic allocation from
        get_bandit_allocation() instead of a fixed 50/50 split. The promotion
        eligibility logic now respects bandit allocation when deciding whether
        to promote. Accuracy snapshots are recorded periodically inside the
        promotion checker loop.
        """
        threshold = self._config.ab_auto_promote_confidence_threshold
        min_samples = self._config.ab_auto_promote_min_samples

        # Sprint 5.49: Record accuracy snapshots periodically if configured
        snapshot_interval = self._config.ab_bandit_accuracy_snapshot_interval_seconds
        now = time.time()
        should_snapshot = snapshot_interval > 0 and (now - self._last_accuracy_snapshot_time) >= snapshot_interval
        if should_snapshot:
            self._last_accuracy_snapshot_time = now

        for name, experiment in list(self._ab_experiments.items()):
            if experiment.get("status") != "running":
                continue

            # Sprint 5.49: Record accuracy snapshot for bandit-managed experiments
            if should_snapshot:
                try:
                    self.record_accuracy_snapshot(name)
                except Exception as exc:
                    logger.debug("accuracy_snapshot_failed_in_promotion_checker", name=name, error=str(exc))

            c_samples = experiment["control_samples"]
            v_samples = experiment["variant_samples"]

            if c_samples < min_samples or v_samples < min_samples:
                continue

            c_acc = experiment["control_accuracy"]
            v_acc = experiment["variant_accuracy"]

            # Sprint 5.49: When bandit is enabled, use bandit allocation to
            # inform promotion decisions. If bandit strongly favors one variant
            # (>70% allocation) AND that variant meets accuracy thresholds,
            # the experiment is a stronger candidate for promotion.
            bandit_boost = 0.0
            bandit_winner = None
            if self._config.ab_bandit_enabled:
                allocation = self.get_bandit_allocation(name)
                if allocation.get("variant", 0.5) > 0.7:
                    bandit_winner = "variant"
                    bandit_boost = 0.02  # Slight confidence boost from bandit signal
                elif allocation.get("control", 0.5) > 0.7:
                    bandit_winner = "control"
                    bandit_boost = 0.02

            winner = None
            if v_acc >= threshold and (v_acc - c_acc + bandit_boost) >= (1.0 - threshold):
                winner = "variant"
            elif c_acc >= threshold and (c_acc - v_acc + bandit_boost) >= (1.0 - threshold):
                winner = "control"

            # Sprint 5.49: If bandit has a clear winner and accuracy is
            # sufficient but gap is borderline, use bandit signal to break tie
            if winner is None and bandit_winner is not None:
                if bandit_winner == "variant" and v_acc >= threshold * 0.95:
                    winner = "variant"
                elif bandit_winner == "control" and c_acc >= threshold * 0.95:
                    winner = "control"

            if winner:
                self._total_ab_auto_promotions += 1
                self.promote_variant(name, variant=winner)
                # Mark as auto-promoted in the data
                experiment["auto_promoted"] = True
                experiment["bandit_winner"] = bandit_winner
                logger.info(
                    "ab_auto_promoted",
                    name=name,
                    winner=winner,
                    control_accuracy=c_acc,
                    variant_accuracy=v_acc,
                    bandit_winner=bandit_winner,
                )

    def get_ab_promotion_checker_status(self) -> dict[str, Any]:
        """Return the status of the auto-promotion checker.

        Sprint 5.45: Provides visibility into the checker's running state and metrics.
        """
        return {
            "running": self._ab_promotion_checker_running,
            "interval_seconds": self._config.ab_auto_promote_interval_seconds,
            "confidence_threshold": self._config.ab_auto_promote_confidence_threshold,
            "min_samples": self._config.ab_auto_promote_min_samples,
            "total_promotions": self._total_ab_promotions,
            "total_auto_promotions": self._total_ab_auto_promotions,
            "running_experiments": len([e for e in self._ab_experiments.values() if e.get("status") == "running"]),
        }

    def notify_decay_event(self, subject: str, decay_amount: float, current_confidence: float) -> str:
        """Record and notify a significant confidence decay event.

        Sprint 5.45: Tracks decay events and sends an alert notification.
        Returns the correlation ID of the alert, or empty string if alerting disabled.

        Parameters
        ----------
        subject:
            The subject that experienced decay (e.g., "vigil_faithfulness").
        decay_amount:
            The magnitude of the confidence decay (0.0-1.0).
        current_confidence:
            The current confidence score after decay.
        """
        event = {
            "subject": subject,
            "decay_amount": decay_amount,
            "current_confidence": current_confidence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self._decay_events.append(event)
        self._total_decay_events += 1

        # Keep only last 100 decay events
        if len(self._decay_events) > 100:
            self._decay_events = self._decay_events[-100:]

        # Send alert
        return self._alert_sender(
            Alert(
                alert_type="confidence_decay",
                severity="warning" if decay_amount < 0.2 else "critical",
                subject=subject,
                message=(
                    f"Confidence decay of {decay_amount:.3f} detected for {subject} (current: {current_confidence:.3f})"
                ),
                data={
                    "subject": subject,
                    "decay_amount": decay_amount,
                    "current_confidence": current_confidence,
                },
            )
        )

    def get_decay_events(self, limit: int = 50) -> list[dict]:
        """Return recent decay events.

        Sprint 5.45: Returns the most recent decay events, up to the limit.
        """
        return self._decay_events[-limit:]

    def restore_ab_experiments_from_store(self) -> int:
        """Restore A/B experiment state from the persistent store.

        Sprint 5.45: Called on startup to restore experiment state.
        Returns the number of experiments restored.
        """
        if self._history_store is None:
            return 0

        try:
            experiments = self._history_store.get_ab_experiments()
            count = 0
            for exp in experiments:
                name = exp.get("name", "")
                if name and name not in self._ab_experiments:
                    self._ab_experiments[name] = exp
                    count += 1

            logger.info(
                "ab_experiments_restored",
                count=count,
                total=len(self._ab_experiments),
            )
            return count
        except Exception as exc:
            logger.warning("ab_experiments_restore_failed", error=str(exc))
            return 0

    def persist_ab_experiment(self, experiment: dict[str, Any]) -> None:
        """Persist an A/B experiment to the history store."""
        if self._history_store is None:
            return
        try:
            self._history_store.record_ab_experiment(experiment)
        except Exception as exc:
            logger.warning("ab_experiment_persist_failed", error=str(exc))

    # -------------------------------------------------------------------
    # Sprint 5.46: Experiment Result Expiry & Cleanup
    # -------------------------------------------------------------------

    def cleanup_expired_experiments(self) -> dict[str, int]:
        """Clean up old/stopped A/B experiments.

        Sprint 5.46: Performs two types of cleanup:
        1. Stop experiments that have been running beyond the configured TTL.
        2. Prune stopped experiment records from memory and store after
           the retention period.

        Sprint 5.47: Sends alert notifications when experiments expire due
        to TTL (detecting forgotten/misconfigured experiments). Tracks
        cumulative cleanup metrics (total_expired, total_pruned, last_run_time).

        Returns a dict with counts: {expired_stopped, pruned, total}.
        """
        now = time.time()
        ttl_seconds = self._config.ab_experiment_ttl_hours * 3600
        retention_seconds = self._config.ab_stopped_experiment_retention_hours * 3600

        expired_stopped = 0
        pruned = 0

        # 1. Stop experiments that exceeded TTL
        if ttl_seconds > 0:
            for name, experiment in list(self._ab_experiments.items()):
                if experiment.get("status") != "running":
                    continue
                started_at = experiment.get("started_at", "")
                if not started_at:
                    continue
                try:
                    started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                    started_epoch = started_dt.timestamp()
                    if now - started_epoch > ttl_seconds:
                        self.stop_ab_experiment(name, result="ttl_expired")
                        expired_stopped += 1
                        self._total_expired_by_ttl += 1
                        logger.info("ab_experiment_ttl_expired", name=name)

                        # Sprint 5.47: Send alert on TTL expiry
                        if self._config.ab_cleanup_alert_on_ttl_expiry:
                            self._alert_sender(
                                Alert(
                                    alert_type="ab_experiment_ttl_expired",
                                    severity="warning",
                                    subject=f"experiment:{name}",
                                    message=(
                                        f"A/B experiment '{name}' expired due to TTL "
                                        f"({self._config.ab_experiment_ttl_hours}h). "
                                        f"This may indicate a forgotten or misconfigured experiment."
                                    ),
                                    data={
                                        "experiment_name": name,
                                        "ttl_hours": self._config.ab_experiment_ttl_hours,
                                        "started_at": started_at,
                                        "control_accuracy": experiment.get("control_accuracy", 0),
                                        "variant_accuracy": experiment.get("variant_accuracy", 0),
                                        "control_samples": experiment.get("control_samples", 0),
                                        "variant_samples": experiment.get("variant_samples", 0),
                                    },
                                )
                            )
                except (ValueError, TypeError):
                    continue

        # 2. Prune stopped experiments past retention period
        if retention_seconds > 0:
            to_prune = []
            for name, experiment in list(self._ab_experiments.items()):
                if experiment.get("status") == "running":
                    continue
                stopped_at = experiment.get("stopped_at", "")
                if not stopped_at:
                    # No stopped_at but not running — prune immediately
                    to_prune.append(name)
                    continue
                try:
                    stopped_dt = datetime.fromisoformat(stopped_at.replace("Z", "+00:00"))
                    stopped_epoch = stopped_dt.timestamp()
                    if now - stopped_epoch > retention_seconds:
                        to_prune.append(name)
                except (ValueError, TypeError):
                    to_prune.append(name)

            for name in to_prune:
                del self._ab_experiments[name]
                # Also delete from persistent store
                if self._history_store is not None:
                    try:
                        self._history_store.delete_ab_experiment(name)
                    except Exception as exc:
                        logger.warning("ab_experiment_prune_from_store_failed", name=name, error=str(exc))
                pruned += 1
                self._total_pruned_stopped += 1
                logger.info("ab_experiment_pruned", name=name)

        self._total_ab_cleanups += 1
        self._last_ab_cleanup_run = now

        result_counts = {
            "expired_stopped": expired_stopped,
            "pruned": pruned,
            "total": expired_stopped + pruned,
        }

        logger.info(
            "ab_cleanup_complete",
            **result_counts,
        )

        return result_counts

    def start_ab_cleanup_checker(self) -> None:
        """Start the cleanup checker background thread.

        Sprint 5.46: Periodically runs cleanup_expired_experiments()
        based on the configured interval.
        """
        interval = self._config.ab_cleanup_interval_seconds
        if interval <= 0:
            logger.info("ab_cleanup_checker_disabled")
            return

        if self._ab_cleanup_checker_running:
            logger.warning("ab_cleanup_checker_already_running")
            return

        self._ab_cleanup_checker_running = True

        def _cleanup_loop():
            logger.info("ab_cleanup_checker_started", interval_seconds=interval)
            while self._ab_cleanup_checker_running:
                try:
                    self.cleanup_expired_experiments()
                except Exception as exc:
                    logger.warning("ab_cleanup_checker_error", error=str(exc))
                time.sleep(interval)

        self._ab_cleanup_checker_thread = threading.Thread(
            target=_cleanup_loop,
            daemon=True,
            name="ab-cleanup-checker",
        )
        self._ab_cleanup_checker_thread.start()

    def stop_ab_cleanup_checker(self) -> None:
        """Stop the cleanup checker background thread."""
        self._ab_cleanup_checker_running = False
        logger.info("ab_cleanup_checker_stopped")

    def get_ab_cleanup_status(self) -> dict[str, Any]:
        """Return the status of the cleanup checker.

        Sprint 5.46: Provides visibility into the cleanup checker's running
        state, metrics, and configuration.
        """
        return {
            "running": self._ab_cleanup_checker_running,
            "interval_seconds": self._config.ab_cleanup_interval_seconds,
            "ttl_hours": self._config.ab_experiment_ttl_hours,
            "retention_hours": self._config.ab_stopped_experiment_retention_hours,
            "total_cleanups": self._total_ab_cleanups,
            "last_cleanup_run": self._last_ab_cleanup_run,
            "running_experiments": len([e for e in self._ab_experiments.values() if e.get("status") == "running"]),
            "stopped_experiments": len([e for e in self._ab_experiments.values() if e.get("status") != "running"]),
        }

    # -------------------------------------------------------------------
    # Sprint 5.46: Promotion Rollback Automation
    # -------------------------------------------------------------------

    def check_promotion_rollback(self) -> list[dict[str, Any]]:
        """Check promoted experiments for accuracy degradation.

        Sprint 5.46: Examines recently promoted experiments to see if
        the promoted variant is causing accuracy degradation within the
        observation window. If so, triggers automatic rollback.

        Returns a list of rollback dicts for experiments that were rolled back.
        """
        if not self._config.ab_rollback_enabled:
            return []

        now = time.time()
        observation_window = self._config.ab_rollback_observation_window_seconds
        drop_threshold = self._config.ab_rollback_accuracy_drop_threshold
        rollbacks = []

        for name, experiment in list(self._ab_experiments.items()):
            if experiment.get("status") != "promoted":
                continue

            promo_ts = experiment.get("promotion_timestamp", "")
            if not promo_ts:
                continue

            try:
                promo_dt = datetime.fromisoformat(promo_ts.replace("Z", "+00:00"))
                promo_epoch = promo_dt.timestamp()
                if now - promo_epoch > observation_window:
                    continue  # Outside observation window
            except (ValueError, TypeError):
                continue

            # Check if the promoted variant has degraded
            promoted = experiment.get("promoted_variant", "variant")
            if promoted == "variant":
                promoted_acc = experiment["variant_accuracy"]
                baseline_acc = experiment["control_accuracy"]
            else:
                promoted_acc = experiment["control_accuracy"]
                baseline_acc = experiment["variant_accuracy"]

            if baseline_acc - promoted_acc > drop_threshold:
                # Sprint 5.48: Dry-run mode — evaluate without actually reverting
                if self._config.ab_rollback_dry_run:
                    dry_run_result = self.auto_rollback_promotion_dry_run(name, experiment)
                    if dry_run_result:
                        rollbacks.append(dry_run_result)
                else:
                    rollback_info = self.auto_rollback_promotion(name, experiment)
                    if rollback_info:
                        rollbacks.append(rollback_info)

        return rollbacks

    def auto_rollback_promotion(self, name: str, experiment: dict[str, Any]) -> dict[str, Any] | None:
        """Perform automatic rollback of a promoted experiment.

        Sprint 5.46: Reverts the experiment to its pre-promotion state,
        records the rollback, and sends a notification alert.

        Sprint 5.47: Also reverts the live system configuration (model
        configuration and auto-tuning policy) back to the pre-promotion
        baseline when ab_rollback_revert_live_config is enabled. The
        rollback is atomic — if config reversion fails, it is logged
        but the experiment status still reverts.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        promoted = experiment.get("promoted_variant", "variant")

        # Sprint 5.47: Revert live configuration if enabled
        config_reversion_result = None
        if self._config.ab_rollback_revert_live_config:
            config_reversion_result = self.revert_live_config(name, experiment)

        # Rollback: revert status and clear promotion
        experiment["status"] = "rolled_back"
        experiment["rolled_back_at"] = now_iso
        experiment["rolled_back_from"] = promoted

        self._total_ab_rollbacks += 1

        rollback_record = {
            "experiment_name": name,
            "rolled_back_variant": promoted,
            "rolled_back_at": now_iso,
            "control_accuracy": experiment["control_accuracy"],
            "variant_accuracy": experiment["variant_accuracy"],
            "auto": True,
            "config_reversion": config_reversion_result,
        }
        self._ab_rollback_history.append(rollback_record)
        # Keep last 50 rollbacks
        if len(self._ab_rollback_history) > 50:
            self._ab_rollback_history = self._ab_rollback_history[-50:]

        # Persist
        self.persist_ab_experiment(experiment)

        # Send notification with config reversion info
        alert_data = {
            "experiment_name": name,
            "rolled_back_variant": promoted,
            "control_accuracy": experiment["control_accuracy"],
            "variant_accuracy": experiment["variant_accuracy"],
            "auto": True,
        }
        if config_reversion_result:
            alert_data["config_reversion"] = config_reversion_result

        self._alert_sender(
            Alert(
                alert_type="ab_experiment_rollback",
                severity="warning",
                subject=f"experiment:{name}",
                message=(
                    f"Auto-rollback triggered for experiment '{name}': variant '{promoted}' caused accuracy degradation"
                ),
                data=alert_data,
            )
        )

        logger.info(
            "ab_auto_rollback",
            name=name,
            rolled_back_variant=promoted,
            config_reverted=config_reversion_result is not None,
        )

        return rollback_record

    def get_promotion_rollback_status(self) -> dict[str, Any]:
        """Return the status of promotion rollback automation.

        Sprint 5.46: Provides visibility into rollback configuration and history.
        """
        return {
            "enabled": self._config.ab_rollback_enabled,
            "observation_window_seconds": self._config.ab_rollback_observation_window_seconds,
            "accuracy_drop_threshold": self._config.ab_rollback_accuracy_drop_threshold,
            "total_rollbacks": self._total_ab_rollbacks,
            "rollback_history": self._ab_rollback_history[-20:],
            "promoted_experiments": [e for e in self._ab_experiments.values() if e.get("status") == "promoted"],
        }

    # -------------------------------------------------------------------
    # Sprint 5.46: Decay Recovery Orchestrator
    # -------------------------------------------------------------------

    def run_decay_recovery_orchestrator(self) -> list[dict[str, Any]]:
        """Check for significant confidence decay and trigger recovery actions.

        Sprint 5.46: When confidence decay exceeds the configured threshold,
        automatically triggers actions such as re-running calibration or
        restarting relevant experiments.

        Returns a list of recovery action dicts.
        """
        if not self._config.decay_recovery_enabled:
            return []

        threshold = self._config.decay_recovery_threshold
        actions = self._config.decay_recovery_actions
        recoveries = []

        # Check recent decay events
        for event in self._decay_events:
            if event.get("decay_amount", 0) < threshold:
                continue

            subject = event.get("subject", "")
            current_confidence = event.get("current_confidence", 0)

            recovery_actions_taken = []
            for action in actions:
                if action == "rerun_calibration":
                    # Mark all experiments for the subject for re-calibration
                    for name, experiment in self._ab_experiments.items():
                        if subject in name and experiment.get("status") == "running":
                            experiment["needs_recalibration"] = True
                            self.persist_ab_experiment(experiment)
                            recovery_actions_taken.append(
                                {
                                    "action": "rerun_calibration",
                                    "experiment": name,
                                }
                            )

                elif action == "restart_experiment":
                    # Restart stopped experiments related to the subject
                    for name, experiment in list(self._ab_experiments.items()):
                        if subject in name and experiment.get("status") in ("stopped", "rolled_back"):
                            # Reset and restart
                            experiment["status"] = "running"
                            experiment["stopped_at"] = None
                            experiment["result"] = None
                            experiment["started_at"] = datetime.now(timezone.utc).isoformat()
                            self.persist_ab_experiment(experiment)
                            recovery_actions_taken.append(
                                {
                                    "action": "restart_experiment",
                                    "experiment": name,
                                }
                            )

            if recovery_actions_taken:
                recovery_record = {
                    "subject": subject,
                    "decay_amount": event.get("decay_amount", 0),
                    "current_confidence": current_confidence,
                    "actions_taken": recovery_actions_taken,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                recoveries.append(recovery_record)
                self._decay_recovery_history.append(recovery_record)
                self._total_decay_recoveries += 1

                # Send notification
                self._alert_sender(
                    Alert(
                        alert_type="decay_recovery",
                        severity="info",
                        subject=subject,
                        message=f"Decay recovery triggered for {subject}: {len(recovery_actions_taken)} actions taken",
                        data=recovery_record,
                    )
                )

        # Keep last 50 recovery records
        if len(self._decay_recovery_history) > 50:
            self._decay_recovery_history = self._decay_recovery_history[-50:]

        return recoveries

    def get_decay_recovery_status(self) -> dict[str, Any]:
        """Return the status of the decay recovery orchestrator.

        Sprint 5.46: Provides visibility into recovery configuration and history.
        """
        return {
            "enabled": self._config.decay_recovery_enabled,
            "threshold": self._config.decay_recovery_threshold,
            "actions": self._config.decay_recovery_actions,
            "total_recoveries": self._total_decay_recoveries,
            "recovery_history": self._decay_recovery_history[-20:],
            "recent_decay_events": self._decay_events[-10:],
        }

    # -------------------------------------------------------------------
    # Sprint 5.46: Graceful Shutdown Persistence
    # -------------------------------------------------------------------

    def persist_all_ab_experiments(self) -> int:
        """Persist all running A/B experiments and stop background checkers.

        Sprint 5.46: Called during graceful shutdown to ensure all experiment
        state is safely persisted. Also stops the auto-promotion checker and
        cleanup checker threads.

        Sprint 5.49: Also persists confidence calibration and pre-promotion
        config snapshots so they survive restarts.
        """
        count = 0

        # Stop background checkers
        self.stop_ab_promotion_checker()
        self.stop_ab_cleanup_checker()

        # Persist all experiments
        for name, experiment in self._ab_experiments.items():
            self.persist_ab_experiment(experiment)
            count += 1

        # Sprint 5.49: Persist confidence calibration
        self.persist_confidence_calibration(self._history_store)

        # Sprint 5.49: Persist pre-promotion config snapshots
        self.persist_pre_promotion_snapshots(self._history_store)

        # Sprint 5.48: Persist statistical test results
        self.persist_statistical_test_results(self._history_store)

        logger.info(
            "ab_experiments_persisted_on_shutdown",
            count=count,
        )

        return count

    # -------------------------------------------------------------------
    # Sprint 5.46: Dashboard Experiment Monitoring
    # -------------------------------------------------------------------

    def get_experiment_monitoring_summary(self) -> dict[str, Any]:
        """Return a comprehensive experiment monitoring summary.

        Sprint 5.46: Aggregates experiment data for the dashboard panel,
        including running experiments, variant metrics, promotion history,
        and auto-promotion checker status.
        """
        running = []
        for name, exp in self._ab_experiments.items():
            if exp.get("status") == "running":
                running.append(exp)

        promotions = []
        for name, exp in self._ab_experiments.items():
            if exp.get("promoted_variant"):
                promotions.append(
                    {
                        "experiment_name": name,
                        "variant": exp["promoted_variant"],
                        "timestamp": exp.get("promotion_timestamp", ""),
                        "auto": exp.get("auto_promoted", False),
                        "control_accuracy": exp["control_accuracy"],
                        "variant_accuracy": exp["variant_accuracy"],
                    }
                )

        return {
            "total_experiments": len(self._ab_experiments),
            "running_experiments": running,
            "running_count": len(running),
            "stopped_count": len([e for e in self._ab_experiments.values() if e.get("status") == "stopped"]),
            "promoted_count": len([e for e in self._ab_experiments.values() if e.get("status") == "promoted"]),
            "rolled_back_count": len([e for e in self._ab_experiments.values() if e.get("status") == "rolled_back"]),
            "promotion_history": promotions,
            "auto_promotion_checker": self.get_ab_promotion_checker_status(),
            "cleanup_status": self.get_ab_cleanup_status(),
            "rollback_status": {
                "enabled": self._config.ab_rollback_enabled,
                "total_rollbacks": self._total_ab_rollbacks,
            },
            "decay_recovery_status": {
                "enabled": self._config.decay_recovery_enabled,
                "total_recoveries": self._total_decay_recoveries,
            },
            # Sprint 5.47: Statistical significance and config reversion status
            "statistical_significance": {
                "enabled": self._config.ab_statistical_significance_enabled,
                "method": self._config.ab_statistical_significance_method,
                "p_value_threshold": self._config.ab_statistical_significance_p_value,
                "total_tests_run": self._total_statistical_tests_run,
                "total_promotions_blocked": self._total_promotions_blocked_by_stats,
            },
            "config_reversion": {
                "total_reversions": self._total_config_reversions,
                "revert_live_config_enabled": self._config.ab_rollback_revert_live_config,
            },
            "confidence_calibration": {
                "enabled": self._config.ab_confidence_calibration_enabled,
                "total_updates": self._total_calibration_updates,
                "calibrated_subjects": list(self._confidence_calibration_map.keys()),
                "last_update_time": self._last_calibration_update_time,
                "persisted": self._last_calibration_persist_time > 0,
            },
            "cleanup_metrics": self.get_cleanup_metrics(),
            # Sprint 5.48: Bandit and dry-run status
            "bandit": self.get_bandit_status(),
            "rollback_dry_run": self.get_rollback_dry_run_status(),
            # Sprint 5.49: Pre-promotion snapshot persistence status
            "pre_promotion_snapshots": {
                "in_memory_count": len(self._pre_promotion_config_snapshots),
                "pending_experiments": list(self._pre_promotion_config_snapshots.keys()),
            },
            # Sprint 5.50: Snapshot GC, calibration drift, and timeline status
            "snapshot_gc": self.get_snapshot_gc_status(),
            "calibration_drift": self.get_calibration_drift_status(),
        }

    # -------------------------------------------------------------------
    # Sprint 5.47: Rollback Integration with Live Configuration
    # -------------------------------------------------------------------

    def revert_live_config(self, name: str, experiment: dict[str, Any]) -> dict[str, Any] | None:
        """Revert live system configuration to pre-promotion baseline.

        Sprint 5.47: When a promotion is rolled back, this method reverts
        the live model configuration back to the control (baseline) state
        and restores the auto-tuning policy from the snapshot taken at
        promotion time. The reversion is logged and the result returned.

        The reversion is coordinated with:
        - ModelSlotResolver: reverts model slot configuration
        - AutoTuningPolicy: restores policy parameters

        Parameters
        ----------
        name:
            The experiment name being rolled back.
        experiment:
            The experiment dict.

        Returns a dict with reversion results, or None if no snapshot found.
        """
        snapshot = self._pre_promotion_config_snapshots.get(name)
        if snapshot is None:
            logger.warning(
                "ab_rollback_no_config_snapshot",
                name=name,
                message="No pre-promotion config snapshot found; cannot revert live config",
            )
            return None

        result = {
            "model_config_reverted": False,
            "auto_tuning_reverted": False,
            "baseline_config": snapshot.get("baseline_config", {}),
            "errors": [],
        }

        # Revert model configuration via callback
        if self._live_config_reverter is not None:
            try:
                baseline_config = snapshot.get("baseline_config", {})
                reverted = self._live_config_reverter(name, baseline_config)
                result["model_config_reverted"] = reverted
                if reverted:
                    logger.info(
                        "ab_rollback_model_config_reverted",
                        name=name,
                        baseline_config=baseline_config,
                    )
                else:
                    result["errors"].append("live_config_reverter returned False")
            except Exception as exc:
                result["errors"].append(f"live_config_reverter error: {exc}")
                logger.warning(
                    "ab_rollback_model_config_revert_failed",
                    name=name,
                    error=str(exc),
                )

        # Revert auto-tuning policy via callback
        if self._auto_tuning_reverter is not None:
            try:
                auto_tuning_snapshot = snapshot.get("auto_tuning_snapshot", {})
                reverted = self._auto_tuning_reverter(auto_tuning_snapshot)
                result["auto_tuning_reverted"] = reverted
                if reverted:
                    logger.info(
                        "ab_rollback_auto_tuning_reverted",
                        name=name,
                    )
                else:
                    result["errors"].append("auto_tuning_reverter returned False")
            except Exception as exc:
                result["errors"].append(f"auto_tuning_reverter error: {exc}")
                logger.warning(
                    "ab_rollback_auto_tuning_revert_failed",
                    name=name,
                    error=str(exc),
                )

        self._total_config_reversions += 1

        # Clean up the snapshot after successful reversion
        if name in self._pre_promotion_config_snapshots:
            del self._pre_promotion_config_snapshots[name]

        # Sprint 5.49: Also delete from persistent store
        if self._history_store is not None and hasattr(self._history_store, "delete_pre_promotion_snapshot"):
            try:
                self._history_store.delete_pre_promotion_snapshot(name)
            except Exception as exc:
                logger.debug("pre_promotion_snapshot_delete_from_store_failed", name=name, error=str(exc))

        return result

    def get_auto_tuning_snapshot(self) -> dict[str, Any]:
        """Capture a snapshot of the current auto-tuning policy for rollback.

        Sprint 5.47: Captures the current auto-tuning policy parameters
        so they can be restored during rollback. This is called at
        promotion time.

        Sprint 5.48: Enhanced to capture the full policy state via to_dict()
        when the auto_tuning_reverter callback is wired, allowing complete
        restoration of all policy parameters on rollback.
        """
        snapshot = {
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
            "source": "alert_manager",
        }
        # If the auto_tuning_reverter is set, try to capture policy state
        if self._auto_tuning_reverter is not None:
            try:
                # Try to extract the policy from the closure
                closure = getattr(self._auto_tuning_reverter, "__closure__", None)
                if closure:
                    for cell in closure:
                        try:
                            contents = cell.cell_contents
                            if hasattr(contents, "to_dict"):
                                snapshot.update(contents.to_dict())
                                snapshot["source"] = "auto_tuning_policy"
                                break
                        except ValueError:
                            # Empty cell, skip
                            continue
            except Exception:
                pass
        return snapshot

    def set_live_config_reverter(self, reverter: Any) -> None:
        """Set the callback for reverting live model configuration.

        Sprint 5.47: The reverter callable receives (experiment_name, baseline_config)
        and returns True if the reversion succeeded, False otherwise.
        Called during rollback to coordinate with ModelSlotResolver.
        """
        self._live_config_reverter = reverter
        logger.info("live_config_reverter_set")

    def set_auto_tuning_reverter(self, reverter: Any) -> None:
        """Set the callback for reverting auto-tuning policy.

        Sprint 5.47: The reverter callable receives a snapshot dict
        and returns True if the reversion succeeded, False otherwise.
        Called during rollback to coordinate with AutoTuningPolicy.
        """
        self._auto_tuning_reverter = reverter
        logger.info("auto_tuning_reverter_set")

    def get_config_reversion_status(self) -> dict[str, Any]:
        """Return the status of live config reversion for rollbacks.

        Sprint 5.47: Provides visibility into config reversion capability
        and history.
        """
        return {
            "revert_live_config_enabled": self._config.ab_rollback_revert_live_config,
            "live_config_reverter_set": self._live_config_reverter is not None,
            "auto_tuning_reverter_set": self._auto_tuning_reverter is not None,
            "total_config_reversions": self._total_config_reversions,
            "pending_snapshots": list(self._pre_promotion_config_snapshots.keys()),
        }

    # -------------------------------------------------------------------
    # Sprint 5.47: Statistical Significance Testing for Promotions
    # -------------------------------------------------------------------

    def compute_statistical_significance(self, name: str) -> dict[str, Any] | None:
        """Compute statistical significance for an A/B experiment.

        Sprint 5.47: Implements proper statistical significance testing
        to replace raw accuracy comparison. Supports three methods:
        - z_test: Z-test for proportions (default, best for large samples)
        - t_test: Welch's t-test for comparing means
        - bootstrap: Bootstrap confidence interval (resampling)

        Returns a dict with: {significant, p_value, method, confidence_interval,
        control_mean, variant_mean, statistic}. Returns None if experiment not found
        or insufficient data.
        """
        if name not in self._ab_experiments:
            return None

        experiment = self._ab_experiments[name]
        method = self._config.ab_statistical_significance_method
        min_samples = self._config.ab_statistical_significance_min_samples

        c_samples = experiment.get("control_samples", 0)
        v_samples = experiment.get("variant_samples", 0)
        c_acc = experiment.get("control_accuracy", 0.0)
        v_acc = experiment.get("variant_accuracy", 0.0)

        # Need minimum samples for meaningful statistical testing
        if c_samples < min_samples or v_samples < min_samples:
            return {
                "significant": False,
                "p_value": None,
                "method": method,
                "reason": f"insufficient_samples (control={c_samples}, variant={v_samples}, min={min_samples})",
                "control_mean": c_acc,
                "variant_mean": v_acc,
                "control_samples": c_samples,
                "variant_samples": v_samples,
            }

        self._total_statistical_tests_run += 1

        if method == "z_test":
            result = self.z_test_proportions(c_acc, v_acc, c_samples, v_samples)
        elif method == "t_test":
            result = self.welch_t_test(c_acc, v_acc, c_samples, v_samples)
        elif method == "bootstrap":
            result = self.bootstrap_ci(c_acc, v_acc, c_samples, v_samples)
        else:
            result = self.z_test_proportions(c_acc, v_acc, c_samples, v_samples)

        # Add common fields
        result["method"] = method
        result["control_mean"] = c_acc
        result["variant_mean"] = v_acc
        result["control_samples"] = c_samples
        result["variant_samples"] = v_samples
        result["significant"] = result.get("p_value", 1.0) < self._config.ab_statistical_significance_p_value

        # Cache the result
        self._statistical_test_results[name] = result

        return result

    def z_test_proportions(self, p1: float, p2: float, n1: int, n2: int) -> dict[str, Any]:
        """Z-test for comparing two proportions.

        Tests H0: p1 = p2 vs H1: p1 != p2.
        Returns {p_value, statistic, confidence_interval}.
        """
        # Pooled proportion under H0
        p_pool = (p1 * n1 + p2 * n2) / (n1 + n2) if (n1 + n2) > 0 else 0

        # Standard error under H0
        if p_pool == 0 or p_pool == 1 or n1 == 0 or n2 == 0:
            return {"p_value": 1.0, "statistic": 0.0, "confidence_interval": [0.0, 0.0]}

        se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))

        if se == 0:
            return {"p_value": 1.0, "statistic": 0.0, "confidence_interval": [0.0, 0.0]}

        # Z statistic
        z = (p2 - p1) / se

        # Two-tailed p-value using normal CDF approximation
        p_value = 2 * (1 - self.normal_cdf(abs(z)))

        # 95% confidence interval for the difference
        se_diff = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2) if n1 > 0 and n2 > 0 else 0
        diff = p2 - p1
        ci_lower = diff - 1.96 * se_diff
        ci_upper = diff + 1.96 * se_diff

        return {
            "p_value": round(p_value, 6),
            "statistic": round(z, 4),
            "confidence_interval": [round(ci_lower, 6), round(ci_upper, 6)],
        }

    def welch_t_test(self, mean1: float, mean2: float, n1: int, n2: int) -> dict[str, Any]:
        """Welch's t-test for comparing two means with unequal variances.

        Assumes variance can be estimated from accuracy proportions:
        var = p * (1 - p) / n for binomial-like accuracy measurements.
        """
        var1 = mean1 * (1 - mean1) / n1 if n1 > 0 else 0
        var2 = mean2 * (1 - mean2) / n2 if n2 > 0 else 0

        se = math.sqrt(var1 + var2)
        if se == 0:
            return {"p_value": 1.0, "statistic": 0.0, "confidence_interval": [0.0, 0.0]}

        t_stat = (mean2 - mean1) / se

        # Welch-Satterthwaite degrees of freedom
        if var1 == 0 and var2 == 0:
            df = n1 + n2 - 2
        else:
            numerator = (var1 + var2) ** 2
            denominator = (var1**2 / (n1 - 1)) + (var2**2 / (n2 - 1)) if n1 > 1 and n2 > 1 else 1
            df = numerator / denominator if denominator > 0 else (n1 + n2 - 2)

        # Approximate p-value from t-distribution using normal approximation for large df
        if df > 30:
            p_value = 2 * (1 - self.normal_cdf(abs(t_stat)))
        else:
            # Use a simple approximation for small df
            p_value = 2 * (1 - self.t_cdf_approx(abs(t_stat), df))

        # Confidence interval
        diff = mean2 - mean1
        if df > 30:
            t_crit = 1.96
        else:
            t_crit = 2.0 + 1.0 / df  # Rough approximation
        ci_lower = diff - t_crit * se
        ci_upper = diff + t_crit * se

        return {
            "p_value": round(max(0, min(1, p_value)), 6),
            "statistic": round(t_stat, 4),
            "confidence_interval": [round(ci_lower, 6), round(ci_upper, 6)],
            "degrees_of_freedom": round(df, 2),
        }

    def bootstrap_ci(self, p1: float, p2: float, n1: int, n2: int, n_bootstrap: int = 1000) -> dict[str, Any]:
        """Bootstrap confidence interval for the difference in proportions.

        Resamples from binomial distributions and computes the empirical
        confidence interval and p-value from the resampled differences.
        """
        random.seed(42)  # Deterministic for reproducibility
        diffs = []
        for _ in range(n_bootstrap):
            # Resample: generate binomial draws centered on observed proportions
            sample1 = sum(1 for _ in range(n1) if random.random() < p1) / n1 if n1 > 0 else p1
            sample2 = sum(1 for _ in range(n2) if random.random() < p2) / n2 if n2 > 0 else p2
            diffs.append(sample2 - sample1)

        diffs.sort()

        # 95% confidence interval from bootstrap distribution
        ci_lower = diffs[int(0.025 * n_bootstrap)]
        ci_upper = diffs[int(0.975 * n_bootstrap)]

        # P-value: proportion of bootstrap samples where difference is <= 0
        count_non_positive = sum(1 for d in diffs if d <= 0)
        p_value = 2 * min(count_non_positive, n_bootstrap - count_non_positive) / n_bootstrap

        # Statistic: observed difference / bootstrap SE
        diff_observed = p2 - p1
        se_bootstrap = (sum((d - sum(diffs) / len(diffs)) ** 2 for d in diffs) / len(diffs)) ** 0.5
        statistic = diff_observed / se_bootstrap if se_bootstrap > 0 else 0.0

        return {
            "p_value": round(max(0, min(1, p_value)), 6),
            "statistic": round(statistic, 4),
            "confidence_interval": [round(ci_lower, 6), round(ci_upper, 6)],
            "bootstrap_samples": n_bootstrap,
        }

    @staticmethod
    def normal_cdf(x: float) -> float:
        """Approximate the standard normal CDF using the error function.

        Uses the approximation: CDF(x) = 0.5 * (1 + erf(x / sqrt(2)))
        """
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    @staticmethod
    def t_cdf_approx(t: float, df: float) -> float:
        """Approximate the CDF of the t-distribution.

        Uses the normal approximation with a correction for small df.
        For df > 4, this is reasonable. For very small df, it's a rough estimate.
        """
        # Hill's approximation for the t-distribution CDF
        x = df / (df + t * t)
        x * (1 + t * t / df) if df > 0 else 0.5
        # Simple approximation: use normal with wider tails
        return 0.5 * (1 + math.erf(t / math.sqrt(2 * df / (df - 2)))) if df > 2 else 0.5 * (1 + math.erf(t / 2))

    def get_statistical_significance_status(self) -> dict[str, Any]:
        """Return the status of statistical significance testing.

        Sprint 5.47: Provides visibility into stat testing configuration
        and results.
        """
        return {
            "enabled": self._config.ab_statistical_significance_enabled,
            "method": self._config.ab_statistical_significance_method,
            "p_value_threshold": self._config.ab_statistical_significance_p_value,
            "min_samples": self._config.ab_statistical_significance_min_samples,
            "total_tests_run": self._total_statistical_tests_run,
            "total_promotions_blocked": self._total_promotions_blocked_by_stats,
            "recent_results": {name: result for name, result in list(self._statistical_test_results.items())[-10:]},
        }

    # -------------------------------------------------------------------
    # Sprint 5.47: Cleanup Scheduler Alerting & Metrics
    # -------------------------------------------------------------------

    def get_cleanup_metrics(self) -> dict[str, Any]:
        """Return cleanup scheduler metrics for the health endpoint.

        Sprint 5.47: Exposes cumulative cleanup metrics so operators can
        monitor cleanup activity without manually checking logs.
        """
        return {
            "total_expired_by_ttl": self._total_expired_by_ttl,
            "total_pruned_stopped": self._total_pruned_stopped,
            "last_run_time": self._last_ab_cleanup_run,
            "total_cleanup_runs": self._total_ab_cleanups,
            "ttl_hours": self._config.ab_experiment_ttl_hours,
            "retention_hours": self._config.ab_stopped_experiment_retention_hours,
            "alert_on_ttl_expiry": self._config.ab_cleanup_alert_on_ttl_expiry,
        }

    # -------------------------------------------------------------------
    # Sprint 5.47: Prediction Confidence Calibration from A/B Results
    # -------------------------------------------------------------------

    def update_confidence_calibration(
        self, subject: str, observed_accuracy: float, predicted_confidence: float
    ) -> float:
        """Update confidence calibration mapping from A/B experiment results.

        Sprint 5.47: Calibrates prediction confidence by comparing
        predicted confidence values with actual observed accuracy from
        A/B experiment results. The calibration factor adjusts future
        predictions to be more accurate.

        Parameters
        ----------
        subject:
            The subject/domain being calibrated.
        observed_accuracy:
            The actual observed accuracy from A/B results.
        predicted_confidence:
            The predicted confidence that was assigned.

        Returns the new calibrated confidence factor.
        """
        if not self._config.ab_confidence_calibration_enabled:
            return 1.0

        # Calibration ratio: how much to scale predictions
        if predicted_confidence > 0:
            calibration_ratio = observed_accuracy / predicted_confidence
        else:
            calibration_ratio = 1.0

        # Exponential moving average for smooth calibration updates
        current = self._confidence_calibration_map.get(subject, 1.0)
        alpha = 0.3  # Weight for new observations
        calibrated = alpha * calibration_ratio + (1 - alpha) * current

        self._confidence_calibration_map[subject] = calibrated
        self._total_calibration_updates += 1
        self._last_calibration_update_time = datetime.now(timezone.utc).isoformat()

        logger.info(
            "confidence_calibration_updated",
            subject=subject,
            observed_accuracy=observed_accuracy,
            predicted_confidence=predicted_confidence,
            calibration_ratio=round(calibration_ratio, 4),
            new_factor=round(calibrated, 4),
        )

        return calibrated

    def get_calibrated_confidence(self, subject: str, raw_confidence: float) -> float:
        """Apply confidence calibration to a raw confidence value.

        Sprint 5.47: Adjusts the raw confidence using the calibration
        factor derived from A/B experiment results. If no calibration
        data exists for the subject, returns the raw confidence unchanged.

        Parameters
        ----------
        subject:
            The subject/domain.
        raw_confidence:
            The uncalibrated confidence value.

        Returns the calibrated confidence (clamped to [0, 1]).
        """
        if not self._config.ab_confidence_calibration_enabled:
            return raw_confidence

        calibration_factor = self._confidence_calibration_map.get(subject, 1.0)
        calibrated = raw_confidence * calibration_factor
        return max(0.0, min(1.0, calibrated))

    def get_confidence_calibration_status(self) -> dict[str, Any]:
        """Return the status of confidence calibration.

        Sprint 5.47: Provides visibility into calibration data.

        Sprint 5.49: Includes last update time and persistence status.
        """
        return {
            "enabled": self._config.ab_confidence_calibration_enabled,
            "total_updates": self._total_calibration_updates,
            "calibrated_subjects": dict(self._confidence_calibration_map),
            "last_update_time": getattr(self, "_last_calibration_update_time", None),
            "persisted": self._last_calibration_persist_time > 0,
        }

    # -------------------------------------------------------------------
    # Sprint 5.49: Confidence Calibration Persistence
    # -------------------------------------------------------------------

    def persist_confidence_calibration(self, store: Any) -> int:
        """Persist the confidence calibration map to the history store.

        Sprint 5.49: Ensures calibration data survives restarts. Called
        during graceful shutdown and can be called periodically.

        Returns the number of calibration entries persisted.
        """
        if store is None:
            return 0

        count = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for subject, factor in self._confidence_calibration_map.items():
            try:
                if hasattr(store, "record_confidence_calibration"):
                    store.record_confidence_calibration(subject, factor, now_iso)
                    count += 1
            except Exception as exc:
                logger.warning(
                    "confidence_calibration_persist_failed",
                    subject=subject,
                    error=str(exc),
                )

        if count > 0:
            self._last_calibration_persist_time = time.time()
            logger.info("confidence_calibration_persisted", count=count)

        return count

    def restore_confidence_calibration(self, store: Any) -> int:
        """Restore confidence calibration data from the history store.

        Sprint 5.49: Called on startup to load persisted calibration data
        so that calibrated values survive restarts.

        Returns the number of calibration entries restored.
        """
        if store is None:
            return 0

        try:
            if not hasattr(store, "get_confidence_calibrations"):
                return 0

            calibrations = store.get_confidence_calibrations()
            count = 0
            for entry in calibrations:
                subject = entry.get("subject", "")
                factor = entry.get("calibration_factor", 1.0)
                if subject:
                    self._confidence_calibration_map[subject] = factor
                    count += 1

            if count > 0:
                logger.info("confidence_calibration_restored", count=count)

            return count
        except Exception as exc:
            logger.warning("confidence_calibration_restore_failed", error=str(exc))
            return 0

    # -------------------------------------------------------------------
    # Sprint 5.49: Pre-Promotion Config Snapshot Persistence
    # -------------------------------------------------------------------

    def persist_pre_promotion_snapshots(self, store: Any) -> int:
        """Persist pre-promotion config snapshots to the history store.

        Sprint 5.49: Ensures that config reversion can still function
        after a process restart by persisting the in-memory snapshots
        to SQLite. Called during graceful shutdown.

        Returns the number of snapshots persisted.
        """
        if store is None:
            return 0

        count = 0
        for name, snapshot in self._pre_promotion_config_snapshots.items():
            try:
                if hasattr(store, "record_pre_promotion_snapshot"):
                    store.record_pre_promotion_snapshot(name, snapshot)
                    count += 1
            except Exception as exc:
                logger.warning(
                    "pre_promotion_snapshot_persist_failed",
                    name=name,
                    error=str(exc),
                )

        if count > 0:
            logger.info("pre_promotion_snapshots_persisted", count=count)

        return count

    def restore_pre_promotion_snapshots(self, store: Any) -> int:
        """Restore pre-promotion config snapshots from the history store.

        Sprint 5.49: Called on startup to reload snapshots so that
        rollback can still revert to the correct pre-promotion state
        even after a process restart.

        Returns the number of snapshots restored.
        """
        if store is None:
            return 0

        try:
            if not hasattr(store, "get_pre_promotion_snapshots"):
                return 0

            snapshots = store.get_pre_promotion_snapshots()
            count = 0
            for entry in snapshots:
                name = entry.get("experiment_name", "")
                snapshot_json = entry.get("snapshot_data", {})
                if name and snapshot_json:
                    self._pre_promotion_config_snapshots[name] = snapshot_json
                    count += 1

            if count > 0:
                logger.info("pre_promotion_snapshots_restored", count=count)

            return count
        except Exception as exc:
            logger.warning("pre_promotion_snapshots_restore_failed", error=str(exc))
            return 0

    # -------------------------------------------------------------------
    # Sprint 5.48: Persist Statistical Test Results
    # -------------------------------------------------------------------

    def persist_statistical_test_results(self, store: Any) -> int:
        """Persist in-memory statistical test results to the history store.

        Sprint 5.48: Ensures statistical test outcomes survive restarts.
        Called during graceful shutdown and can also be called periodically.

        Returns the number of test results persisted.
        """
        if store is None:
            return 0

        count = 0
        for name, result in self._statistical_test_results.items():
            try:
                if hasattr(store, "record_statistical_test_result"):
                    store.record_statistical_test_result(name, result)
                    count += 1
            except Exception as exc:
                logger.warning(
                    "statistical_test_result_persist_failed",
                    name=name,
                    error=str(exc),
                )

        if count > 0:
            logger.info("statistical_test_results_persisted", count=count)

        return count

    def record_accuracy_snapshot(self, name: str) -> dict[str, Any] | None:
        """Record a per-variant accuracy snapshot for time-series visualization.

        Sprint 5.48: Captures the current accuracy of both control and variant
        at a point in time, enabling the dashboard mini charts to render real
        historical data rather than synthesized oscillation. Persists the
        snapshot to the history store for durability across restarts.

        Parameters
        ----------
        name:
            The experiment name.

        Returns the snapshot dict, or None if the experiment doesn't exist.
        """
        if name not in self._ab_experiments:
            return None

        experiment = self._ab_experiments[name]
        now_iso = datetime.now(timezone.utc).isoformat()

        snapshot = {
            "experiment_name": name,
            "timestamp": now_iso,
            "control_accuracy": experiment.get("control_accuracy", 0.0),
            "variant_accuracy": experiment.get("variant_accuracy", 0.0),
            "control_samples": experiment.get("control_samples", 0),
            "variant_samples": experiment.get("variant_samples", 0),
            "status": experiment.get("status", "running"),
        }

        # Append to in-memory timeseries list on the experiment
        if "accuracy_timeseries" not in experiment:
            experiment["accuracy_timeseries"] = []
        experiment["accuracy_timeseries"].append(snapshot)
        # Keep last 200 snapshots per experiment
        if len(experiment["accuracy_timeseries"]) > 200:
            experiment["accuracy_timeseries"] = experiment["accuracy_timeseries"][-200:]

        # Persist to store
        if self._history_store is not None and hasattr(self._history_store, "record_accuracy_timeseries"):
            try:
                self._history_store.record_accuracy_timeseries(snapshot)
            except Exception as exc:
                logger.warning("accuracy_snapshot_persist_failed", name=name, error=str(exc))

        logger.debug("accuracy_snapshot_recorded", name=name)

        return snapshot

    def get_accuracy_timeseries(self, name: str) -> list[dict[str, Any]]:
        """Return accuracy time-series data for an experiment.

        Sprint 5.48: Returns the list of accuracy snapshots for the dashboard
        mini charts. Falls back to the persistent store if no in-memory data.

        Parameters
        ----------
        name:
            The experiment name.

        Returns a list of snapshot dicts sorted by timestamp.
        """
        if name not in self._ab_experiments:
            # Try persistent store
            if self._history_store is not None and hasattr(self._history_store, "get_accuracy_timeseries"):
                try:
                    return self._history_store.get_accuracy_timeseries(name)
                except Exception:
                    pass
            return []

        experiment = self._ab_experiments[name]
        timeseries = experiment.get("accuracy_timeseries", [])

        # If in-memory is empty, try loading from store
        if (
            not timeseries
            and self._history_store is not None
            and hasattr(self._history_store, "get_accuracy_timeseries")
        ):
            try:
                timeseries = self._history_store.get_accuracy_timeseries(name)
                if timeseries:
                    experiment["accuracy_timeseries"] = timeseries
            except Exception:
                pass

        return timeseries

    # -------------------------------------------------------------------
    # Sprint 5.48: Multi-Armed Bandit Support
    # -------------------------------------------------------------------

    def get_bandit_allocation(self, name: str) -> dict[str, float]:
        """Compute traffic allocation for experiment variants using bandit algorithms.

        Sprint 5.48: Implements Thompson Sampling and UCB (Upper Confidence Bound)
        for dynamic traffic allocation in A/B experiments. Instead of a fixed 50/50
        split, bandit algorithms progressively allocate more traffic to the
        better-performing variant based on observed results.

        Thompson Sampling uses a Beta distribution posterior for each variant,
        sampling from it to determine allocation probabilities. UCB uses an
        optimistic confidence bound to balance exploration and exploitation.

        Parameters
        ----------
        name:
            The experiment name.

        Returns a dict mapping variant names to allocation fractions (summing to 1.0).
        """
        if not self._config.ab_bandit_enabled:
            # Default 50/50 allocation
            return {"control": 0.5, "variant": 0.5}

        if name not in self._ab_experiments:
            return {"control": 0.5, "variant": 0.5}

        experiment = self._ab_experiments[name]
        c_samples = max(experiment.get("control_samples", 0), 1)
        v_samples = max(experiment.get("variant_samples", 0), 1)
        c_acc = experiment.get("control_accuracy", 0.5)
        v_acc = experiment.get("variant_accuracy", 0.5)

        # Sprint 5.50: Adaptive bandit method selection
        # When enabled, choose the best method based on experiment characteristics
        effective_method = self._config.ab_bandit_method
        if self._config.ab_bandit_adaptive_method_enabled:
            effective_method = self.select_adaptive_bandit_method(name, c_samples, v_samples, c_acc, v_acc)

        # Initialize or update bandit state
        if name not in self._bandit_state:
            self._bandit_state[name] = {
                "control_alpha": 1.0,
                "control_beta": 1.0,
                "variant_alpha": 1.0,
                "variant_beta": 1.0,
            }

        state = self._bandit_state[name]

        if effective_method == "thompson":
            # Thompson Sampling: Update Beta posterior with observed results
            # Alpha = successes + 1, Beta = failures + 1
            state["control_alpha"] = c_acc * c_samples + 1.0
            state["control_beta"] = (1 - c_acc) * c_samples + 1.0
            state["variant_alpha"] = v_acc * v_samples + 1.0
            state["variant_beta"] = (1 - v_acc) * v_samples + 1.0

            # Sample from each posterior
            c_sample = random.betavariate(
                max(state["control_alpha"], 0.01),
                max(state["control_beta"], 0.01),
            )
            v_sample = random.betavariate(
                max(state["variant_alpha"], 0.01),
                max(state["variant_beta"], 0.01),
            )

            total = c_sample + v_sample
            if total == 0:
                return {"control": 0.5, "variant": 0.5}

            allocation = {
                "control": round(c_sample / total, 4),
                "variant": round(v_sample / total, 4),
            }

        elif effective_method == "ucb":
            # UCB1: Q(a) + c * sqrt(ln(N) / n(a))
            total_samples = c_samples + v_samples
            if total_samples < 2:
                return {"control": 0.5, "variant": 0.5}

            explore_rate = self._config.ab_bandit_explore_rate
            log_total = math.log(total_samples)

            c_ucb = c_acc + explore_rate * math.sqrt(log_total / c_samples) if c_samples > 0 else 1.0
            v_ucb = v_acc + explore_rate * math.sqrt(log_total / v_samples) if v_samples > 0 else 1.0

            total_ucb = c_ucb + v_ucb
            if total_ucb == 0:
                return {"control": 0.5, "variant": 0.5}

            allocation = {
                "control": round(c_ucb / total_ucb, 4),
                "variant": round(v_ucb / total_ucb, 4),
            }

        elif effective_method == "epsilon_greedy":
            # Sprint 5.49: Epsilon-greedy bandit
            # With probability epsilon, explore (equal allocation).
            # With probability (1 - epsilon), exploit (allocate to the best variant).
            epsilon = self._config.ab_bandit_explore_rate
            if random.random() < epsilon:
                # Explore: equal allocation
                allocation = {"control": 0.5, "variant": 0.5}
            else:
                # Exploit: allocate proportionally to accuracy
                total_acc = c_acc + v_acc
                if total_acc == 0:
                    allocation = {"control": 0.5, "variant": 0.5}
                else:
                    allocation = {
                        "control": round(c_acc / total_acc, 4),
                        "variant": round(v_acc / total_acc, 4),
                    }
                    # Give the winner a strong allocation
                    if c_acc > v_acc:
                        allocation = {"control": round(1.0 - epsilon / 2, 4), "variant": round(epsilon / 2, 4)}
                    elif v_acc > c_acc:
                        allocation = {"control": round(epsilon / 2, 4), "variant": round(1.0 - epsilon / 2, 4)}

        else:
            allocation = {"control": 0.5, "variant": 0.5}

        # Sprint 5.49: Contextual bandit adjustment
        # When contextual bandits are enabled, adjust allocation based on
        # context features (alert type, subject). This is exploratory and
        # uses a simple lookup of historical rewards per context.
        if self._config.ab_bandit_contextual_enabled and self._bandit_context_rewards:
            allocation = self.adjust_allocation_for_context(name, allocation)

        self._total_bandit_allocations += 1

        # Ensure allocations sum to 1.0
        alloc_sum = allocation["control"] + allocation["variant"]
        if alloc_sum > 0:
            allocation["control"] = round(allocation["control"] / alloc_sum, 4)
            allocation["variant"] = round(allocation["variant"] / alloc_sum, 4)

        # Sprint 5.50: Log the bandit allocation decision
        if self._config.ab_bandit_decision_logging_enabled:
            self.log_bandit_decision(
                name=name,
                method=effective_method,
                allocation=allocation,
                c_samples=c_samples,
                v_samples=v_samples,
            )

        return allocation

    def adjust_allocation_for_context(self, name: str, base_allocation: dict[str, float]) -> dict[str, float]:
        """Adjust bandit allocation based on contextual features.

        Sprint 5.49: Exploratory contextual bandit support. Uses historical
        reward data per context key to adjust the base allocation. Context
        keys are derived from experiment metadata using the configured
        feature list (ab_bandit_contextual_features).

        This is intentionally simple: it looks up the average reward for
        each variant under the current context and shifts allocation
        toward the variant that has historically performed better in
        this context. If insufficient data exists, returns the base
        allocation unchanged.

        Parameters
        ----------
        name:
            The experiment name.
        base_allocation:
            The base allocation from the non-contextual bandit method.

        Returns the adjusted allocation dict.
        """
        if name not in self._ab_experiments:
            return base_allocation

        experiment = self._ab_experiments[name]
        metadata = experiment.get("metadata", {})
        features = self._config.ab_bandit_contextual_features

        # Build a context key from the configured features
        context_parts = []
        for feat in features:
            val = metadata.get(feat, "")
            if val:
                context_parts.append(f"{feat}={val}")
        if not context_parts:
            return base_allocation

        context_key = "|".join(context_parts)

        # Look up historical rewards for this context
        context_rewards = self._bandit_context_rewards.get(name, {})
        control_key = f"{context_key}:control"
        variant_key = f"{context_key}:variant"

        control_rewards = context_rewards.get(control_key, [])
        variant_rewards = context_rewards.get(variant_key, [])

        # Need at least 5 observations per variant for context to matter
        if len(control_rewards) < 5 or len(variant_rewards) < 5:
            return base_allocation

        control_avg = sum(control_rewards) / len(control_rewards)
        variant_avg = sum(variant_rewards) / len(variant_rewards)

        # Shift allocation by up to 15% toward the better-performing variant
        shift = min(0.15, abs(variant_avg - control_avg) * 0.5)
        if variant_avg > control_avg:
            adjusted = {
                "control": round(max(0.1, base_allocation["control"] - shift), 4),
                "variant": round(min(0.9, base_allocation["variant"] + shift), 4),
            }
        elif control_avg > variant_avg:
            adjusted = {
                "control": round(min(0.9, base_allocation["control"] + shift), 4),
                "variant": round(max(0.1, base_allocation["variant"] - shift), 4),
            }
        else:
            return base_allocation

        # Re-normalize
        total = adjusted["control"] + adjusted["variant"]
        if total > 0:
            adjusted["control"] = round(adjusted["control"] / total, 4)
            adjusted["variant"] = round(adjusted["variant"] / total, 4)

        return adjusted

    def record_bandit_context_reward(
        self, name: str, variant: str, reward: float, context: dict[str, str] | None = None
    ) -> None:
        """Record a reward observation for contextual bandit learning.

        Sprint 5.49: Called when an A/B result is recorded, this method
        stores the reward (accuracy) under a context key derived from
        the experiment metadata and any provided context overrides.

        Parameters
        ----------
        name:
            The experiment name.
        variant:
            Which variant ("control" or "variant").
        reward:
            The observed reward (typically accuracy, 0-1).
        context:
            Optional context overrides (merged with experiment metadata).
        """
        if not self._config.ab_bandit_contextual_enabled:
            return

        if name not in self._ab_experiments:
            return

        experiment = self._ab_experiments[name]
        metadata = dict(experiment.get("metadata", {}))
        if context:
            metadata.update(context)

        features = self._config.ab_bandit_contextual_features
        context_parts = []
        for feat in features:
            val = metadata.get(feat, "")
            if val:
                context_parts.append(f"{feat}={val}")

        if not context_parts:
            return

        context_key = "|".join(context_parts) + f":{variant}"

        if name not in self._bandit_context_rewards:
            self._bandit_context_rewards[name] = {}
        if context_key not in self._bandit_context_rewards[name]:
            self._bandit_context_rewards[name][context_key] = []

        self._bandit_context_rewards[name][context_key].append(reward)
        # Keep last 100 rewards per context
        if len(self._bandit_context_rewards[name][context_key]) > 100:
            self._bandit_context_rewards[name][context_key] = self._bandit_context_rewards[name][context_key][-100:]

    def get_bandit_status(self) -> dict[str, Any]:
        """Return the status of multi-armed bandit allocation.

        Sprint 5.48: Provides visibility into bandit configuration and state.

        Sprint 5.49: Includes contextual bandit and epsilon-greedy status.

        Sprint 5.50: Includes decision logging and adaptive method selection status.
        """
        return {
            "enabled": self._config.ab_bandit_enabled,
            "method": self._config.ab_bandit_method,
            "explore_rate": self._config.ab_bandit_explore_rate,
            "total_allocations": self._total_bandit_allocations,
            "active_experiments": list(self._bandit_state.keys()),
            "contextual_enabled": self._config.ab_bandit_contextual_enabled,
            "contextual_features": self._config.ab_bandit_contextual_features,
            "contextual_rewards_tracked": sum(
                len(rewards)
                for exp_rewards in self._bandit_context_rewards.values()
                for rewards in exp_rewards.values()
            ),
            # Sprint 5.50: Decision logging
            "decision_logging_enabled": self._config.ab_bandit_decision_logging_enabled,
            "total_decisions_logged": self._total_bandit_decisions_logged,
            # Sprint 5.50: Adaptive method selection
            "adaptive_method": self.get_adaptive_bandit_status(),
        }

    # -------------------------------------------------------------------
    # Sprint 5.48: Rollback Dry-Run Mode
    # -------------------------------------------------------------------

    def auto_rollback_promotion_dry_run(self, name: str, experiment: dict[str, Any]) -> dict[str, Any]:
        """Evaluate rollback conditions without actually reverting.

        Sprint 5.48: When ab_rollback_dry_run is enabled, this method
        evaluates the same conditions as _auto_rollback_promotion but
        only logs what *would* happen, including the config reversion
        that would occur. This provides a safety net before enabling
        fully automatic rollback.

        Parameters
        ----------
        name:
            The experiment name being evaluated.
        experiment:
            The experiment dict.

        Returns a dict describing what would happen on actual rollback.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        promoted = experiment.get("promoted_variant", "variant")

        self._total_dry_run_evaluations += 1

        # Evaluate config reversion that would happen
        config_reversion_preview = None
        if self._config.ab_rollback_revert_live_config:
            snapshot = self._pre_promotion_config_snapshots.get(name)
            if snapshot:
                config_reversion_preview = {
                    "would_revert_model_config": self._live_config_reverter is not None,
                    "would_revert_auto_tuning": self._auto_tuning_reverter is not None,
                    "baseline_config_keys": list(snapshot.get("baseline_config", {}).keys()),
                    "snapshot_timestamp": snapshot.get("timestamp", ""),
                }
            else:
                config_reversion_preview = {
                    "would_revert_model_config": False,
                    "would_revert_auto_tuning": False,
                    "reason": "no_pre_promotion_snapshot",
                }

        dry_run_result = {
            "dry_run": True,
            "would_rollback": True,
            "experiment_name": name,
            "rolled_back_variant": promoted,
            "control_accuracy": experiment["control_accuracy"],
            "variant_accuracy": experiment["variant_accuracy"],
            "evaluated_at": now_iso,
            "config_reversion_preview": config_reversion_preview,
        }

        self._total_dry_run_would_rollback += 1

        logger.info(
            "ab_rollback_dry_run_would_rollback",
            name=name,
            variant=promoted,
            control_accuracy=experiment["control_accuracy"],
            variant_accuracy=experiment["variant_accuracy"],
            would_revert_config=config_reversion_preview is not None
            and config_reversion_preview.get("would_revert_model_config", False),
        )

        return dry_run_result

    def get_rollback_dry_run_status(self) -> dict[str, Any]:
        """Return the status of rollback dry-run mode.

        Sprint 5.48: Provides visibility into dry-run evaluations.
        """
        return {
            "enabled": self._config.ab_rollback_dry_run,
            "total_evaluations": self._total_dry_run_evaluations,
            "total_would_rollback": self._total_dry_run_would_rollback,
        }

    # -------------------------------------------------------------------
    # Sprint 5.50: Bandit Decision Logging & Replay
    # -------------------------------------------------------------------

    def log_bandit_decision(
        self,
        name: str,
        method: str,
        allocation: dict[str, float],
        c_samples: int = 0,
        v_samples: int = 0,
    ) -> None:
        """Log a bandit allocation decision to the persistent store.

        Sprint 5.50: Records every bandit allocation decision with full
        context for debugging and auditing. The decision includes the method
        used, the resulting allocation, the sample sizes at decision time,
        and any contextual features that were active.

        Parameters
        ----------
        name:
            The experiment name.
        method:
            The bandit method used (thompson, ucb, epsilon_greedy, adaptive).
        allocation:
            The resulting allocation dict.
        c_samples:
            Control sample count at decision time.
        v_samples:
            Variant sample count at decision time.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        # Build context features dict if contextual bandits are enabled
        context_features = {}
        if self._config.ab_bandit_contextual_enabled and name in self._ab_experiments:
            metadata = self._ab_experiments[name].get("metadata", {})
            for feat in self._config.ab_bandit_contextual_features:
                val = metadata.get(feat, "")
                if val:
                    context_features[feat] = val

        # Compute confidence as the absolute difference in allocation
        confidence = abs(allocation.get("variant", 0.5) - allocation.get("control", 0.5))

        decision = {
            "experiment_name": name,
            "method": method,
            "allocation": allocation,
            "confidence": round(confidence, 4),
            "context_features": context_features,
            "sample_sizes": {"control": c_samples, "variant": v_samples},
            "timestamp": now_iso,
        }

        # Persist to store
        if self._history_store is not None and hasattr(self._history_store, "record_bandit_decision"):
            try:
                self._history_store.record_bandit_decision(decision)
                self._total_bandit_decisions_logged += 1
            except Exception as exc:
                logger.warning("bandit_decision_log_failed", name=name, error=str(exc))

    def get_bandit_decisions(
        self,
        experiment_name: str | None = None,
        method: str | None = None,
        since: str | None = None,
        before: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query bandit decision log for debugging and auditing.

        Sprint 5.50: Delegates to the history store for querying persisted
        bandit decisions. Supports filtering by experiment name, method,
        and date range.

        Returns a list of decision dicts, most recent first.
        """
        if self._history_store is None or not hasattr(self._history_store, "get_bandit_decisions"):
            return []

        try:
            return self._history_store.get_bandit_decisions(
                experiment_name=experiment_name,
                method=method,
                since=since,
                before=before,
                limit=limit,
            )
        except Exception as exc:
            logger.warning("bandit_decisions_query_failed", error=str(exc))
            return []

    def replay_bandit_decisions(self, experiment_name: str, limit: int = 50) -> list[dict]:
        """Replay historical bandit allocation decisions for an experiment.

        Sprint 5.50: Returns the sequence of bandit decisions for an
        experiment in chronological order, enabling operators to understand
        how allocation evolved over time and audit the bandit's behavior.

        Parameters
        ----------
        experiment_name:
            The experiment to replay decisions for.
        limit:
            Maximum number of decisions to return.

        Returns a list of decision dicts sorted chronologically (oldest first).
        """
        decisions = self.get_bandit_decisions(
            experiment_name=experiment_name,
            limit=limit,
        )
        # Reverse to get chronological order (oldest first)
        decisions.reverse()
        return decisions

    # -------------------------------------------------------------------
    # Sprint 5.50: Adaptive Bandit Method Selection
    # -------------------------------------------------------------------

    def select_adaptive_bandit_method(
        self,
        name: str,
        c_samples: int,
        v_samples: int,
        c_acc: float,
        v_acc: float,
    ) -> str:
        """Select the best bandit method based on experiment characteristics.

        Sprint 5.50: Uses logged decision data and experiment characteristics
        to automatically choose between Thompson Sampling, UCB, and
        epsilon-greedy. The selection logic considers:

        1. Sample size: Thompson Sampling excels with large samples due to
           its Bayesian posterior updating. UCB is better with small samples
           because it explicitly models uncertainty.
        2. Variance: When the accuracy gap between variants is small (low
           variance), epsilon-greedy's direct exploitation is effective.
           When variance is high, Thompson Sampling's stochastic exploration
           is preferable.
        3. Context availability: If contextual bandits are enabled and have
           sufficient data, Thompson Sampling is preferred for its posterior
           composition with context.

        The method also consults historical decision logs to evaluate which
        method has produced the most confident allocations for similar
        experiments in the past.

        Parameters
        ----------
        name:
            The experiment name.
        c_samples:
            Control variant sample count.
        v_samples:
            Variant variant sample count.
        c_acc:
            Control variant accuracy.
        v_acc:
            Variant variant accuracy.

        Returns the selected method name: "thompson", "ucb", or "epsilon_greedy".
        """
        total_samples = c_samples + v_samples
        acc_gap = abs(v_acc - c_acc)
        variance = acc_gap  # Simple proxy: larger gap = higher variance signal

        # Default: use configured method
        selected = self._config.ab_bandit_method

        # Heuristic-based selection
        if total_samples < 100:
            # Small sample regime: UCB's optimism under uncertainty is best
            selected = "ucb"
        elif variance < 0.05:
            # Low variance: epsilon-greedy's direct exploitation works well
            selected = "epsilon_greedy"
        else:
            # Large sample, high variance: Thompson Sampling's Bayesian approach
            selected = "thompson"

        # If contextual bandits are enabled and have data, prefer Thompson
        if self._config.ab_bandit_contextual_enabled and self._bandit_context_rewards:
            if name in self._bandit_context_rewards:
                selected = "thompson"

        # Consult historical decision logs for performance data
        if self._history_store is not None and hasattr(self._history_store, "get_bandit_decisions"):
            try:
                recent_decisions = self._history_store.get_bandit_decisions(
                    experiment_name=name,
                    limit=20,
                )
                if len(recent_decisions) >= 10:
                    # Evaluate which method produced the highest average confidence
                    method_confidences: dict[str, list[float]] = {}
                    for d in recent_decisions:
                        m = d.get("method", "")
                        conf = d.get("confidence", 0.0)
                        if m:
                            method_confidences.setdefault(m, []).append(conf)

                    if method_confidences:
                        avg_conf = {m: sum(confs) / len(confs) for m, confs in method_confidences.items()}
                        best_method = max(avg_conf, key=avg_conf.get)
                        # Only switch if the best method has significantly higher confidence
                        if avg_conf[best_method] > avg_conf.get(selected, 0.0) + 0.05:
                            selected = best_method
            except Exception:
                pass

        # Track method selection for this experiment
        previous = self._adaptive_method_history.get(name)
        if previous is not None and previous != selected:
            self._total_adaptive_method_switches += 1
            logger.info(
                "adaptive_bandit_method_switched",
                name=name,
                from_method=previous,
                to_method=selected,
            )
        self._adaptive_method_history[name] = selected

        return selected

    def get_adaptive_bandit_status(self) -> dict[str, Any]:
        """Return the status of adaptive bandit method selection.

        Sprint 5.50: Exposes the current active method per experiment and
        the switching logic summary.
        """
        return {
            "enabled": self._config.ab_bandit_adaptive_method_enabled,
            "default_method": self._config.ab_bandit_method,
            "total_switches": self._total_adaptive_method_switches,
            "active_methods": dict(self._adaptive_method_history),
        }

    # -------------------------------------------------------------------
    # Sprint 5.50: Snapshot Garbage Collection
    # -------------------------------------------------------------------

    def start_snapshot_gc(self) -> None:
        """Start the snapshot garbage collection background thread.

        Sprint 5.50: Periodically cleans up stale pre-promotion snapshots
        for experiments that are no longer active (stopped, rolled back,
        or expired).
        """
        interval = self._config.ab_snapshot_gc_interval_seconds
        if interval <= 0 or not self._config.ab_snapshot_gc_enabled:
            logger.info("snapshot_gc_disabled")
            return

        if self._snapshot_gc_running:
            logger.warning("snapshot_gc_already_running")
            return

        self._snapshot_gc_running = True

        def _gc_loop():
            logger.info("snapshot_gc_started", interval_seconds=interval)
            while self._snapshot_gc_running:
                try:
                    self.run_snapshot_gc()
                except Exception as exc:
                    logger.warning("snapshot_gc_error", error=str(exc))
                time.sleep(interval)

        self._snapshot_gc_thread = threading.Thread(
            target=_gc_loop,
            daemon=True,
            name="snapshot-gc",
        )
        self._snapshot_gc_thread.start()

    def stop_snapshot_gc(self) -> None:
        """Stop the snapshot garbage collection background thread."""
        self._snapshot_gc_running = False
        logger.info("snapshot_gc_stopped")

    def run_snapshot_gc(self) -> int:
        """Run a single snapshot garbage collection pass.

        Sprint 5.50: Identifies active experiments (running or promoted
        status) and removes snapshots for experiments that are no longer
        active or have exceeded the retention period.

        Returns the number of snapshots removed.
        """
        # Determine which experiments are still active
        active_names = {
            name for name, exp in self._ab_experiments.items() if exp.get("status") in ("running", "promoted")
        }

        # Also keep snapshots that exist in memory (they may not be in _ab_experiments yet)
        active_names.update(self._pre_promotion_config_snapshots.keys())

        removed = 0
        if self._history_store is not None and hasattr(self._history_store, "cleanup_stale_pre_promotion_snapshots"):
            try:
                removed = self._history_store.cleanup_stale_pre_promotion_snapshots(
                    active_experiment_names=active_names,
                    max_age_hours=self._config.ab_snapshot_gc_max_age_hours,
                )
            except Exception as exc:
                logger.warning("snapshot_gc_store_cleanup_failed", error=str(exc))

        # Also clean in-memory snapshots for stopped/rolled-back experiments
        stale_in_memory = []
        for name, snapshot in self._pre_promotion_config_snapshots.items():
            exp = self._ab_experiments.get(name)
            if exp is not None and exp.get("status") in ("stopped", "rolled_back"):
                stale_in_memory.append(name)

        for name in stale_in_memory:
            del self._pre_promotion_config_snapshots[name]
            removed += 1

        self._total_snapshot_gc_runs += 1
        self._total_snapshots_cleaned += removed
        self._last_snapshot_gc_run = time.time()

        if removed > 0:
            logger.info("snapshot_gc_completed", removed=removed, active=len(active_names))

        return removed

    def get_snapshot_gc_status(self) -> dict[str, Any]:
        """Return the status of snapshot garbage collection.

        Sprint 5.50: Provides visibility into GC activity.
        """
        return {
            "enabled": self._config.ab_snapshot_gc_enabled,
            "max_age_hours": self._config.ab_snapshot_gc_max_age_hours,
            "total_runs": self._total_snapshot_gc_runs,
            "total_cleaned": self._total_snapshots_cleaned,
            "last_run_time": self._last_snapshot_gc_run,
        }

    # -------------------------------------------------------------------
    # Sprint 5.50: Calibration Drift Detection
    # -------------------------------------------------------------------

    def check_calibration_drift(self) -> list[dict]:
        """Check all calibration factors for significant drift.

        Sprint 5.50: Monitors calibration factors over time and detects
        when any factor deviates significantly from 1.0 (neutral). A
        calibration factor of 1.0 means predictions are perfectly calibrated.
        Factors >1.0 mean the system is over-confident; factors <1.0 mean
        it's under-confident. When drift exceeds the threshold (default 20%),
        an alert is sent via the existing notification system and the drift
        is recorded for visibility in the health endpoint.

        Returns a list of subjects with detected drift.
        """
        if not self._config.ab_calibration_drift_check_enabled:
            return []

        threshold = self._config.ab_calibration_drift_threshold
        drifted = []

        for subject, factor in self._confidence_calibration_map.items():
            deviation = abs(factor - 1.0)
            if deviation > threshold:
                drift_info = {
                    "subject": subject,
                    "calibration_factor": round(factor, 4),
                    "deviation": round(deviation, 4),
                    "threshold": threshold,
                    "direction": "over_confident" if factor > 1.0 else "under_confident",
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                }
                drifted.append(drift_info)

                # Record drift alert
                self._calibration_drift_alerts.append(drift_info)
                # Keep last 50 drift alerts
                if len(self._calibration_drift_alerts) > 50:
                    self._calibration_drift_alerts = self._calibration_drift_alerts[-50:]

                self._total_calibration_drift_alerts += 1

                # Send alert via the existing notification system
                self._alert_sender(
                    Alert(
                        alert_type="calibration_drift",
                        severity="warning",
                        subject=f"calibration:{subject}",
                        message=(
                            f"Calibration factor for '{subject}' has drifted {deviation:.1%} from 1.0 "
                            f"(current: {factor:.4f}, threshold: {threshold:.1%}). "
                            f"Direction: {'over-confident' if factor > 1.0 else 'under-confident'}."
                        ),
                        data=drift_info,
                    )
                )

                logger.warning(
                    "calibration_drift_detected",
                    subject=subject,
                    factor=factor,
                    deviation=deviation,
                    threshold=threshold,
                )

        return drifted

    def get_calibration_drift_status(self) -> dict[str, Any]:
        """Return the status of calibration drift detection.

        Sprint 5.50: Exposes drift metrics and recent alerts.
        """
        return {
            "enabled": self._config.ab_calibration_drift_check_enabled,
            "threshold": self._config.ab_calibration_drift_threshold,
            "total_drift_alerts": self._total_calibration_drift_alerts,
            "recent_alerts": self._calibration_drift_alerts[-10:],
            "current_factors": {
                subject: round(factor, 4)
                for subject, factor in self._confidence_calibration_map.items()
                if abs(factor - 1.0) > self._config.ab_calibration_drift_threshold
            },
        }

    # -------------------------------------------------------------------
    # Sprint 5.50: Historical Promotion Timeline (API)
    # -------------------------------------------------------------------

    def get_experiment_event_timeline(
        self,
        experiment_name: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
        before: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Return a unified timeline of experimentation events.

        Sprint 5.50: Aggregates promotion, rollback, decay recovery, and
        bandit decision events into a single chronological view. Delegates
        to the history store for querying persisted data.

        Parameters
        ----------
        experiment_name:
            Filter by experiment name.
        event_type:
            Filter by event type (promotion, rollback, decay_recovery, bandit_decision).
        since:
            ISO 8601 datetime — only return events after this.
        before:
            ISO 8601 datetime — only return events before this.
        limit:
            Maximum number of events to return.

        Returns a list of event dicts sorted by timestamp descending.
        """
        if self._history_store is None or not hasattr(self._history_store, "get_experiment_event_timeline"):
            return []

        try:
            return self._history_store.get_experiment_event_timeline(
                experiment_name=experiment_name,
                event_type=event_type,
                since=since,
                before=before,
                limit=limit,
            )
        except Exception as exc:
            logger.warning("event_timeline_query_failed", error=str(exc))
            return []


# ---------------------------------------------------------------------------
# Sprint 5.62: AlertLifecycleManager — owns alert lifecycle state
# ---------------------------------------------------------------------------


class AlertLifecycleManager:
    """Owns alert lifecycle state: history, delivery status, mute rules,
    rate-limiting, escalation tracking, and correlation ID generation.

    Sprint 5.62: Extracted from AlertManager to centralize lifecycle
    concerns and eliminate direct attribute access from outside the
    manager.  All lifecycle state is owned here and accessed through
    clearly defined methods.
    """

    # Maximum number of delivery failure records to keep
    _MAX_FAILURE_HISTORY = 50
    # Maximum number of in-memory alert history entries
    _MAX_ALERT_HISTORY = 50

    def __init__(self, config: AlertConfig) -> None:
        self._config = config
        self._history_store: Any = None  # Set via set_history_store()

        # Rate-limiting: (alert_type, subject) -> last_alert_timestamp
        self._last_alert_time: dict[tuple[str, str], float] = {}
        # History of sent alerts (last 50) — in-memory fallback
        self._alert_history: list[dict] = []
        # Delivery failure history — in-memory fallback
        self._delivery_failures: list[DeliveryFailure] = []
        # Delivery status tracking — correlation_id -> status dict
        self._delivery_status: dict[str, dict[str, Any]] = {}
        # Mute rules — (alert_type, subject) -> {expires_at, muted_by, rule_id}
        self._mute_rules: dict[tuple[str, str], dict[str, Any]] = {}
        # Correlation ID counter
        self._correlation_counter: int = 0
        # Occurrence tracking for escalation
        self._occurrence_tracker: dict[tuple[str, str], list[float]] = {}

        self._lock = threading.Lock()

    # -- History store -------------------------------------------------------

    def set_history_store(self, store: Any) -> None:
        """Attach a persistent AlertHistoryStore."""
        self._history_store = store

    # -- Mute rules ----------------------------------------------------------

    def add_mute_rule(
        self,
        alert_type: str,
        subject: str,
        duration_seconds: int = 3600,
        muted_by: str = "operator",
        auto_mute_on_ack: bool = False,
    ) -> dict:
        """Add a mute rule for a specific (alert_type, subject) pair.

        Sprint 5.62: Moved from AlertManager.
        """
        key = (alert_type, subject)
        now = time.time()
        expires_at = (now + duration_seconds) if duration_seconds > 0 else 0

        rule = {
            "alert_type": alert_type,
            "subject": subject,
            "muted_by": muted_by,
            "muted_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": duration_seconds,
            "expires_at": expires_at,
            "auto_mute_on_ack": auto_mute_on_ack,
        }

        with self._lock:
            self._mute_rules[key] = rule

        # Persist to store if attached
        if self._history_store is not None:
            try:
                rule_id = self._history_store.record_mute_rule(rule)
                rule["id"] = rule_id
            except Exception as exc:
                logger.warning(
                    "alert_mute_rule_persist_failed",
                    error=str(exc),
                )

        logger.info(
            "alert_mute_rule_added",
            alert_type=alert_type,
            subject=subject,
            duration_seconds=duration_seconds,
            muted_by=muted_by,
        )

        return rule

    def remove_mute_rule(self, alert_type: str, subject: str) -> bool:
        """Remove a mute rule for a specific (alert_type, subject) pair.

        Sprint 5.62: Moved from AlertManager.
        """
        key = (alert_type, subject)
        with self._lock:
            removed = self._mute_rules.pop(key, None) is not None

        if removed and self._history_store is not None:
            try:
                self._history_store.delete_mute_rule(alert_type, subject)
            except Exception as exc:
                logger.warning(
                    "alert_mute_rule_delete_failed",
                    error=str(exc),
                )

        if removed:
            logger.info(
                "alert_mute_rule_removed",
                alert_type=alert_type,
                subject=subject,
            )

        return removed

    def get_mute_rules(self) -> list[dict]:
        """Return all active mute rules.

        Sprint 5.62: Moved from AlertManager.
        """
        now = time.time()
        with self._lock:
            # Prune expired rules before returning
            expired_keys = []
            for key, rule in self._mute_rules.items():
                expires_at = rule.get("expires_at", 0)
                if expires_at > 0 and now > expires_at:
                    expired_keys.append(key)
            for key in expired_keys:
                del self._mute_rules[key]

            return [dict(r) for r in self._mute_rules.values()]

    def check_mute_rule(self, alert_type: str, subject: str) -> str | None:
        """Check if an alert is muted. Returns 'muted' or None.

        Sprint 5.62: Extracted from AlertManager.send_alert().
        Returns 'muted' if the (alert_type, subject) pair has an active
        mute rule, None otherwise.  Lazily removes expired rules.
        """
        key = (alert_type, subject)
        now = time.time()
        with self._lock:
            mute_entry = self._mute_rules.get(key)
            if mute_entry is not None:
                expires_at = mute_entry.get("expires_at", 0)
                if expires_at > 0 and now > expires_at:
                    # Mute rule expired — remove it
                    del self._mute_rules[key]
                    logger.info(
                        "alert_mute_expired",
                        alert_type=alert_type,
                        subject=subject,
                    )
                else:
                    return "muted"
        return None

    # -- Rate limiting -------------------------------------------------------

    def check_rate_limit(self, alert_type: str, subject: str) -> bool:
        """Check if an alert should be rate-limited.

        Sprint 5.62: Extracted from AlertManager.send_alert().
        Returns True if the alert should be rate-limited (too soon after
        the last alert of the same type+subject), False otherwise.
        """
        key = (alert_type, subject)
        now = time.time()
        last_time = self._last_alert_time.get(key, 0)
        if now - last_time < self._config.min_alert_interval_seconds:
            return True
        return False

    def record_rate_limit_time(self, alert_type: str, subject: str) -> None:
        """Record the current time for rate-limiting purposes.

        Sprint 5.62: Extracted from AlertManager.send_alert().
        """
        key = (alert_type, subject)
        self._last_alert_time[key] = time.time()

    # -- Correlation ID generation -------------------------------------------

    def get_next_correlation_id(self) -> str:
        """Generate and return the next correlation ID.

        Sprint 5.62: Extracted from AlertManager.send_alert().
        """
        self._correlation_counter += 1
        return f"alert-{self._correlation_counter}-{uuid.uuid4().hex[:8]}"

    def get_next_digest_correlation_id(self) -> str:
        """Generate and return the next digest correlation ID.

        Sprint 5.62: Extracted from AlertManager._handle_digest_flush().
        """
        self._correlation_counter += 1
        return f"digest-{self._correlation_counter}-{uuid.uuid4().hex[:8]}"

    # -- Alert history -------------------------------------------------------

    def record_alert(self, alert_dict: dict) -> None:
        """Record an alert in the in-memory history.

        Sprint 5.62: Extracted from AlertManager.send_alert().
        """
        self._alert_history.append(alert_dict)
        if len(self._alert_history) > self._MAX_ALERT_HISTORY:
            self._alert_history = self._alert_history[-self._MAX_ALERT_HISTORY :]

    def get_alert_history(
        self,
        alert_type: str | None = None,
        severity: str | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return alert history with optional filtering.

        Sprint 5.62: Moved from AlertManager. When a persistent
        AlertHistoryStore is attached, queries it for full history.
        """
        if self._history_store is not None:
            try:
                return self._history_store.get_alert_history(
                    alert_type=alert_type,
                    severity=severity,
                    since=since,
                    limit=limit,
                )
            except Exception as exc:
                logger.warning(
                    "alert_history_store_query_fallback",
                    error=str(exc),
                )

        history = self._alert_history

        if alert_type:
            history = [a for a in history if a.get("alert_type") == alert_type]
        if severity:
            history = [a for a in history if a.get("severity") == severity]
        if since:
            history = [a for a in history if a.get("timestamp", "") > since]

        # Return most recent first
        return list(reversed(history[-limit:]))

    # -- Delivery failures ---------------------------------------------------

    def record_failure(self, failure: DeliveryFailure) -> None:
        """Record a delivery failure in the history.

        Sprint 5.62: Moved from AlertManager._record_failure().
        Also persists to SQLite store if attached.
        """
        self._delivery_failures.append(failure)
        if len(self._delivery_failures) > self._MAX_FAILURE_HISTORY:
            self._delivery_failures = self._delivery_failures[-self._MAX_FAILURE_HISTORY :]

        # Also persist to the SQLite store if attached
        if self._history_store is not None:
            try:
                self._history_store.record_delivery_failure(failure.to_dict())
            except Exception as exc:
                logger.warning(
                    "alert_history_store_failure_write_failed",
                    error=str(exc),
                )

    def get_delivery_failures(self, transport: str | None = None, limit: int = 20) -> list[dict]:
        """Return delivery failure history.

        Sprint 5.62: Moved from AlertManager.
        """
        if self._history_store is not None:
            try:
                return self._history_store.get_delivery_failures(
                    transport=transport,
                    limit=limit,
                )
            except Exception as exc:
                logger.warning(
                    "alert_history_store_failures_fallback",
                    error=str(exc),
                )

        failures = self._delivery_failures
        if transport:
            failures = [f for f in failures if f.transport == transport]
        return [f.to_dict() for f in reversed(failures[-limit:])]

    # -- Delivery status -----------------------------------------------------

    def set_delivery_status(self, correlation_id: str, status_dict: dict) -> None:
        """Set the delivery status for a correlation ID.

        Sprint 5.62: New method for AlertLifecycleManager.
        """
        with self._lock:
            self._delivery_status[correlation_id] = status_dict

    def get_delivery_status(self, correlation_id: str) -> dict | None:
        """Return delivery status for a given correlation ID.

        Sprint 5.62: Moved from AlertManager.
        """
        with self._lock:
            status = self._delivery_status.get(correlation_id)
            if status is not None:
                return dict(status)
            # Also check the persistent store
            if self._history_store is not None:
                try:
                    return self._history_store.get_delivery_status_by_correlation_id(correlation_id)
                except Exception:
                    pass
        return None

    # -- Escalation ----------------------------------------------------------

    def check_escalation(self, alert: Alert, now: float) -> bool:
        """Check if an alert should be escalated based on occurrence count.

        Sprint 5.62: Moved from AlertManager._check_escalation().
        """
        threshold = self._config.escalation_threshold
        if threshold <= 0:
            return False

        key = (alert.alert_type, alert.subject)
        window = self._config.escalation_window_seconds

        # Track this occurrence
        if key not in self._occurrence_tracker:
            self._occurrence_tracker[key] = []

        occurrences = self._occurrence_tracker[key]
        occurrences.append(now)

        # Prune occurrences outside the window
        cutoff = now - window
        occurrences[:] = [t for t in occurrences if t > cutoff]

        # Check if threshold is reached
        if len(occurrences) >= threshold:
            self._occurrence_tracker[key] = []
            logger.info(
                "alert_escalation_triggered",
                alert_type=alert.alert_type,
                subject=alert.subject,
                occurrence_count=len(occurrences),
                threshold=threshold,
                escalated_to=self._config.escalation_severity,
            )
            return True

        return False

    # -- State rebuild from persistent store ---------------------------------

    def rebuild_rate_limit_state(self) -> None:
        """Rebuild in-memory rate-limiting state from persistent store.

        Sprint 5.62: Moved from AlertManager._rebuild_rate_limit_state().
        """
        if self._history_store is None:
            return

        try:
            recent = self._history_store.get_recent_alerts_for_dedup(
                window_seconds=self._config.min_alert_interval_seconds,
            )
            if recent:
                self._last_alert_time.update(recent)
                logger.info(
                    "alert_rate_limit_state_rebuilt",
                    keys_rebuilt=len(recent),
                )
        except Exception as exc:
            logger.warning(
                "alert_rate_limit_rebuild_failed",
                error=str(exc),
            )

    def rebuild_mute_rules(self) -> None:
        """Rebuild in-memory mute rules from persistent store.

        Sprint 5.62: Moved from AlertManager._rebuild_mute_rules().
        """
        if self._history_store is None:
            return

        try:
            rules = self._history_store.get_active_mute_rules()
            if rules:
                now = time.time()
                for rule in rules:
                    key = (rule.get("alert_type", ""), rule.get("subject", ""))
                    if not key[0] or not key[1]:
                        continue
                    expires_at = rule.get("expires_at", 0)
                    if expires_at > 0 and now > expires_at:
                        continue
                    with self._lock:
                        self._mute_rules[key] = rule
                logger.info(
                    "alert_mute_rules_rebuilt",
                    rules_rebuilt=len(rules),
                )
        except Exception as exc:
            logger.warning(
                "alert_mute_rules_rebuild_failed",
                error=str(exc),
            )

    def rebuild_delivery_status(self) -> None:
        """Rebuild in-memory delivery status from persistent store.

        Sprint 5.62: Moved from AlertManager._rebuild_delivery_status().
        """
        if self._history_store is None:
            return

        try:
            recent = self._history_store.get_recent_delivery_statuses(limit=100)
            if recent:
                with self._lock:
                    for status_dict in recent:
                        cid = status_dict.get("correlation_id", "")
                        if cid and cid not in self._delivery_status:
                            self._delivery_status[cid] = status_dict
                logger.info(
                    "alert_delivery_status_rebuilt",
                    statuses_rebuilt=len(recent),
                )
        except Exception as exc:
            logger.warning(
                "alert_delivery_status_rebuild_failed",
                error=str(exc),
            )

    # -- Status summary ------------------------------------------------------

    def get_status_summary(self) -> dict[str, Any]:
        """Return a status dict for the StatusAggregator.

        Sprint 5.62: New method.
        """
        with self._lock:
            return {
                "active_mute_rules": len(self._mute_rules),
                "alert_history_count": len(self._alert_history),
                "delivery_failure_count": len(self._delivery_failures),
                "delivery_status_count": len(self._delivery_status),
                "correlation_counter": self._correlation_counter,
                "rate_limited_subjects": len(self._last_alert_time),
                "occurrence_tracking_subjects": len(self._occurrence_tracker),
            }


# ---------------------------------------------------------------------------
# Sprint 5.62: PruningManager — owns delivery status pruning state
# ---------------------------------------------------------------------------


class PruningManager:
    """Owns the delivery status pruning scheduler and its state.

    Sprint 5.62: Extracted from AlertManager to centralize pruning
    concerns.  Manages the background pruning thread, scheduling,
    and history recording.
    """

    def __init__(self, config: AlertConfig) -> None:
        self._config = config
        self._history_store: Any = None  # Set via set_history_store()

        self._prune_scheduler_thread: threading.Thread | None = None
        self._prune_scheduler_running: bool = False
        self._prune_scheduler_stop_event = threading.Event()
        self._last_prune_run: float = 0.0
        self._next_prune_run: float = 0.0
        self._total_scheduled_prunes: int = 0
        self._pruning_history: list[dict] = []

        self._lock = threading.Lock()

    # -- History store -------------------------------------------------------

    def set_history_store(self, store: Any) -> None:
        """Attach a persistent AlertHistoryStore."""
        self._history_store = store

    # -- Prune scheduler -----------------------------------------------------

    def start_prune_scheduler(self) -> bool:
        """Start the background delivery status pruning scheduler.

        Sprint 5.62: Moved from AlertManager.start_prune_scheduler().
        """
        interval = self._config.delivery_status_prune_interval_seconds
        if interval <= 0:
            return False

        with self._lock:
            if self._prune_scheduler_running:
                return False
            self._prune_scheduler_running = True

        mgr = self  # capture for closure

        def _scheduler_loop() -> None:
            while mgr._prune_scheduler_running:
                interval_secs = mgr._config.delivery_status_prune_interval_seconds
                if interval_secs <= 0:
                    break
                # Use Event.wait() instead of time.sleep() so stop() can
                # interrupt the sleep immediately instead of waiting the full
                # interval (which can be hours in production and causes 5s
                # hangs in tests due to join(timeout=5.0)).
                if mgr._prune_scheduler_stop_event.wait(timeout=interval_secs):
                    # Event was set — stop requested during sleep
                    break
                if not mgr._prune_scheduler_running:
                    break
                try:
                    mgr._run_scheduled_prune()
                except Exception as exc:
                    logger.warning(
                        "scheduled_prune_failed",
                        error=str(exc),
                    )

        self._prune_scheduler_thread = threading.Thread(
            target=_scheduler_loop,
            daemon=True,
            name="delivery-status-prune-scheduler",
        )
        self._prune_scheduler_thread.start()

        self._next_prune_run = time.time() + self._config.delivery_status_prune_interval_seconds

        logger.info(
            "prune_scheduler_started",
            interval_seconds=self._config.delivery_status_prune_interval_seconds,
        )
        return True

    def stop_prune_scheduler(self) -> None:
        """Stop the delivery status pruning scheduler.

        Sprint 5.62: Moved from AlertManager.stop_prune_scheduler().
        Sprint 13.2: Use Event to interrupt sleep immediately instead of
        waiting for join(timeout=5.0) which caused 5s test delays.
        """
        self._prune_scheduler_running = False
        self._prune_scheduler_stop_event.set()
        if self._prune_scheduler_thread is not None:
            self._prune_scheduler_thread.join(timeout=2.0)
            self._prune_scheduler_thread = None
        self._prune_scheduler_stop_event.clear()

        logger.info("prune_scheduler_stopped")

    def _run_scheduled_prune(self) -> int:
        """Execute a single scheduled prune cycle.

        Sprint 5.62: Moved from AlertManager._run_scheduled_prune().
        """
        if self._history_store is None:
            return 0

        deleted = self._history_store.prune_delivery_status(
            max_rows=self._config.delivery_status_max_rows,
            max_age_days=self._config.delivery_status_max_age_days,
        )

        now = time.time()
        self._last_prune_run = now
        self._next_prune_run = now + self._config.delivery_status_prune_interval_seconds
        self._total_scheduled_prunes += 1

        # Record pruning history
        run_record = {
            "timestamp": now,
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "records_deleted": deleted,
            "max_age_days": self._config.delivery_status_max_age_days,
            "max_rows": self._config.delivery_status_max_rows,
        }
        with self._lock:
            self._pruning_history.append(run_record)
            max_history = self._config.pruning_history_size
            if len(self._pruning_history) > max_history:
                self._pruning_history = self._pruning_history[-max_history:]

        logger.info(
            "scheduled_prune_completed",
            deleted=deleted,
            total_scheduled_prunes=self._total_scheduled_prunes,
        )

        return deleted

    def get_prune_scheduler_status(self) -> dict:
        """Return the current state of the pruning scheduler.

        Sprint 5.62: Moved from AlertManager.get_prune_scheduler_status().
        """
        total_rows_pruned = sum(r.get("records_deleted", 0) for r in self._pruning_history)

        return {
            "running": self._prune_scheduler_running,
            "interval_seconds": self._config.delivery_status_prune_interval_seconds,
            "last_prune_run": self._last_prune_run,
            "next_prune_run": self._next_prune_run,
            "total_scheduled_prunes": self._total_scheduled_prunes,
            "total_rows_pruned": total_rows_pruned,
            "max_age_days": self._config.delivery_status_max_age_days,
            "max_rows": self._config.delivery_status_max_rows,
            "history": list(self._pruning_history),
        }

    def get_pruning_history(self, limit: int | None = None) -> list[dict]:
        """Return pruning run history records.

        Sprint 5.62: Moved from AlertManager.get_pruning_history().
        """
        with self._lock:
            history = list(self._pruning_history)
        if limit:
            history = history[-limit:]
        return history

    # -- Status summary ------------------------------------------------------

    def get_status_summary(self) -> dict[str, Any]:
        """Return a status dict for the StatusAggregator.

        Sprint 5.62: New method. Includes prune scheduler status
        and pruning history.
        """
        return self.get_prune_scheduler_status()


class StatusAggregator:
    """Builds the ``get_status()`` dict by querying each sub-manager.

    Sprint 5.60: Extracted from ``AlertManager.get_status()`` which had
    grown to ~170 lines of inline dict construction.  Each sub-manager
    exposes a ``get_status_summary()`` that returns its own section,
    keeping the aggregation logic centralized and testable.

    Sprint 5.63: Added ``build_async()`` for concurrent sub-manager
    queries using ``asyncio.gather()``, reducing total lock acquisition
    time when ``get_status()`` is called frequently.  Also added a
    lightweight result cache with configurable TTL (default 200ms)
    to avoid redundant sub-manager queries during rapid polling.
    """

    # Cache TTL for status results — prevents redundant sub-manager queries
    # during rapid polling (e.g., dashboard auto-refresh every 500ms)
    _cache_ttl_seconds: float = 0.2  # 200ms

    def __init__(self, alert_manager: AlertManager) -> None:
        self._mgr = alert_manager
        # Sprint 5.63: Result cache
        self._cached_result: dict | None = None
        self._cache_timestamp: float = 0.0

    def invalidate_cache(self) -> None:
        """Invalidate the cached status result.

        Called when state changes (e.g. WS session register/unregister)
        so the next get_status() call reflects the current state.
        """
        self._cached_result = None

    def _collect_sub_manager_summaries(self) -> dict[str, dict[str, Any]]:
        """Collect all sub-manager status summaries.

        Sprint 5.63: Extracted into a helper so both sync and async
        paths can share the same summary-collection logic.  Returns a
        dict mapping sub-manager name to its summary dict.
        """
        mgr = self._mgr
        return {
            "delivery": mgr._delivery_mgr.get_status_summary(),
            "realtime": mgr._realtime_bus.get_status_summary(),
            "prediction": mgr._prediction_mgr.get_status_summary(),
            "lifecycle": mgr._lifecycle_mgr.get_status_summary(),
            "pruning": mgr._pruning_mgr.get_status_summary(),
            "digest": mgr._digest_mgr.get_status_summary(),
            "ab_experiment": mgr._ab_experiment_mgr.get_status_summary(),
            "circuit_breaker": mgr._throttle_mgr.get_circuit_breaker_status(),
            "cb_auto_tune": mgr._throttle_mgr.get_cb_auto_tune_status(),
        }

    async def _collect_sub_manager_summaries_async(self) -> dict[str, dict[str, Any]]:
        """Collect sub-manager summaries concurrently using asyncio.gather().

        Sprint 5.63: Runs all ``get_status_summary()`` calls concurrently
        to reduce total lock acquisition time.  Falls back to sequential
        collection if any individual call fails.
        """
        mgr = self._mgr

        async def _safe_call(name: str, fn) -> tuple[str, dict[str, Any]]:
            """Wrap a synchronous get_status_summary call for async gather."""
            # These are synchronous methods that acquire locks — run them
            # in a thread pool to avoid blocking the event loop.
            loop = asyncio.get_running_loop()
            try:
                result = await loop.run_in_executor(None, fn)
                return (name, result)
            except Exception:
                return (name, {})

        tasks = [
            _safe_call("delivery", mgr._delivery_mgr.get_status_summary),
            _safe_call("realtime", mgr._realtime_bus.get_status_summary),
            _safe_call("prediction", mgr._prediction_mgr.get_status_summary),
            _safe_call("lifecycle", mgr._lifecycle_mgr.get_status_summary),
            _safe_call("pruning", mgr._pruning_mgr.get_status_summary),
            _safe_call("digest", mgr._digest_mgr.get_status_summary),
            _safe_call("ab_experiment", mgr._ab_experiment_mgr.get_status_summary),
            _safe_call("circuit_breaker", mgr._throttle_mgr.get_circuit_breaker_status),
            _safe_call("cb_auto_tune", mgr._throttle_mgr.get_cb_auto_tune_status),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        summaries = {}
        for result in results:
            if isinstance(result, Exception):
                continue
            name, summary = result
            summaries[name] = summary
        return summaries

    def build(self) -> dict:
        """Return the full alerting status dict.

        Sprint 5.63: Uses a lightweight result cache (200ms TTL) to
        avoid redundant sub-manager queries during rapid polling.
        """
        # Sprint 5.63: Return cached result if still fresh
        now = time.time()
        if self._cached_result is not None:
            cache_age = now - self._cache_timestamp
            if cache_age < self._cache_ttl_seconds:
                return self._cached_result

        result = self._build_internal()
        self._cached_result = result
        self._cache_timestamp = now
        return result

    async def build_async(self) -> dict:
        """Return the full alerting status dict with concurrent sub-manager queries.

        Sprint 5.63: Uses ``asyncio.gather()`` with ``run_in_executor()``
        to collect sub-manager summaries concurrently, reducing total
        lock acquisition time.  Falls back to sequential ``build()``
        if not in an async context.
        """
        # Check cache first
        now = time.time()
        if self._cached_result is not None:
            cache_age = now - self._cache_timestamp
            if cache_age < self._cache_ttl_seconds:
                return self._cached_result

        try:
            summaries = await self._collect_sub_manager_summaries_async()
            result = self._assemble_status(summaries)
            self._cached_result = result
            self._cache_timestamp = now
            return result
        except Exception:
            # Fallback to synchronous build
            return self.build()

    def _build_internal(self) -> dict:
        """Build status dict using synchronous sub-manager queries."""
        summaries = self._collect_sub_manager_summaries()
        return self._assemble_status(summaries)

    def _assemble_status(self, summaries: dict[str, dict[str, Any]]) -> dict:
        """Assemble the full status dict from pre-collected summaries.

        Sprint 5.63: Extracted from ``build()`` to allow both sync
        and async paths to share the same assembly logic.
        """
        mgr = self._mgr
        config = mgr._config

        delivery_summary = summaries.get("delivery", {})
        realtime_summary = summaries.get("realtime", {})
        prediction_summary = summaries.get("prediction", {})
        lifecycle_summary = summaries.get("lifecycle", {})
        pruning_summary = summaries.get("pruning", {})
        digest_summary = summaries.get("digest", {})
        ab_summary = summaries.get("ab_experiment", {})
        cb_summary = summaries.get("circuit_breaker", {})
        cb_auto_tune_summary = summaries.get("cb_auto_tune", {})

        status: dict[str, Any] = {
            "enabled": config.enabled,
            "webhook_configured": bool(config.webhook_url),
            "webhook_url_valid": mgr._webhook_url_valid,
            "email_configured": bool(config.email_to),
            "smtp_auth_configured": bool(config.smtp_username),
            # Delivery counters (from DeliveryManager)
            "total_alerts_sent": delivery_summary.get("total_alerts_sent", 0),
            "total_rate_limited": delivery_summary.get("total_rate_limited", 0),
            "total_send_failures": delivery_summary.get("total_send_failures", 0),
            "total_webhook_retries": delivery_summary.get("total_webhook_retries", 0),
            # Sprint 5.62: Recent alerts/failures from AlertLifecycleManager
            "recent_alerts": mgr._lifecycle_mgr._alert_history[-5:],
            "recent_failures": [f.to_dict() for f in mgr._lifecycle_mgr._delivery_failures[-5:]],
            "alert_types_enabled": {
                "quality_degradation": config.alert_on_quality_degradation,
                "pool_adjustment": config.alert_on_pool_adjustment,
                "batch_reduction": config.alert_on_batch_reduction,
            },
            "webhook_retry_config": {
                "max_retries": config.webhook_max_retries,
                "base_delay_seconds": config.webhook_retry_base_delay_seconds,
            },
            "history_store_attached": mgr._history_store is not None,
            "routes": config.routes if config.routes else {},
            # Escalation
            "escalation": {
                "threshold": config.escalation_threshold,
                "window_seconds": config.escalation_window_seconds,
                "severity": config.escalation_severity,
                "additional_transports": config.escalation_additional_transports,
            },
            # Mute rules & digest
            "active_mute_rules": lifecycle_summary.get("active_mute_rules", 0),
            "digest": digest_summary,
            # Real-time subscriber counts (from RealtimeEventBus)
            "sse_subscribers": realtime_summary.get("sse_subscribers", 0),
            "ws_subscribers": realtime_summary.get("ws_subscribers", 0),
            # Delivery by transport (from DeliveryManager)
            "delivery_by_transport": delivery_summary.get("delivery_by_transport", {}),
            "alert_groups": len(mgr._alert_groups),
            # Config fields
            "delivery_status_max_age_days": config.delivery_status_max_age_days,
            "ws_auth_configured": bool(config.ws_auth_token),
            "ws_rate_limit_per_minute": config.ws_rate_limit_per_minute,
            "causal_grouping": {
                "enabled": config.causal_grouping_enabled,
                "window_seconds": config.causal_grouping_window_seconds,
            },
            # Sessions & groups
            "ws_sessions": len(mgr._ws_sessions),
            "alert_group_ttl": {
                "ttl_hours": config.alert_group_ttl_hours,
                "total_groups": len(mgr._alert_groups),
                "groups_cleaned": mgr._total_groups_cleaned,
            },
            "delivery_status_max_rows": config.delivery_status_max_rows,
            # Heartbeat & pruning
            "ws_heartbeat": {
                "interval_seconds": config.ws_heartbeat_interval_seconds,
                "missed_limit": config.ws_heartbeat_missed_limit,
                "dead_sessions_cleaned": mgr._total_dead_sessions_cleaned,
            },
            "prune_scheduler": pruning_summary,
            # WS batching, compression & connection pool (from RealtimeEventBus)
            "ws_batching": realtime_summary.get("ws_batching", {}),
            "ws_connection_pool": realtime_summary.get("ws_connection_pool", {}),
            "auto_merge": {
                "window_seconds": config.auto_merge_window_seconds,
                "similarity_threshold": config.auto_merge_similarity_threshold,
                "suggestions_available": len(mgr._auto_merge_suggestions),
                "total_suggested": mgr._total_auto_merges_suggested,
                "total_applied": mgr._total_auto_merges_applied,
            },
            "causal_prediction": prediction_summary.get("causal_prediction", {}),
            "prediction_accuracy": prediction_summary.get("prediction_accuracy", {}),
            # Sprint 5.63: Background prediction tracking status
            "prediction_bg_tracking": mgr._prediction_mgr.get_bg_tracking_status(),
            "auto_merge_policy": {
                "mode": config.auto_merge_mode,
                "cooldown_seconds": config.auto_merge_cooldown_seconds,
                "type_thresholds": config.auto_merge_type_thresholds,
                "last_auto_merge_time": mgr._last_auto_merge_time,
            },
            "notification_channels": {
                "slack_configured": bool(config.slack_webhook_url),
                "pagerduty_configured": bool(config.pagerduty_integration_key),
                "notification_routes": config.notification_routes,
            },
            # Learned prediction
            "learned_prediction": prediction_summary.get("learned_prediction", {}),
            # Circuit breaker (cached)
            "circuit_breaker": cb_summary,
            # Delivery receipts (from DeliveryManager + config)
            "delivery_receipts": {
                "enabled": config.delivery_receipts_enabled,
                **delivery_summary.get("delivery_receipts", {}),
            },
            # WS compression (from RealtimeEventBus)
            "ws_compression": realtime_summary.get("ws_compression", {}),
            # Offline cache
            "offline_cache_enabled": config.offline_cache_enabled,
            # Transition persistence
            "transition_persistence": prediction_summary.get("transition_persistence", {}),
            # Circuit breaker auto-tune
            "circuit_breaker_auto_tune": cb_auto_tune_summary,
            # Delivery receipt polling
            "delivery_receipt_polling": mgr.get_delivery_polling_status(),
            # Native WS deflate (from RealtimeEventBus)
            "ws_native_deflate": realtime_summary.get("ws_native_deflate", {}),
            # A/B experiment metrics
            **ab_summary,
            # Sprint 5.63: StatusAggregator cache metadata
            "_cache_ttl_ms": round(self._cache_ttl_seconds * 1000),
        }
        return status


# ---------------------------------------------------------------------------
# Alert manager
# ---------------------------------------------------------------------------


class AlertManager:
    """Manages operator alerting for auto-tuning events.

    Sprint 5.26 improvements:
    - Webhook delivery uses retry with exponential backoff
    - Webhook URLs are validated on first use (or at startup via validate_config)
    - SMTP authentication support (username/password)
    - Delivery failure history with full context for each failure

    Sprint 5.30 improvements:
    - Async alert dispatch: send_alert() returns a correlation ID immediately
      and performs actual transport delivery in a background thread
    - Alert acknowledgment/dismissal with persistent state
    - Configurable severity escalation based on occurrence count
    - Rate-limiting state rebuilt from persistent store on restart

    Provides a single ``send_alert()`` method that:
    1. Checks if the alert type is enabled
    2. Rate-limits identical alerts (per type + subject)
    3. Checks for severity escalation
    4. Records the alert (with correlation ID) to in-memory + persistent store
    5. Dispatches to configured transports in a background thread
    6. Returns a correlation ID immediately

    Usage::

        alert_mgr = AlertManager(AlertConfig(enabled=True, webhook_url="..."))
        correlation_id = alert_mgr.send_alert(Alert(
            alert_type="quality_degradation",
            severity="warning",
            subject="vigil_faithfulness",
            message="Faithfulness score dropped from 0.85 to 0.60",
            data={"previous": 0.85, "current": 0.60},
        ))
    """

    def __init__(self, config: AlertConfig | None = None) -> None:
        self._config = config or AlertConfig()

        # Sprint 5.60: Sub-managers own their respective state domains
        self._realtime_bus = RealtimeEventBus(self._config)
        self._delivery_mgr = DeliveryManager()
        # Sprint 5.62: AlertLifecycleManager owns lifecycle state
        self._lifecycle_mgr = AlertLifecycleManager(self._config)
        # Sprint 5.62: PruningManager owns pruning scheduler state
        self._pruning_mgr = PruningManager(self._config)
        self._status_aggregator = StatusAggregator(self)

        # Sprint 5.26: Webhook URL validation status (lazy, on first use)
        self._webhook_url_validated: bool = False
        self._webhook_url_valid: bool | None = None
        self._webhook_url_validation_reason: str = ""
        # Sprint 5.29: Persistent alert history store (optional)
        self._history_store: Any = None
        # Sprint 5.32: Alert correlation groups — group_key -> list of correlation_ids
        self._alert_groups: dict[str, list[str]] = {}
        # Sprint 5.34: WebSocket session tracking — session_id -> {websocket, connected_at, remote_addr}
        self._ws_sessions: dict[str, dict[str, Any]] = {}
        # Sprint 5.34: Alert group metadata — group_key -> last_activity_at (epoch float)
        self._alert_groups_metadata: dict[str, float] = {}
        # Sprint 5.34: Group TTL cleanup counter
        self._total_groups_cleaned: int = 0
        # Sprint 5.35: Dead session cleanup counter
        self._total_dead_sessions_cleaned: int = 0
        # Sprint 5.36: Auto-merge suggestions — list of pending suggestions
        self._auto_merge_suggestions: list[dict] = []
        self._total_auto_merges_suggested: int = 0
        self._total_auto_merges_applied: int = 0
        self._last_auto_merge_time: float = 0.0  # Cooldown tracking
        # Sprint 5.61: ThrottleManager owns throttle & circuit-breaker state
        self._throttle_mgr = ThrottleManager(self._config, self._history_store)
        # Sprint 5.61: ABExperimentManager owns all A/B experiment state
        # (Must be created before PredictionManager so calibration callback can be wired)
        self._ab_experiment_mgr = ABExperimentManager(self._config, self._history_store, self._realtime_bus)
        # Wire alert sender so ABExperimentManager can send alerts
        self._ab_experiment_mgr.set_alert_sender(self.send_alert)
        # Sprint 5.61: PredictionManager owns prediction, causal chain, and transition learning state
        self._prediction_mgr = PredictionManager(self._config, self._history_store, self._realtime_bus)
        # Wire calibration callback so PredictionManager can use A/B calibration
        self._prediction_mgr.set_calibration_callback(self._ab_experiment_mgr.get_calibrated_confidence)
        # Sprint 5.61: DigestManager owns digest buffering and flushing state
        self._digest_mgr = DigestManager(self._config)
        # Wire flush callback so DigestManager can delegate actual dispatch
        self._digest_mgr.set_flush_callback(self._handle_digest_flush)
        # (Sprint 5.39: Circuit breaker auto-tuning state moved to ThrottleManager)
        # Sprint 5.39: Delivery receipt polling
        self._receipt_polling_thread: threading.Thread | None = None
        self._receipt_polling_running: bool = False
        self._email_delivery_statuses: dict[str, dict[str, Any]] = {}
        self._total_receipt_polls: int = 0
        self._total_email_status_updates: int = 0
        # Lock for thread-safe mutation of shared state
        self._lock = threading.Lock()

        # Sprint 5.63: Start background prediction tracking
        self._prediction_mgr.start_bg_tracking()

    # -- Sub-manager public accessors (Sprint 5.60 / 5.62) -----------------

    @property
    def realtime_bus(self) -> RealtimeEventBus:
        """The real-time event distribution sub-manager."""
        return self._realtime_bus

    @property
    def delivery_mgr(self) -> DeliveryManager:
        """The delivery tracking sub-manager."""
        return self._delivery_mgr

    @property
    def lifecycle_mgr(self) -> AlertLifecycleManager:
        """The alert lifecycle sub-manager (Sprint 5.62)."""
        return self._lifecycle_mgr

    @property
    def pruning_mgr(self) -> PruningManager:
        """The pruning scheduler sub-manager (Sprint 5.62)."""
        return self._pruning_mgr

    @property
    def throttle_mgr(self) -> ThrottleManager:
        """The throttle and circuit-breaker sub-manager."""
        return self._throttle_mgr

    @property
    def prediction_mgr(self) -> PredictionManager:
        """The prediction and transition learning sub-manager."""
        return self._prediction_mgr

    @property
    def digest_mgr(self) -> DigestManager:
        """The digest buffering and flushing sub-manager."""
        return self._digest_mgr

    @property
    def config(self) -> AlertConfig:
        return self._config

    @config.setter
    def config(self, value: AlertConfig) -> None:
        self._config = value
        # Reset validation state when config changes (URL may have changed)
        self._webhook_url_validated = False
        self._webhook_url_valid = None
        self._webhook_url_validation_reason = ""

    def validate_config(self) -> list[str]:
        """Validate the alerting configuration and return any warnings.

        This can be called at startup to catch configuration problems
        early.  Returns a list of warning strings (empty if all valid).
        Does NOT raise — warnings are informational for the operator.
        """
        warnings: list[str] = []

        if not self._config.enabled:
            return warnings

        # Validate webhook URL if configured
        if self._config.webhook_url:
            is_valid, reason = validate_webhook_url(self._config.webhook_url)
            if not is_valid:
                warnings.append(f"Webhook URL invalid: {reason}")
                self._webhook_url_valid = False
                self._webhook_url_validation_reason = reason
            else:
                self._webhook_url_valid = True
                self._webhook_url_validation_reason = ""
            self._webhook_url_validated = True
        else:
            self._webhook_url_validated = True

        # Validate email configuration
        if self._config.email_to:
            if not self._config.smtp_host:
                warnings.append("Email alerts configured (email_to) but smtp_host is empty. Email delivery will fail.")

        # Check that at least one transport is configured
        if not self._config.webhook_url and not self._config.email_to:
            warnings.append(
                "Alerting is enabled but no transports are configured. "
                "Set webhook_url or email_to to receive notifications."
            )

        # Sprint 5.30: Validate escalation configuration
        if self._config.escalation_threshold > 0:
            valid_severities = {"info", "warning", "critical"}
            if self._config.escalation_severity not in valid_severities:
                warnings.append(
                    f"Escalation severity '{self._config.escalation_severity}' is not "
                    f"one of {valid_severities}. Escalation may not work as expected."
                )

        return warnings

    def send_alert(self, alert: Alert) -> str:
        """Send an alert through configured transports.

        Sprint 5.30: Refactored to be fully non-blocking. The actual
        webhook and email delivery happens in a background thread.
        Returns a correlation ID immediately that can be used to track
        the alert's delivery status.

        Returns a correlation ID string. If alerting is disabled or the
        alert type is disabled, returns an empty string. If rate-limited,
        returns the string "rate_limited".
        """
        # Check master switch
        if not self._config.enabled:
            logger.debug(
                "alert_skipped_disabled",
                alert_type=alert.alert_type,
                subject=alert.subject,
            )
            return ""  # Not an error — just not configured

        # Check if this alert type is enabled
        if not self._is_alert_type_enabled(alert.alert_type):
            logger.debug(
                "alert_skipped_type_disabled",
                alert_type=alert.alert_type,
            )
            return ""

        # Sprint 5.62: Mute rule check via AlertLifecycleManager
        now = time.time()
        mute_result = self._lifecycle_mgr.check_mute_rule(alert.alert_type, alert.subject)
        if mute_result == "muted":
            self._delivery_mgr.increment_rate_limited()
            logger.debug(
                "alert_muted",
                alert_type=alert.alert_type,
                subject=alert.subject,
            )
            return "muted"

        # Sprint 5.62: Rate-limit check via AlertLifecycleManager
        if self._lifecycle_mgr.check_rate_limit(alert.alert_type, alert.subject):
            self._delivery_mgr.increment_rate_limited()
            logger.debug(
                "alert_rate_limited",
                alert_type=alert.alert_type,
                subject=alert.subject,
            )
            return "rate_limited"

        # Record the alert time
        self._lifecycle_mgr.record_rate_limit_time(alert.alert_type, alert.subject)

        # Sprint 5.38: Alert throttling & circuit breaker
        self._throttle_mgr.record_throttle_window(now)
        if self._throttle_mgr.check_circuit_breaker(now):
            # Circuit breaker active — force digest-only delivery
            # Non-critical alerts are buffered; critical alerts still dispatch
            if alert.severity != "critical":
                self._throttle_mgr.increment_throttled_alerts()
                logger.info(
                    "alert_throttled_circuit_breaker",
                    alert_type=alert.alert_type,
                    severity=alert.severity,
                    subject=alert.subject,
                )
                # Buffer for digest even if digest not normally enabled
                correlation_id = self._lifecycle_mgr.get_next_correlation_id()
                alert_dict = alert.to_dict()
                alert_dict["correlation_id"] = correlation_id
                self._lifecycle_mgr.record_alert(alert_dict)
                self._delivery_mgr.increment_sent()
                with self._lock:
                    self._digest_mgr.buffer_alert(alert_dict)
                    # Notify subscribers about throttled alert
                    self._realtime_bus.notify_realtime_subscribers(
                        {
                            "event": "alert_throttled",
                            "correlation_id": correlation_id,
                            "alert_type": alert.alert_type,
                            "severity": alert.severity,
                            "subject": alert.subject,
                            "circuit_breaker_active": True,
                        }
                    )
                return f"throttled:{correlation_id}"

        # Sprint 5.62: Check for severity escalation via AlertLifecycleManager
        escalated = self._lifecycle_mgr.check_escalation(alert, now)
        if escalated:
            original_severity = alert.severity
            alert = Alert(
                alert_type=alert.alert_type,
                severity=self._config.escalation_severity,
                subject=alert.subject,
                message=f"[ESCALATED from {original_severity}] {alert.message}",
                data={**alert.data, "escalated_from": original_severity, "escalated": True},
                timestamp=alert.timestamp,
            )

        # Sprint 5.62: Generate correlation ID via AlertLifecycleManager
        correlation_id = self._lifecycle_mgr.get_next_correlation_id()

        # Log the alert regardless of transport success
        logger.info(
            "alert_dispatched",
            alert_type=alert.alert_type,
            severity=alert.severity,
            subject=alert.subject,
            message=alert.message[:200],
            correlation_id=correlation_id,
        )

        # Sprint 5.62: Record in history via AlertLifecycleManager
        alert_dict = alert.to_dict()
        alert_dict["correlation_id"] = correlation_id
        self._lifecycle_mgr.record_alert(alert_dict)

        self._delivery_mgr.increment_sent()

        # Sprint 5.38: Record alert for learned prediction model
        # Sprint 5.63: Enqueue for background processing to reduce hot-path latency
        self._prediction_mgr.enqueue_transition_learning(alert, now)
        self._prediction_mgr.enqueue_accuracy_check(alert)

        # Persist to SQLite store if attached
        if self._history_store is not None:
            try:
                self._history_store.record_alert(alert_dict)
            except Exception as exc:
                logger.warning(
                    "alert_history_store_write_failed",
                    error=str(exc),
                )

        # Sprint 5.30: Determine transports (include escalation extras if escalated)
        transports = self._get_transports_for_alert(alert.alert_type)
        if escalated and self._config.escalation_additional_transports:
            for t in self._config.escalation_additional_transports:
                if t not in transports:
                    if (t == "webhook" and self._config.webhook_url) or (t == "email" and self._config.email_to):
                        transports.append(t)

        # Sprint 5.32: Alert correlation grouping
        self._add_alert_to_group(alert, correlation_id)

        # Sprint 5.31: Check if this alert should go to digest buffer instead
        # Sprint 5.32: Use per-type digest overrides if configured
        # Sprint 5.61: Delegates buffering and flush decision to DigestManager
        if self._config.digest_enabled and alert.severity == "info" and not escalated:
            with self._lock:
                self._digest_mgr.buffer_alert(alert_dict)
                # Check if we should flush the digest
                if self._digest_mgr.should_flush(alert.alert_type):
                    buffered = self._digest_mgr.flush_digest()
                    self._handle_digest_flush(buffered)
                # Record delivery status as "buffered" for digest
                status_dict = {
                    "status": "buffered_for_digest",
                    "correlation_id": correlation_id,
                    "alert_type": alert.alert_type,
                    "severity": alert.severity,
                    "subject": alert.subject,
                    "transports": transports,
                    "transport_results": {},
                    "dispatched_at": datetime.now(timezone.utc).isoformat(),
                }
                # Sprint 5.62: Store delivery status via AlertLifecycleManager
                self._lifecycle_mgr.set_delivery_status(correlation_id, status_dict)
                # Sprint 5.32: Persist delivery status to SQLite
                self._persist_delivery_status(status_dict)
            # Notify SSE/WebSocket subscribers about the buffered alert
            self._realtime_bus.notify_realtime_subscribers(
                {
                    "event": "alert_buffered",
                    "correlation_id": correlation_id,
                    "alert_type": alert.alert_type,
                    "severity": alert.severity,
                    "subject": alert.subject,
                }
            )
            return correlation_id

        # Sprint 5.30: Dispatch to transports in a background thread
        # This makes send_alert() non-blocking
        dispatch_thread = threading.Thread(
            target=self._dispatch_to_transports,
            args=(alert, transports, correlation_id),
            daemon=True,
            name=f"alert-dispatch-{correlation_id}",
        )
        dispatch_thread.start()

        return correlation_id

    def _dispatch_to_transports(
        self,
        alert: Alert,
        transports: list[str],
        correlation_id: str,
    ) -> None:
        """Perform actual transport dispatch in a background thread.

        Sprint 5.30: This method runs in a daemon thread so that
        send_alert() can return immediately.  Delivery attempts,
        successes, and failures are still reliably recorded in the
        AlertHistoryStore even when dispatch happens asynchronously.

        Sprint 5.31: Tracks per-transport delivery outcomes in
        _delivery_status so operators can query the status of any
        alert using its correlation ID.
        """
        # Initialize delivery status tracking for this correlation ID
        transport_results: dict[str, dict[str, Any]] = {}
        status_dict = {
            "status": "dispatching",
            "correlation_id": correlation_id,
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "subject": alert.subject,
            "transports": transports,
            "transport_results": transport_results,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        }
        # Sprint 5.62: Store delivery status via AlertLifecycleManager
        self._lifecycle_mgr.set_delivery_status(correlation_id, status_dict)
        # Sprint 5.32: Persist initial delivery status to SQLite
        self._persist_delivery_status(status_dict)

        all_succeeded = True

        if "webhook" in transports and self._config.webhook_url:
            try:
                self._send_webhook_with_retry(alert)
                transport_results["webhook"] = {
                    "status": "delivered",
                    "retries": 0,
                }
            except Exception as exc:
                all_succeeded = False
                self._delivery_mgr.increment_send_failure()
                retry_count = self._config.webhook_max_retries
                failure = DeliveryFailure(
                    transport="webhook",
                    alert_type=alert.alert_type,
                    subject=alert.subject,
                    error_message=str(exc),
                    retry_attempt=retry_count,
                    final=True,
                )
                self._record_failure(failure)
                transport_results["webhook"] = {
                    "status": "failed",
                    "error": str(exc),
                    "retries": retry_count,
                }
                logger.warning(
                    "alert_webhook_failed_final",
                    url=self._config.webhook_url[:50],
                    error=str(exc),
                    retries_attempted=retry_count,
                    correlation_id=correlation_id,
                )

        if "email" in transports and self._config.email_to:
            try:
                self._send_email(alert)
                transport_results["email"] = {
                    "status": "delivered",
                    "retries": 0,
                }
            except Exception as exc:
                all_succeeded = False
                self._delivery_mgr.increment_send_failure()
                failure = DeliveryFailure(
                    transport="email",
                    alert_type=alert.alert_type,
                    subject=alert.subject,
                    error_message=str(exc),
                    retry_attempt=0,
                    final=True,
                )
                self._record_failure(failure)
                transport_results["email"] = {
                    "status": "failed",
                    "error": str(exc),
                    "retries": 0,
                }
                logger.warning(
                    "alert_email_failed",
                    to=self._config.email_to[:50],
                    error=str(exc),
                    correlation_id=correlation_id,
                )

        # Sprint 5.37: Slack notification channel
        if "slack" in transports and self._config.slack_webhook_url:
            try:
                slack_receipt = self._send_slack_notification(alert)
                transport_results["slack"] = {
                    "status": "delivered",
                    "retries": 0,
                }
                # Sprint 5.38: Capture Slack delivery receipt (message timestamp)
                if slack_receipt and self._config.delivery_receipts_enabled:
                    transport_results["slack"]["receipt"] = slack_receipt
            except Exception as exc:
                all_succeeded = False
                self._delivery_mgr.increment_send_failure()
                failure = DeliveryFailure(
                    transport="slack",
                    alert_type=alert.alert_type,
                    subject=alert.subject,
                    error_message=str(exc),
                    retry_attempt=0,
                    final=True,
                )
                self._record_failure(failure)
                transport_results["slack"] = {
                    "status": "failed",
                    "error": str(exc),
                    "retries": 0,
                }
                logger.warning(
                    "alert_slack_failed",
                    url=self._config.slack_webhook_url[:50],
                    error=str(exc),
                    correlation_id=correlation_id,
                )

        # Sprint 5.37: PagerDuty notification channel
        if "pagerduty" in transports and self._config.pagerduty_integration_key:
            try:
                pd_receipt = self._send_pagerduty_notification(alert)
                transport_results["pagerduty"] = {
                    "status": "delivered",
                    "retries": 0,
                }
                # Sprint 5.38: Capture PagerDuty delivery receipt (dedup_key)
                if pd_receipt and self._config.delivery_receipts_enabled:
                    transport_results["pagerduty"]["receipt"] = pd_receipt
            except Exception as exc:
                all_succeeded = False
                self._delivery_mgr.increment_send_failure()
                failure = DeliveryFailure(
                    transport="pagerduty",
                    alert_type=alert.alert_type,
                    subject=alert.subject,
                    error_message=str(exc),
                    retry_attempt=0,
                    final=True,
                )
                self._record_failure(failure)
                transport_results["pagerduty"] = {
                    "status": "failed",
                    "error": str(exc),
                    "retries": 0,
                }
                logger.warning(
                    "alert_pagerduty_failed",
                    error=str(exc),
                    correlation_id=correlation_id,
                )

        # Update final delivery status
        final_status = "delivered" if all_succeeded else "partial" if transport_results else "failed"
        # Sprint 5.62: Update delivery status via AlertLifecycleManager
        existing_status = self._lifecycle_mgr.get_delivery_status(correlation_id)
        if existing_status is not None:
            existing_status["status"] = final_status
            existing_status["completed_at"] = datetime.now(timezone.utc).isoformat()
            self._lifecycle_mgr.set_delivery_status(correlation_id, existing_status)
            # Sprint 5.32: Update persistent delivery status
            self._update_persistent_delivery_status(correlation_id, final_status)

        # Sprint 5.32: Track delivery success/failure by transport
        for transport_name, result in transport_results.items():
            self._delivery_mgr.record_transport_result(transport_name, result.get("status", "unknown"))

        # Sprint 5.38: Track multi-channel delivery receipts
        if self._config.delivery_receipts_enabled:
            self._record_delivery_receipts(correlation_id, transport_results)

        # Sprint 5.31 → 5.32: Notify SSE/WebSocket subscribers about the delivery result
        self._realtime_bus.notify_realtime_subscribers(
            {
                "event": "alert_delivered" if all_succeeded else "alert_delivery_failed",
                "correlation_id": correlation_id,
                "alert_type": alert.alert_type,
                "severity": alert.severity,
                "subject": alert.subject,
                "transport_results": transport_results,
            }
        )

    # Sprint 5.30: Alert acknowledgment / dismissal

    def acknowledge_alert(self, alert_id: int, acknowledged_by: str = "operator") -> bool:
        """Acknowledge an alert by its database ID.

        Sprint 5.30: Delegates to the AlertHistoryStore if attached.
        Returns True if successful, False if the store is not attached
        or the alert was not found.
        """
        if self._history_store is not None:
            try:
                return self._history_store.acknowledge_alert(alert_id, acknowledged_by)
            except Exception as exc:
                logger.warning(
                    "alert_acknowledge_failed",
                    alert_id=alert_id,
                    error=str(exc),
                )
                return False
        logger.warning("alert_acknowledge_no_store", alert_id=alert_id)
        return False

    def dismiss_alert(self, alert_id: int, dismissed_by: str = "operator") -> bool:
        """Dismiss an alert by its database ID.

        Sprint 5.30: Delegates to the AlertHistoryStore if attached.
        Returns True if successful, False if the store is not attached
        or the alert was not found.
        """
        if self._history_store is not None:
            try:
                return self._history_store.dismiss_alert(alert_id, dismissed_by)
            except Exception as exc:
                logger.warning(
                    "alert_dismiss_failed",
                    alert_id=alert_id,
                    error=str(exc),
                )
                return False
        logger.warning("alert_dismiss_no_store", alert_id=alert_id)
        return False

    def get_status(self) -> dict:
        """Return alerting status for the health endpoint.

        Sprint 5.60: Delegates to StatusAggregator which queries each
        sub-manager's ``get_status_summary()`` instead of reaching
        directly into private attributes.
        """
        return self._status_aggregator.build()

    def get_alert_history(
        self,
        alert_type: str | None = None,
        severity: str | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return alert history with optional filtering.

        Sprint 5.62: Delegates to AlertLifecycleManager.
        """
        return self._lifecycle_mgr.get_alert_history(
            alert_type=alert_type,
            severity=severity,
            since=since,
            limit=limit,
        )

    def get_delivery_failures(self, transport: str | None = None, limit: int = 20) -> list[dict]:
        """Return delivery failure history, optionally filtered by transport.

        Sprint 5.62: Delegates to AlertLifecycleManager.
        """
        return self._lifecycle_mgr.get_delivery_failures(transport=transport, limit=limit)

    # -----------------------------------------------------------------------
    # Sprint 5.31: Delivery status tracking
    # -----------------------------------------------------------------------

    def get_delivery_status(self, correlation_id: str) -> dict | None:
        """Return delivery status for a given correlation ID.

        Sprint 5.62: Delegates to AlertLifecycleManager.
        """
        return self._lifecycle_mgr.get_delivery_status(correlation_id)

    # -----------------------------------------------------------------------
    # Sprint 5.31: Alert silencing / muting rules
    # -----------------------------------------------------------------------

    def add_mute_rule(
        self,
        alert_type: str,
        subject: str,
        duration_seconds: int = 3600,
        muted_by: str = "operator",
        auto_mute_on_ack: bool = False,
    ) -> dict:
        """Add a mute rule for a specific (alert_type, subject) pair.

        Sprint 5.62: Delegates to AlertLifecycleManager.
        """
        return self._lifecycle_mgr.add_mute_rule(
            alert_type=alert_type,
            subject=subject,
            duration_seconds=duration_seconds,
            muted_by=muted_by,
            auto_mute_on_ack=auto_mute_on_ack,
        )

    def remove_mute_rule(self, alert_type: str, subject: str) -> bool:
        """Remove a mute rule for a specific (alert_type, subject) pair.

        Sprint 5.62: Delegates to AlertLifecycleManager.
        """
        return self._lifecycle_mgr.remove_mute_rule(alert_type, subject)

    def get_mute_rules(self) -> list[dict]:
        """Return all active mute rules.

        Sprint 5.62: Delegates to AlertLifecycleManager.
        """
        return self._lifecycle_mgr.get_mute_rules()

    # -----------------------------------------------------------------------
    # Sprint 5.31: Alert digest / aggregation
    # Sprint 5.61: Delegates to DigestManager; _handle_digest_flush is the
    #   callback that handles actual dispatch when DigestManager decides to flush.
    # -----------------------------------------------------------------------

    def _handle_digest_flush(self, buffered_alerts: list[dict]) -> None:
        """Handle a digest flush by dispatching the buffered alerts.

        Sprint 5.61: This is the flush callback wired into DigestManager.
        It receives the list of buffered alert dicts and is responsible
        for creating the digest Alert, recording it in history, and
        dispatching to transports.
        """
        if not buffered_alerts:
            return

        # Build a digest summary
        type_counts: dict[str, int] = {}
        for alert_dict in buffered_alerts:
            at = alert_dict.get("alert_type", "unknown")
            type_counts[at] = type_counts.get(at, 0) + 1

        summary_parts = [f"{count} {atype.replace('_', ' ')}" for atype, count in sorted(type_counts.items())]
        summary_message = f"Alert digest: {', '.join(summary_parts)}"

        digest_alert = Alert(
            alert_type="digest",
            severity="info",
            subject="alert_digest",
            message=summary_message,
            data={
                "digest": True,
                "alert_count": len(buffered_alerts),
                "type_counts": type_counts,
                "buffered_alerts": buffered_alerts,
            },
        )

        # Dispatch the digest alert through normal channels
        transports = self._get_transports_for_alert("digest")
        if not transports:
            # Use all configured transports as fallback
            transports = []
            if self._config.webhook_url:
                transports.append("webhook")
            if self._config.email_to:
                transports.append("email")

        # Generate a correlation ID for the digest
        # Sprint 5.62: Via AlertLifecycleManager
        digest_correlation_id = self._lifecycle_mgr.get_next_digest_correlation_id()

        # Record in history
        digest_dict = digest_alert.to_dict()
        digest_dict["correlation_id"] = digest_correlation_id
        self._lifecycle_mgr.record_alert(digest_dict)

        # Persist to store if attached
        if self._history_store is not None:
            try:
                self._history_store.record_alert(digest_dict)
            except Exception:
                pass

        # Dispatch in background thread
        dispatch_thread = threading.Thread(
            target=self._dispatch_to_transports,
            args=(digest_alert, transports, digest_correlation_id),
            daemon=True,
            name=f"alert-digest-{digest_correlation_id}",
        )
        dispatch_thread.start()

        logger.info(
            "alert_digest_flushed",
            alert_count=len(buffered_alerts),
            type_counts=type_counts,
            correlation_id=digest_correlation_id,
        )

    def check_digest_flush(self) -> None:
        """Check if the digest buffer should be flushed based on time.

        Sprint 5.61: Delegates to DigestManager.check_digest_flush().
        """
        self._digest_mgr.check_digest_flush()

    # -----------------------------------------------------------------------
    # Sprint 5.32: Delivery status persistence
    # -----------------------------------------------------------------------

    def _persist_delivery_status(self, status_dict: dict) -> None:
        """Persist delivery status to the AlertHistoryStore.

        Sprint 5.32: Writes the current delivery status to SQLite so
        that it survives process restarts. Called whenever a new
        delivery status is created (dispatching or buffered_for_digest).

        Must be called with self._lock held.
        """
        if self._history_store is not None:
            try:
                self._history_store.record_delivery_status(status_dict)
            except Exception as exc:
                logger.warning(
                    "alert_delivery_status_persist_failed",
                    correlation_id=status_dict.get("correlation_id", ""),
                    error=str(exc),
                )

    def _update_persistent_delivery_status(self, correlation_id: str, final_status: str) -> None:
        """Update the persistent delivery status record.

        Sprint 5.32: Called when delivery completes (delivered/partial/failed)
        to update the SQLite record with the final status and completion time.

        Must be called with self._lock held.
        """
        if self._history_store is not None:
            try:
                self._history_store.update_delivery_status(
                    correlation_id=correlation_id,
                    status=final_status,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
            except Exception as exc:
                logger.warning(
                    "alert_delivery_status_update_failed",
                    correlation_id=correlation_id,
                    error=str(exc),
                )

    # -----------------------------------------------------------------------
    # Sprint 5.32: Alert correlation & grouping
    # -----------------------------------------------------------------------

    def _add_alert_to_group(self, alert: Alert, correlation_id: str) -> None:
        """Add an alert to a correlation group based on its subject.

        Sprint 5.32: Related alerts sharing the same subject are
        grouped together. This enables the dashboard to visually
        cluster related alerts and supports bulk actions on a group.

        Sprint 5.33: Persists group membership to AlertHistoryStore
        so groups survive process restarts. Also supports causal
        grouping when enabled.

        Grouping rules (pragmatic, start simple):
        - Alerts with the same subject are grouped together
        - The group key is the subject string
        - Pool exhaustion + quality degradation on same store → same group
        - When causal_grouping_enabled, alerts in the causal chain
          (pool_adjustment → quality_degradation → batch_reduction)
          sharing the same subject within the time window are grouped
          under a `causal:{subject}` key.
        """
        if not alert.subject:
            return

        # Sprint 5.34: Run TTL cleanup before adding new groups
        self.cleanup_expired_groups()

        group_key = alert.subject
        with self._lock:
            if group_key not in self._alert_groups:
                self._alert_groups[group_key] = []
            self._alert_groups[group_key].append(correlation_id)
            # Keep only last 50 correlation IDs per group
            if len(self._alert_groups[group_key]) > 50:
                self._alert_groups[group_key] = self._alert_groups[group_key][-50:]

            # Sprint 5.34: Update group's last_activity_at timestamp
            self._alert_groups_metadata[group_key] = time.time()

            # Sprint 5.33: Persist group membership to store
            if self._history_store is not None:
                try:
                    self._history_store.record_alert_group(group_key, correlation_id)
                except Exception as exc:
                    logger.warning(
                        "alert_group_persist_failed",
                        group_key=group_key,
                        correlation_id=correlation_id,
                        error=str(exc),
                    )

        # Sprint 5.33: Causal grouping
        if self._config.causal_grouping_enabled:
            self._add_causal_group(alert, correlation_id)

        # Sprint 5.36: Causal chain prediction
        self.predict_causal_chain(alert)

    def get_alert_groups(self) -> dict[str, list[str]]:
        """Return all alert correlation groups.

        Sprint 5.32: Returns a dict mapping group keys (subjects) to
        lists of correlation IDs. This enables the dashboard to display
        related alerts together and support bulk operations.
        """
        with self._lock:
            return dict(self._alert_groups)

    # -----------------------------------------------------------------------
    # Sprint 5.34: Alert group TTL & auto-cleanup
    # -----------------------------------------------------------------------

    def cleanup_expired_groups(self) -> int:
        """Dissolve alert groups whose TTL has expired.

        Sprint 5.34: Iterates all groups and removes those where
        (now - last_activity_at) > alert_group_ttl_hours * 3600.
        Also removes from persistent store if attached.

        Returns the count of dissolved groups.
        """
        ttl_hours = self._config.alert_group_ttl_hours
        if ttl_hours <= 0:
            return 0

        now = time.time()
        expired_keys: list[str] = []

        with self._lock:
            for group_key, last_activity in list(self._alert_groups_metadata.items()):
                if (now - last_activity) > ttl_hours * 3600:
                    expired_keys.append(group_key)

            for key in expired_keys:
                del self._alert_groups[key]
                del self._alert_groups_metadata[key]
                self._total_groups_cleaned += 1

                # Delete from persistent store
                if self._history_store is not None:
                    try:
                        self._history_store.delete_alert_group(key)
                    except Exception as exc:
                        logger.warning(
                            "alert_group_ttl_delete_failed",
                            group_key=key,
                            error=str(exc),
                        )

        if expired_keys:
            logger.info(
                "alert_groups_ttl_expired",
                expired_count=len(expired_keys),
                group_keys=expired_keys,
                ttl_hours=ttl_hours,
            )

        return len(expired_keys)

    # -----------------------------------------------------------------------
    # Sprint 5.34: WebSocket session management
    # -----------------------------------------------------------------------

    def register_ws_session(self, session_id: str, websocket: Any, remote_addr: str = "") -> None:
        """Register a WebSocket session with a unique ID.

        Sprint 5.34: Tracks active WebSocket connections so operators
        can monitor who is connected to the dashboard.

        Sprint 5.35: Includes last_heartbeat_at for dead-session detection.

        Broadcasts a ws_session_connected event to other subscribers.
        """
        now = time.time()
        with self._lock:
            self._ws_sessions[session_id] = {
                "websocket": websocket,
                "connected_at": datetime.now(timezone.utc).isoformat(),
                "remote_addr": remote_addr,
                "last_heartbeat_at": now,
                "missed_heartbeats": 0,
            }

        # Invalidate status cache so ws_sessions count is fresh
        self._status_aggregator.invalidate_cache()

        # Notify other subscribers about the new session
        self._realtime_bus.notify_realtime_subscribers(
            {
                "event": "ws_session_connected",
                "session_id": session_id,
            }
        )

        logger.info(
            "ws_session_registered",
            session_id=session_id,
            remote_addr=remote_addr,
        )

    def unregister_ws_session(self, session_id: str) -> None:
        """Remove a WebSocket session.

        Sprint 5.34: Broadcasts a ws_session_disconnected event to
        other subscribers before removing the session.
        """
        # Notify other subscribers about the disconnect
        self._realtime_bus.notify_realtime_subscribers(
            {
                "event": "ws_session_disconnected",
                "session_id": session_id,
            }
        )

        with self._lock:
            self._ws_sessions.pop(session_id, None)

        # Invalidate status cache so ws_sessions count is fresh
        self._status_aggregator.invalidate_cache()

        logger.info(
            "ws_session_unregistered",
            session_id=session_id,
        )

    def get_ws_sessions(self) -> list[dict]:
        """Return info about active WebSocket sessions.

        Sprint 5.34: Returns a list of session info dicts (without
        the actual websocket object) for API exposure.

        Sprint 5.35: Includes last_heartbeat_at and missed_heartbeats.
        """
        with self._lock:
            return [
                {
                    "session_id": sid,
                    "connected_at": info.get("connected_at", ""),
                    "remote_addr": info.get("remote_addr", ""),
                    "last_heartbeat_at": info.get("last_heartbeat_at", 0),
                    "missed_heartbeats": info.get("missed_heartbeats", 0),
                }
                for sid, info in self._ws_sessions.items()
            ]

    # -----------------------------------------------------------------------
    # Sprint 5.35: WebSocket heartbeat & dead-session detection
    # -----------------------------------------------------------------------

    def update_ws_session_heartbeat(self, session_id: str) -> bool:
        """Update the last heartbeat time for a WebSocket session.

        Sprint 5.35: Called when a heartbeat/ping response is received
        from the client. Resets the missed heartbeat counter.

        Returns True if the session was found, False otherwise.
        """
        with self._lock:
            session = self._ws_sessions.get(session_id)
            if session is None:
                return False
            session["last_heartbeat_at"] = time.time()
            session["missed_heartbeats"] = 0
        return True

    def increment_missed_heartbeats(self) -> list[str]:
        """Increment missed heartbeat count for all active sessions.

        Sprint 5.35: Called periodically by the heartbeat checker.
        Returns a list of session IDs that exceeded the missed limit
        and should be cleaned up.
        """
        limit = self._config.ws_heartbeat_missed_limit
        dead_sessions: list[str] = []

        with self._lock:
            for session_id, session in list(self._ws_sessions.items()):
                session["missed_heartbeats"] = session.get("missed_heartbeats", 0) + 1
                if session["missed_heartbeats"] >= limit:
                    dead_sessions.append(session_id)

        return dead_sessions

    def cleanup_dead_ws_sessions(self) -> int:
        """Detect and remove dead WebSocket sessions based on missed heartbeats.

        Sprint 5.35: Iterates all active sessions, increments missed
        heartbeats, and removes sessions that have exceeded the limit.
        Also removes them from the ws_subscribers list.

        Returns the count of cleaned up sessions.
        """
        dead_session_ids = self.increment_missed_heartbeats()
        if not dead_session_ids:
            return 0

        for session_id in dead_session_ids:
            # Get the websocket object before removing the session
            with self._lock:
                session = self._ws_sessions.get(session_id)
                ws_obj = session.get("websocket") if session else None

            # Remove from subscribers
            if ws_obj is not None:
                self._realtime_bus.remove_ws_subscriber(ws_obj)

            # Unregister the session (broadcasts disconnect event)
            with self._lock:
                self._ws_sessions.pop(session_id, None)

            self._total_dead_sessions_cleaned += 1

            logger.info(
                "ws_session_dead_cleaned",
                session_id=session_id,
                reason="missed_heartbeats_exceeded",
            )

        if dead_session_ids:
            self._realtime_bus.notify_realtime_subscribers(
                {
                    "event": "ws_dead_sessions_cleaned",
                    "count": len(dead_session_ids),
                    "session_ids": dead_session_ids,
                }
            )

        return len(dead_session_ids)

    def bulk_acknowledge_group(self, group_key: str, acknowledged_by: str = "operator") -> dict:
        """Acknowledge all alerts in a correlation group.

        Sprint 5.32: Finds all alerts in the specified group and
        acknowledges them. Returns a summary of how many were
        acknowledged vs. not found.
        """
        with self._lock:
            correlation_ids = list(self._alert_groups.get(group_key, []))

        acknowledged = 0
        not_found = 0
        for cid in correlation_ids:
            # Find the alert by correlation_id in the history store
            if self._history_store is not None:
                try:
                    # Look up the alert by correlation_id
                    alert = self._history_store.get_alert_by_correlation_id(cid)
                    if alert and alert.get("acknowledged", 0) == 0:
                        if self._history_store.acknowledge_alert(alert["id"], acknowledged_by):
                            acknowledged += 1
                        else:
                            not_found += 1
                    else:
                        not_found += 1
                except Exception:
                    not_found += 1
            else:
                not_found += 1

        logger.info(
            "alert_bulk_acknowledge",
            group_key=group_key,
            acknowledged=acknowledged,
            not_found=not_found,
        )

        return {
            "group_key": group_key,
            "acknowledged": acknowledged,
            "not_found": not_found,
            "total": len(correlation_ids),
        }

    def bulk_dismiss_group(self, group_key: str, dismissed_by: str = "operator") -> dict:
        """Dismiss all alerts in a correlation group.

        Sprint 5.32: Finds all alerts in the specified group and
        dismisses them. Returns a summary of how many were dismissed
        vs. not found.
        """
        with self._lock:
            correlation_ids = list(self._alert_groups.get(group_key, []))

        dismissed = 0
        not_found = 0
        for cid in correlation_ids:
            if self._history_store is not None:
                try:
                    alert = self._history_store.get_alert_by_correlation_id(cid)
                    if alert and alert.get("acknowledged", 0) == 0:
                        if self._history_store.dismiss_alert(alert["id"], dismissed_by):
                            dismissed += 1
                        else:
                            not_found += 1
                    else:
                        not_found += 1
                except Exception:
                    not_found += 1
            else:
                not_found += 1

        logger.info(
            "alert_bulk_dismiss",
            group_key=group_key,
            dismissed=dismissed,
            not_found=not_found,
        )

        return {
            "group_key": group_key,
            "dismissed": dismissed,
            "not_found": not_found,
            "total": len(correlation_ids),
        }

    # -----------------------------------------------------------------------
    # Sprint 5.35: Alert group merge & split
    # -----------------------------------------------------------------------

    def merge_alert_groups(self, source_key: str, target_key: str) -> dict:
        """Merge source group into target group.

        Sprint 5.35: Moves all correlation IDs from source_key into
        target_key, then deletes the source group. If target_key does
        not exist, it is created. Causal group metadata and persistent
        store records are updated accordingly.

        Returns a summary dict with merge details.
        """
        with self._lock:
            source_cids = list(self._alert_groups.get(source_key, []))
            if not source_cids:
                return {
                    "status": "empty_source",
                    "source_key": source_key,
                    "target_key": target_key,
                    "merged_count": 0,
                }

            # Merge into target
            if target_key not in self._alert_groups:
                self._alert_groups[target_key] = []
            existing = set(self._alert_groups[target_key])
            merged = 0
            for cid in source_cids:
                if cid not in existing:
                    self._alert_groups[target_key].append(cid)
                    existing.add(cid)
                    merged += 1

            # Keep only last 50 per group
            if len(self._alert_groups[target_key]) > 50:
                self._alert_groups[target_key] = self._alert_groups[target_key][-50:]

            # Update metadata for target
            self._alert_groups_metadata[target_key] = time.time()

            # Remove source group
            del self._alert_groups[source_key]
            self._alert_groups_metadata.pop(source_key, None)

        # Update persistent store
        if self._history_store is not None:
            try:
                # Delete source group records
                self._history_store.delete_alert_group(source_key)
                # Re-record target group memberships
                for cid in self._alert_groups[target_key]:
                    self._history_store.record_alert_group(target_key, cid)
            except Exception as exc:
                logger.warning(
                    "alert_group_merge_persist_failed",
                    source_key=source_key,
                    target_key=target_key,
                    error=str(exc),
                )

        logger.info(
            "alert_groups_merged",
            source_key=source_key,
            target_key=target_key,
            merged_count=merged,
        )

        self._realtime_bus.notify_realtime_subscribers(
            {
                "event": "alert_groups_merged",
                "source_key": source_key,
                "target_key": target_key,
                "merged_count": merged,
            }
        )

        return {
            "status": "ok",
            "source_key": source_key,
            "target_key": target_key,
            "merged_count": merged,
            "total_in_target": len(self._alert_groups.get(target_key, [])),
        }

    def split_alert_group(self, group_key: str, correlation_ids: list[str], new_group_key: str | None = None) -> dict:
        """Split a group by moving specified correlation IDs to a new group.

        Sprint 5.35: Removes the specified correlation IDs from the
        source group and creates a new group with them. If new_group_key
        is not provided, one is generated based on the original key.

        Returns a summary dict with split details.
        """
        with self._lock:
            existing_cids = list(self._alert_groups.get(group_key, []))
            if not existing_cids:
                return {
                    "status": "empty_source",
                    "group_key": group_key,
                    "split_count": 0,
                }

            # Determine which CIDs to move
            to_move = [cid for cid in correlation_ids if cid in set(existing_cids)]
            if not to_move:
                return {
                    "status": "no_matching_cids",
                    "group_key": group_key,
                    "split_count": 0,
                }

            # Generate new group key if not provided
            if not new_group_key:
                new_group_key = f"{group_key}_split_{uuid.uuid4().hex[:6]}"

            # Create new group with the split CIDs
            self._alert_groups[new_group_key] = to_move
            self._alert_groups_metadata[new_group_key] = time.time()

            # Remove moved CIDs from source
            remaining = [cid for cid in existing_cids if cid not in set(to_move)]
            if remaining:
                self._alert_groups[group_key] = remaining
            else:
                del self._alert_groups[group_key]
                self._alert_groups_metadata.pop(group_key, None)

        # Update persistent store
        if self._history_store is not None:
            try:
                # Delete old group and re-record both
                self._history_store.delete_alert_group(group_key)
                # Re-record remaining in source
                if group_key in self._alert_groups:
                    for cid in self._alert_groups[group_key]:
                        self._history_store.record_alert_group(group_key, cid)
                # Record new group
                for cid in to_move:
                    self._history_store.record_alert_group(new_group_key, cid)
            except Exception as exc:
                logger.warning(
                    "alert_group_split_persist_failed",
                    group_key=group_key,
                    new_group_key=new_group_key,
                    error=str(exc),
                )

        logger.info(
            "alert_group_split",
            group_key=group_key,
            new_group_key=new_group_key,
            split_count=len(to_move),
        )

        self._realtime_bus.notify_realtime_subscribers(
            {
                "event": "alert_group_split",
                "source_key": group_key,
                "new_group_key": new_group_key,
                "split_count": len(to_move),
            }
        )

        return {
            "status": "ok",
            "group_key": group_key,
            "new_group_key": new_group_key,
            "split_count": len(to_move),
            "remaining_in_source": len(self._alert_groups.get(group_key, [])),
        }

    # -----------------------------------------------------------------------
    # Sprint 5.36: Alert group auto-merge by similarity
    # -----------------------------------------------------------------------

    def suggest_auto_merges(self) -> list[dict]:
        """Suggest alert groups that could be merged based on similarity.

        Sprint 5.36: Compares all non-causal groups and identifies pairs
        where subjects overlap significantly (measured by token overlap)
        and the groups have recent activity within auto_merge_window_seconds.
        Returns a list of merge suggestion dicts, each containing source_key,
        target_key, similarity score, and reason.

        Sprint 5.37: Respects auto_merge_mode and cooldown.
        If auto_merge_mode is "auto", applies merges automatically
        (respecting auto_merge_cooldown_seconds). Per-alert-type
        similarity thresholds from auto_merge_type_thresholds are used
        when available.
        """
        window = self._config.auto_merge_window_seconds
        if window <= 0:
            return []

        now = time.time()
        threshold = self._config.auto_merge_similarity_threshold
        suggestions: list[dict] = []

        with self._lock:
            group_keys = list(self._alert_groups.keys())

        # Compare each pair of non-causal groups
        non_causal_keys = [k for k in group_keys if not k.startswith("causal:")]
        for i, key_a in enumerate(non_causal_keys):
            for key_b in non_causal_keys[i + 1 :]:
                # Check if both groups have recent activity
                meta_a = self._alert_groups_metadata.get(key_a, 0)
                meta_b = self._alert_groups_metadata.get(key_b, 0)
                if (now - meta_a) > window or (now - meta_b) > window:
                    continue

                # Sprint 5.37: Use per-type threshold if available
                type_threshold = threshold  # Default global threshold

                # Compute subject similarity using token overlap
                similarity = self._compute_subject_similarity(key_a, key_b)
                if similarity >= type_threshold:
                    suggestions.append(
                        {
                            "source_key": key_a,
                            "target_key": key_b,
                            "similarity": round(similarity, 3),
                            "reason": f"Subjects overlap {similarity:.0%} and both active within {window}s",
                            "source_count": len(self._alert_groups.get(key_a, [])),
                            "target_count": len(self._alert_groups.get(key_b, [])),
                        }
                    )

        with self._lock:
            self._auto_merge_suggestions = suggestions
            self._total_auto_merges_suggested += len(suggestions)

        if suggestions:
            logger.info(
                "auto_merge_suggestions_generated",
                count=len(suggestions),
            )

        # Sprint 5.37: Auto-apply if mode is "auto" and cooldown has elapsed
        if self._config.auto_merge_mode == "auto" and suggestions:
            now_ts = time.time()
            if now_ts - self._last_auto_merge_time >= self._config.auto_merge_cooldown_seconds:
                for suggestion in suggestions:
                    result = self.apply_auto_merge(
                        suggestion["source_key"],
                        suggestion["target_key"],
                    )
                    if result.get("status") == "ok":
                        with self._lock:
                            self._last_auto_merge_time = time.time()
                        logger.info(
                            "auto_merge_auto_applied",
                            source_key=suggestion["source_key"],
                            target_key=suggestion["target_key"],
                        )
                        break  # Only one auto-merge per cooldown window

        return suggestions

    def apply_auto_merge(self, source_key: str, target_key: str) -> dict:
        """Apply an auto-merge suggestion by merging the two groups.

        Sprint 5.36: Performs the actual merge and tracks the count
        of auto-merges applied. Returns the merge result dict.
        """
        result = self.merge_alert_groups(source_key, target_key)
        if result.get("status") == "ok":
            with self._lock:
                self._total_auto_merges_applied += 1
                # Remove from suggestions
                self._auto_merge_suggestions = [
                    s
                    for s in self._auto_merge_suggestions
                    if not (s["source_key"] == source_key and s["target_key"] == target_key)
                ]
        return result

    def get_auto_merge_suggestions(self) -> list[dict]:
        """Return current auto-merge suggestions."""
        with self._lock:
            return list(self._auto_merge_suggestions)

    @staticmethod
    def _compute_subject_similarity(key_a: str, key_b: str) -> float:
        """Compute similarity between two group keys using token overlap.

        Uses Jaccard similarity on word-level tokens after normalizing
        (lowercasing, splitting on non-alphanumeric). Returns a float
        between 0.0 and 1.0.
        """
        import re as _re

        tokens_a = set(_re.findall(r"[a-z0-9]+", key_a.lower()))
        tokens_b = set(_re.findall(r"[a-z0-9]+", key_b.lower()))
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union)

    # -----------------------------------------------------------------------
    # Sprint 5.37: Notification channel diversification — Slack & PagerDuty
    # -----------------------------------------------------------------------

    def _send_slack_notification(self, alert: Alert) -> dict[str, str] | None:
        """Send alert notification to a Slack webhook.

        Sprint 5.37: Posts a Slack-compatible message to the configured
        slack_webhook_url. Uses the same retry/backoff logic as webhook.

        Sprint 5.38: Returns a delivery receipt dict with message_ts when
        delivery_receipts_enabled is True. Returns None on early return
        or when no URL configured.
        """
        if not self._config.slack_webhook_url:
            return None

        severity_colors = {
            "info": "#3b82f6",
            "warning": "#f59e0b",
            "critical": "#ef4444",
        }
        color = severity_colors.get(alert.severity, "#64748b")

        payload = json.dumps(
            {
                "attachments": [
                    {
                        "color": color,
                        "title": f"[AIP Brain] {alert.severity.upper()}: {alert.subject}",
                        "text": alert.message,
                        "fields": [
                            {"title": "Type", "value": alert.alert_type, "short": True},
                            {"title": "Severity", "value": alert.severity, "short": True},
                            {"title": "Time", "value": alert.timestamp, "short": False},
                        ],
                        "footer": "AIP Brain Alerting",
                        "ts": int(time.time()),
                    }
                ],
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            self._config.slack_webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"Slack webhook returned HTTP {resp.status}")
                # Sprint 5.38: Parse response body for message timestamp receipt
                if self._config.delivery_receipts_enabled:
                    try:
                        body = resp.read().decode("utf-8", errors="replace")
                        resp_data = json.loads(body) if body else {}
                        message_ts = resp_data.get("ts", "")
                        return {"message_ts": message_ts, "channel": resp_data.get("channel", "")}
                    except (json.JSONDecodeError, AttributeError):
                        pass
                return None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", str(exc))
            raise RuntimeError(f"Slack webhook connection failed: {reason}") from exc

    def _send_pagerduty_notification(self, alert: Alert) -> dict[str, str] | None:
        """Send alert notification to PagerDuty via Events API v2.

        Sprint 5.37: Posts a PagerDuty event to the configured
        pagerduty_integration_key. Supports info, warning, critical
        severity mapping.

        Sprint 5.38: Returns a delivery receipt dict with dedup_key and
        event status when delivery_receipts_enabled is True.
        """
        if not self._config.pagerduty_integration_key:
            return None

        severity_map = {
            "info": "info",
            "warning": "warning",
            "critical": "critical",
        }
        pd_severity = severity_map.get(alert.severity, "warning")

        # Sprint 5.38: Generate dedup_key for PagerDuty receipt tracking
        dedup_key = f"aip-brain-{alert.alert_type}-{alert.subject}-{uuid.uuid4().hex[:8]}"

        payload = json.dumps(
            {
                "routing_key": self._config.pagerduty_integration_key,
                "event_action": "trigger",
                "dedup_key": dedup_key,
                "payload": {
                    "summary": f"[AIP Brain] {alert.alert_type}: {alert.subject} — {alert.message[:200]}",
                    "severity": pd_severity,
                    "source": "aip-brain",
                    "component": alert.alert_type,
                    "group": alert.subject,
                    "class": alert.alert_type,
                    "timestamp": alert.timestamp,
                    "custom_details": alert.data,
                },
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            "https://events.pagerduty.com/v2/enqueue",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"PagerDuty API returned HTTP {resp.status}")
                # Sprint 5.38: Parse response body for dedup_key and status receipt
                if self._config.delivery_receipts_enabled:
                    try:
                        body = resp.read().decode("utf-8", errors="replace")
                        resp_data = json.loads(body) if body else {}
                        return {
                            "dedup_key": resp_data.get("dedup_key", dedup_key),
                            "status": resp_data.get("status", "triggered"),
                            "message": resp_data.get("message", ""),
                        }
                    except (json.JSONDecodeError, AttributeError):
                        pass
                return {"dedup_key": dedup_key} if self._config.delivery_receipts_enabled else None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", str(exc))
            raise RuntimeError(f"PagerDuty connection failed: {reason}") from exc

    # -----------------------------------------------------------------------
    # Sprint 5.35: Delivery status pruning scheduler
    # -----------------------------------------------------------------------

    def start_prune_scheduler(self) -> bool:
        """Start the background delivery status pruning scheduler.

        Sprint 5.62: Delegates to PruningManager.
        """
        return self._pruning_mgr.start_prune_scheduler()

    def stop_prune_scheduler(self) -> None:
        """Stop the delivery status pruning scheduler.

        Sprint 5.62: Delegates to PruningManager.
        """
        self._pruning_mgr.stop_prune_scheduler()

    def _run_scheduled_prune(self) -> int:
        """Execute a single scheduled prune cycle.

        Sprint 5.62: Delegates to PruningManager.
        """
        return self._pruning_mgr._run_scheduled_prune()

    def get_prune_scheduler_status(self) -> dict:
        """Return the current state of the pruning scheduler.

        Sprint 5.62: Delegates to PruningManager.
        """
        return self._pruning_mgr.get_prune_scheduler_status()

    def get_pruning_history(self, limit: int | None = None) -> list[dict]:
        """Return pruning run history records.

        Sprint 5.62: Delegates to PruningManager.
        """
        return self._pruning_mgr.get_pruning_history(limit)

    # -----------------------------------------------------------------------
    # Sprint 5.32: Digest customization per alert type
    # Sprint 5.61: Delegated to DigestManager.get_digest_settings()
    # -----------------------------------------------------------------------

    def _get_digest_settings(self, alert_type: str) -> tuple[int, int]:
        """Get digest interval and min_alerts for a specific alert type.

        Sprint 5.61: Delegates to DigestManager.get_digest_settings().
        """
        return self._digest_mgr.get_digest_settings(alert_type)

    def _is_alert_type_enabled(self, alert_type: str) -> bool:
        """Check if a specific alert type is enabled."""
        mapping = {
            "quality_degradation": self._config.alert_on_quality_degradation,
            "pool_adjustment": self._config.alert_on_pool_adjustment,
            "batch_reduction": self._config.alert_on_batch_reduction,
        }
        result = mapping.get(alert_type)
        if result is not None:
            return result
        # Sprint 5.45/5.46: New alert types
        if alert_type == "ab_experiment_promotion":
            return True  # A/B experiment promotions are always sent when alerting is enabled
        if alert_type == "ab_experiment_rollback":
            return True  # A/B experiment rollbacks are always sent when alerting is enabled
        if alert_type == "confidence_decay":
            return self._config.alert_on_quality_degradation  # Decay uses quality degradation flag
        if alert_type == "decay_recovery":
            return True  # Decay recovery notifications are always sent
        return True

    # Sprint 5.30: Severity escalation logic

    def _check_escalation(self, alert: Alert, now: float) -> bool:
        """Check if an alert should be escalated based on occurrence count.

        Sprint 5.62: Delegates to AlertLifecycleManager.
        """
        return self._lifecycle_mgr.check_escalation(alert, now)

    # Sprint 5.29: Persistent history store integration

    def attach_history_store(self, store: Any) -> None:
        """Attach a persistent AlertHistoryStore for durable alert history.

        Sprint 5.29: When a persistent store is attached, all new alerts
        and delivery failures are written to SQLite in addition to the
        in-memory buffers.  Query methods (get_alert_history,
        get_delivery_failures) prefer the persistent store when available,
        enabling full history access across process restarts.

        Sprint 5.30: After attaching the store, rebuilds the in-memory
        rate-limiting state from the persistent store so that duplicate
        alert storms are prevented immediately after a restart.

        Sprint 13.3: If the store is a raw async AlertHistoryStore
        (not already a SyncAlertHistoryBridge), it is automatically
        wrapped in a SyncAlertHistoryBridge so that all synchronous
        callers in AlertManager can invoke store methods without await.

        Parameters
        ----------
        store:
            An AlertHistoryStore instance, a SyncAlertHistoryBridge
            instance, or any object with the expected store methods.
        """
        # Auto-wrap raw async AlertHistoryStore in SyncAlertHistoryBridge
        from aip.adapter.alert_history_store import SyncAlertHistoryBridge as _Bridge

        if isinstance(store, _Bridge):
            # Already a bridge — use as-is, but ensure initialized
            store.initialize()
        else:
            from aip.adapter.alert_history_store import AlertHistoryStore as _AHS

            if isinstance(store, _AHS):
                bridge = _Bridge(store)
                # Ensure DB tables are created (sync bridge awaits the
                # async initialize() internally)
                bridge.initialize()
                store = bridge

        self._history_store = store
        # Sprint 5.61: Propagate store reference to ThrottleManager
        self._throttle_mgr._history_store = store
        # Sprint 5.61: Propagate store reference to PredictionManager
        self._prediction_mgr._history_store = store
        # Sprint 5.61: Propagate store reference to ABExperimentManager
        self._ab_experiment_mgr._history_store = store
        # Sprint 5.62: Propagate store reference to AlertLifecycleManager
        self._lifecycle_mgr.set_history_store(store)
        # Sprint 5.62: Propagate store reference to PruningManager
        self._pruning_mgr.set_history_store(store)
        logger.info(
            "alert_history_store_attached",
            store_type=type(store).__name__,
        )

        # Sprint 5.30: Rebuild rate-limiting state from persistent store
        self._rebuild_rate_limit_state()

        # Sprint 5.31: Rebuild mute rules from persistent store
        self._rebuild_mute_rules()

        # Sprint 5.32: Rebuild delivery status from persistent store
        self._rebuild_delivery_status()

        # Sprint 5.33: Rebuild alert groups from persistent store
        self._rebuild_alert_groups()

    def _rebuild_rate_limit_state(self) -> None:
        """Rebuild in-memory rate-limiting state from persistent store.

        Sprint 5.62: Delegates to AlertLifecycleManager.
        """
        self._lifecycle_mgr.rebuild_rate_limit_state()

    def _rebuild_mute_rules(self) -> None:
        """Rebuild in-memory mute rules from persistent store.

        Sprint 5.62: Delegates to AlertLifecycleManager.
        """
        self._lifecycle_mgr.rebuild_mute_rules()

    # Sprint 5.33: Causal alert grouping

    _CAUSAL_CHAIN = ["pool_adjustment", "quality_degradation", "batch_reduction"]

    def _add_causal_group(self, alert: Alert, correlation_id: str) -> None:
        """Add an alert to a causal group if it matches the causal chain.

        Sprint 5.33: When causal_grouping_enabled is True, alerts with
        alert_types in the causal chain (pool_adjustment →
        quality_degradation → batch_reduction) that share the same
        subject within the configured time window are grouped under
        `causal:{subject}`.

        Sprint 5.35: Time-window enforcement — only group alerts into
        causal chains if the group's last activity was within the
        configured causal_grouping_window_seconds. If the group has
        been inactive beyond the window, a new causal group is started.
        """
        if alert.alert_type not in self._CAUSAL_CHAIN:
            return

        causal_key = f"causal:{alert.subject}"
        now = time.time()
        window = self._config.causal_grouping_window_seconds

        with self._lock:
            # Sprint 5.35: Check time window — if the causal group's last
            # activity was outside the window, dissolve it and start fresh
            if causal_key in self._alert_groups_metadata:
                last_activity = self._alert_groups_metadata[causal_key]
                if (now - last_activity) > window:
                    # Group is stale — dissolve it before adding new entry
                    logger.debug(
                        "causal_group_window_expired",
                        causal_key=causal_key,
                        elapsed_seconds=round(now - last_activity, 1),
                        window_seconds=window,
                    )
                    # Delete from persistent store
                    if self._history_store is not None:
                        try:
                            self._history_store.delete_alert_group(causal_key)
                        except Exception:
                            pass
                    del self._alert_groups[causal_key]
                    del self._alert_groups_metadata[causal_key]

            if causal_key not in self._alert_groups:
                self._alert_groups[causal_key] = []
            self._alert_groups[causal_key].append(correlation_id)

            # Sprint 5.34: Update causal group's last_activity_at timestamp
            self._alert_groups_metadata[causal_key] = now

            # Keep only correlation IDs from alerts within the time window
            # We keep up to 50 per causal group
            if len(self._alert_groups[causal_key]) > 50:
                self._alert_groups[causal_key] = self._alert_groups[causal_key][-50:]

            # Persist to store
            if self._history_store is not None:
                try:
                    self._history_store.record_alert_group(causal_key, correlation_id)
                except Exception as exc:
                    logger.warning(
                        "causal_group_persist_failed",
                        causal_key=causal_key,
                        correlation_id=correlation_id,
                        error=str(exc),
                    )

    def _rebuild_alert_groups(self) -> None:
        """Rebuild in-memory alert groups from persistent store.

        Sprint 5.33: On startup (or after attaching a history store),
        queries the AlertHistoryStore for persisted alert group memberships
        and populates _alert_groups. This ensures groups survive restarts.
        """
        if self._history_store is None:
            return

        try:
            groups = self._history_store.get_alert_groups()
            if groups:
                with self._lock:
                    for group_key, correlation_ids in groups.items():
                        if group_key not in self._alert_groups:
                            self._alert_groups[group_key] = []
                        # Merge, avoiding duplicates
                        existing = set(self._alert_groups[group_key])
                        for cid in correlation_ids:
                            if cid not in existing:
                                self._alert_groups[group_key].append(cid)
                                existing.add(cid)
                logger.info(
                    "alert_groups_rebuilt",
                    groups_rebuilt=len(groups),
                )
        except Exception as exc:
            logger.warning(
                "alert_groups_rebuild_failed",
                error=str(exc),
            )

    def _rebuild_delivery_status(self) -> None:
        """Rebuild in-memory delivery status from persistent store.

        Sprint 5.62: Delegates to AlertLifecycleManager.
        """
        self._lifecycle_mgr.rebuild_delivery_status()

    def _get_transports_for_alert(self, alert_type: str) -> list[str]:
        """Determine which transports to use for a given alert type.

        Sprint 5.29: If ``routes`` is configured in AlertConfig, only
        the transports listed for this alert_type are used.  If the
        alert_type has no entry in routes, or routes is empty, all
        configured transports are used (default behavior).

        Sprint 5.37: Also considers notification_routes for per-severity
        or per-alert-type channel routing. New channels: slack, pagerduty.

        Returns a list of transport names: "webhook", "email", "slack", "pagerduty".
        """
        # Sprint 5.37: Check notification_routes first (severity/type -> channels)
        # This takes priority over the older routes config
        if self._config.notification_routes:
            # Check by alert_type first, then by severity
            for key in [alert_type]:
                if key in self._config.notification_routes:
                    configured = self._config.notification_routes[key]
                    result = []
                    for t in configured:
                        if t == "webhook" and self._config.webhook_url:
                            result.append(t)
                        elif t == "email" and self._config.email_to:
                            result.append(t)
                        elif t == "slack" and self._config.slack_webhook_url:
                            result.append(t)
                        elif t == "pagerduty" and self._config.pagerduty_integration_key:
                            result.append(t)
                    return result

        if self._config.routes and alert_type in self._config.routes:
            configured = self._config.routes[alert_type]
            # Only return transports that are actually configured
            result = []
            for t in configured:
                if t == "webhook" and self._config.webhook_url:
                    result.append(t)
                elif t == "email" and self._config.email_to:
                    result.append(t)
                elif t == "slack" and self._config.slack_webhook_url:
                    result.append(t)
                elif t == "pagerduty" and self._config.pagerduty_integration_key:
                    result.append(t)
            return result

        # Default: all configured transports
        transports = []
        if self._config.webhook_url:
            transports.append("webhook")
        if self._config.email_to:
            transports.append("email")
        # Sprint 5.37: Include new notification channels
        if self._config.slack_webhook_url:
            transports.append("slack")
        if self._config.pagerduty_integration_key:
            transports.append("pagerduty")
        return transports

    def _validate_webhook_url_lazy(self) -> bool:
        """Validate the webhook URL on first use.

        Returns True if the URL is valid or already validated.
        Logs a warning if the URL fails validation but does NOT prevent
        the send attempt (the URL might still work for non-standard cases).
        """
        if self._webhook_url_validated:
            return self._webhook_url_valid is not False

        is_valid, reason = validate_webhook_url(self._config.webhook_url)
        self._webhook_url_validated = True
        self._webhook_url_valid = is_valid
        self._webhook_url_validation_reason = reason

        if not is_valid:
            logger.warning(
                "alert_webhook_url_invalid",
                url=self._config.webhook_url[:50],
                reason=reason,
                note="Delivery will be attempted but may fail",
            )

        return is_valid

    def _send_webhook_with_retry(self, alert: Alert) -> None:
        """POST alert payload to the configured webhook URL with retry.

        Uses exponential backoff between retries.  The initial attempt
        plus up to ``webhook_max_retries`` retry attempts are made.

        Backoff timing: attempt N waits (base_delay * 2^N) seconds
        before retrying.  For the default base_delay of 1.0 and
        max_retries of 3, the delays are 1s, 2s, 4s.
        """
        # Lazy-validate the URL on first use
        self._validate_webhook_url_lazy()

        max_retries = self._config.webhook_max_retries
        base_delay = self._config.webhook_retry_base_delay_seconds
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                self._send_webhook_once(alert)
                # Success — return immediately
                if attempt > 0:
                    logger.info(
                        "alert_webhook_retry_succeeded",
                        url=self._config.webhook_url[:50],
                        attempt=attempt,
                    )
                return
            except Exception as exc:
                last_error = exc
                if attempt < max_retries:
                    # Record intermediate failure
                    self._delivery_mgr.increment_webhook_retry()
                    delay = base_delay * (2**attempt)
                    self._record_failure(
                        DeliveryFailure(
                            transport="webhook",
                            alert_type=alert.alert_type,
                            subject=alert.subject,
                            error_message=str(exc),
                            retry_attempt=attempt,
                            final=False,
                        )
                    )
                    logger.info(
                        "alert_webhook_retry",
                        url=self._config.webhook_url[:50],
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        delay_seconds=delay,
                        error=str(exc),
                    )
                    time.sleep(delay)

        # All retries exhausted — raise the last error
        raise last_error or RuntimeError("Webhook delivery failed after all retries")

    def _send_webhook_once(self, alert: Alert) -> None:
        """POST alert payload to the configured webhook URL (single attempt).

        Uses stdlib urllib to avoid adding requests/aiohttp dependency.
        Timeout is 10 seconds — alerts must not block the caller.
        """
        payload = json.dumps(
            {
                "source": "aip-brain",
                "alert": alert.to_dict(),
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            self._config.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"Webhook returned HTTP {resp.status}")
        except urllib.error.URLError as exc:
            # Provide more context for common connection errors
            reason = getattr(exc, "reason", str(exc))
            raise RuntimeError(f"Webhook connection failed: {reason}") from exc

    def _send_email(self, alert: Alert) -> None:
        """Send alert email via SMTP with optional authentication.

        Sprint 5.26: Added SMTP authentication support.  When
        smtp_username is configured, the SMTP session authenticates
        using LOGIN method before sending.  TLS behavior is controlled
        by smtp_use_tls (default True).

        Sprint 5.30: This method is called from a background thread,
        so it does not block the main event loop or calling code.
        """
        import smtplib
        from email.mime.text import MIMEText

        subject = f"[AIP Brain] {alert.severity.upper()}: {alert.subject}"
        body = (
            f"Alert Type: {alert.alert_type}\n"
            f"Severity: {alert.severity}\n"
            f"Subject: {alert.subject}\n"
            f"Time: {alert.timestamp}\n\n"
            f"{alert.message}\n\n"
            f"Data:\n{json.dumps(alert.data, indent=2)}\n"
        )

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = self._config.email_from
        msg["To"] = self._config.email_to

        with smtplib.SMTP(self._config.smtp_host, self._config.smtp_port, timeout=10) as smtp:
            # SMTP EHLO is required before STARTTLS
            smtp.ehlo()

            if self._config.smtp_use_tls:
                smtp.starttls()
                smtp.ehlo()  # Re-identify after TLS upgrade

            # Authenticate if credentials are provided
            if self._config.smtp_username:
                # Environment variable takes precedence over TOML config.
                # This ensures operators can override secrets without editing
                # the config file (credential sovereignty: no secrets in TOML).
                import os

                password = os.environ.get("AIP_SMTP_PASSWORD") or self._config.smtp_password
                if password:
                    smtp.login(self._config.smtp_username, password)

            smtp.send_message(msg)

    def _record_failure(self, failure: DeliveryFailure) -> None:
        """Record a delivery failure in the history.

        Sprint 5.62: Delegates to AlertLifecycleManager.
        """
        self._lifecycle_mgr.record_failure(failure)

    # -----------------------------------------------------------------------
    # Sprint 5.62: Public facade methods (backward-compat wrappers removed)
    # These methods remain as they are part of the documented public API.
    # -----------------------------------------------------------------------

    # -- ThrottleManager facades --------------------------------------------

    def get_circuit_breaker_status(self) -> dict[str, Any]:
        """Return circuit-breaker status and metrics.

        Sprint 5.62: Public facade — delegates to ThrottleManager.
        """
        return self._throttle_mgr.get_circuit_breaker_status()

    def compute_cb_auto_tune_threshold(self) -> int:
        """Compute the auto-tune circuit-breaker threshold.

        Sprint 5.62: Public facade — delegates to ThrottleManager.
        """
        return self._throttle_mgr.compute_cb_auto_tune_threshold()

    def get_cb_effective_threshold(self) -> int:
        """Return the effective circuit-breaker threshold.

        Sprint 5.62: Public facade — delegates to ThrottleManager.
        """
        return self._throttle_mgr.get_cb_effective_threshold()

    def update_cb_auto_tune(self) -> dict:
        """Update the circuit-breaker auto-tune parameters.

        Sprint 5.62: Public facade — delegates to ThrottleManager.
        """
        return self._throttle_mgr.update_cb_auto_tune()

    def get_cb_auto_tune_status(self) -> dict:
        """Return circuit-breaker auto-tune status.

        Sprint 5.62: Public facade — delegates to ThrottleManager.
        """
        return self._throttle_mgr.get_cb_auto_tune_status()

    # -- PredictionManager facades ------------------------------------------

    def predict_causal_chain(self, alert: Alert) -> list[dict]:
        """Predict the causal chain of alerts following *alert*.

        Sprint 5.62: Public facade — delegates to PredictionManager.
        """
        return self._prediction_mgr.predict_causal_chain(alert)

    def predict_causal_chain_learned(self, alert: Alert) -> list[dict]:
        """Predict the causal chain using the learned transition model.

        Sprint 5.62: Public facade — delegates to PredictionManager.
        """
        return self._prediction_mgr.predict_causal_chain_learned(alert)

    def get_transition_probabilities(self, from_type: str | None = None) -> dict[str, Any]:
        """Return transition probabilities from the learned model.

        Sprint 5.62: Public facade — delegates to PredictionManager.
        """
        return self._prediction_mgr.get_transition_probabilities(from_type)

    def record_prediction_outcome(self, alert: Alert) -> None:
        """Record whether a previous prediction materialized.

        Sprint 5.62: Public facade — delegates to PredictionManager.
        """
        return self._prediction_mgr.record_prediction_outcome(alert)

    def get_prediction_accuracy(self) -> dict[str, Any]:
        """Return prediction accuracy metrics.

        Sprint 5.62: Public facade — delegates to PredictionManager.
        """
        return self._prediction_mgr.get_prediction_accuracy()

    def get_causal_predictions(self, subject: str | None = None) -> dict[str, list[dict]] | list[dict]:
        """Return current causal predictions, optionally filtered by subject.

        Sprint 5.62: Public facade — delegates to PredictionManager.
        """
        return self._prediction_mgr.get_causal_predictions(subject)

    def retrain_transition_model(self) -> dict:
        """Retrain the learned transition model.

        Sprint 5.62: Public facade — delegates to PredictionManager.
        """
        return self._prediction_mgr.retrain_transition_model()

    def check_retrain_needed(self) -> bool:
        """Check whether the transition model should be retrained.

        Sprint 5.62: Public facade — delegates to PredictionManager.
        """
        return self._prediction_mgr.check_retrain_needed()

    def persist_transition_model(self) -> bool:
        """Persist the transition model to the alert history store.

        Sprint 5.62: Public facade — delegates to PredictionManager.
        """
        return self._prediction_mgr.persist_transition_model()

    def load_transition_model(self) -> bool:
        """Load the transition model from the alert history store.

        Sprint 5.62: Public facade — delegates to PredictionManager.
        """
        return self._prediction_mgr.load_transition_model()

    # -- DeliveryManager facades --------------------------------------------

    def _record_delivery_receipts(
        self,
        correlation_id: str,
        transport_results: dict[str, dict[str, Any]],
    ) -> None:
        """Record delivery receipts from transport results.

        Sprint 5.62: Public facade — delegates to DeliveryManager.
        """
        self._delivery_mgr.record_delivery_receipts(correlation_id, transport_results, config=self._config)

    def get_delivery_receipts(self, correlation_id: str) -> dict[str, Any]:
        """Return delivery receipts for a given correlation ID.

        Sprint 5.62: Public facade — delegates to DeliveryManager.
        """
        return self._delivery_mgr.get_delivery_receipts(correlation_id)

    def get_all_delivery_receipts(self, limit: int = 50) -> dict[str, dict[str, Any]]:
        """Return all delivery receipts, limited to most recent.

        Sprint 5.62: Public facade — delegates to DeliveryManager.
        """
        return self._delivery_mgr.get_all_delivery_receipts(limit)

    # -- RealtimeEventBus facades -------------------------------------------

    def compress_ws_message(self, data: str) -> tuple[str, bool]:
        """Compress a WebSocket message using zlib deflate.

        Sprint 5.62: Public facade — delegates to RealtimeEventBus.
        """
        return self._realtime_bus.compress_ws_message(data)

    def decompress_ws_message(self, compressed_b64: str) -> str:
        """Decompress a base64-encoded zlib-compressed WebSocket message.

        Sprint 5.62: Public facade — delegates to RealtimeEventBus.
        """
        return self._realtime_bus.decompress_ws_message(compressed_b64)

    def get_compression_status(self) -> dict[str, Any]:
        """Return WebSocket compression status and metrics.

        Sprint 5.62: Public facade — delegates to RealtimeEventBus.
        """
        return self._realtime_bus.get_compression_status()

    # -----------------------------------------------------------------------
    # Sprint 5.39: Delivery receipt polling
    # -----------------------------------------------------------------------

    def start_receipt_polling(self) -> None:
        """Start the background receipt polling thread.

        Sprint 5.39: Launches a daemon thread that periodically polls
        for email delivery status updates when polling is enabled.
        """
        if not self._config.delivery_receipt_polling_enabled:
            return
        if self._receipt_polling_running:
            return

        self._receipt_polling_running = True

        def _poll_loop() -> None:
            while self._receipt_polling_running:
                try:
                    self.poll_email_delivery_status()
                except Exception as exc:
                    logger.debug(
                        "receipt_poll_error",
                        error=str(exc),
                    )
                interval = self._config.delivery_receipt_poll_interval_seconds
                if interval <= 0:
                    interval = 300
                # Sleep in small increments for responsiveness
                elapsed = 0
                while elapsed < interval and self._receipt_polling_running:
                    time.sleep(1)
                    elapsed += 1

        self._receipt_polling_thread = threading.Thread(
            target=_poll_loop,
            daemon=True,
            name="aip-receipt-polling",
        )
        self._receipt_polling_thread.start()
        logger.info(
            "receipt_polling_started",
            interval_seconds=self._config.delivery_receipt_poll_interval_seconds,
        )

    def stop_receipt_polling(self) -> None:
        """Stop the receipt polling thread.

        Sprint 5.39: Sets the running flag to False, causing the
        polling loop to exit on its next iteration.
        """
        self._receipt_polling_running = False
        if self._receipt_polling_thread is not None:
            self._receipt_polling_thread.join(timeout=5)
            self._receipt_polling_thread = None
        logger.info("receipt_polling_stopped")

    def poll_email_delivery_status(self) -> dict:
        """Poll for email delivery status updates.

        Sprint 5.39: For each correlation_id that has email delivery
        but no confirmed read/delivery status, checks the webhook URL
        for updates. Updates internal tracking and returns a summary.

        If email_delivery_webhook_url is configured, POSTs to it with
        the correlation_ids to check. Otherwise, marks email as "sent"
        for tracking purposes.

        Returns a dict summarizing the polling results.
        """
        if not self._config.delivery_receipt_polling_enabled:
            return {"polled": 0, "updated": 0}

        self._total_receipt_polls += 1
        updated = 0
        polled = 0

        with self._lock:
            # Find correlation IDs with email channel that need status updates
            pending_cids = []
            for cid, receipts in self._delivery_mgr.iter_delivery_receipts():
                if "email" in receipts:
                    email_receipt = receipts["email"]
                    current_status = email_receipt.get("delivery_status", "unknown")
                    # Only poll for emails that are "sent" or "delivered" but not yet "read"
                    if current_status in ("sent", "delivered", "unknown"):
                        pending_cids.append(cid)

        if not pending_cids:
            return {"polled": 0, "updated": 0}

        # If webhook URL is configured, poll it for status updates
        if self._config.email_delivery_webhook_url:
            try:
                payload = json.dumps(
                    {
                        "action": "check_delivery_status",
                        "correlation_ids": pending_cids,
                    }
                ).encode("utf-8")

                req = urllib.request.Request(
                    self._config.email_delivery_webhook_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )

                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        body = resp.read().decode("utf-8")
                        try:
                            results = json.loads(body)
                            if isinstance(results, dict):
                                statuses = results.get("statuses", {})
                                for cid, status_info in statuses.items():
                                    if isinstance(status_info, dict):
                                        self.update_email_delivery_status(
                                            cid,
                                            status_info.get("status", "unknown"),
                                            status_info,
                                        )
                                        updated += 1
                        except (json.JSONDecodeError, TypeError):
                            pass

                polled = len(pending_cids)
            except Exception as exc:
                logger.debug(
                    "email_delivery_poll_failed",
                    error=str(exc),
                )
                polled = len(pending_cids)
        else:
            # No webhook URL — mark as "sent" for tracking if not already tracked
            for cid in pending_cids:
                with self._lock:
                    if cid not in self._email_delivery_statuses:
                        self._email_delivery_statuses[cid] = {
                            "email": {
                                "status": "sent",
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                            }
                        }
                        updated += 1
                polled += 1

        if updated > 0:
            self._total_email_status_updates += updated
            logger.debug(
                "email_delivery_poll_updated",
                polled=polled,
                updated=updated,
            )

        return {"polled": polled, "updated": updated}

    def update_email_delivery_status(self, correlation_id: str, status: str, details: dict[str, Any]) -> None:
        """Update the email delivery status for a correlation ID.

        Sprint 5.39: Merges new status information with existing
        delivery receipt data. Tracks status progression:
        sent → delivered → read (or bounced/failed).
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._lock:
            # Update email delivery statuses
            if correlation_id not in self._email_delivery_statuses:
                self._email_delivery_statuses[correlation_id] = {}

            self._email_delivery_statuses[correlation_id]["email"] = {
                "status": status,
                "updated_at": now_iso,
                **{k: v for k, v in details.items() if k != "status"},
            }

            # Also merge into delivery receipts if that channel exists
            self._delivery_mgr.update_email_delivery_status(
                correlation_id,
                {
                    "delivery_status": status,
                    "email_poll_updated_at": now_iso,
                },
            )

        self._total_email_status_updates += 1
        logger.debug(
            "email_delivery_status_updated",
            correlation_id=correlation_id,
            status=status,
        )

    def get_enhanced_delivery_receipts(self, correlation_id: str) -> dict[str, Any]:
        """Return delivery receipts with email polling status merged.

        Sprint 5.60: Delegates to DeliveryManager with email status merge.
        """
        with self._lock:
            receipts = dict(self._delivery_mgr.get_delivery_receipts(correlation_id))

            # Merge email polling status
            email_status = self._email_delivery_statuses.get(correlation_id, {})
            if "email" in email_status:
                if "email" in receipts:
                    receipts["email"].update(email_status["email"])
                else:
                    receipts["email"] = dict(email_status["email"])

            return receipts

    def get_delivery_polling_status(self) -> dict[str, Any]:
        """Return delivery receipt polling status and metrics.

        Sprint 5.39: For dashboard and API visibility into the
        polling subsystem.
        """
        with self._lock:
            return {
                "enabled": self._config.delivery_receipt_polling_enabled,
                "poll_interval_seconds": self._config.delivery_receipt_poll_interval_seconds,
                "email_read_tracking_enabled": self._config.email_read_tracking_enabled,
                "webhook_configured": bool(self._config.email_delivery_webhook_url),
                "total_polls": self._total_receipt_polls,
                "total_email_status_updates": self._total_email_status_updates,
                "tracked_email_count": len(self._email_delivery_statuses),
                "polling_active": self._receipt_polling_running,
            }

    # -- RealtimeEventBus facades (native deflate) ------------------------

    def set_ws_permessage_deflate_negotiated(self, negotiated: bool) -> None:
        """Set whether native permessage-deflate was negotiated.

        Sprint 5.62: Public facade — delegates to RealtimeEventBus.
        """
        self._realtime_bus.set_ws_permessage_deflate_negotiated(negotiated)

    def compress_ws_message_native_aware(self, data: str) -> tuple[str, bool]:
        """Compress a WebSocket message with native deflate awareness.

        Sprint 5.62: Public facade — delegates to RealtimeEventBus.
        """
        return self._realtime_bus.compress_ws_message_native_aware(data)

    def decompress_ws_message_native_aware(self, data: str) -> str:
        """Decompress a WebSocket message with native deflate awareness.

        Sprint 5.62: Public facade — delegates to RealtimeEventBus.
        """
        return self._realtime_bus.decompress_ws_message_native_aware(data)

    def get_native_deflate_status(self) -> dict[str, Any]:
        """Return native permessage-deflate status and metrics.

        Sprint 5.62: Public facade — delegates to RealtimeEventBus and
        adds the ``mode`` field for backward compatibility with API
        consumers that expect it.
        """
        result = self._realtime_bus.get_native_deflate_status()
        # Add computed 'mode' field for API compatibility
        native_enabled = result.get("enabled", False)
        native_negotiated = result.get("native_negotiated", False)
        if not native_enabled and not self._config.ws_compression_enabled:
            mode = "disabled"
        elif native_enabled and native_negotiated:
            mode = "native"
        elif native_enabled and not native_negotiated:
            mode = "fallback_application_level"
        else:
            mode = "application_level"
        result["mode"] = mode
        result["native_enabled"] = native_enabled
        return result

    # -- ABExperimentManager facades -----------------------------------------

    # Sprint 5.63: AB experiment facade methods
    # Only the highest-usage methods are exposed as facades.  For less
    # common operations, use ``alert_manager.ab_experiment_mgr.<method>()``
    # directly.

    def start_ab_experiment(
        self,
        name: str,
        control_config: dict[str, Any],
        variant_config: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a new A/B experiment.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        Called from API endpoints and integration tests.
        """
        return self._ab_experiment_mgr.start_ab_experiment(
            name,
            control_config,
            variant_config,
            metadata,
        )

    def stop_ab_experiment(self, name: str, result: str | None = None) -> dict[str, Any] | None:
        """Stop a running A/B experiment.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.stop_ab_experiment(name, result)

    def get_ab_experiments(self, status: str | None = None) -> list[dict[str, Any]]:
        """Return all A/B experiments, optionally filtered by status.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_ab_experiments(status)

    def get_ab_experiment(self, name: str) -> dict[str, Any] | None:
        """Return a single A/B experiment by name.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_ab_experiment(name)

    def record_ab_result(
        self,
        name: str,
        variant: str,
        accuracy: float,
        samples: int = 1,
    ) -> dict[str, Any] | None:
        """Record a result for an A/B experiment variant.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.record_ab_result(name, variant, accuracy, samples)

    def check_promotion_rollback(self) -> list[dict[str, Any]]:
        """Check running experiments for promotion rollback conditions.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.check_promotion_rollback()

    def start_ab_promotion_checker(self) -> None:
        """Start the auto-promotion checker background thread.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        Called from app.py on startup.
        """
        self._ab_experiment_mgr.start_ab_promotion_checker()

    def stop_ab_promotion_checker(self) -> None:
        """Stop the auto-promotion checker background thread.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        """
        self._ab_experiment_mgr.stop_ab_promotion_checker()

    def start_ab_cleanup_checker(self) -> None:
        """Start the A/B experiment cleanup checker background thread.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        """
        self._ab_experiment_mgr.start_ab_cleanup_checker()

    def stop_ab_cleanup_checker(self) -> None:
        """Stop the A/B experiment cleanup checker.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        """
        self._ab_experiment_mgr.stop_ab_cleanup_checker()

    def set_live_config_reverter(self, callback: Any) -> None:
        """Set the live config reversion callback.

        Sprint 5.62: Public facade — delegates to ABExperimentManager.
        Called from app.py on startup.
        """
        self._ab_experiment_mgr.set_live_config_reverter(callback)

    def set_auto_tuning_reverter(self, callback: Any) -> None:
        """Set the auto-tuning reversion callback.

        Sprint 5.62: Public facade — delegates to ABExperimentManager.
        Called from app.py on startup.
        """
        self._ab_experiment_mgr.set_auto_tuning_reverter(callback)

    def restore_confidence_calibration(self, store: Any) -> int:
        """Restore confidence calibration data from persistent store.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        Called from app.py on startup.
        """
        return self._ab_experiment_mgr.restore_confidence_calibration(store)

    def restore_pre_promotion_snapshots(self, store: Any) -> int:
        """Restore pre-promotion config snapshots from persistent store.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        Called from app.py on startup.
        """
        return self._ab_experiment_mgr.restore_pre_promotion_snapshots(store)

    def start_snapshot_gc(self) -> None:
        """Start the snapshot garbage collector background thread.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        Called from app.py on startup.
        """
        self._ab_experiment_mgr.start_snapshot_gc()

    def check_calibration_drift(self) -> list[dict]:
        """Check for confidence calibration drift.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        Called from app.py on startup.
        """
        return self._ab_experiment_mgr.check_calibration_drift()

    def get_bandit_allocation(self, name: str) -> dict[str, float]:
        """Return current bandit allocation for an experiment.

        Sprint 5.63: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_bandit_allocation(name)

    # -------------------------------------------------------------------
    # Sprint 5.46/5.47: AB Experiment public facade delegates
    # -------------------------------------------------------------------

    def promote_variant(self, name: str, variant: str = "variant") -> dict[str, Any] | None:
        """Promote a variant in an A/B experiment.

        Sprint 5.46: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.promote_variant(name, variant)

    def cleanup_expired_experiments(self) -> dict[str, int]:
        """Clean up old/stopped A/B experiments.

        Sprint 5.46: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.cleanup_expired_experiments()

    def get_ab_cleanup_status(self) -> dict[str, Any]:
        """Return the status of the A/B experiment cleanup checker.

        Sprint 5.46: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_ab_cleanup_status()

    def notify_decay_event(self, subject: str, decay_amount: float, current_confidence: float) -> str:
        """Record and notify a significant confidence decay event.

        Sprint 5.46: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.notify_decay_event(subject, decay_amount, current_confidence)

    def get_decay_events(self, limit: int = 50) -> list[dict]:
        """Return recent decay events.

        Sprint 5.46: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_decay_events(limit)

    def get_decay_recovery_status(self) -> dict[str, Any]:
        """Return the status of decay recovery.

        Sprint 5.46: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_decay_recovery_status()

    def restore_ab_experiments_from_store(self) -> int:
        """Restore A/B experiment state from the persistent store.

        Sprint 5.46: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.restore_ab_experiments_from_store()

    def persist_all_ab_experiments(self) -> int:
        """Persist all running A/B experiments and stop background checkers.

        Sprint 5.46: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.persist_all_ab_experiments()

    def get_experiment_monitoring_summary(self) -> dict[str, Any]:
        """Return a comprehensive experiment monitoring summary.

        Sprint 5.46: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_experiment_monitoring_summary()

    def get_promotion_rollback_status(self) -> dict[str, Any]:
        """Return the status of promotion rollback.

        Sprint 5.46: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_promotion_rollback_status()

    def _check_auto_promotion(self) -> None:
        """Check all running experiments for auto-promotion eligibility.

        Sprint 5.46: Internal facade — delegates to ABExperimentManager.check_auto_promotion.
        """
        self._ab_experiment_mgr.check_auto_promotion()

    def get_ab_promotion_checker_status(self) -> dict[str, Any]:
        """Return the status of the auto-promotion checker.

        Sprint 5.46: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_ab_promotion_checker_status()

    def run_decay_recovery_orchestrator(self) -> list[dict[str, Any]]:
        """Run the decay recovery orchestrator.

        Sprint 5.46: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.run_decay_recovery_orchestrator()

    # -------------------------------------------------------------------
    # Sprint 5.47: Statistical significance & calibration facade delegates
    # -------------------------------------------------------------------

    def compute_statistical_significance(self, name: str) -> dict[str, Any] | None:
        """Compute statistical significance for an A/B experiment.

        Sprint 5.47: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.compute_statistical_significance(name)

    def get_statistical_significance_status(self) -> dict[str, Any]:
        """Return the status of statistical significance testing.

        Sprint 5.47: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_statistical_significance_status()

    def update_confidence_calibration(
        self, subject: str, observed_accuracy: float, predicted_confidence: float
    ) -> float:
        """Update confidence calibration mapping from A/B experiment results.

        Sprint 5.47: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.update_confidence_calibration(subject, observed_accuracy, predicted_confidence)

    def get_calibrated_confidence(self, subject: str, raw_confidence: float) -> float:
        """Apply confidence calibration to a raw confidence value.

        Sprint 5.47: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_calibrated_confidence(subject, raw_confidence)

    def get_config_reversion_status(self) -> dict[str, Any]:
        """Return the status of config reversion for rollback.

        Sprint 5.47: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_config_reversion_status()

    def get_cleanup_metrics(self) -> dict[str, Any]:
        """Return cumulative cleanup metrics.

        Sprint 5.47: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_cleanup_metrics()

    def get_confidence_calibration_status(self) -> dict[str, Any]:
        """Return the status of confidence calibration.

        Sprint 5.47: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_confidence_calibration_status()

    @staticmethod
    def _normal_cdf(x: float) -> float:
        """Approximate the standard normal CDF.

        Sprint 5.47: Static helper — delegates to ABExperimentManager.normal_cdf.
        Used by statistical significance tests.
        """
        return ABExperimentManager.normal_cdf(x)

    # ------------------------------------------------------------------
    # Sprint 5.48/5.49: Bandit, persistence, and accuracy facade methods
    # ------------------------------------------------------------------

    def _get_auto_tuning_snapshot(self) -> dict[str, Any]:
        """Capture auto-tuning policy snapshot for rollback.

        Sprint 5.48: Internal facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_auto_tuning_snapshot()

    def record_accuracy_snapshot(self, name: str) -> dict[str, Any] | None:
        """Record an accuracy snapshot for time-series visualization.

        Sprint 5.48: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.record_accuracy_snapshot(name)

    def get_accuracy_timeseries(self, name: str) -> list[dict[str, Any]]:
        """Return accuracy time-series data for an experiment.

        Sprint 5.48: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_accuracy_timeseries(name)

    def get_bandit_status(self) -> dict[str, Any]:
        """Return the status of multi-armed bandit allocation.

        Sprint 5.48: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_bandit_status()

    def get_rollback_dry_run_status(self) -> dict[str, Any]:
        """Return the status of rollback dry-run mode.

        Sprint 5.48: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_rollback_dry_run_status()

    def persist_statistical_test_results(self, store: Any = None) -> int:
        """Persist in-memory statistical test results to the history store.

        Sprint 5.48: Public facade — delegates to ABExperimentManager.
        If *store* is omitted, the manager's attached history store is used.
        """
        target = store if store is not None else self._ab_experiment_mgr._history_store
        return self._ab_experiment_mgr.persist_statistical_test_results(target)

    def persist_confidence_calibration(self, store: Any = None) -> int:
        """Persist confidence calibration data to the history store.

        Sprint 5.49: Public facade — delegates to ABExperimentManager.
        If *store* is omitted, the manager's attached history store is used.
        """
        target = store if store is not None else self._ab_experiment_mgr._history_store
        return self._ab_experiment_mgr.persist_confidence_calibration(target)

    def persist_pre_promotion_snapshots(self, store: Any = None) -> int:
        """Persist pre-promotion config snapshots to the history store.

        Sprint 5.49: Public facade — delegates to ABExperimentManager.
        If *store* is omitted, the manager's attached history store is used.
        """
        target = store if store is not None else self._ab_experiment_mgr._history_store
        return self._ab_experiment_mgr.persist_pre_promotion_snapshots(target)

    def record_bandit_context_reward(
        self, name: str, variant: str, reward: float, context: dict[str, str] | None = None
    ) -> None:
        """Record a reward observation for contextual bandit learning.

        Sprint 5.49: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.record_bandit_context_reward(name, variant, reward, context)

    # ------------------------------------------------------------------
    # Sprint 5.48/5.49: Property proxies for sub-manager state
    # ------------------------------------------------------------------

    @property
    def _confidence_calibration_map(self) -> dict[str, float]:
        """Proxy to ABExperimentManager's calibration map."""
        return self._ab_experiment_mgr._confidence_calibration_map

    @_confidence_calibration_map.setter
    def _confidence_calibration_map(self, value: dict[str, float]) -> None:
        self._ab_experiment_mgr._confidence_calibration_map = value

    @property
    def _pre_promotion_config_snapshots(self) -> dict[str, Any]:
        """Proxy to ABExperimentManager's pre-promotion snapshots."""
        return self._ab_experiment_mgr._pre_promotion_config_snapshots

    @_pre_promotion_config_snapshots.setter
    def _pre_promotion_config_snapshots(self, value: dict[str, Any]) -> None:
        self._ab_experiment_mgr._pre_promotion_config_snapshots = value

    @property
    def _last_accuracy_snapshot_time(self) -> float:
        """Proxy to ABExperimentManager's last accuracy snapshot timestamp."""
        return self._ab_experiment_mgr._last_accuracy_snapshot_time

    @_last_accuracy_snapshot_time.setter
    def _last_accuracy_snapshot_time(self, value: float) -> None:
        self._ab_experiment_mgr._last_accuracy_snapshot_time = value

    # ------------------------------------------------------------------
    # Sprint 5.50: Observability, adaptability, and data hygiene facades
    # ------------------------------------------------------------------

    def replay_bandit_decisions(self, experiment_name: str, limit: int = 50) -> list[dict]:
        """Replay bandit decisions for an experiment from the history store.

        Sprint 5.50: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.replay_bandit_decisions(experiment_name, limit)

    def get_experiment_event_timeline(self, **kwargs) -> list[dict]:
        """Return a unified event timeline from the history store.

        Sprint 5.50: Public facade — delegates to ABExperimentManager
        which in turn queries the SyncAlertHistoryBridge.
        """
        return self._ab_experiment_mgr.get_experiment_event_timeline(**kwargs)

    def _select_adaptive_bandit_method(
        self, name: str, c_samples: int, v_samples: int, c_acc: float, v_acc: float
    ) -> str:
        """Select the best bandit method based on experiment characteristics.

        Sprint 5.50: Internal facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.select_adaptive_bandit_method(name, c_samples, v_samples, c_acc, v_acc)

    def _run_snapshot_gc(self) -> int:
        """Run snapshot garbage collection to clean up stale data.

        Sprint 5.50: Internal facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.run_snapshot_gc()

    def get_snapshot_gc_status(self) -> dict[str, Any]:
        """Return the status of snapshot garbage collection.

        Sprint 5.50: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_snapshot_gc_status()

    def get_calibration_drift_status(self) -> dict[str, Any]:
        """Return the status of calibration drift detection.

        Sprint 5.50: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_calibration_drift_status()

    def get_adaptive_bandit_status(self) -> dict[str, Any]:
        """Return the status of adaptive bandit method selection.

        Sprint 5.50: Public facade — delegates to ABExperimentManager.
        """
        return self._ab_experiment_mgr.get_adaptive_bandit_status()

    @property
    def _total_adaptive_method_switches(self) -> int:
        """Proxy to ABExperimentManager's adaptive method switch count."""
        return self._ab_experiment_mgr._total_adaptive_method_switches

    @_total_adaptive_method_switches.setter
    def _total_adaptive_method_switches(self, value: int) -> None:
        self._ab_experiment_mgr._total_adaptive_method_switches = value

    @property
    def ab_experiment_mgr(self) -> "ABExperimentManager":
        """The A/B experiment sub-manager.

        Sprint 5.62: Direct access to the sub-manager replaces the
        removed backward-compat property proxies and method delegations.
        Callers should use alert_manager.ab_experiment_mgr.<method>()
        instead of alert_manager.<method>() for operations not exposed
        as facade methods.
        """
        return self._ab_experiment_mgr
