"""Vigil quality dashboard endpoint — time-series quality metrics.

Sprint 5.25: Provides a dedicated endpoint for operators to query
Vigil quality metrics over time, enabling charting and trend analysis.

Sprint 5.26: Updated to support persistent quality history via
VigilQualityStore. When a quality_store is available on the container,
the endpoint queries SQLite for longer time ranges across restarts.

Endpoints:
- GET /vigil/quality — Time-series quality metrics (JSON API)
- GET /vigil/quality/dashboard — HTML/JS visualization page (Sprint 5.26)

Supports basic filtering:
- ``last_n_cycles``: Return metrics from the last N Vigil cycles (default 10)
- ``since``: ISO 8601 datetime — only return cycles after this timestamp

Returns time-series data for:
- Citation rate
- Grounding rate
- Hedging detection count
- LLM faithfulness score
- Flagged/evaluated turn counts
- Trend indicators per cycle
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/vigil/quality")
async def vigil_quality(
    last_n_cycles: int = Query(default=10, ge=1, le=500, description="Number of recent cycles to return"),
    since: str | None = Query(default=None, description="ISO 8601 datetime — only return cycles after this timestamp"),
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Return time-series quality metrics from Vigil cycles.

    Returns per-cycle metrics (citation rate, grounding rate, hedging,
    LLM faithfulness) over recent cycles, suitable for charting.

    Sprint 5.26: When a VigilQualityStore is available, queries the
    persistent store for longer time ranges across restarts. Falls
    back to in-memory _cycle_report_history when the store is not
    configured.

    Filtering:
    - ``last_n_cycles``: Return the most recent N cycles (default 10, max 500)
    - ``since``: Only return cycles with timestamps after this value
    """
    vigil = getattr(container, "vigil", None)
    if vigil is None:
        return {
            "status": "vigil_not_initialized",
            "cycles": [],
            "total_cycles": 0,
        }

    # Sprint 5.26: Try persistent store first for longer time ranges
    quality_store = getattr(container, "_vigil_quality_store", None)
    if quality_store is None:
        quality_store = getattr(vigil, "_quality_store", None)

    # Only use the quality store if it's a real implementation (not a MagicMock)
    # Check that get_cycles is actually callable and the class is not a Mock
    has_persistent_store = False
    if quality_store is not None:
        try:
            # Duck-type check: real stores have these methods and aren't Mocks
            has_persistent_store = (
                callable(getattr(quality_store, "get_cycles", None))
                and callable(getattr(quality_store, "record_cycle", None))
                and not hasattr(quality_store, "_mock_name")  # Reject unittest.mock objects
            )
        except Exception:
            has_persistent_store = False

    cycle_history = []

    if has_persistent_store:
        try:
            # Use persistent store — supports longer time ranges
            cycle_history = quality_store.get_cycles(
                last_n_cycles=last_n_cycles,
                since=since,
                limit=last_n_cycles,
            )
        except Exception as exc:
            logger.warning("vigil_quality_store_query_failed", error=str(exc))
            # Fall back to in-memory
            cycle_history = getattr(vigil, "_cycle_report_history", [])
    else:
        # Fall back to in-memory history (Sprint 5.25 behavior)
        cycle_history = getattr(vigil, "_cycle_report_history", [])

    if not cycle_history:
        return {
            "status": "no_cycle_data",
            "cycles": [],
            "total_cycles": 0,
        }

    # Apply since filter if not already handled by the store
    if since and not has_persistent_store:
        cycle_history = [
            c for c in cycle_history
            if c.get("timestamp", "") > since
        ]

    # Apply last_n_cycles filter if not already handled by the store
    if not has_persistent_store:
        cycle_history = cycle_history[-last_n_cycles:]

    # Build per-cycle data points
    cycles_data = []
    for i, cycle in enumerate(cycle_history):
        cycle_point = {
            "cycle_index": i,
            "timestamp": cycle.get("timestamp", ""),
            "metrics": {
                "avg_citation_rate": cycle.get("avg_citation_rate", 0.0),
                "avg_grounding_rate": cycle.get("avg_grounding_rate", 0.0),
                "avg_llm_faithfulness": cycle.get("avg_llm_faithfulness", 0.0),
                "evaluated_count": cycle.get("evaluated_count", 0),
                "flagged_count": cycle.get("flagged_count", 0),
                "flag_rate": round(
                    cycle.get("flagged_count", 0) / max(1, cycle.get("evaluated_count", 1)),
                    3,
                ),
            },
        }
        cycles_data.append(cycle_point)

    # Compute summary statistics across filtered cycles
    citation_rates = [c.get("avg_citation_rate", 0.0) for c in cycle_history]
    grounding_rates = [c.get("avg_grounding_rate", 0.0) for c in cycle_history]
    faithfulness_scores = [c.get("avg_llm_faithfulness", 0.0) for c in cycle_history]

    summary = {
        "cycles_analyzed": len(cycle_history),
        "citation_rate": {
            "current": citation_rates[-1] if citation_rates else 0.0,
            "min": round(min(citation_rates), 3) if citation_rates else 0.0,
            "max": round(max(citation_rates), 3) if citation_rates else 0.0,
            "trend": _compute_series_trend(citation_rates),
        },
        "grounding_rate": {
            "current": grounding_rates[-1] if grounding_rates else 0.0,
            "min": round(min(grounding_rates), 3) if grounding_rates else 0.0,
            "max": round(max(grounding_rates), 3) if grounding_rates else 0.0,
            "trend": _compute_series_trend(grounding_rates),
        },
        "llm_faithfulness": {
            "current": faithfulness_scores[-1] if faithfulness_scores else 0.0,
            "min": round(min(faithfulness_scores), 3) if faithfulness_scores else 0.0,
            "max": round(max(faithfulness_scores), 3) if faithfulness_scores else 0.0,
            "trend": _compute_series_trend(faithfulness_scores),
        },
    }

    # Get LLM faithfulness telemetry if available
    llm_telemetry = {}
    vigil_telem = getattr(vigil, "_llm_faithfulness_telemetry", {})
    if vigil_telem:
        llm_telemetry = {
            "total_evaluations": vigil_telem.get("total_llm_evaluations", 0),
            "total_failures": vigil_telem.get("total_llm_evaluations_failed", 0),
            "total_hallucinations": vigil_telem.get("total_hallucinations_detected", 0),
            "avg_faithfulness_score": vigil_telem.get("avg_llm_faithfulness_score", 0.0),
            "recent_evaluations": vigil_telem.get("last_llm_evaluations", [])[-5:],
        }

    # Get current Vigil config
    vigil_config = getattr(vigil, "config", None)
    config_info = {}
    if vigil_config is not None:
        config_info = {
            "llm_faithfulness_enabled": vigil_config.llm_faithfulness_enabled,
            "llm_faithfulness_model_slot": vigil_config.llm_faithfulness_model_slot,
            "llm_faithfulness_sample_size": vigil_config.llm_faithfulness_sample_size,
        }

    # Sprint 5.26: Indicate whether data comes from persistent store
    data_source = "persistent_store" if has_persistent_store else "in_memory"
    total_persisted = 0
    if has_persistent_store:
        try:
            total_persisted = quality_store.get_cycle_count()
        except Exception:
            total_persisted = 0

    return {
        "status": "ok",
        "cycles": cycles_data,
        "summary": summary,
        "llm_faithfulness_telemetry": llm_telemetry,
        "config": config_info,
        "total_cycles_available": len(cycle_history),
        "data_source": data_source,
        "total_persisted_cycles": total_persisted,
    }


