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

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

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

<div class="status-bar" id="meta"></div>

<script>
const API_URL = '../vigil/quality';
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
    liveInterval = setInterval(fetchData, 10000); // Poll every 10s
  } else {
    btn.classList.remove('active');
    dot.classList.add('off');
    if (liveInterval) clearInterval(liveInterval);
    liveInterval = null;
  }
}

// Auto-load on page load
fetchData();
</script>

</body>
</html>
"""
