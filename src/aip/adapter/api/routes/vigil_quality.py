"""Vigil quality dashboard endpoint — time-series quality metrics.

Sprint 5.25: Provides a dedicated endpoint for operators to query
Vigil quality metrics over time, enabling charting and trend analysis.

Endpoint: GET /vigil/quality

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

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/vigil/quality")
async def vigil_quality(
    last_n_cycles: int = Query(default=10, ge=1, le=50, description="Number of recent cycles to return"),
    since: str | None = Query(default=None, description="ISO 8601 datetime — only return cycles after this timestamp"),
    container: AipContainer = Depends(get_container),
) -> dict[str, Any]:
    """Return time-series quality metrics from Vigil cycles.

    Returns per-cycle metrics (citation rate, grounding rate, hedging,
    LLM faithfulness) over recent cycles, suitable for charting.

    Filtering:
    - ``last_n_cycles``: Return the most recent N cycles (default 10)
    - ``since``: Only return cycles with timestamps after this value

    The data comes from Vigil's ``_cycle_report_history`` which stores
    the last 10 cycles of quality metrics.
    """
    vigil = getattr(container, "vigil", None)
    if vigil is None:
        return {
            "status": "vigil_not_initialized",
            "cycles": [],
            "total_cycles": 0,
        }

    # Get cycle report history
    cycle_history = getattr(vigil, "_cycle_report_history", [])
    if not cycle_history:
        return {
            "status": "no_cycle_data",
            "cycles": [],
            "total_cycles": 0,
        }

    # Apply since filter if provided
    filtered = cycle_history
    if since:
        filtered = [
            c for c in filtered
            if c.get("timestamp", "") > since
        ]

    # Apply last_n_cycles filter
    filtered = filtered[-last_n_cycles:]

    # Build per-cycle data points
    cycles_data = []
    for i, cycle in enumerate(filtered):
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
    citation_rates = [c.get("avg_citation_rate", 0.0) for c in filtered]
    grounding_rates = [c.get("avg_grounding_rate", 0.0) for c in filtered]
    faithfulness_scores = [c.get("avg_llm_faithfulness", 0.0) for c in filtered]

    summary = {
        "cycles_analyzed": len(filtered),
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

    return {
        "status": "ok",
        "cycles": cycles_data,
        "summary": summary,
        "llm_faithfulness_telemetry": llm_telemetry,
        "config": config_info,
        "total_cycles_available": len(cycle_history),
    }


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
