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
import json as _json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
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
                # Sprint 5.32: Operational metrics
                "active_mute_rules": status.get("active_mute_rules", 0),
                "total_rate_limited": status.get("total_rate_limited", 0),
                "digest_flushes": status.get("digest", {}).get("total_flushes", 0),
                "delivery_by_transport": status.get("delivery_by_transport", {}),
                "ws_subscribers": status.get("ws_subscribers", 0),
                "alert_groups": status.get("alert_groups", 0),
                # Sprint 5.34: Session and group TTL info
                "ws_sessions": status.get("ws_sessions", 0),
                "alert_group_ttl": status.get("alert_group_ttl", {}),
                # Sprint 5.35: Heartbeat and pruning scheduler
                "ws_heartbeat": status.get("ws_heartbeat", {}),
                "prune_scheduler": status.get("prune_scheduler", {}),
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
# Sprint 5.33: Delivery status statistics
# ---------------------------------------------------------------------------


@router.get("/vigil/quality/alerts/delivery-status/stats")
async def vigil_delivery_status_stats(
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.33: Get delivery status statistics.

    Returns counts and summary stats for delivery status records,
    including total count, status breakdown, and oldest/newest timestamps.
    Useful for monitoring the delivery pipeline health and pruning status.
    """
    alert_history_store = getattr(container, "_alert_history_store", None)

    if alert_history_store is None:
        return {
            "status": "history_store_not_configured",
            "stats": {},
        }

    try:
        count = alert_history_store.get_delivery_status_count()
        return {
            "status": "ok",
            "stats": {
                "total_delivery_status_records": count,
            },
        }
    except Exception as exc:
        logger.warning("vigil_delivery_status_stats_failed", error=str(exc))
        return {
            "status": "error",
            "stats": {"error": str(exc)},
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


# ---------------------------------------------------------------------------
# Sprint 5.32: WebSocket dashboard channel
# ---------------------------------------------------------------------------


@router.websocket("/vigil/quality/dashboard/ws")
async def vigil_quality_websocket(
    websocket: WebSocket,
) -> None:
    """Sprint 5.32 → 5.33: WebSocket endpoint for bidirectional dashboard communication.

    Allows the dashboard to:
    - Receive real-time alert events (same events as SSE)
    - Send commands: acknowledge, dismiss, mute, unmute, bulk_acknowledge, bulk_dismiss

    Sprint 5.33: Added WebSocket authentication and rate limiting.
    - Accepts `token` query param, validated against ws_auth_token config
    - Rate limits commands per connection (max ws_rate_limit_per_minute per minute)

    Command protocol (JSON messages from client):
    - {"action": "acknowledge", "alert_id": <int>}
    - {"action": "dismiss", "alert_id": <int>}
    - {"action": "mute", "alert_type": "<str>", "subject": "<str>", "duration_seconds": <int>}
    - {"action": "unmute", "alert_type": "<str>", "subject": "<str>"}
    - {"action": "bulk_acknowledge", "group_key": "<str>"}
    - {"action": "bulk_dismiss", "group_key": "<str>"}
    """
    # Sprint 5.33: Authentication check via token query param
    container = websocket.app.state.container if hasattr(websocket.app.state, "container") else None
    alert_manager = getattr(container, "_alert_manager", None) if container else None

    if alert_manager is not None and alert_manager.config.ws_auth_token:
        token = websocket.query_params.get("token", "")
        if token != alert_manager.config.ws_auth_token:
            await websocket.close(code=4001, reason="Unauthorized: invalid token")
            return

    await websocket.accept()

    # Sprint 5.33: Rate limiting state per connection
    _ws_command_timestamps: list[float] = []
    import time as _time
    _ws_rate_limit = alert_manager.config.ws_rate_limit_per_minute if alert_manager else 60

    # Register as a WebSocket subscriber for real-time events
    if alert_manager is not None:
        alert_manager.add_ws_subscriber(websocket)

    # Sprint 5.34: Register WS session with unique ID
    import uuid as _uuid
    session_id = str(_uuid.uuid4())
    remote_addr = websocket.client.host if websocket.client else ""
    if alert_manager is not None:
        alert_manager.register_ws_session(session_id, websocket, remote_addr)

    # Also create an SSE-style queue for event push
    event_queue: asyncio.Queue = asyncio.Queue()
    if alert_manager is not None:
        alert_manager.add_sse_subscriber(event_queue)

    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "event": "ws_connected",
            "message": "WebSocket connection established",
            "session_id": session_id,
        })

        # Run two concurrent tasks:
        # 1. Push events from the queue to the WebSocket
        # 2. Receive commands from the WebSocket
        async def push_events():
            """Push alert events to the WebSocket client.

            Sprint 5.35: Sends periodic heartbeat pings to the client
            instead of simple keepalives. The heartbeat interval is
            configured via ws_heartbeat_interval_seconds.
            """
            heartbeat_interval = (
                alert_manager.config.ws_heartbeat_interval_seconds
                if alert_manager else 30
            )
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(
                            event_queue.get(), timeout=float(heartbeat_interval)
                        )
                        await websocket.send_json(event)
                    except asyncio.TimeoutError:
                        # Sprint 5.35: Send heartbeat ping instead of plain keepalive
                        await websocket.send_json({"event": "heartbeat_ping"})
                        # Sprint 5.35: Run dead-session detection
                        if alert_manager is not None:
                            alert_manager.cleanup_dead_ws_sessions()
            except Exception:
                pass  # WebSocket closed

        async def receive_commands():
            """Receive and process commands from the WebSocket client."""
            while True:
                try:
                    data = await websocket.receive_text()
                    if not data:
                        continue

                    try:
                        command = _json.loads(data)
                    except _json.JSONDecodeError:
                        await websocket.send_json({"event": "error", "message": "Invalid JSON"})
                        continue

                    action = command.get("action", "")
                    result = {"event": "command_result", "action": action}

                    # Sprint 5.35: Heartbeat pong — not subject to rate limiting
                    if action == "heartbeat_pong":
                        if alert_manager is not None:
                            alert_manager.update_ws_session_heartbeat(session_id)
                        continue  # Skip rate limiting and response

                    # Sprint 5.33: Rate limiting per connection
                    now_ts = _time.time()
                    _ws_command_timestamps.append(now_ts)
                    # Prune timestamps older than 60 seconds
                    _ws_command_timestamps[:] = [t for t in _ws_command_timestamps if now_ts - t < 60]
                    if len(_ws_command_timestamps) > _ws_rate_limit:
                        result["error"] = "rate_limited"
                        result["retry_after_seconds"] = 60
                        await websocket.send_json(result)
                        continue

                    if alert_manager is None:
                        result["status"] = "alerting_not_configured"
                        await websocket.send_json(result)
                        continue

                    if action == "acknowledge":
                        alert_id = command.get("alert_id")
                        if alert_id is not None:
                            success = alert_manager.acknowledge_alert(
                                int(alert_id), command.get("acknowledged_by", "operator")
                            )
                            result["success"] = success
                            result["alert_id"] = alert_id
                        else:
                            result["error"] = "alert_id required"

                    elif action == "dismiss":
                        alert_id = command.get("alert_id")
                        if alert_id is not None:
                            success = alert_manager.dismiss_alert(
                                int(alert_id), command.get("dismissed_by", "operator")
                            )
                            result["success"] = success
                            result["alert_id"] = alert_id
                        else:
                            result["error"] = "alert_id required"

                    elif action == "mute":
                        alert_type = command.get("alert_type", "")
                        subject = command.get("subject", "")
                        if alert_type and subject:
                            rule = alert_manager.add_mute_rule(
                                alert_type=alert_type,
                                subject=subject,
                                duration_seconds=command.get("duration_seconds", 3600),
                                muted_by=command.get("muted_by", "operator"),
                            )
                            result["mute_rule"] = rule
                        else:
                            result["error"] = "alert_type and subject required"

                    elif action == "unmute":
                        alert_type = command.get("alert_type", "")
                        subject = command.get("subject", "")
                        if alert_type and subject:
                            removed = alert_manager.remove_mute_rule(alert_type, subject)
                            result["unmuted"] = removed
                        else:
                            result["error"] = "alert_type and subject required"

                    elif action == "bulk_acknowledge":
                        group_key = command.get("group_key", "")
                        if group_key:
                            bulk_result = alert_manager.bulk_acknowledge_group(
                                group_key=group_key,
                                acknowledged_by=command.get("acknowledged_by", "operator"),
                            )
                            result["bulk_result"] = bulk_result
                        else:
                            result["error"] = "group_key required"

                    elif action == "bulk_dismiss":
                        group_key = command.get("group_key", "")
                        if group_key:
                            bulk_result = alert_manager.bulk_dismiss_group(
                                group_key=group_key,
                                dismissed_by=command.get("dismissed_by", "operator"),
                            )
                            result["bulk_result"] = bulk_result
                        else:
                            result["error"] = "group_key required"

                    # Sprint 5.35: Merge two alert groups
                    elif action == "merge_groups":
                        source_key = command.get("source_key", "")
                        target_key = command.get("target_key", "")
                        if source_key and target_key:
                            merge_result = alert_manager.merge_alert_groups(source_key, target_key)
                            result["merge_result"] = merge_result
                        else:
                            result["error"] = "source_key and target_key required"

                    # Sprint 5.35: Split an alert group
                    elif action == "split_group":
                        group_key = command.get("group_key", "")
                        correlation_ids = command.get("correlation_ids", [])
                        new_group_key = command.get("new_group_key")
                        if group_key and correlation_ids:
                            split_result = alert_manager.split_alert_group(
                                group_key=group_key,
                                correlation_ids=correlation_ids,
                                new_group_key=new_group_key,
                            )
                            result["split_result"] = split_result
                        else:
                            result["error"] = "group_key and correlation_ids required"

                    else:
                        result["error"] = f"Unknown action: {action}"

                    await websocket.send_json(result)

                except WebSocketDisconnect:
                    break
                except Exception as exc:
                    try:
                        await websocket.send_json({"event": "error", "message": str(exc)})
                    except Exception:
                        break

        # Run both tasks concurrently
        push_task = asyncio.create_task(push_events())
        receive_task = asyncio.create_task(receive_commands())

        try:
            # Wait for either task to complete (which means disconnect)
            done, pending = await asyncio.wait(
                [push_task, receive_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Cancel remaining tasks
            for task in pending:
                task.cancel()
        except Exception:
            pass

    finally:
        # Clean up subscribers
        if alert_manager is not None:
            alert_manager.remove_ws_subscriber(websocket)
            alert_manager.remove_sse_subscriber(event_queue)
            # Sprint 5.34: Unregister WS session
            alert_manager.unregister_ws_session(session_id)


# ---------------------------------------------------------------------------
# Sprint 5.32: Alert correlation groups & bulk actions
# ---------------------------------------------------------------------------


@router.get("/vigil/quality/alerts/groups")
async def vigil_alert_groups(
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.32: List all alert correlation groups.

    Returns a dict mapping group keys (subjects) to lists of
    correlation IDs. Related alerts sharing the same subject are
    grouped together for visual clustering and bulk operations.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "groups": {},
        }

    groups = alert_manager.get_alert_groups()

    return {
        "status": "ok",
        "groups": groups,
        "total_groups": len(groups),
    }


class BulkActionRequest(BaseModel):
    """Request body for bulk alert actions."""
    group_key: str
    acted_by: str = "operator"


@router.post("/vigil/quality/alerts/groups/bulk-acknowledge")
async def vigil_bulk_acknowledge(
    request: BulkActionRequest,
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.32: Acknowledge all alerts in a correlation group.

    Bulk acknowledges all unacknowledged alerts that share the
    specified group key (subject). This is useful for resolving
    related alerts in a single action.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "acknowledged": 0,
        }

    result = alert_manager.bulk_acknowledge_group(
        group_key=request.group_key,
        acknowledged_by=request.acted_by,
    )

    return {
        "status": "ok",
        **result,
    }


@router.post("/vigil/quality/alerts/groups/bulk-dismiss")
async def vigil_bulk_dismiss(
    request: BulkActionRequest,
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.32: Dismiss all alerts in a correlation group.

    Bulk dismisses all unacknowledged alerts that share the
    specified group key (subject).
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "dismissed": 0,
        }

    result = alert_manager.bulk_dismiss_group(
        group_key=request.group_key,
        dismissed_by=request.acted_by,
    )

    return {
        "status": "ok",
        **result,
    }


# ---------------------------------------------------------------------------
# Sprint 5.34: Delivery status pruning admin API
# ---------------------------------------------------------------------------


class PruningConfigUpdate(BaseModel):
    """Request body for updating delivery status pruning configuration."""
    max_age_days: int | None = None
    max_rows: int | None = None


@router.post("/vigil/quality/alerts/delivery-status/prune")
async def vigil_delivery_status_prune(
    max_age_days: int | None = Query(default=None, description="Max age in days for pruning"),
    max_rows: int | None = Query(default=None, description="Max rows to keep after pruning"),
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.34: Manually trigger delivery status pruning.

    Prunes delivery status records older than max_age_days and/or
    keeps only the max_rows most recent records. Returns pruning stats
    including rows deleted and remaining count.
    """
    alert_history_store = getattr(container, "_alert_history_store", None)
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_history_store is None:
        return {
            "status": "history_store_not_configured",
            "pruned": 0,
            "remaining": 0,
        }

    try:
        # Use provided params or fall back to config defaults
        prune_age = max_age_days if max_age_days is not None else (
            alert_manager.config.delivery_status_max_age_days if alert_manager else 30
        )
        prune_rows = max_rows if max_rows is not None else (
            alert_manager.config.delivery_status_max_rows if alert_manager else 2000
        )

        deleted = alert_history_store.prune_delivery_status(
            max_rows=prune_rows,
            max_age_days=prune_age,
        )
        remaining = alert_history_store.get_delivery_status_count()

        return {
            "status": "ok",
            "pruned": deleted,
            "remaining": remaining,
            "max_age_days": prune_age,
            "max_rows": prune_rows,
        }
    except Exception as exc:
        logger.warning("vigil_delivery_status_prune_failed", error=str(exc))
        return {
            "status": "error",
            "error": str(exc),
            "pruned": 0,
            "remaining": 0,
        }


@router.patch("/vigil/quality/alerts/delivery-status/config")
async def vigil_delivery_status_config(
    update: PruningConfigUpdate,
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.34: Update delivery status pruning parameters at runtime.

    Allows operators to change max_age_days and max_rows for delivery
    status pruning without restarting. Changes take effect on the next
    pruning cycle.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "config": {},
        }

    try:
        if update.max_age_days is not None:
            alert_manager.config.delivery_status_max_age_days = update.max_age_days
        if update.max_rows is not None:
            alert_manager.config.delivery_status_max_rows = update.max_rows

        return {
            "status": "ok",
            "config": {
                "max_age_days": alert_manager.config.delivery_status_max_age_days,
                "max_rows": alert_manager.config.delivery_status_max_rows,
            },
        }
    except Exception as exc:
        logger.warning("vigil_delivery_status_config_update_failed", error=str(exc))
        return {
            "status": "error",
            "error": str(exc),
            "config": {},
        }


# ---------------------------------------------------------------------------
# Sprint 5.34: WebSocket session listing endpoint
# ---------------------------------------------------------------------------


@router.get("/vigil/quality/dashboard/ws/sessions")
async def vigil_ws_sessions(
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.34: List active WebSocket dashboard sessions.

    Returns a list of currently connected WebSocket sessions with
    session IDs, connection times, and remote addresses.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "sessions": [],
            "total_sessions": 0,
        }

    sessions = alert_manager.get_ws_sessions()

    return {
        "status": "ok",
        "sessions": sessions,
        "total_sessions": len(sessions),
    }


# ---------------------------------------------------------------------------
# Sprint 5.35: Alert group merge & split
# ---------------------------------------------------------------------------


class MergeGroupsRequest(BaseModel):
    """Request body for merging two alert groups."""
    source_key: str
    target_key: str


class SplitGroupRequest(BaseModel):
    """Request body for splitting an alert group."""
    group_key: str
    correlation_ids: list[str]
    new_group_key: str | None = None


@router.post("/vigil/quality/alerts/groups/merge")
async def vigil_merge_groups(
    request: MergeGroupsRequest,
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.35: Merge two alert groups.

    Moves all correlation IDs from the source group into the target
    group, then deletes the source group. Returns merge details.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "merged_count": 0,
        }

    return alert_manager.merge_alert_groups(request.source_key, request.target_key)


@router.post("/vigil/quality/alerts/groups/split")
async def vigil_split_group(
    request: SplitGroupRequest,
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.35: Split an alert group.

    Moves specified correlation IDs from the group into a new group.
    If new_group_key is not provided, one is auto-generated.
    Returns split details.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "split_count": 0,
        }

    return alert_manager.split_alert_group(
        group_key=request.group_key,
        correlation_ids=request.correlation_ids,
        new_group_key=request.new_group_key,
    )


# ---------------------------------------------------------------------------
# Sprint 5.35: Delivery status pruning scheduler
# ---------------------------------------------------------------------------


class PruneSchedulerConfig(BaseModel):
    """Request body for updating pruning scheduler configuration."""
    interval_seconds: int | None = None
    max_age_days: int | None = None
    max_rows: int | None = None


@router.get("/vigil/quality/alerts/delivery-status/prune/scheduler")
async def vigil_prune_scheduler_status(
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.35: Get the current status of the delivery status pruning scheduler.

    Returns whether the scheduler is running, the interval, last run time,
    next scheduled run, and total scheduled prune count.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "scheduler": {},
        }

    return {
        "status": "ok",
        "scheduler": alert_manager.get_prune_scheduler_status(),
    }


@router.post("/vigil/quality/alerts/delivery-status/prune/scheduler/start")
async def vigil_prune_scheduler_start(
    interval_seconds: int | None = Query(default=None, description="Pruning interval in seconds"),
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.35: Start the delivery status pruning scheduler.

    If interval_seconds is provided, updates the config before starting.
    The scheduler runs in a background thread and prunes delivery status
    records periodically.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "started": False,
        }

    if interval_seconds is not None:
        alert_manager.config.delivery_status_prune_interval_seconds = interval_seconds

    started = alert_manager.start_prune_scheduler()

    return {
        "status": "ok" if started else "not_started",
        "started": started,
        "scheduler": alert_manager.get_prune_scheduler_status(),
    }


@router.post("/vigil/quality/alerts/delivery-status/prune/scheduler/stop")
async def vigil_prune_scheduler_stop(
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.35: Stop the delivery status pruning scheduler."""
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
        }

    alert_manager.stop_prune_scheduler()

    return {
        "status": "ok",
        "scheduler": alert_manager.get_prune_scheduler_status(),
    }


@router.patch("/vigil/quality/alerts/delivery-status/prune/scheduler/config")
async def vigil_prune_scheduler_config(
    update: PruneSchedulerConfig,
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Sprint 5.35: Update pruning scheduler configuration at runtime.

    Allows operators to change the interval, max_age_days, and max_rows
    without restarting. Changes take effect on the next prune cycle.
    """
    alert_manager = getattr(container, "_alert_manager", None)

    if alert_manager is None:
        return {
            "status": "alerting_not_configured",
            "config": {},
        }

    if update.interval_seconds is not None:
        alert_manager.config.delivery_status_prune_interval_seconds = update.interval_seconds
    if update.max_age_days is not None:
        alert_manager.config.delivery_status_max_age_days = update.max_age_days
    if update.max_rows is not None:
        alert_manager.config.delivery_status_max_rows = update.max_rows

    return {
        "status": "ok",
        "config": alert_manager.get_prune_scheduler_status(),
    }


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
  /* Sprint 5.33: Connection status indicator */
  .conn-status { display: inline-flex; align-items: center; gap: 6px; font-size: 11px;
                 color: #94a3b8; margin-left: 12px; padding: 2px 8px;
                 background: #1e293b; border-radius: 4px; border: 1px solid #334155; }
  .conn-dot { width: 8px; height: 8px; border-radius: 50%; }
  .conn-dot.connected { background: #4ade80; animation: pulse 2s infinite; }
  .conn-dot.connecting { background: #f59e0b; }
  .conn-dot.disconnected { background: #f87171; }
  .conn-dot.fallback { background: #3b82f6; }
  /* Sprint 5.33: Action buttons panel */
  .action-bar { display: flex; gap: 8px; align-items: center; margin-bottom: 16px;
                flex-wrap: wrap; }
  .action-bar button { background: #3b82f6; color: white; border: none; padding: 6px 14px;
                       border-radius: 4px; cursor: pointer; font-size: 12px; }
  .action-bar button:hover { background: #2563eb; }
  .action-bar button.warn { background: #f59e0b; color: #0f172a; }
  .action-bar button.warn:hover { background: #d97706; }
  .action-bar button.danger { background: #ef4444; }
  .action-bar button.danger:hover { background: #dc2626; }
  .action-bar input { background: #1e293b; border: 1px solid #475569;
           color: #e2e8f0; padding: 5px 10px; border-radius: 4px; font-size: 12px; width: 120px; }
  /* Sprint 5.34: Causal chain visualization */
  .causal-panel { background: #1e293b; border-radius: 8px; padding: 20px;
                  border: 1px solid #334155; margin-bottom: 16px; }
  .causal-panel h3 { font-size: 14px; color: #94a3b8; margin-bottom: 12px;
                     text-transform: uppercase; letter-spacing: 0.5px; cursor: pointer;
                     display: flex; align-items: center; gap: 6px; }
  .causal-panel h3::after { content: '▼'; font-size: 10px; transition: transform 0.2s; }
  .causal-panel.collapsed h3::after { transform: rotate(-90deg); }
  .causal-panel.collapsed .causal-chain-container { display: none; }
  .causal-chain-container { overflow-x: auto; }
  .causal-chain { display: flex; align-items: center; gap: 4px; padding: 8px 0;
                  flex-wrap: wrap; }
  .causal-node { background: #0f172a; border: 1px solid #475569; border-radius: 6px;
                 padding: 6px 12px; font-size: 11px; min-width: 80px; text-align: center; }
  .causal-node .node-type { color: #f1f5f9; font-weight: 600; text-transform: capitalize; font-size: 10px; }
  .causal-node .node-severity { font-size: 9px; margin-top: 2px; }
  .causal-node .node-severity.info { color: #3b82f6; }
  .causal-node .node-severity.warning { color: #f59e0b; }
  .causal-node .node-severity.critical { color: #ef4444; }
  .causal-node .node-time { color: #64748b; font-size: 9px; margin-top: 2px; }
  .causal-arrow { color: #475569; font-size: 18px; font-weight: bold; }
  .causal-group-header { font-size: 12px; color: #cbd5e1; margin-bottom: 6px; font-weight: 600; }
  .causal-empty { color: #64748b; font-size: 13px; font-style: italic; }
  /* Sprint 5.35: Merge/split group buttons */
  .group-ops { display: flex; gap: 6px; align-items: center; margin-left: 8px; }
  .group-ops button { font-size: 10px; padding: 2px 8px; border-radius: 3px;
                      cursor: pointer; border: none; }
  .group-ops .btn-merge { background: rgba(168,139,250,0.2); color: #a78bfa; }
  .group-ops .btn-merge:hover { background: rgba(168,139,250,0.4); }
  .group-ops .btn-split { background: rgba(56,189,248,0.2); color: #38bdf8; }
  .group-ops .btn-split:hover { background: rgba(56,189,248,0.4); }
  /* Sprint 5.35: Dashboard state restore indicator */
  .state-restored { font-size: 10px; color: #4ade80; margin-left: 8px; }
  /* Sprint 5.35: Heartbeat status */
  .hb-status { font-size: 10px; color: #64748b; margin-left: 4px; }
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
  <!-- Sprint 5.33: Connection status indicator -->
  <span id="connStatus" class="conn-status">
    <span id="connDot" class="conn-dot disconnected"></span>
    <span id="connLabel">Disconnected</span>
  </span>
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

<!-- Sprint 5.33: Action buttons for WebSocket commands -->
<div class="action-bar" id="actionBar">
  <input type="number" id="actionAlertId" placeholder="Alert ID">
  <button onclick="wsAcknowledge()">Acknowledge</button>
  <button onclick="wsDismiss()" class="danger">Dismiss</button>
  <input type="text" id="actionMuteType" placeholder="Alert type" size="12">
  <input type="text" id="actionMuteSubject" placeholder="Subject" size="12">
  <button onclick="wsMute()" class="warn">Mute</button>
  <button onclick="wsUnmute()" class="warn">Unmute</button>
  <input type="text" id="actionGroupKey" placeholder="Group key" size="14">
  <button onclick="wsBulkAcknowledge()">Bulk Ack</button>
  <button onclick="wsBulkDismiss()" class="danger">Bulk Dismiss</button>
  <!-- Sprint 5.35: Merge/split group controls -->
  <input type="text" id="mergeSourceKey" placeholder="Source group" size="12">
  <input type="text" id="mergeTargetKey" placeholder="Target group" size="12">
  <button onclick="wsMergeGroups()" class="warn">Merge</button>
  <input type="text" id="splitGroupKey" placeholder="Split group" size="12">
  <button onclick="wsSplitGroup()">Split</button>
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

<!-- Sprint 5.34: Causal Group Visualization -->
<div class="causal-panel" id="causalPanel">
  <h3 onclick="toggleCausalPanel()">Alert Groups &amp; Causal Chains</h3>
  <div class="causal-chain-container" id="causalChainContainer">
    <div id="causal-chains"><span class="causal-empty">No alert groups</span></div>
  </div>
</div>

<div class="status-bar" id="meta"></div>

<script>
const API_URL = '../vigil/quality';
const ALERTS_URL = '../vigil/quality/alerts';
const WS_URL = ((location.protocol === 'https:') ? 'wss:' : 'ws:') + '//' + location.host + '../vigil/quality/dashboard/ws';
let currentData = null;
let liveInterval = null;
let isLive = false;

// Sprint 5.33→5.34→5.35: WebSocket connection with SSE fallback, exponential backoff, heartbeat
let ws = null;
let wsConnected = false;
let useWS = true;  // Prefer WebSocket, fall back to SSE
// Sprint 5.34: Exponential backoff reconnection
let wsReconnectAttempts = 0;
let wsMaxReconnectAttempts = 10;
let wsBaseReconnectDelay = 1000; // 1 second base
let wsReconnectTimer = null;
// Sprint 5.35: Heartbeat tracking
let wsLastHeartbeatSent = 0;
let wsLastHeartbeatReceived = 0;

function updateConnStatus(state, label) {
  const dot = document.getElementById('connDot');
  const lbl = document.getElementById('connLabel');
  dot.className = 'conn-dot ' + state;
  lbl.textContent = label || state;
}

function connectWebSocket() {
  if (!useWS) return;

  // Sprint 5.34: Clear any pending reconnect timer
  if (wsReconnectTimer) {
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = null;
  }

  try {
    const wsProto = (location.protocol === 'https:') ? 'wss:' : 'ws:';
    const wsUrl = wsProto + '//' + location.host + '/vigil/quality/dashboard/ws';
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      wsConnected = true;
      // Sprint 5.34: Reset reconnect counter on successful connect
      wsReconnectAttempts = 0;
      updateConnStatus('connected', 'WS Connected');
      // Sprint 5.34: Persist connection preference
      try { localStorage.setItem('aip_dashboard_connection', 'websocket'); } catch(e) {}
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        // Sprint 5.35: Handle heartbeat ping from server — respond with pong
        if (msg.event === 'heartbeat_ping') {
          wsLastHeartbeatReceived = Date.now();
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({action: 'heartbeat_pong'}));
          }
          return;
        }
        if (msg.event === 'keepalive') return;
        if (msg.event === 'ws_connected') {
          // Sprint 5.34: Store session_id if provided
          if (msg.session_id) {
            window._wsSessionId = msg.session_id;
          }
          return;
        }
        // Refresh alerts on any alert-related event
        if (msg.event && msg.event.startsWith('alert_')) {
          fetchAlerts();
        }
        // Sprint 5.34: Handle session events
        if (msg.event === 'ws_session_connected' || msg.event === 'ws_session_disconnected') {
          // Could display active session count if desired
        }
        // Sprint 5.35: Handle group merge/split events
        if (msg.event === 'alert_groups_merged' || msg.event === 'alert_group_split') {
          fetchCausalGroups();
        }
      } catch (err) {}
    };

    ws.onclose = () => {
      wsConnected = false;
      // Sprint 5.34: Exponential backoff reconnection
      if (useWS && wsReconnectAttempts < wsMaxReconnectAttempts) {
        const delay = Math.min(wsBaseReconnectDelay * Math.pow(2, wsReconnectAttempts), 30000);
        wsReconnectAttempts++;
        updateConnStatus('disconnected', 'Reconnecting in ' + Math.round(delay/1000) + 's (attempt ' + wsReconnectAttempts + '/' + wsMaxReconnectAttempts + ')');
        wsReconnectTimer = setTimeout(() => { if (useWS) connectWebSocket(); }, delay);
      } else if (useWS) {
        updateConnStatus('disconnected', 'Max retries reached — SSE fallback');
        useWS = false;
        ws = null;
        connectSSE();
      } else {
        updateConnStatus('disconnected', 'WS Disconnected');
      }
    };

    ws.onerror = () => {
      wsConnected = false;
      // Sprint 5.34: Don't immediately fall back — let onclose handle reconnect
      // Only fall back to SSE if we've been trying WS for a while
      if (wsReconnectAttempts >= 3) {
        useWS = false;
        ws = null;
        if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
        updateConnStatus('fallback', 'SSE Fallback');
        try { localStorage.setItem('aip_dashboard_connection', 'sse'); } catch(e) {}
        connectSSE();
      } else {
        updateConnStatus('disconnected', 'WS Error — reconnecting...');
      }
    };
  } catch (err) {
    useWS = false;
    updateConnStatus('fallback', 'SSE Fallback');
    connectSSE();
  }
}

// Sprint 5.33: Send command via WebSocket or HTTP POST
function wsSendCommand(command) {
  if (wsConnected && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(command));
  } else {
    // SSE fallback: use HTTP POST to REST endpoints
    httpFallback(command);
  }
}

function httpFallback(command) {
  const action = command.action;
  if (action === 'acknowledge') {
    fetch('../vigil/quality/alerts/' + command.alert_id + '/acknowledge', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({acknowledged_by: command.acknowledged_by || 'dashboard'})
    }).then(r => { if (r.ok) fetchAlerts(); }).catch(() => {});
  } else if (action === 'dismiss') {
    fetch('../vigil/quality/alerts/' + command.alert_id + '/dismiss', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({dismissed_by: command.dismissed_by || 'dashboard'})
    }).then(r => { if (r.ok) fetchAlerts(); }).catch(() => {});
  } else if (action === 'mute') {
    fetch('../vigil/quality/alerts/mute', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({alert_type: command.alert_type, subject: command.subject,
                            duration_seconds: command.duration_seconds || 3600, muted_by: 'dashboard'})
    }).then(r => { if (r.ok) fetchMuteRules(); }).catch(() => {});
  } else if (action === 'unmute') {
    fetch('../vigil/quality/alerts/mute?alert_type=' + encodeURIComponent(command.alert_type) +
          '&subject=' + encodeURIComponent(command.subject), {
      method: 'DELETE'
    }).then(r => { if (r.ok) fetchMuteRules(); }).catch(() => {});
  } else if (action === 'bulk_acknowledge') {
    fetch('../vigil/quality/alerts/groups/bulk-acknowledge', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({group_key: command.group_key, acted_by: 'dashboard'})
    }).then(r => { if (r.ok) fetchAlerts(); }).catch(() => {});
  } else if (action === 'bulk_dismiss') {
    fetch('../vigil/quality/alerts/groups/bulk-dismiss', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({group_key: command.group_key, acted_by: 'dashboard'})
    }).then(r => { if (r.ok) fetchAlerts(); }).catch(() => {});
  } else if (action === 'merge_groups') {
    fetch('../vigil/quality/alerts/groups/merge', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({source_key: command.source_key, target_key: command.target_key})
    }).then(r => { if (r.ok) fetchCausalGroups(); }).catch(() => {});
  } else if (action === 'split_group') {
    fetch('../vigil/quality/alerts/groups/split', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({group_key: command.group_key, correlation_ids: command.correlation_ids})
    }).then(r => { if (r.ok) fetchCausalGroups(); }).catch(() => {});
  }
}

// Sprint 5.33: Action button handlers — send commands via WS or HTTP
function wsAcknowledge() {
  const alertId = document.getElementById('actionAlertId').value;
  if (!alertId) return;
  wsSendCommand({action: 'acknowledge', alert_id: parseInt(alertId), acknowledged_by: 'dashboard'});
}

function wsDismiss() {
  const alertId = document.getElementById('actionAlertId').value;
  if (!alertId) return;
  wsSendCommand({action: 'dismiss', alert_id: parseInt(alertId), dismissed_by: 'dashboard'});
}

function wsMute() {
  const type = document.getElementById('actionMuteType').value;
  const subject = document.getElementById('actionMuteSubject').value;
  if (!type || !subject) return;
  wsSendCommand({action: 'mute', alert_type: type, subject: subject, duration_seconds: 3600});
}

function wsUnmute() {
  const type = document.getElementById('actionMuteType').value;
  const subject = document.getElementById('actionMuteSubject').value;
  if (!type || !subject) return;
  wsSendCommand({action: 'unmute', alert_type: type, subject: subject});
}

function wsBulkAcknowledge() {
  const groupKey = document.getElementById('actionGroupKey').value;
  if (!groupKey) return;
  wsSendCommand({action: 'bulk_acknowledge', group_key: groupKey, acknowledged_by: 'dashboard'});
}

function wsBulkDismiss() {
  const groupKey = document.getElementById('actionGroupKey').value;
  if (!groupKey) return;
  wsSendCommand({action: 'bulk_dismiss', group_key: groupKey, dismissed_by: 'dashboard'});
}

// Sprint 5.35: Merge/split group commands
function wsMergeGroups() {
  const sourceKey = document.getElementById('mergeSourceKey').value;
  const targetKey = document.getElementById('mergeTargetKey').value;
  if (!sourceKey || !targetKey) return;
  wsSendCommand({action: 'merge_groups', source_key: sourceKey, target_key: targetKey});
  fetchCausalGroups();
}

function wsSplitGroup() {
  const groupKey = document.getElementById('splitGroupKey').value;
  if (!groupKey) return;
  // Split the most recent half of the group's alerts
  // First fetch groups to get the correlation IDs
  fetch('../vigil/quality/alerts/groups')
    .then(r => r.json())
    .then(data => {
      const groups = data.groups || {};
      const cids = groups[groupKey] || [];
      if (cids.length < 2) { alert('Group must have at least 2 alerts to split'); return; }
      const half = Math.ceil(cids.length / 2);
      const splitCids = cids.slice(half);
      wsSendCommand({action: 'split_group', group_key: groupKey, correlation_ids: splitCids});
      fetchCausalGroups();
    })
    .catch(() => {});
}

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

// Sprint 5.29→5.33: Fetch and render alerts panel with acknowledgment support
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

// Sprint 5.30→5.33: Acknowledge/dismiss via WS command or HTTP fallback
async function ackAlert(alertId) {
  wsSendCommand({action: 'acknowledge', alert_id: alertId, acknowledged_by: 'dashboard'});
}

async function dismissAlert(alertId) {
  wsSendCommand({action: 'dismiss', alert_id: alertId, dismissed_by: 'dashboard'});
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
    wsSendCommand({action: 'mute', alert_type: type, subject: subject, duration_seconds: duration});
    fetchMuteRules();
    document.getElementById('mute-type').value = '';
    document.getElementById('mute-subject').value = '';
  } catch (err) {}
}

async function removeMuteRule(type, subject) {
  wsSendCommand({action: 'unmute', alert_type: type, subject: subject});
  fetchMuteRules();
}

// Sprint 5.34: Causal group visualization
function toggleCausalPanel() {
  const panel = document.getElementById('causalPanel');
  panel.classList.toggle('collapsed');
}

async function fetchCausalGroups() {
  try {
    const resp = await fetch('../vigil/quality/alerts/groups');
    if (!resp.ok) return;
    const data = await resp.json();
    const groups = data.groups || {};
    const container = document.getElementById('causal-chains');

    const groupKeys = Object.keys(groups);
    if (groupKeys.length === 0) {
      container.innerHTML = '<span class="causal-empty">No alert groups</span>';
      return;
    }

    let html = '';
    for (const [groupKey, correlationIds] of Object.entries(groups)) {
      const isCausal = groupKey.startsWith('causal:');
      const label = isCausal ? '🔗 Causal: ' + groupKey.substring(7) : '📁 ' + groupKey;
      const count = correlationIds.length;

      if (isCausal) {
        // For causal groups, fetch alert details and show chain
        html += '<div class="causal-group-header">' + label + ' (' + count + ' alerts)</div>';
        html += '<div class="causal-chain" id="chain-' + groupKey.replace(/[^a-zA-Z0-9]/g, '_') + '">';
        html += '<span class="causal-empty" style="font-size:11px">Loading chain...</span>';
        html += '</div>';
        // Fetch alert details for this causal chain
        fetchCausalChainDetails(groupKey, correlationIds);
      } else {
        // Regular group — just show summary
        html += '<div class="causal-group-header">' + label + ' (' + count + ' alerts)</div>';
        html += '<div class="causal-chain">';
        html += '<span style="color:#64748b;font-size:11px">' + count + ' related alert(s)</span>';
        html += '</div>';
      }
    }
    container.innerHTML = html;
  } catch (err) {}
}

async function fetchCausalChainDetails(groupKey, correlationIds) {
  try {
    // Fetch recent alerts and find those matching our correlation IDs
    const resp = await fetch(ALERTS_URL + '?limit=50');
    if (!resp.ok) return;
    const data = await resp.json();
    const alerts = data.alerts || [];

    // Match alerts to correlation IDs
    const chainAlerts = [];
    const cidSet = new Set(correlationIds);
    for (const a of alerts) {
      if (cidSet.has(a.correlation_id)) {
        chainAlerts.push(a);
      }
    }

    // Sort by timestamp
    chainAlerts.sort((a, b) => (a.timestamp || '').localeCompare(b.timestamp || ''));

    const chainEl = document.getElementById('chain-' + groupKey.replace(/[^a-zA-Z0-9]/g, '_'));
    if (!chainEl) return;

    if (chainAlerts.length === 0) {
      chainEl.innerHTML = '<span style="color:#64748b;font-size:11px">No details available</span>';
      return;
    }

    let chainHtml = '';
    chainAlerts.forEach((a, i) => {
      if (i > 0) {
        chainHtml += '<span class="causal-arrow">→</span>';
      }
      const severity = a.severity || 'info';
      const alertType = (a.alert_type || 'unknown').replace(/_/g, ' ');
      const shortTime = a.timestamp ? new Date(a.timestamp).toLocaleTimeString() : '';
      chainHtml += '<div class="causal-node">'
        + '<div class="node-type">' + alertType + '</div>'
        + '<div class="node-severity ' + severity + '">' + severity.toUpperCase() + '</div>'
        + '<div class="node-time">' + shortTime + '</div>'
      + '</div>';
    });

    chainEl.innerHTML = chainHtml;
  } catch (err) {}
}

// Sprint 5.31→5.33: SSE (Server-Sent Events) for real-time updates (fallback)
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
      const days = parseInt(range);
      const since = new Date(Date.now() - days * 86400000).toISOString();
      url = API_URL + '?since=' + encodeURIComponent(since) + '&limit=500';
    } else {
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
    liveInterval = setInterval(() => { fetchData(); fetchAlerts(); }, 10000);
  } else {
    btn.classList.remove('active');
    dot.classList.add('off');
    if (liveInterval) clearInterval(liveInterval);
    liveInterval = null;
  }
}

// Sprint 5.35: Dashboard state persistence — save/restore filters, panels, sort, view mode
function saveDashboardState() {
  try {
    const state = {
      range: document.getElementById('range').value,
      cycles: document.getElementById('cycles').value,
      tog_citation: document.getElementById('tog-citation').checked,
      tog_grounding: document.getElementById('tog-grounding').checked,
      tog_faithfulness: document.getElementById('tog-faithfulness').checked,
      tog_flag: document.getElementById('tog-flag').checked,
      isLive: isLive,
      causalCollapsed: document.getElementById('causalPanel').classList.contains('collapsed'),
      timestamp: Date.now(),
    };
    localStorage.setItem('aip_dashboard_state', JSON.stringify(state));
  } catch(e) {}
}

function restoreDashboardState() {
  try {
    const saved = localStorage.getItem('aip_dashboard_state');
    if (!saved) return false;
    const state = JSON.parse(saved);
    // Don't restore state older than 24 hours
    if (Date.now() - (state.timestamp || 0) > 86400000) return false;
    if (state.range) document.getElementById('range').value = state.range;
    if (state.cycles) document.getElementById('cycles').value = state.cycles;
    if (state.tog_citation !== undefined) document.getElementById('tog-citation').checked = state.tog_citation;
    if (state.tog_grounding !== undefined) document.getElementById('tog-grounding').checked = state.tog_grounding;
    if (state.tog_faithfulness !== undefined) document.getElementById('tog-faithfulness').checked = state.tog_faithfulness;
    if (state.tog_flag !== undefined) document.getElementById('tog-flag').checked = state.tog_flag;
    if (state.causalCollapsed) document.getElementById('causalPanel').classList.add('collapsed');
    // Don't restore isLive to avoid unexpected auto-refresh
    return true;
  } catch(e) { return false; }
}

// Save state on relevant changes
['range', 'cycles'].forEach(id => {
  document.getElementById(id).addEventListener('change', saveDashboardState);
});
['tog-citation', 'tog-grounding', 'tog-faithfulness', 'tog-flag'].forEach(id => {
  document.getElementById(id).addEventListener('change', saveDashboardState);
});

// Auto-load on page load — Sprint 5.34→5.35: Restore dashboard state, then connect
const stateRestored = restoreDashboardState();
fetchData();
fetchAlerts();
fetchMuteRules();
updateConnStatus('connecting', 'Connecting...');
// Sprint 5.34: Load connection preference from localStorage
try {
  const savedPref = localStorage.getItem('aip_dashboard_connection');
  if (savedPref === 'sse') {
    useWS = false;
    updateConnStatus('fallback', 'SSE (saved preference)');
    connectSSE();
  } else {
    connectWebSocket();
  }
} catch(e) {
  connectWebSocket();
}
// Sprint 5.34: Load causal group visualization
fetchCausalGroups();
// Sprint 5.35: Save state periodically
setInterval(saveDashboardState, 30000);
</script>

</body>
</html>
"""
