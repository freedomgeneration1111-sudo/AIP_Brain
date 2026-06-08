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

        # Rate-limit check
        key = (alert.alert_type, alert.subject)
        now = time.time()
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
        """
        if "webhook" in transports and self._config.webhook_url:
            try:
                self._send_webhook_with_retry(alert)
            except Exception as exc:
                self._total_send_failures += 1
                failure = DeliveryFailure(
                    transport="webhook",
                    alert_type=alert.alert_type,
                    subject=alert.subject,
                    error_message=str(exc),
                    retry_attempt=self._config.webhook_max_retries,
                    final=True,
                )
                self._record_failure(failure)
                logger.warning(
                    "alert_webhook_failed_final",
                    url=self._config.webhook_url[:50],
                    error=str(exc),
                    retries_attempted=self._config.webhook_max_retries,
                    correlation_id=correlation_id,
                )

        if "email" in transports and self._config.email_to:
            try:
                self._send_email(alert)
            except Exception as exc:
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
                logger.warning(
                    "alert_email_failed",
                    to=self._config.email_to[:50],
                    error=str(exc),
                    correlation_id=correlation_id,
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

    # Sprint 5.29: Config-driven alert routing

    def _get_transports_for_alert(self, alert_type: str) -> list[str]:
        """Determine which transports to use for a given alert type.

        Sprint 5.29: If ``routes`` is configured in AlertConfig, only
        the transports listed for this alert_type are used.  If the
        alert_type has no entry in routes, or routes is empty, all
        configured transports are used (default behavior).

        Returns a list of transport names: "webhook", "email".
        """
        if self._config.routes and alert_type in self._config.routes:
            configured = self._config.routes[alert_type]
            # Only return transports that are actually configured
            result = []
            for t in configured:
                if t == "webhook" and self._config.webhook_url:
                    result.append(t)
                elif t == "email" and self._config.email_to:
                    result.append(t)
            return result

        # Default: all configured transports
        transports = []
        if self._config.webhook_url:
            transports.append("webhook")
        if self._config.email_to:
            transports.append("email")
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
