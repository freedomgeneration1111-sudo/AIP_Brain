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
"""

from __future__ import annotations

import json
import re
import time
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

    Provides a single ``send_alert()`` method that:
    1. Checks if the alert type is enabled
    2. Rate-limits identical alerts (per type + subject)
    3. Dispatches to configured transports (webhook with retries, email with auth)
    4. Logs all alerts regardless of transport success
    5. Records delivery failures with detailed context

    Usage::

        alert_mgr = AlertManager(AlertConfig(enabled=True, webhook_url="..."))
        alert_mgr.send_alert(Alert(
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
        # History of sent alerts (last 50)
        self._alert_history: list[dict] = []
        # Sprint 5.26: Delivery failure history
        self._delivery_failures: list[DeliveryFailure] = []
        # Sprint 5.26: Webhook URL validation status (lazy, on first use)
        self._webhook_url_validated: bool = False
        self._webhook_url_valid: bool | None = None
        self._webhook_url_validation_reason: str = ""
        # Counters
        self._total_alerts_sent = 0
        self._total_alerts_rate_limited = 0
        self._total_send_failures = 0
        self._total_webhook_retries = 0

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

        return warnings

    def send_alert(self, alert: Alert) -> bool:
        """Send an alert through configured transports.

        Returns True if the alert was dispatched (or would have been
        if alerting is disabled), False if rate-limited.
        """
        # Check master switch
        if not self._config.enabled:
            logger.debug(
                "alert_skipped_disabled",
                alert_type=alert.alert_type,
                subject=alert.subject,
            )
            return True  # Not an error — just not configured

        # Check if this alert type is enabled
        if not self._is_alert_type_enabled(alert.alert_type):
            logger.debug(
                "alert_skipped_type_disabled",
                alert_type=alert.alert_type,
            )
            return True

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
            return False

        # Record the alert time
        self._last_alert_time[key] = now

        # Log the alert regardless of transport success
        logger.info(
            "alert_dispatched",
            alert_type=alert.alert_type,
            severity=alert.severity,
            subject=alert.subject,
            message=alert.message[:200],
        )

        # Record in history
        alert_dict = alert.to_dict()
        self._alert_history.append(alert_dict)
        if len(self._alert_history) > 50:
            self._alert_history = self._alert_history[-50:]

        self._total_alerts_sent += 1

        # Dispatch to transports
        if self._config.webhook_url:
            try:
                self._send_webhook_with_retry(alert)
            except Exception as exc:
                self._total_send_failures += 1
                self._record_failure(DeliveryFailure(
                    transport="webhook",
                    alert_type=alert.alert_type,
                    subject=alert.subject,
                    error_message=str(exc),
                    retry_attempt=self._config.webhook_max_retries,
                    final=True,
                ))
                logger.warning(
                    "alert_webhook_failed_final",
                    url=self._config.webhook_url[:50],
                    error=str(exc),
                    retries_attempted=self._config.webhook_max_retries,
                )

        if self._config.email_to:
            try:
                self._send_email(alert)
            except Exception as exc:
                self._total_send_failures += 1
                self._record_failure(DeliveryFailure(
                    transport="email",
                    alert_type=alert.alert_type,
                    subject=alert.subject,
                    error_message=str(exc),
                    retry_attempt=0,
                    final=True,
                ))
                logger.warning(
                    "alert_email_failed",
                    to=self._config.email_to[:50],
                    error=str(exc),
                )

        # Return True if the alert was dispatched (even if no transports
        # were configured — the alert was logged and recorded in history).
        # Returns False only if the alert was rate-limited.
        return True

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
        }

    def get_delivery_failures(self, transport: str | None = None, limit: int = 20) -> list[dict]:
        """Return delivery failure history, optionally filtered by transport.

        Parameters
        ----------
        transport:
            If provided, return only failures for this transport ("webhook" or "email").
        limit:
            Maximum number of failures to return (most recent first).
        """
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

        Uses stdlib smtplib.  This is a blocking call but SMTP sends
        are typically fast.  If this becomes a concern, it can be
        moved to a background thread in a future sprint.
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