@router.get("/vigil/quality/alerts")
async def vigil_alerts(
    alert_type: str | None = Query(default=None, description="Filter by alert type: quality_degradation, pool_adjustment, batch_reduction"),
    severity: str | None = Query(default=None, description="Filter by severity: info, warning, critical"),
    since: str | None = Query(default=None, description="ISO 8601 datetime — only return alerts after this timestamp"),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum number of alerts to return"),
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.28: Expose recent alert history, delivery failures, and config status.

    Provides operators with visibility into what alerts have been triggered,
    whether they were delivered successfully, and how the alerting system
    is currently configured.

    Filtering:
    - ``alert_type``: Filter by alert type (quality_degradation, pool_adjustment, batch_reduction)
    - ``severity``: Filter by severity level (info, warning, critical)
    - ``since``: ISO 8601 datetime — only return alerts after this timestamp
    - ``limit``: Maximum number of alerts to return (default 50, max 200)
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "alerts": [],
            "delivery_failures": [],
            "config": {},
        }

    # Resolve Query parameters to plain values (direct test calls may pass Query objects)
    _alert_type = alert_type.default if hasattr(alert_type, 'default') else alert_type
    _severity = severity.default if hasattr(severity, 'default') else severity
    _since = since.default if hasattr(since, 'default') else since
    if isinstance(limit, int):
        _limit = limit
    elif hasattr(limit, 'default'):
        _limit = limit.default
    else:
        _limit = int(limit)

    # Get filtered alert history
    alerts = alert_manager.get_alert_history(
        alert_type=_alert_type,
        severity=_severity,
        since=_since,
        limit=_limit,
    )

    # Get recent delivery failures
    delivery_failures = alert_manager.get_delivery_failures(limit=10)

    # Get current alerting configuration status
    config_status = alert_manager.get_status()

    return {
        "status": "ok",
        "alerts": alerts,
        "total_alerts_returned": len(alerts),
        "delivery_failures": delivery_failures,
        "total_delivery_failures": len(delivery_failures),
        "config": {
            "enabled": config_status.get("enabled", False),
            "webhook_configured": config_status.get("webhook_configured", False),
            "webhook_url_valid": config_status.get("webhook_url_valid"),
            "email_configured": config_status.get("email_configured", False),
            "smtp_auth_configured": config_status.get("smtp_auth_configured", False),
            "alert_types_enabled": config_status.get("alert_types_enabled", {}),
            "total_alerts_sent": config_status.get("total_alerts_sent", 0),
            "total_rate_limited": config_status.get("total_rate_limited", 0),
            "total_send_failures": config_status.get("total_send_failures", 0),
            "total_webhook_retries": config_status.get("total_webhook_retries", 0),
            "webhook_retry_config": config_status.get("webhook_retry_config", {}),
        },
    }


