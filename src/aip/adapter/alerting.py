"""Operator alerting — lightweight webhook/email notification system.

Sprint 5.25: Provides configurable notifications for significant events
in the self-tuning system:

- Vigil quality degradation (faithfulness score dropping over multiple cycles)
- Read pool auto-sizing adjustments (increases AND rollbacks)
- Graph extraction batch size reductions due to high parse failure rate

Design principles:
- Lightweight and opt-in — alerting is disabled by default
- Multiple transport mechanisms (webhook, email)
- Non-blocking — alerts are fire-and-forget; failures are logged but never
  interrupt the calling code
- Configurable via ``[alerting]`` section in ``aip.config.toml``

Configuration example::

    [alerting]
    enabled = true
    webhook_url = "https://hooks.slack.com/services/..."
    email_to = "ops@example.com"
    email_from = "aip-brain@example.com"
    smtp_host = "smtp.example.com"
    smtp_port = 587
    alert_on_quality_degradation = true
    alert_on_pool_adjustment = true
    alert_on_batch_reduction = true
    min_alert_interval_seconds = 300   # Rate-limit: don't re-alert for 5 min
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
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
    """

    enabled: bool = False
    webhook_url: str = ""
    email_to: str = ""
    email_from: str = "aip-brain@localhost"
    smtp_host: str = ""
    smtp_port: int = 587
    alert_on_quality_degradation: bool = True
    alert_on_pool_adjustment: bool = True
    alert_on_batch_reduction: bool = True
    min_alert_interval_seconds: int = 300


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
            from datetime import datetime, timezone
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
# Alert manager
# ---------------------------------------------------------------------------


class AlertManager:
    """Manages operator alerting for auto-tuning events.

    Provides a single ``send_alert()`` method that:
    1. Checks if the alert type is enabled
    2. Rate-limits identical alerts (per type + subject)
    3. Dispatches to configured transports (webhook, email)
    4. Logs all alerts regardless of transport success

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

    def __init__(self, config: AlertConfig | None = None) -> None:
        self._config = config or AlertConfig()
        # Rate-limiting: (alert_type, subject) -> last_alert_timestamp
        self._last_alert_time: dict[tuple[str, str], float] = {}
        # History of sent alerts (last 50)
        self._alert_history: list[dict] = []
        # Counters
        self._total_alerts_sent = 0
        self._total_alerts_rate_limited = 0
        self._total_send_failures = 0

    @property
    def config(self) -> AlertConfig:
        return self._config

    @config.setter
    def config(self, value: AlertConfig) -> None:
        self._config = value

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
        sent_any = False

        if self._config.webhook_url:
            try:
                self._send_webhook(alert)
                sent_any = True
            except Exception as exc:
                self._total_send_failures += 1
                logger.warning(
                    "alert_webhook_failed",
                    url=self._config.webhook_url[:50],
                    error=str(exc),
                )

        if self._config.email_to:
            try:
                self._send_email(alert)
                sent_any = True
            except Exception as exc:
                self._total_send_failures += 1
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
            "email_configured": bool(self._config.email_to),
            "total_alerts_sent": self._total_alerts_sent,
            "total_rate_limited": self._total_alerts_rate_limited,
            "total_send_failures": self._total_send_failures,
            "recent_alerts": self._alert_history[-5:],
            "alert_types_enabled": {
                "quality_degradation": self._config.alert_on_quality_degradation,
                "pool_adjustment": self._config.alert_on_pool_adjustment,
                "batch_reduction": self._config.alert_on_batch_reduction,
            },
        }

    def _is_alert_type_enabled(self, alert_type: str) -> bool:
        """Check if a specific alert type is enabled."""
        mapping = {
            "quality_degradation": self._config.alert_on_quality_degradation,
            "pool_adjustment": self._config.alert_on_pool_adjustment,
            "batch_reduction": self._config.alert_on_batch_reduction,
        }
        return mapping.get(alert_type, True)

    def _send_webhook(self, alert: Alert) -> None:
        """POST alert payload to the configured webhook URL.

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

        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Webhook returned HTTP {resp.status}")

    def _send_email(self, alert: Alert) -> None:
        """Send alert email via SMTP.

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
            smtp.starttls()
            smtp.send_message(msg)
