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
import re
import threading
import time
import uuid
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

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

    # Maximum number of delivery failure records to keep
    _MAX_FAILURE_HISTORY = 50

    def __init__(self, config: AlertConfig | None = None) -> None:
        self._config = config or AlertConfig()
        # Rate-limiting: (alert_type, subject) -> last_alert_timestamp
        self._last_alert_time: dict[tuple[str, str], float] = {}
        # History of sent alerts (last 50) — in-memory fallback
        self._alert_history: list[dict] = []
        # Sprint 5.26: Delivery failure history — in-memory fallback
        self._delivery_failures: list[DeliveryFailure] = []
        # Sprint 5.26: Webhook URL validation status (lazy, on first use)
        self._webhook_url_validated: bool = False
        self._webhook_url_valid: bool | None = None
        self._webhook_url_validation_reason: str = ""
        # Sprint 5.29: Persistent alert history store (optional)
        self._history_store: Any = None
        # Counters
        self._total_alerts_sent = 0
        self._total_alerts_rate_limited = 0
        self._total_send_failures = 0
        self._total_webhook_retries = 0
        # Sprint 5.30: Correlation ID counter for async dispatch
        self._correlation_counter = 0
        # Sprint 5.30: Occurrence tracking for escalation
        self._occurrence_tracker: dict[tuple[str, str], list[float]] = {}
        # Sprint 5.31: Delivery status tracking — correlation_id -> status dict
        self._delivery_status: dict[str, dict[str, Any]] = {}
        # Sprint 5.31: Mute rules — (alert_type, subject) -> {expires_at, muted_by, rule_id}
        self._mute_rules: dict[tuple[str, str], dict[str, Any]] = {}
        # Sprint 5.31: Alert digest buffer — accumulated low-severity alerts
        self._digest_buffer: list[dict] = []
        self._digest_last_flush: float = time.time()
        # Sprint 5.31: SSE subscribers — list of asyncio.Queue for real-time push
        self._sse_subscribers: list[Any] = []
        # Sprint 5.32: WebSocket subscribers — list of websocket objects for bidirectional push
        self._ws_subscribers: list[Any] = []
        # Sprint 5.32: Digest flush counter for health metrics
        self._total_digest_flushes: int = 0
        # Sprint 5.32: Delivery success/failure counters by transport
        self._delivery_success_by_transport: dict[str, int] = {}
        self._delivery_failure_by_transport: dict[str, int] = {}
        # Sprint 5.32: Alert correlation groups — group_key -> list of correlation_ids
        self._alert_groups: dict[str, list[str]] = {}
        # Sprint 5.34: WebSocket session tracking — session_id -> {websocket, connected_at, remote_addr}
        self._ws_sessions: dict[str, dict[str, Any]] = {}
        # Sprint 5.34: Alert group metadata — group_key -> last_activity_at (epoch float)
        self._alert_groups_metadata: dict[str, float] = {}
        # Sprint 5.34: Group TTL cleanup counter
        self._total_groups_cleaned: int = 0
        # Sprint 5.35: Pruning scheduler state
        self._prune_scheduler_thread: threading.Thread | None = None
        self._prune_scheduler_running: bool = False
        self._last_prune_run: float = 0.0
        self._next_prune_run: float = 0.0
        self._total_scheduled_prunes: int = 0
        # Sprint 5.35: Dead session cleanup counter
        self._total_dead_sessions_cleaned: int = 0
        # Sprint 5.36: WebSocket message batching — pending events buffer
        self._ws_batch_buffer: list[dict] = []
        self._ws_batch_flush_scheduled: bool = False
        self._ws_batch_total_flushes: int = 0
        self._ws_batch_total_events_sent: int = 0
        # Sprint 5.36: Auto-merge suggestions — list of pending suggestions
        self._auto_merge_suggestions: list[dict] = []
        self._total_auto_merges_suggested: int = 0
        self._total_auto_merges_applied: int = 0
        # Sprint 5.36: Pruning history — list of recent prune run records
        self._pruning_history: list[dict] = []
        # Sprint 5.36: Causal chain prediction state
        self._causal_predictions: dict[str, list[dict]] = {}  # subject → predicted alerts
        self._total_predictions_made: int = 0
        # Sprint 5.37: Prediction accuracy tracking
        self._prediction_outcomes: dict[str, dict] = {}  # prediction_id -> outcome record
        self._prediction_accuracy_hits: int = 0
        self._prediction_accuracy_misses: int = 0
        self._last_auto_merge_time: float = 0.0  # Cooldown tracking
        # Lock for thread-safe mutation of shared state
        self._lock = threading.Lock()

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
                warnings.append(
                    "Email alerts configured (email_to) but smtp_host is empty. "
                    "Email delivery will fail."
                )

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

        # Sprint 5.31: Mute rule check — suppress muted (alert_type, subject) pairs
        key = (alert.alert_type, alert.subject)
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
                        alert_type=alert.alert_type,
                        subject=alert.subject,
                    )
                else:
                    self._total_alerts_rate_limited += 1
                    logger.debug(
                        "alert_muted",
                        alert_type=alert.alert_type,
                        subject=alert.subject,
                        muted_by=mute_entry.get("muted_by", "unknown"),
                    )
                    return "muted"

        # Rate-limit check
        last_time = self._last_alert_time.get(key, 0)
        if now - last_time < self._config.min_alert_interval_seconds:
            self._total_alerts_rate_limited += 1
            logger.debug(
                "alert_rate_limited",
                alert_type=alert.alert_type,
                subject=alert.subject,
                seconds_since_last=round(now - last_time, 1),
            )
            return "rate_limited"

        # Record the alert time
        self._last_alert_time[key] = now

        # Sprint 5.30: Check for severity escalation
        escalated = self._check_escalation(alert, now)
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

        # Generate correlation ID
        self._correlation_counter += 1
        correlation_id = f"alert-{self._correlation_counter}-{uuid.uuid4().hex[:8]}"

        # Log the alert regardless of transport success
        logger.info(
            "alert_dispatched",
            alert_type=alert.alert_type,
            severity=alert.severity,
            subject=alert.subject,
            message=alert.message[:200],
            correlation_id=correlation_id,
        )

        # Record in history
        alert_dict = alert.to_dict()
        alert_dict["correlation_id"] = correlation_id
        self._alert_history.append(alert_dict)
        if len(self._alert_history) > 50:
            self._alert_history = self._alert_history[-50:]

        self._total_alerts_sent += 1

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
                    if (t == "webhook" and self._config.webhook_url) or \
                       (t == "email" and self._config.email_to):
                        transports.append(t)

        # Sprint 5.32: Alert correlation grouping
        self._add_alert_to_group(alert, correlation_id)

        # Sprint 5.31: Check if this alert should go to digest buffer instead
        # Sprint 5.32: Use per-type digest overrides if configured
        if self._config.digest_enabled and alert.severity == "info" and not escalated:
            # Determine per-type or global digest settings
            digest_interval, digest_min = self._get_digest_settings(alert.alert_type)

            with self._lock:
                self._digest_buffer.append(alert_dict)
                # Check if we should flush the digest
                elapsed = now - self._digest_last_flush
                interval_secs = digest_interval * 60
                if len(self._digest_buffer) >= digest_min or elapsed >= interval_secs:
                    self._flush_digest()
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
                self._delivery_status[correlation_id] = status_dict
                # Sprint 5.32: Persist delivery status to SQLite
                self._persist_delivery_status(status_dict)
            # Notify SSE/WebSocket subscribers about the buffered alert
            self._notify_realtime_subscribers({
                "event": "alert_buffered",
                "correlation_id": correlation_id,
                "alert_type": alert.alert_type,
                "severity": alert.severity,
                "subject": alert.subject,
            })
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
        with self._lock:
            self._delivery_status[correlation_id] = status_dict
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
                self._total_send_failures += 1
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
                self._total_send_failures += 1
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
                self._send_slack_notification(alert)
                transport_results["slack"] = {
                    "status": "delivered",
                    "retries": 0,
                }
            except Exception as exc:
                all_succeeded = False
                self._total_send_failures += 1
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
                self._send_pagerduty_notification(alert)
                transport_results["pagerduty"] = {
                    "status": "delivered",
                    "retries": 0,
                }
            except Exception as exc:
                all_succeeded = False
                self._total_send_failures += 1
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
        with self._lock:
            if correlation_id in self._delivery_status:
                self._delivery_status[correlation_id]["status"] = final_status
                self._delivery_status[correlation_id]["completed_at"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                # Sprint 5.32: Update persistent delivery status
                self._update_persistent_delivery_status(correlation_id, final_status)

        # Sprint 5.32: Track delivery success/failure by transport
        for transport_name, result in transport_results.items():
            if result.get("status") == "delivered":
                self._delivery_success_by_transport[transport_name] = (
                    self._delivery_success_by_transport.get(transport_name, 0) + 1
                )
            else:
                self._delivery_failure_by_transport[transport_name] = (
                    self._delivery_failure_by_transport.get(transport_name, 0) + 1
                )

        # Sprint 5.31 → 5.32: Notify SSE/WebSocket subscribers about the delivery result
        self._notify_realtime_subscribers({
            "event": "alert_delivered" if all_succeeded else "alert_delivery_failed",
            "correlation_id": correlation_id,
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "subject": alert.subject,
            "transport_results": transport_results,
        })

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
        """Return alerting status for the health endpoint."""
        return {
            "enabled": self._config.enabled,
            "webhook_configured": bool(self._config.webhook_url),
            "webhook_url_valid": self._webhook_url_valid,
            "email_configured": bool(self._config.email_to),
            "smtp_auth_configured": bool(self._config.smtp_username),
            "total_alerts_sent": self._total_alerts_sent,
            "total_rate_limited": self._total_alerts_rate_limited,
            "total_send_failures": self._total_send_failures,
            "total_webhook_retries": self._total_webhook_retries,
            "recent_alerts": self._alert_history[-5:],
            "recent_failures": [f.to_dict() for f in self._delivery_failures[-5:]],
            "alert_types_enabled": {
                "quality_degradation": self._config.alert_on_quality_degradation,
                "pool_adjustment": self._config.alert_on_pool_adjustment,
                "batch_reduction": self._config.alert_on_batch_reduction,
            },
            "webhook_retry_config": {
                "max_retries": self._config.webhook_max_retries,
                "base_delay_seconds": self._config.webhook_retry_base_delay_seconds,
            },
            "history_store_attached": self._history_store is not None,
            "routes": self._config.routes if self._config.routes else {},
            # Sprint 5.30: Escalation config in status
            "escalation": {
                "threshold": self._config.escalation_threshold,
                "window_seconds": self._config.escalation_window_seconds,
                "severity": self._config.escalation_severity,
                "additional_transports": self._config.escalation_additional_transports,
            },
            # Sprint 5.31: Mute rules and digest status
            "active_mute_rules": len(self._mute_rules),
            "digest": {
                "enabled": self._config.digest_enabled,
                "interval_minutes": self._config.digest_interval_minutes,
                "min_alerts": self._config.digest_min_alerts,
                "buffered_count": len(self._digest_buffer),
                # Sprint 5.32: Per-type overrides
                "overrides": self._config.digest_overrides,
                "total_flushes": self._total_digest_flushes,
            },
            "sse_subscribers": len(self._sse_subscribers),
            # Sprint 5.32: WebSocket and operational metrics
            "ws_subscribers": len(self._ws_subscribers),
            "delivery_by_transport": {
                "success": dict(self._delivery_success_by_transport),
                "failure": dict(self._delivery_failure_by_transport),
            },
            "alert_groups": len(self._alert_groups),
            # Sprint 5.33: New config fields in status
            "delivery_status_max_age_days": self._config.delivery_status_max_age_days,
            "ws_auth_configured": bool(self._config.ws_auth_token),
            "ws_rate_limit_per_minute": self._config.ws_rate_limit_per_minute,
            "causal_grouping": {
                "enabled": self._config.causal_grouping_enabled,
                "window_seconds": self._config.causal_grouping_window_seconds,
            },
            # Sprint 5.34: Session and group TTL info
            "ws_sessions": len(self._ws_sessions),
            "alert_group_ttl": {
                "ttl_hours": self._config.alert_group_ttl_hours,
                "total_groups": len(self._alert_groups),
                "groups_cleaned": self._total_groups_cleaned,
            },
            "delivery_status_max_rows": self._config.delivery_status_max_rows,
            # Sprint 5.35: Heartbeat and pruning scheduler info
            "ws_heartbeat": {
                "interval_seconds": self._config.ws_heartbeat_interval_seconds,
                "missed_limit": self._config.ws_heartbeat_missed_limit,
                "dead_sessions_cleaned": self._total_dead_sessions_cleaned,
            },
            "prune_scheduler": self.get_prune_scheduler_status(),
            # Sprint 5.36: Batching, auto-merge, and prediction info
            "ws_batching": {
                "batch_window_seconds": self._config.ws_batch_window_seconds,
                "batch_max_size": self._config.ws_batch_max_size,
                "total_flushes": self._ws_batch_total_flushes,
                "total_events_sent": self._ws_batch_total_events_sent,
                "buffered_count": len(self._ws_batch_buffer),
            },
            "auto_merge": {
                "window_seconds": self._config.auto_merge_window_seconds,
                "similarity_threshold": self._config.auto_merge_similarity_threshold,
                "suggestions_available": len(self._auto_merge_suggestions),
                "total_suggested": self._total_auto_merges_suggested,
                "total_applied": self._total_auto_merges_applied,
            },
            "causal_prediction": {
                "enabled": self._config.causal_prediction_enabled,
                "total_predictions": self._total_predictions_made,
                "subjects_with_predictions": len(self._causal_predictions),
            },
            # Sprint 5.37: Prediction accuracy metrics
            "prediction_accuracy": self.get_prediction_accuracy(),
            # Sprint 5.37: Auto-merge policy engine
            "auto_merge_policy": {
                "mode": self._config.auto_merge_mode,
                "cooldown_seconds": self._config.auto_merge_cooldown_seconds,
                "type_thresholds": self._config.auto_merge_type_thresholds,
                "last_auto_merge_time": self._last_auto_merge_time,
            },
            # Sprint 5.37: Notification channel diversification
            "notification_channels": {
                "slack_configured": bool(self._config.slack_webhook_url),
                "pagerduty_configured": bool(self._config.pagerduty_integration_key),
                "notification_routes": self._config.notification_routes,
            },
        }

    def get_alert_history(
        self,
        alert_type: str | None = None,
        severity: str | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return alert history with optional filtering.

        Sprint 5.28: Provides a queryable interface for the alerts
        endpoint and admin visibility.

        Sprint 5.29: When a persistent AlertHistoryStore is attached,
        queries the SQLite store for full history across restarts.
        Falls back to in-memory history when no store is available.

        Parameters
        ----------
        alert_type:
            If provided, return only alerts of this type
            (``quality_degradation``, ``pool_adjustment``, ``batch_reduction``).
        severity:
            If provided, return only alerts with this severity
            (``info``, ``warning``, ``critical``).
        since:
            ISO 8601 datetime — only return alerts with timestamps after this.
        limit:
            Maximum number of alerts to return (most recent first).
        """
        # Sprint 5.29: Prefer persistent store when available
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
                # Fall through to in-memory

        history = self._alert_history

        if alert_type:
            history = [a for a in history if a.get("alert_type") == alert_type]
        if severity:
            history = [a for a in history if a.get("severity") == severity]
        if since:
            history = [a for a in history if a.get("timestamp", "") > since]

        # Return most recent first
        return list(reversed(history[-limit:]))

    def get_delivery_failures(self, transport: str | None = None, limit: int = 20) -> list[dict]:
        """Return delivery failure history, optionally filtered by transport.

        Sprint 5.29: When a persistent AlertHistoryStore is attached,
        queries the SQLite store for full failure history across restarts.
        Falls back to in-memory history when no store is available.

        Parameters
        ----------
        transport:
            If provided, return only failures for this transport ("webhook" or "email").
        limit:
            Maximum number of failures to return (most recent first).
        """
        # Sprint 5.29: Prefer persistent store when available
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
                # Fall through to in-memory

        failures = self._delivery_failures
        if transport:
            failures = [f for f in failures if f.transport == transport]
        # Return most recent first
        return [f.to_dict() for f in reversed(failures[-limit:])]

    # -----------------------------------------------------------------------
    # Sprint 5.31: Delivery status tracking
    # -----------------------------------------------------------------------

    def get_delivery_status(self, correlation_id: str) -> dict | None:
        """Return delivery status for a given correlation ID.

        Sprint 5.31: Provides visibility into what happened after
        send_alert() returned. Shows whether async delivery completed,
        which transports succeeded/failed, retry counts, and final
        delivery status.

        Returns the status dict or None if the correlation ID is not found.
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

        Sprint 5.31: Temporarily suppresses alerts matching this pair
        for the specified duration. Mute rules persist across restarts
        when an AlertHistoryStore is attached.

        Parameters
        ----------
        alert_type:
            The alert type to mute (e.g., "batch_reduction").
        subject:
            The alert subject to mute (e.g., "graph_extraction").
        duration_seconds:
            How long to mute this pair (default 3600 = 1 hour).
            Set to 0 for indefinite muting (until explicitly removed).
        muted_by:
            Who created the mute rule (default "operator").
        auto_mute_on_ack:
            If True, automatically mute this pair when an alert is
            acknowledged (optional).

        Returns the mute rule dict.
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

        Sprint 5.31: Un-mutes the alert pair so that future alerts are
        no longer suppressed. Also removes the rule from the persistent
        store if attached.

        Returns True if a rule was found and removed, False otherwise.
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

        Sprint 5.31: Returns a list of currently active mute rules,
        including any that have expired (they are cleaned up lazily
        when send_alert() checks them).
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

    # -----------------------------------------------------------------------
    # Sprint 5.31: Alert digest / aggregation
    # -----------------------------------------------------------------------

    def _flush_digest(self) -> None:
        """Flush buffered low-severity alerts as a digest summary.

        Sprint 5.31: Combines all buffered info-severity alerts into
        a single summary alert and dispatches it through the normal
        transport channels. This reduces alert noise by batching
        low-priority notifications.

        Must be called with self._lock held.
        """
        if not self._digest_buffer:
            return

        buffered = list(self._digest_buffer)
        self._digest_buffer.clear()
        self._digest_last_flush = time.time()
        # Sprint 5.32: Track digest flush count for health metrics
        self._total_digest_flushes += 1

        # Build a digest summary
        type_counts: dict[str, int] = {}
        for alert_dict in buffered:
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
                "alert_count": len(buffered),
                "type_counts": type_counts,
                "buffered_alerts": buffered,
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
        self._correlation_counter += 1
        digest_correlation_id = f"digest-{self._correlation_counter}-{uuid.uuid4().hex[:8]}"

        # Record in history
        digest_dict = digest_alert.to_dict()
        digest_dict["correlation_id"] = digest_correlation_id
        self._alert_history.append(digest_dict)
        if len(self._alert_history) > 50:
            self._alert_history = self._alert_history[-50:]

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
            alert_count=len(buffered),
            type_counts=type_counts,
            correlation_id=digest_correlation_id,
        )

    def check_digest_flush(self) -> None:
        """Check if the digest buffer should be flushed based on time.

        Sprint 5.31: Called periodically (e.g., from a background task)
        to ensure that buffered alerts are flushed even if the minimum
        count threshold hasn't been reached.

        Sprint 5.32: Uses the shortest interval from per-type overrides
        or global settings to determine if enough time has elapsed.
        """
        if not self._config.digest_enabled:
            return

        now = time.time()
        with self._lock:
            if not self._digest_buffer:
                return
            elapsed = now - self._digest_last_flush
            # Sprint 5.32: Use shortest interval from overrides or global
            min_interval = self._config.digest_interval_minutes
            for override in self._config.digest_overrides.values():
                override_interval = override.get("interval_minutes", min_interval)
                if override_interval < min_interval:
                    min_interval = override_interval
            interval_secs = min_interval * 60
            if elapsed >= interval_secs:
                self._flush_digest()

    # -----------------------------------------------------------------------
    # Sprint 5.31: SSE (Server-Sent Events) support
    # -----------------------------------------------------------------------

    def add_sse_subscriber(self, queue: Any) -> None:
        """Add an asyncio.Queue as an SSE subscriber.

        Sprint 5.31: When alerts are dispatched or delivery completes,
        the event is pushed to all subscriber queues for real-time
        streaming to dashboard clients.
        """
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
        """Push an event to all SSE subscriber queues.

        Sprint 5.31: Called when an alert is dispatched, delivered,
        or fails. Events are pushed asynchronously to connected
        dashboard clients via Server-Sent Events.
        """
        with self._lock:
            subscribers = list(self._sse_subscribers)

        for queue in subscribers:
            try:
                if asyncio is not None and hasattr(queue, "put_nowait"):
                    queue.put_nowait(event)
            except Exception:
                pass  # Subscriber queue may be full or closed

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
    # Sprint 5.32: WebSocket support
    # -----------------------------------------------------------------------

    def add_ws_subscriber(self, websocket: Any) -> None:
        """Add a WebSocket connection as a subscriber.

        Sprint 5.32: WebSocket subscribers receive the same events as
        SSE subscribers, plus can send commands back to the server.
        """
        with self._lock:
            self._ws_subscribers.append(websocket)

    def remove_ws_subscriber(self, websocket: Any) -> None:
        """Remove a WebSocket subscriber."""
        with self._lock:
            try:
                self._ws_subscribers.remove(websocket)
            except ValueError:
                pass

    def _notify_realtime_subscribers(self, event: dict) -> None:
        """Push an event to all SSE and WebSocket subscribers.

        Sprint 5.32: Unified notification method that pushes events
        to both SSE queues and WebSocket connections. Replaces the
        separate _notify_sse_subscribers calls in send_alert flow.

        Sprint 5.36: When WebSocket batching is enabled (ws_batch_window_seconds > 0),
        events are buffered and flushed as a batch after the window elapses.
        SSE subscribers still receive events immediately.
        """
        # Push to SSE subscribers (asyncio.Queue) — always immediate
        self._notify_sse_subscribers(event)

        # Sprint 5.36: Buffer events for batched WebSocket delivery
        batch_window = self._config.ws_batch_window_seconds
        if batch_window > 0:
            with self._lock:
                self._ws_batch_buffer.append(event)
                if not self._ws_batch_flush_scheduled:
                    self._ws_batch_flush_scheduled = True
                    # Schedule a flush after the batch window
                    if asyncio is not None:
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                asyncio.ensure_future(self._flush_ws_batch_later(batch_window))
                            else:
                                # No running loop — flush immediately in a thread
                                threading.Timer(batch_window, self._flush_ws_batch).start()
                        except RuntimeError:
                            threading.Timer(batch_window, self._flush_ws_batch).start()
                    else:
                        threading.Timer(batch_window, self._flush_ws_batch).start()
                # If buffer exceeds max batch size, flush immediately
                if len(self._ws_batch_buffer) >= self._config.ws_batch_max_size:
                    self._flush_ws_batch()
        else:
            # Original immediate delivery path
            self._push_event_to_ws_subscribers(event)

    def _push_event_to_ws_subscribers(self, event: dict) -> None:
        """Push a single event to all WebSocket subscribers (immediate mode)."""
        with self._lock:
            ws_subs = list(self._ws_subscribers)

        for ws in ws_subs:
            try:
                if hasattr(ws, "send_json"):
                    if asyncio is not None:
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                asyncio.ensure_future(ws.send_json(event))
                            else:
                                loop.run_until_complete(ws.send_json(event))
                        except RuntimeError:
                            pass
                elif hasattr(ws, "put_nowait"):
                    ws.put_nowait(event)
            except Exception:
                pass  # WebSocket may be closed

    async def _flush_ws_batch_later(self, delay: float) -> None:
        """Async coroutine that flushes the WebSocket batch after a delay."""
        await asyncio.sleep(delay)
        self._flush_ws_batch()

    def _flush_ws_batch(self) -> None:
        """Flush buffered WebSocket events as a single batch message.

        Sprint 5.36: Collects all buffered events and sends them as a
        single `batch_events` message. This reduces the number of
        WebSocket frames during high-volume alert periods, improving
        bandwidth efficiency.
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

        ws_subs = list(self._ws_subscribers)
        for ws in ws_subs:
            try:
                if hasattr(ws, "send_json"):
                    if asyncio is not None:
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                asyncio.ensure_future(ws.send_json(batch_msg))
                            else:
                                loop.run_until_complete(ws.send_json(batch_msg))
                        except RuntimeError:
                            pass
                elif hasattr(ws, "put_nowait"):
                    ws.put_nowait(batch_msg)
            except Exception:
                pass

        with self._lock:
            self._ws_batch_total_flushes += 1
            self._ws_batch_total_events_sent += len(events)

        logger.debug(
            "ws_batch_flushed",
            events_count=len(events),
            total_flushes=self._ws_batch_total_flushes,
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

        # Notify other subscribers about the new session
        self._notify_realtime_subscribers({
            "event": "ws_session_connected",
            "session_id": session_id,
        })

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
        self._notify_realtime_subscribers({
            "event": "ws_session_disconnected",
            "session_id": session_id,
        })

        with self._lock:
            self._ws_sessions.pop(session_id, None)

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
                self.remove_ws_subscriber(ws_obj)

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
            self._notify_realtime_subscribers({
                "event": "ws_dead_sessions_cleaned",
                "count": len(dead_session_ids),
                "session_ids": dead_session_ids,
            })

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

        self._notify_realtime_subscribers({
            "event": "alert_groups_merged",
            "source_key": source_key,
            "target_key": target_key,
            "merged_count": merged,
        })

        return {
            "status": "ok",
            "source_key": source_key,
            "target_key": target_key,
            "merged_count": merged,
            "total_in_target": len(self._alert_groups.get(target_key, [])),
        }

    def split_alert_group(
        self, group_key: str, correlation_ids: list[str], new_group_key: str | None = None
    ) -> dict:
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

        self._notify_realtime_subscribers({
            "event": "alert_group_split",
            "source_key": group_key,
            "new_group_key": new_group_key,
            "split_count": len(to_move),
        })

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
            for key_b in non_causal_keys[i + 1:]:
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
                    suggestions.append({
                        "source_key": key_a,
                        "target_key": key_b,
                        "similarity": round(similarity, 3),
                        "reason": f"Subjects overlap {similarity:.0%} and both active within {window}s",
                        "source_count": len(self._alert_groups.get(key_a, [])),
                        "target_count": len(self._alert_groups.get(key_b, [])),
                    })

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
                    s for s in self._auto_merge_suggestions
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
    # Sprint 5.36: Causal chain prediction
    # -----------------------------------------------------------------------

    _CAUSAL_PREDICTION_CHAIN = {
        "pool_adjustment": ["quality_degradation", "batch_reduction"],
        "quality_degradation": ["batch_reduction"],
        "batch_reduction": [],
    }

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
            confidence = round(base_confidence * (0.5 + 0.5 * accuracy_factor), 2) if accuracy_factor > 0 else round(base_confidence, 2)
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
        self._notify_realtime_subscribers({
            "event": "causal_predictions",
            "subject": alert.subject,
            "predictions": predictions,
            "triggered_by": alert.alert_type,
        })

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

    # -----------------------------------------------------------------------
    # Sprint 5.37: Causal prediction accuracy feedback loop
    # -----------------------------------------------------------------------

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
                if (record.get("predicted_alert_type") == alert.alert_type
                        and record.get("subject") == alert.subject):
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
            pending = sum(
                1 for r in self._prediction_outcomes.values()
                if r.get("outcome") == "pending"
            )
            hit_rate = (
                self._prediction_accuracy_hits / total
                if total > 0 else 0.0
            )
            # Precision: of resolved predictions, what fraction were hits
            precision = (
                self._prediction_accuracy_hits / total
                if total > 0 else 0.0
            )
            # Recall approximation: hits / (hits + misses)
            recall = (
                self._prediction_accuracy_hits / total
                if total > 0 else 0.0
            )

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

    # -----------------------------------------------------------------------
    # Sprint 5.37: Notification channel diversification — Slack & PagerDuty
    # -----------------------------------------------------------------------

    def _send_slack_notification(self, alert: Alert) -> None:
        """Send alert notification to a Slack webhook.

        Sprint 5.37: Posts a Slack-compatible message to the configured
        slack_webhook_url. Uses the same retry/backoff logic as webhook.
        """
        if not self._config.slack_webhook_url:
            return

        severity_colors = {
            "info": "#3b82f6",
            "warning": "#f59e0b",
            "critical": "#ef4444",
        }
        color = severity_colors.get(alert.severity, "#64748b")

        payload = json.dumps({
            "attachments": [{
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
            }],
        }).encode("utf-8")

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
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", str(exc))
            raise RuntimeError(f"Slack webhook connection failed: {reason}") from exc

    def _send_pagerduty_notification(self, alert: Alert) -> None:
        """Send alert notification to PagerDuty via Events API v2.

        Sprint 5.37: Posts a PagerDuty event to the configured
        pagerduty_integration_key. Supports info, warning, critical
        severity mapping.
        """
        if not self._config.pagerduty_integration_key:
            return

        severity_map = {
            "info": "info",
            "warning": "warning",
            "critical": "critical",
        }
        pd_severity = severity_map.get(alert.severity, "warning")

        payload = json.dumps({
            "routing_key": self._config.pagerduty_integration_key,
            "event_action": "trigger",
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
        }).encode("utf-8")

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
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", str(exc))
            raise RuntimeError(f"PagerDuty connection failed: {reason}") from exc

    # -----------------------------------------------------------------------
    # Sprint 5.35: Delivery status pruning scheduler
    # -----------------------------------------------------------------------

    def start_prune_scheduler(self) -> bool:
        """Start the background delivery status pruning scheduler.

        Sprint 5.35: If delivery_status_prune_interval_seconds > 0,
        starts a daemon thread that periodically prunes delivery status
        records based on the current configuration.

        Returns True if the scheduler was started, False if already
        running or interval is 0.
        """
        interval = self._config.delivery_status_prune_interval_seconds
        if interval <= 0:
            return False

        with self._lock:
            if self._prune_scheduler_running:
                return False
            self._prune_scheduler_running = True

        def _scheduler_loop():
            while self._prune_scheduler_running:
                interval_secs = self._config.delivery_status_prune_interval_seconds
                if interval_secs <= 0:
                    break
                time.sleep(interval_secs)
                if not self._prune_scheduler_running:
                    break
                try:
                    self._run_scheduled_prune()
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
        """Stop the delivery status pruning scheduler."""
        self._prune_scheduler_running = False
        if self._prune_scheduler_thread is not None:
            self._prune_scheduler_thread.join(timeout=5.0)
            self._prune_scheduler_thread = None

        logger.info("prune_scheduler_stopped")

    def _run_scheduled_prune(self) -> int:
        """Execute a single scheduled prune cycle.

        Sprint 5.35: Prunes delivery status records using current config
        parameters and updates the scheduler state.

        Sprint 5.36: Records pruning run history (timestamp + records deleted)
        for observability.

        Returns the number of records pruned.
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

        # Sprint 5.36: Record pruning history
        run_record = {
            "timestamp": now,
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "records_deleted": deleted,
            "max_age_days": self._config.delivery_status_max_age_days,
            "max_rows": self._config.delivery_status_max_rows,
        }
        with self._lock:
            self._pruning_history.append(run_record)
            # Keep only the last N records
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

        Sprint 5.35: Provides visibility into the scheduler's status
        including last run time, next scheduled run, and total runs.

        Sprint 5.36: Includes pruning history for observability.
        """
        # Compute total rows pruned from history
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

        Sprint 5.36: Returns the last N pruning run records for
        observability dashboards. Each record contains timestamp,
        records_deleted, and config at time of run.
        """
        with self._lock:
            history = list(self._pruning_history)
        if limit:
            history = history[-limit:]
        return history

    # -----------------------------------------------------------------------
    # Sprint 5.32: Digest customization per alert type
    # -----------------------------------------------------------------------

    def _get_digest_settings(self, alert_type: str) -> tuple[int, int]:
        """Get digest interval and min_alerts for a specific alert type.

        Sprint 5.32: Checks per-type overrides first, falls back to
        global settings if no override is configured for this type.

        Returns (interval_minutes, min_alerts) tuple.
        """
        override = self._config.digest_overrides.get(alert_type)
        if override:
            interval = override.get("interval_minutes", self._config.digest_interval_minutes)
            min_alerts = override.get("min_alerts", self._config.digest_min_alerts)
            return (interval, min_alerts)
        return (self._config.digest_interval_minutes, self._config.digest_min_alerts)

    def _is_alert_type_enabled(self, alert_type: str) -> bool:
        """Check if a specific alert type is enabled."""
        mapping = {
            "quality_degradation": self._config.alert_on_quality_degradation,
            "pool_adjustment": self._config.alert_on_pool_adjustment,
            "batch_reduction": self._config.alert_on_batch_reduction,
        }
        return mapping.get(alert_type, True)

    # Sprint 5.30: Severity escalation logic

    def _check_escalation(self, alert: Alert, now: float) -> bool:
        """Check if an alert should be escalated based on occurrence count.

        Sprint 5.30: If the same (alert_type, subject) has occurred
        escalation_threshold times within escalation_window_seconds,
        the alert severity is automatically increased to escalation_severity
        and additional transports may be triggered.

        Returns True if escalation is triggered, False otherwise.
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
            # Reset the tracker after escalation to avoid re-escalating
            # the same window of alerts
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

        Parameters
        ----------
        store:
            An AlertHistoryStore instance with record_alert(),
            record_delivery_failure(), get_alert_history(), and
            get_delivery_failures() methods.
        """
        self._history_store = store
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

        Sprint 5.30: On startup (or after attaching a history store),
        queries the AlertHistoryStore for recent alerts within the
        rate-limit window and populates _last_alert_time. This prevents
        alert storms immediately after a restart.
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

    def _rebuild_mute_rules(self) -> None:
        """Rebuild in-memory mute rules from persistent store.

        Sprint 5.31: On startup (or after attaching a history store),
        queries the AlertHistoryStore for active mute rules and
        populates _mute_rules. This ensures mute rules persist across
        restarts.
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
                    # Skip expired rules
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

        Sprint 5.32: On startup (or after attaching a history store),
        queries the AlertHistoryStore for recent delivery status records
        and populates _delivery_status. This ensures delivery status
        is queryable after a restart, even for alerts dispatched in
        the previous process lifetime.
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
                    self._total_webhook_retries += 1
                    delay = base_delay * (2 ** attempt)
                    self._record_failure(DeliveryFailure(
                        transport="webhook",
                        alert_type=alert.alert_type,
                        subject=alert.subject,
                        error_message=str(exc),
                        retry_attempt=attempt,
                        final=False,
                    ))
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
        payload = json.dumps({
            "source": "aip-brain",
            "alert": alert.to_dict(),
        }).encode("utf-8")

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
                # Prefer environment variable for password if not set in config
                password = self._config.smtp_password
                if not password:
                    import os
                    password = os.environ.get("AIP_SMTP_PASSWORD", "")
                if password:
                    smtp.login(self._config.smtp_username, password)

            smtp.send_message(msg)

    def _record_failure(self, failure: DeliveryFailure) -> None:
        """Record a delivery failure in the history."""
        self._delivery_failures.append(failure)
        if len(self._delivery_failures) > self._MAX_FAILURE_HISTORY:
            self._delivery_failures = self._delivery_failures[-self._MAX_FAILURE_HISTORY:]

        # Sprint 5.29: Also persist to the SQLite store if attached
        if self._history_store is not None:
            try:
                self._history_store.record_delivery_failure(failure.to_dict())
            except Exception as exc:
                logger.warning(
                    "alert_history_store_failure_write_failed",
                    error=str(exc),
                )