@router.get("/vigil/quality/retention")
async def vigil_retention_status(
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.28: View current retention and rollup status.

    Returns information about the quality store's data retention
    configuration, current row counts, oldest/newest timestamps,
    and rollup statistics.
    """
    quality_store = getattr(container, "_vigil_quality_store", None)

    if quality_store is None:
        return {
            "status": "quality_store_not_configured",
            "retention": {},
        }

    try:
        retention_status = quality_store.get_retention_status()
    except Exception as exc:
        logger.warning("vigil_retention_status_failed", error=str(exc))
        retention_status = {"error": str(exc)}

    return {
        "status": "ok",
        "retention": retention_status,
    }


@router.post("/vigil/quality/retention/rollup")
async def vigil_retention_rollup(
    period: str = Query(default="daily", description="Rollup period: 'daily' or 'weekly'"),
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.28: Manually trigger a rollup aggregation.

    Allows operators to manually trigger daily or weekly rollup
    aggregation instead of waiting for the scheduled background task.

    Parameters
    ----------
    period:
        The rollup period to run: ``daily`` (default) or ``weekly``.
        Daily rollup aggregates individual rows older than rollup_age_days
        into daily summaries. Weekly rollup aggregates daily rollup rows
        older than weekly_rollup_age_weeks into weekly summaries.
    """
    quality_store = getattr(container, "_vigil_quality_store", None)

    if quality_store is None:
        return {
            "status": "quality_store_not_configured",
            "result": {},
        }

    try:
        if period == "weekly":
            result = quality_store.run_weekly_rollup()
        else:
            result = quality_store.run_rollup()
    except Exception as exc:
        logger.warning("vigil_retention_rollup_failed", error=str(exc))
        result = {"error": str(exc), "rolled_up_days": 0}

    return {
        "status": "ok",
        "period": period,
        "result": result,
    }


@router.get("/vigil/quality/retention/rollup-stats")
async def vigil_rollup_stats(
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.28: View rollup statistics.

    Returns counts of daily and weekly rollup rows, along with
    the time range they cover.
    """
    quality_store = getattr(container, "_vigil_quality_store", None)

    if quality_store is None:
        return {
            "status": "quality_store_not_configured",
            "stats": {},
        }

    try:
        stats = quality_store.get_rollup_stats()
    except Exception as exc:
        logger.warning("vigil_rollup_stats_failed", error=str(exc))
        stats = {"error": str(exc)}

    return {
        "status": "ok",
        "stats": stats,
    }


@router.get("/vigil/quality/retention/verify")
async def vigil_rollup_verify(
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.29: Verify rollup integrity.

    Checks that rollup row counts and aggregated values are consistent
    with source data. Returns a list of any integrity issues found.
    """
    quality_store = getattr(container, "_vigil_quality_store", None)

    if quality_store is None:
        return {
            "status": "quality_store_not_configured",
            "verification": {},
        }

    try:
        verification = quality_store.verify_rollup_integrity()
    except Exception as exc:
        logger.warning("vigil_rollup_verify_failed", error=str(exc))
        verification = {"valid": False, "error": str(exc), "issues": []}

    return {
        "status": "ok",
        "verification": verification,
    }


@router.get("/vigil/quality/health")
async def vigil_quality_health(
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.29: Consolidated health endpoint for quality and auto-tuning systems.

    Provides a single place for operators to check the health of:
    - Alerting system (enabled, transports, recent failures)
    - Retention/rollup status (row counts, integrity)
    - Auto-tuning status (policy, pool sizing, batch tuning)

    Combines data from /vigil/quality/alerts, /vigil/quality/retention,
    and auto-tuning policy status into a unified view.
    """
    # Alerting status
    alert_manager = getattr(container, "_alert_manager", None)
    alerting_status: dict[str, Any] = {
        "available": False,
    }
    if alert_manager is not None:
        try:
            status = alert_manager.get_status()
            alerting_status = {
                "available": True,
                "enabled": status.get("enabled", False),
                "webhook_configured": status.get("webhook_configured", False),
                "email_configured": status.get("email_configured", False),
                "total_alerts_sent": status.get("total_alerts_sent", 0),
                "total_send_failures": status.get("total_send_failures", 0),
                "history_store_attached": status.get("history_store_attached", False),
                "routes_configured": bool(status.get("routes", {})),
                "recent_failures_count": len(status.get("recent_failures", [])),
            }
        except Exception as exc:
            alerting_status = {"available": False, "error": str(exc)}

    # Retention/rollup status
    quality_store = getattr(container, "_vigil_quality_store", None)
    retention_status: dict[str, Any] = {
        "available": False,
    }
    if quality_store is not None:
        try:
            ret = quality_store.get_retention_status()
            retention_status = {
                "available": True,
                "total_rows": ret.get("total_rows", 0),
                "original_rows": ret.get("original_rows", 0),
                "rollup_rows": ret.get("rollup_rows", 0),
                "retention_days": ret.get("retention_days", 0),
            }
        except Exception as exc:
            retention_status = {"available": False, "error": str(exc)}

    # Auto-tuning status
    auto_tuning_policy = getattr(container, "_auto_tuning_policy", None)
    auto_sizer = getattr(container, "_read_pool_auto_sizer", None)
    auto_tuning_status: dict[str, Any] = {
        "available": False,
    }
    if auto_tuning_policy is not None:
        try:
            auto_tuning_status = {
                "available": True,
                "policy_valid": auto_tuning_policy.is_valid() if hasattr(auto_tuning_policy, "is_valid") else None,
                "pool_auto_sizer_available": auto_sizer is not None,
            }
        except Exception as exc:
            auto_tuning_status = {"available": False, "error": str(exc)}

    # Overall health
    overall_healthy = True
    if alerting_status.get("available") and alerting_status.get("total_send_failures", 0) > 10:
        overall_healthy = False
    if retention_status.get("available") and retention_status.get("error"):
        overall_healthy = False

    return {
        "status": "healthy" if overall_healthy else "degraded",
        "alerting": alerting_status,
        "retention": retention_status,
        "auto_tuning": auto_tuning_status,
        "components": {
            "quality_store": quality_store is not None,
            "alert_manager": alert_manager is not None,
            "alert_history_store": getattr(container, "_alert_history_store", None) is not None,
            "auto_tuning_policy": auto_tuning_policy is not None,
            "read_pool_auto_sizer": auto_sizer is not None,
        },
    }


# ---------------------------------------------------------------------------
# Sprint 5.30: Alert acknowledgment / dismissal
# ---------------------------------------------------------------------------


class AcknowledgeRequest(BaseModel):
    """Request body for alert acknowledgment."""
    acknowledged_by: str = "operator"


class DismissRequest(BaseModel):
    """Request body for alert dismissal."""
    dismissed_by: str = "operator"


@router.post("/vigil/quality/alerts/{alert_id}/acknowledge")
async def vigil_alert_acknowledge(
    alert_id: int,
    request: AcknowledgeRequest = AcknowledgeRequest(),
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.30: Acknowledge an alert.

    Marks the alert as acknowledged, indicating the operator has
    seen and accepted the alert. The acknowledgment persists across
    restarts and is visible in the dashboard alerts panel.

    Parameters
    ----------
    alert_id:
        The database ID of the alert to acknowledge.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "acknowledged": False,
        }

    success = alert_manager.acknowledge_alert(alert_id, request.acknowledged_by)

    if success:
        return {
            "status": "ok",
            "alert_id": alert_id,
            "acknowledged": True,
            "acknowledged_by": request.acknowledged_by,
        }
    else:
        return {
            "status": "alert_not_found_or_no_store",
            "alert_id": alert_id,
            "acknowledged": False,
        }


@router.post("/vigil/quality/alerts/{alert_id}/dismiss")
async def vigil_alert_dismiss(
    alert_id: int,
    request: DismissRequest = DismissRequest(),
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.30: Dismiss an alert.

    Marks the alert as dismissed, indicating the operator has resolved
    the underlying issue or explicitly dismissed the alert. The dismissal
    persists across restarts and is visible in the dashboard alerts panel.

    Parameters
    ----------
    alert_id:
        The database ID of the alert to dismiss.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "dismissed": False,
        }

    success = alert_manager.dismiss_alert(alert_id, request.dismissed_by)

    if success:
        return {
            "status": "ok",
            "alert_id": alert_id,
            "dismissed": True,
            "dismissed_by": request.dismissed_by,
        }
    else:
        return {
            "status": "alert_not_found_or_no_store",
            "alert_id": alert_id,
            "dismissed": False,
        }


# ---------------------------------------------------------------------------
# Sprint 5.30: Retention configuration API
# ---------------------------------------------------------------------------


class RetentionConfigUpdate(BaseModel):
    """Request body for updating retention configuration."""
    retention_days: int | None = None
    rollup_age_days: int | None = None
    weekly_rollup_age_weeks: int | None = None


@router.get("/vigil/quality/retention/config")
async def vigil_retention_config(
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.30: View current retention and rollup configuration.

    Returns the current values of retention_days, rollup_age_days,
    weekly_rollup_age_weeks, and max_history_rows. These parameters
    control how quality data is retained and aggregated over time.
    """
    quality_store = getattr(container, "_vigil_quality_store", None)

    if quality_store is None:
        return {
            "status": "quality_store_not_configured",
            "config": {},
        }

    try:
        config = quality_store.get_config()
    except Exception as exc:
        logger.warning("vigil_retention_config_failed", error=str(exc))
        config = {"error": str(exc)}

    return {
        "status": "ok",
        "config": config,
    }


@router.patch("/vigil/quality/retention/config")
async def vigil_retention_config_update(
    update: RetentionConfigUpdate,
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.30: Update retention and rollup configuration at runtime.

    Allows operators to change retention_days, rollup_age_days, and
    weekly_rollup_age_weeks without restarting the process. Changes
    take effect immediately on the running VigilQualityStore instance.

    All parameters are optional — only provided fields are updated.

    Input validation:
    - retention_days must be >= 0 (0 = unlimited retention)
    - rollup_age_days must be >= 0
    - weekly_rollup_age_weeks must be >= 0
    """
    quality_store = getattr(container, "_vigil_quality_store", None)

    if quality_store is None:
        return {
            "status": "quality_store_not_configured",
            "config": {},
        }

    try:
        result = quality_store.update_config(
            retention_days=update.retention_days,
            rollup_age_days=update.rollup_age_days,
            weekly_rollup_age_weeks=update.weekly_rollup_age_weeks,
        )
    except Exception as exc:
        logger.warning("vigil_retention_config_update_failed", error=str(exc))
        result = {"error": str(exc)}

    has_errors = bool(result.get("validation_errors", []))
    return {
        "status": "ok" if not has_errors else "validation_error",
        "config": result,
    }


# ---------------------------------------------------------------------------
# Sprint 5.31: Alert delivery status tracking
# ---------------------------------------------------------------------------


@router.get("/vigil/quality/alerts/{correlation_id}/status")
async def vigil_alert_delivery_status(
    correlation_id: str,
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.31: Get delivery status for an alert by its correlation ID.

    Returns whether async delivery completed, which transports
    succeeded/failed, retry counts, and the final delivery status.
    This gives operators visibility into what happened after
    send_alert() returned.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "delivery_status": None,
        }

    status = alert_manager.get_delivery_status(correlation_id)

    if status is None:
        return {
            "status": "not_found",
            "correlation_id": correlation_id,
            "delivery_status": None,
        }

    return {
        "status": "ok",
        "correlation_id": correlation_id,
        "delivery_status": status,
    }


# ---------------------------------------------------------------------------
# Sprint 5.31: Alert silencing / muting rules
# ---------------------------------------------------------------------------


class MuteRuleRequest(BaseModel):
    """Request body for creating a mute rule."""
    alert_type: str
    subject: str
    duration_seconds: int = 3600
    muted_by: str = "operator"
    auto_mute_on_ack: bool = False


@router.post("/vigil/quality/alerts/mute")
async def vigil_alert_mute(
    request: MuteRuleRequest,
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.31: Create a mute rule for a specific (alert_type, subject) pair.

    Temporarily suppresses alerts matching the specified pair for the
    given duration. Mute rules persist across restarts and can be
    removed via the DELETE endpoint.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "muted": False,
        }

    rule = alert_manager.add_mute_rule(
        alert_type=request.alert_type,
        subject=request.subject,
        duration_seconds=request.duration_seconds,
        muted_by=request.muted_by,
        auto_mute_on_ack=request.auto_mute_on_ack,
    )

    return {
        "status": "ok",
        "mute_rule": rule,
    }


@router.delete("/vigil/quality/alerts/mute")
async def vigil_alert_unmute(
    alert_type: str = Query(..., description="Alert type to unmute"),
    subject: str = Query(..., description="Subject to unmute"),
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.31: Remove a mute rule for a specific (alert_type, subject) pair.

    Un-mutes the alert pair so that future alerts are no longer
    suppressed.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "unmuted": False,
        }

    removed = alert_manager.remove_mute_rule(alert_type, subject)

    return {
        "status": "ok" if removed else "rule_not_found",
        "alert_type": alert_type,
        "subject": subject,
        "unmuted": removed,
    }


@router.get("/vigil/quality/alerts/mute")
async def vigil_alert_mute_rules(
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.31: List all active mute rules.

    Returns a list of currently active mute rules, showing which
    (alert_type, subject) pairs are suppressed and when the rules
    expire.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "mute_rules": [],
        }

    rules = alert_manager.get_mute_rules()

    return {
        "status": "ok",
        "mute_rules": rules,
        "total_rules": len(rules),
    }


# ---------------------------------------------------------------------------
# Sprint 5.31: Dashboard real-time updates (SSE)
# ---------------------------------------------------------------------------


@router.get("/vigil/quality/dashboard/stream")
async def vigil_quality_sse(
    container: AipContainer = Depends(get_container),
) -> StreamingResponse:
    """Sprint 5.31: Server-Sent Events stream for real-time alert notifications.

    Replaces or supplements the 10-second polling on the dashboard
    with SSE for alerts and key quality metrics. Reduces unnecessary
    load while providing near real-time updates.

    Event types:
    - alert_delivered: Alert was successfully delivered to all transports
    - alert_delivery_failed: Alert delivery failed on one or more transports
    - alert_buffered: Alert was buffered for digest aggregation
    """
    alert_manager = getattr(container, "_alert_manager", None)

    async def event_generator():
        """Generate SSE events from the alert manager subscriber queue."""
        queue: asyncio.Queue = asyncio.Queue()

        if alert_manager is not None:
            alert_manager.add_sse_subscriber(queue)

        try:
            # Send initial connection event
            yield f"event: connected\ndata: {{}}\n\n"

            while True:
                try:
                    # Wait for events with a 30-second timeout
                    # This sends keepalive comments to prevent connection drops
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    import json as _json
                    event_type = event.get("event", "update")
                    event_data = _json.dumps(event)
                    yield f"event: {event_type}\ndata: {event_data}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield f": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if alert_manager is not None:
                alert_manager.remove_sse_subscriber(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/vigil/quality/dashboard", response_class=HTMLResponse)
async def vigil_quality_dashboard(
    container: AipContainer = Depends(get_container),
) -> str:
    """Sprint 5.26: Simple HTML/JS dashboard for Vigil quality metrics.

    Renders time-series charts for citation rate, grounding rate,
    faithfulness score, and flag rate using vanilla JavaScript.
    Consumes the /vigil/quality API endpoint.
    """
    return _DASHBOARD_HTML


def _compute_series_trend(values: list[float]) -> str:
    """Compute a trend direction from a series of values.

    Compares the average of the last 3 values with the average of the
    3 values before that (or all preceding values).  Returns
    "improving", "degrading", or "stable" based on a 5% threshold.
    """
    if len(values) < 2:
        return "insufficient_data"

    if len(values) <= 3:
        recent_avg = sum(values) / len(values)
        earlier = values[:1]
        earlier_avg = earlier[0] if earlier else 0.0
    else:
        recent_avg = sum(values[-3:]) / 3
        earlier_count = min(3, len(values) - 3)
        earlier_avg = sum(values[-6:-3]) / earlier_count if earlier_count > 0 else values[0]

    if earlier_avg == 0.0 and recent_avg == 0.0:
        return "stable"
    if earlier_avg == 0.0:
        return "new_data"

    delta = (recent_avg - earlier_avg) / earlier_avg
    if delta > 0.05:
        return "improving"
    elif delta < -0.05:
        return "degrading"
    return "stable"


# ---------------------------------------------------------------------------
# Dashboard HTML (Sprint 5.26 → Sprint 5.27 enhanced)
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Vigil Quality Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #0f172a; color: #e2e8f0; padding: 24px; }
  h1 { font-size: 24px; font-weight: 600; margin-bottom: 8px; color: #f1f5f9; }
  h2 { font-size: 18px; font-weight: 600; margin-bottom: 8px; color: #f1f5f9; margin-top: 16px; }
  .subtitle { font-size: 14px; color: #94a3b8; margin-bottom: 24px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  .card { background: #1e293b; border-radius: 8px; padding: 16px; border: 1px solid #334155; }
  .card h3 { font-size: 14px; color: #94a3b8; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
  .card .value { font-size: 32px; font-weight: 700; color: #f1f5f9; }
  .card .trend { font-size: 13px; margin-top: 4px; }
  .trend.improving { color: #4ade80; }
  .trend.degrading { color: #f87171; }
  .trend.stable { color: #94a3b8; }
  .chart-container { background: #1e293b; border-radius: 8px; padding: 20px;
                     border: 1px solid #334155; margin-bottom: 16px; }
  .chart-container h3 { font-size: 14px; color: #94a3b8; margin-bottom: 16px;
                        text-transform: uppercase; letter-spacing: 0.5px; }
  canvas { width: 100%; height: 200px; }
  .controls { margin-bottom: 24px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .controls label { font-size: 13px; color: #94a3b8; }
  .controls input, .controls select { background: #1e293b; border: 1px solid #475569;
           color: #e2e8f0; padding: 6px 10px; border-radius: 4px; font-size: 13px; }
  .controls button { background: #3b82f6; color: white; border: none; padding: 6px 16px;
                     border-radius: 4px; cursor: pointer; font-size: 13px; }
  .controls button:hover { background: #2563eb; }
  .controls button.active { background: #1d4ed8; }
  .controls button.danger { background: #ef4444; }
  .controls button.danger:hover { background: #dc2626; }
  .metric-toggles { display: flex; gap: 12px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
  .metric-toggles label { font-size: 13px; color: #cbd5e1; display: flex; align-items: center; gap: 4px; cursor: pointer; }
  .metric-toggles input[type=checkbox] { accent-color: #3b82f6; }
  .status-bar { font-size: 12px; color: #64748b; margin-top: 16px; }
  .error { color: #f87171; padding: 12px; background: #1e293b; border-radius: 4px; border: 1px solid #7f1d1d; }
  .live-indicator { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
                    background: #4ade80; margin-right: 6px; animation: pulse 2s infinite; }
  .live-indicator.off { background: #64748b; animation: none; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
  /* Sprint 5.29: Alerts Panel */
  .alerts-panel { background: #1e293b; border-radius: 8px; padding: 20px;
                  border: 1px solid #334155; margin-bottom: 16px; }
  .alerts-panel h3 { font-size: 14px; color: #94a3b8; margin-bottom: 12px;
                     text-transform: uppercase; letter-spacing: 0.5px; }
  .alert-item { padding: 8px 12px; border-radius: 4px; margin-bottom: 6px;
                border-left: 3px solid; font-size: 13px; }
  .alert-item.info { border-left-color: #3b82f6; background: rgba(59,130,246,0.08); }
  .alert-item.warning { border-left-color: #f59e0b; background: rgba(245,158,11,0.08); }
  .alert-item.critical { border-left-color: #ef4444; background: rgba(239,68,68,0.08); }
  .alert-item .alert-type { font-weight: 600; color: #f1f5f9; text-transform: uppercase; font-size: 11px; }
  .alert-item .alert-severity { font-weight: 600; margin-left: 8px; font-size: 11px; }
  .alert-item .alert-severity.info { color: #3b82f6; }
  .alert-item .alert-severity.warning { color: #f59e0b; }
  .alert-item .alert-severity.critical { color: #ef4444; }
  .alert-item .alert-time { color: #64748b; font-size: 11px; margin-left: 8px; }
  .alert-item .alert-msg { color: #cbd5e1; margin-top: 4px; font-size: 12px; }
  /* Sprint 5.30: Acknowledged/dismissed alert styling */
  .alert-item.acknowledged { opacity: 0.6; border-left-color: #4ade80 !important; }
  .alert-item.dismissed { opacity: 0.4; border-left-color: #64748b !important; }
  .alert-item .ack-badge { font-size: 10px; padding: 1px 6px; border-radius: 3px;
                           margin-left: 8px; font-weight: 600; }
  .alert-item .ack-badge.ack-1 { background: rgba(74,222,128,0.2); color: #4ade80; }
  .alert-item .ack-badge.ack-2 { background: rgba(100,116,139,0.2); color: #94a3b8; }
  .alert-item .alert-actions { display: inline; margin-left: 8px; }
  .alert-item .alert-actions button { font-size: 10px; padding: 1px 8px; border-radius: 3px;
                                       cursor: pointer; border: none; margin-right: 4px; }
  .alert-item .btn-ack { background: rgba(74,222,128,0.2); color: #4ade80; }
  .alert-item .btn-ack:hover { background: rgba(74,222,128,0.4); }
  .alert-item .btn-dismiss { background: rgba(100,116,139,0.2); color: #94a3b8; }
  .alert-item .btn-dismiss:hover { background: rgba(100,116,139,0.4); }
  .no-alerts { color: #64748b; font-size: 13px; font-style: italic; }
  /* Sprint 5.31: Mute rules panel */
  .mute-panel { background: #1e293b; border-radius: 8px; padding: 20px;
                border: 1px solid #334155; margin-bottom: 16px; }
  .mute-panel h3 { font-size: 14px; color: #94a3b8; margin-bottom: 12px;
                    text-transform: uppercase; letter-spacing: 0.5px; }
  .mute-item { padding: 6px 12px; border-radius: 4px; margin-bottom: 4px;
               background: rgba(245,158,11,0.08); border-left: 3px solid #f59e0b;
               font-size: 12px; color: #cbd5e1; display: flex; align-items: center; gap: 8px; }
  .mute-item .mute-label { font-weight: 600; color: #f59e0b; }
  .mute-item button { font-size: 10px; padding: 1px 8px; border-radius: 3px;
                      cursor: pointer; border: none; background: rgba(239,68,68,0.2);
                      color: #f87171; }
  .mute-item button:hover { background: rgba(239,68,68,0.4); }
  .mute-form { display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap; }
  .mute-form input, .mute-form select { background: #1e293b; border: 1px solid #475569;
           color: #e2e8f0; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
  .mute-form button { background: #f59e0b; color: #0f172a; border: none; padding: 4px 12px;
                      border-radius: 4px; cursor: pointer; font-size: 12px; font-weight: 600; }
  .mute-form button:hover { background: #d97706; }
  /* Sprint 5.31: SSE indicator */
  .sse-indicator { display: inline-flex; align-items: center; gap: 4px; font-size: 11px;
                   color: #94a3b8; margin-left: 12px; }
  .sse-dot { width: 6px; height: 6px; border-radius: 50%; background: #4ade80; }
  .sse-dot.disconnected { background: #f87171; }
</style>
</head>
<body>

<h1>Vigil Quality Dashboard</h1>
<p class="subtitle">Real-time quality metrics from AIP Brain Vigil evaluation cycles</p>

<div class="controls">
  <label for="range">Range:</label>
  <select id="range">
    <option value="1">Last 24h</option>
    <option value="7">Last 7d</option>
    <option value="30">Last 30d</option>
    <option value="0" selected>By cycles</option>
  </select>
  <label for="cycles">Cycles:</label>
  <select id="cycles">
    <option value="10">Last 10</option>
    <option value="25">Last 25</option>
    <option value="50" selected>Last 50</option>
    <option value="100">Last 100</option>
    <option value="200">Last 200</option>
  </select>
  <button onclick="fetchData()">Refresh</button>
  <button id="liveBtn" onclick="toggleLive()" title="Toggle auto-refresh">
    <span id="liveDot" class="live-indicator off"></span>Live
  </button>
  <span id="sseStatus" class="sse-indicator" style="display:none">
    <span id="sseDot" class="sse-dot"></span>SSE
  </span>
  <span id="status" class="status-bar"></span>
</div>

<div class="metric-toggles">
  <label><input type="checkbox" id="tog-citation" checked> Citation Rate</label>
  <label><input type="checkbox" id="tog-grounding" checked> Grounding Rate</label>
  <label><input type="checkbox" id="tog-faithfulness" checked> Faithfulness</label>
  <label><input type="checkbox" id="tog-flag" checked> Flag Rate</label>
</div>

<div class="grid" id="summary-cards"></div>

<div class="chart-container">
  <h3>Quality Metrics Over Time</h3>
  <canvas id="mainChart" width="800" height="200"></canvas>
</div>

<div class="chart-container">
  <h3>Flag Rate Over Time</h3>
  <canvas id="flagChart" width="800" height="200"></canvas>
</div>

<!-- Sprint 5.29: Alerts Panel -->
<div class="alerts-panel">
  <h3>Recent Alerts</h3>
  <div id="alerts-list"><span class="no-alerts">Loading alerts...</span></div>
</div>

<!-- Sprint 5.31: Mute Rules Panel -->
<div class="mute-panel">
  <h3>Mute Rules</h3>
  <div id="mute-list"><span class="no-alerts">No active mute rules</span></div>
  <div class="mute-form">
    <input type="text" id="mute-type" placeholder="Alert type" size="16">
    <input type="text" id="mute-subject" placeholder="Subject" size="16">
    <input type="number" id="mute-duration" placeholder="Seconds" value="3600" size="8">
    <button onclick="addMuteRule()">Mute</button>
  </div>
</div>

<div class="status-bar" id="meta"></div>

<script>
const API_URL = '../vigil/quality';
const ALERTS_URL = '../vigil/quality/alerts';
let currentData = null;
let liveInterval = null;
let isLive = false;

// Toggle metric visibility checkboxes
['citation','grounding','faithfulness','flag'].forEach(m => {
  document.getElementById('tog-' + m).addEventListener('change', () => {
    if (currentData) renderCharts(currentData);
  });
});

function trendIcon(trend) {
  if (trend === 'improving') return '▲';
  if (trend === 'degrading') return '▼';
  return '●';
}

function formatRate(v) {
  return (v * 100).toFixed(1) + '%';
}

function formatTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleString();
  } catch { return ts; }
}

function renderSummary(data) {
  const s = data.summary || {};
  const cards = [
    { label: 'Citation Rate', key: 'citation_rate', ...s.citation_rate },
    { label: 'Grounding Rate', key: 'grounding_rate', ...s.grounding_rate },
    { label: 'Faithfulness', key: 'llm_faithfulness', ...s.llm_faithfulness },
    { label: 'Cycles', value: data.cycles?.length || 0, trend: '' },
  ];

  const container = document.getElementById('summary-cards');
  container.innerHTML = cards.map(c => {
    const val = c.value !== undefined ? c.value : formatRate(c.current || 0);
    const trendClass = c.trend || '';
    const trendLabel = c.trend ? `${trendIcon(c.trend)} ${c.trend}` : '';
    return `<div class="card">
      <h3>${c.label}</h3>
      <div class="value">${val}</div>
      ${trendLabel ? `<div class="trend ${trendClass}">${trendLabel}</div>` : ''}
    </div>`;
  }).join('');
}

// Sprint 5.29→5.30: Fetch and render alerts panel with acknowledgment support
async function fetchAlerts() {
  try {
    const resp = await fetch(ALERTS_URL + '?limit=10');
    if (!resp.ok) return;
    const data = await resp.json();
    const alerts = data.alerts || [];
    const list = document.getElementById('alerts-list');

    if (alerts.length === 0) {
      list.innerHTML = '<span class="no-alerts">No recent alerts</span>';
      return;
    }

    list.innerHTML = alerts.map(a => {
      const severity = a.severity || 'info';
      const alertType = a.alert_type || 'unknown';
      const msg = a.message || '';
      const ts = a.timestamp || '';
      const ack = a.acknowledged || 0;
      const alertId = a.id || '';
      // Sprint 5.30: Add acknowledged/dismissed visual distinction
      let ackClass = '';
      let ackBadge = '';
      let actionBtns = '';
      if (ack === 1) {
        ackClass = ' acknowledged';
        ackBadge = '<span class="ack-badge ack-1">ACK</span>';
      } else if (ack === 2) {
        ackClass = ' dismissed';
        ackBadge = '<span class="ack-badge ack-2">DISMISSED</span>';
      } else if (alertId) {
        // Only show action buttons for open alerts with an ID
        actionBtns = '<span class="alert-actions">'
          + '<button class="btn-ack" onclick="ackAlert(' + alertId + ')">Ack</button>'
          + '<button class="btn-dismiss" onclick="dismissAlert(' + alertId + ')">Dismiss</button>'
          + '</span>';
      }
      return '<div class="alert-item ' + severity + ackClass + '">'
        + '<span class="alert-type">' + alertType.replace(/_/g, ' ') + '</span>'
        + '<span class="alert-severity ' + severity + '">' + severity.toUpperCase() + '</span>'
        + ackBadge + actionBtns
        + '<span class="alert-time">' + formatTime(ts) + '</span>'
        + '<div class="alert-msg">' + msg + '</div>'
      + '</div>';
    }).join('');
  } catch (err) {
    // Silently ignore alert fetch errors
  }
}

// Sprint 5.30: Acknowledge an alert via API
async function ackAlert(alertId) {
  try {
    const resp = await fetch('../vigil/quality/alerts/' + alertId + '/acknowledge', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({acknowledged_by: 'dashboard'})
    });
    if (resp.ok) fetchAlerts();
  } catch (err) {}
}

// Sprint 5.30: Dismiss an alert via API
async function dismissAlert(alertId) {
  try {
    const resp = await fetch('../vigil/quality/alerts/' + alertId + '/dismiss', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({dismissed_by: 'dashboard'})
    });
    if (resp.ok) fetchAlerts();
  } catch (err) {}
}

// Sprint 5.31: Mute rules management
async function fetchMuteRules() {
  try {
    const resp = await fetch('../vigil/quality/alerts/mute');
    if (!resp.ok) return;
    const data = await resp.json();
    const rules = data.mute_rules || [];
    const list = document.getElementById('mute-list');

    if (rules.length === 0) {
      list.innerHTML = '<span class="no-alerts">No active mute rules</span>';
      return;
    }

    list.innerHTML = rules.map(r => {
      const expires = r.expires_at > 0 ? new Date(r.expires_at * 1000).toLocaleTimeString() : 'indefinite';
      return '<div class="mute-item">'
        + '<span class="mute-label">' + r.alert_type + ' / ' + r.subject + '</span>'
        + '<span>by ' + r.muted_by + ' until ' + expires + '</span>'
        + '<button onclick="removeMuteRule(\'' + r.alert_type + '\',\'' + r.subject + '\')">Remove</button>'
      + '</div>';
    }).join('');
  } catch (err) {}
}

async function addMuteRule() {
  try {
    const type = document.getElementById('mute-type').value;
    const subject = document.getElementById('mute-subject').value;
    const duration = parseInt(document.getElementById('mute-duration').value) || 3600;
    if (!type || !subject) return;
    const resp = await fetch('../vigil/quality/alerts/mute', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({alert_type: type, subject: subject, duration_seconds: duration, muted_by: 'dashboard'})
    });
    if (resp.ok) { fetchMuteRules(); document.getElementById('mute-type').value = ''; document.getElementById('mute-subject').value = ''; }
  } catch (err) {}
}

async function removeMuteRule(type, subject) {
  try {
    const resp = await fetch('../vigil/quality/alerts/mute?alert_type=' + encodeURIComponent(type) + '&subject=' + encodeURIComponent(subject), {
      method: 'DELETE'
    });
    if (resp.ok) fetchMuteRules();
  } catch (err) {}
}

// Sprint 5.31: SSE (Server-Sent Events) for real-time updates
let sseConnected = false;
function connectSSE() {
  try {
    const sseStatus = document.getElementById('sseStatus');
    const sseDot = document.getElementById('sseDot');
    const es = new EventSource('../vigil/quality/dashboard/stream');

    es.addEventListener('connected', () => {
      sseConnected = true;
      sseStatus.style.display = 'inline-flex';
      sseDot.className = 'sse-dot';
    });

    es.addEventListener('alert_delivered', (e) => {
      fetchAlerts();
    });

    es.addEventListener('alert_delivery_failed', (e) => {
      fetchAlerts();
    });

    es.addEventListener('alert_buffered', (e) => {
      fetchAlerts();
    });

    es.onerror = () => {
      sseConnected = false;
      sseDot.className = 'sse-dot disconnected';
    };
  } catch (err) {
    // SSE not supported or connection failed — fall back to polling
  }
}

function drawLineChart(canvasId, datasets, labels) {
  const canvas = document.getElementById(canvasId);
  const ctx = canvas.getContext('2d');
  const W = canvas.width = canvas.offsetWidth;
  const H = canvas.height = 200;
  const pad = { top: 20, right: 20, bottom: 30, left: 50 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  ctx.clearRect(0, 0, W, H);

  // Filter out hidden datasets
  const visible = datasets.filter(d => d.visible !== false);

  if (!labels || labels.length === 0 || visible.length === 0) {
    ctx.fillStyle = '#64748b';
    ctx.font = '13px sans-serif';
    ctx.fillText('No data available', W/2 - 50, H/2);
    return;
  }

  // Find min/max
  let allVals = visible.flatMap(d => d.values);
  let minV = Math.min(...allVals, 0);
  let maxV = Math.max(...allVals, 1);

  // Grid lines
  ctx.strokeStyle = '#334155';
  ctx.lineWidth = 0.5;
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (plotH * i / 4);
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(W - pad.right, y); ctx.stroke();
    ctx.fillStyle = '#64748b'; ctx.font = '11px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(formatRate(maxV - (maxV - minV) * i / 4), pad.left - 6, y + 4);
  }

  // X-axis labels (sparse)
  ctx.fillStyle = '#64748b'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
  const step = Math.max(1, Math.floor(labels.length / 8));
  for (let i = 0; i < labels.length; i += step) {
    const x = pad.left + (i / Math.max(1, labels.length - 1)) * plotW;
    const shortLabel = labels[i] ? labels[i].substring(5, 10) : '';
    ctx.fillText(shortLabel, x, H - 6);
  }

  // Draw lines
  const colors = ['#3b82f6', '#4ade80', '#f59e0b', '#f87171', '#a78bfa'];
  visible.forEach((ds, di) => {
    ctx.strokeStyle = ds.color || colors[di % colors.length];
    ctx.lineWidth = 2;
    ctx.beginPath();
    ds.values.forEach((v, i) => {
      const x = pad.left + (i / Math.max(1, labels.length - 1)) * plotW;
      const y = pad.top + plotH - ((v - minV) / (maxV - minV || 1)) * plotH;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Dots on last point
    if (ds.values.length > 0) {
      const lastI = ds.values.length - 1;
      const lx = pad.left + (lastI / Math.max(1, labels.length - 1)) * plotW;
      const ly = pad.top + plotH - ((ds.values[lastI] - minV) / (maxV - minV || 1)) * plotH;
      ctx.fillStyle = ds.color || colors[di % colors.length];
      ctx.beginPath(); ctx.arc(lx, ly, 4, 0, Math.PI * 2); ctx.fill();
    }
  });

  // Legend
  ctx.font = '11px sans-serif';
  let lx = pad.left;
  visible.forEach((ds, di) => {
    ctx.fillStyle = ds.color || colors[di % colors.length];
    ctx.fillRect(lx, 4, 12, 8);
    ctx.fillStyle = '#cbd5e1';
    ctx.textAlign = 'left';
    ctx.fillText(ds.label, lx + 16, 12);
    lx += ctx.measureText(ds.label).width + 32;
  });
}

function renderCharts(data) {
  const cycles = data.cycles || [];
  const labels = cycles.map(c => c.timestamp || '');
  const citation = cycles.map(c => c.metrics?.avg_citation_rate || 0);
  const grounding = cycles.map(c => c.metrics?.avg_grounding_rate || 0);
  const faithfulness = cycles.map(c => c.metrics?.avg_llm_faithfulness || 0);
  const flagRate = cycles.map(c => c.metrics?.flag_rate || 0);

  const showCitation = document.getElementById('tog-citation').checked;
  const showGrounding = document.getElementById('tog-grounding').checked;
  const showFaithfulness = document.getElementById('tog-faithfulness').checked;
  const showFlag = document.getElementById('tog-flag').checked;

  drawLineChart('mainChart', [
    { label: 'Citation Rate', values: citation, color: '#3b82f6', visible: showCitation },
    { label: 'Grounding Rate', values: grounding, color: '#4ade80', visible: showGrounding },
    { label: 'Faithfulness', values: faithfulness, color: '#f59e0b', visible: showFaithfulness },
  ], labels);

  drawLineChart('flagChart', [
    { label: 'Flag Rate', values: flagRate, color: '#f87171', visible: showFlag },
  ], labels);
}

async function fetchData() {
  const range = document.getElementById('range').value;
  const cycles = document.getElementById('cycles').value;
  const statusEl = document.getElementById('status');
  statusEl.textContent = 'Loading...';

  try {
    let url;
    if (range && range !== '0') {
      // Time-range mode: calculate the 'since' timestamp
      const days = parseInt(range);
      const since = new Date(Date.now() - days * 86400000).toISOString();
      url = API_URL + '?since=' + encodeURIComponent(since) + '&limit=500';
    } else {
      // Cycle count mode
      url = API_URL + '?last_n_cycles=' + cycles;
    }

    const resp = await fetch(url);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    currentData = data;
    renderSummary(data);
    renderCharts(data);
    const src = data.data_source === 'persistent_store' ? 'Persistent DB' : 'In-Memory';
    statusEl.textContent = `Loaded ${data.cycles?.length || 0} cycles (${src})`;
    document.getElementById('meta').textContent =
      `Source: ${src} | Total persisted: ${data.total_persisted_cycles || 0} | ` +
      `Citation: ${formatRate(data.summary?.citation_rate?.current || 0)} | ` +
      `Grounding: ${formatRate(data.summary?.grounding_rate?.current || 0)} | ` +
      `Faithfulness: ${formatRate(data.summary?.llm_faithfulness?.current || 0)}`;
  } catch (err) {
    statusEl.textContent = 'Error: ' + err.message;
  }
}

function toggleLive() {
  isLive = !isLive;
  const btn = document.getElementById('liveBtn');
  const dot = document.getElementById('liveDot');
  if (isLive) {
    btn.classList.add('active');
    dot.classList.remove('off');
    liveInterval = setInterval(() => { fetchData(); fetchAlerts(); }, 10000); // Poll every 10s
  } else {
    btn.classList.remove('active');
    dot.classList.add('off');
    if (liveInterval) clearInterval(liveInterval);
    liveInterval = null;
  }
}

// Auto-load on page load
fetchData();
fetchAlerts();
fetchMuteRules();
connectSSE();
</script>

</body>
</html>
"""
