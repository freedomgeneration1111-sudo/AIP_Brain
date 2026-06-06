"""Events API route — live actor activity feed.

Read-only surface for observing what the actors (Beast, Sexton, Vigil)
are actually doing.  No actions from this surface — observer only.

Returns events from the EventStore (append-only SQLite) with optional
filters for actor, event_type, and limit.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Query

from aip.adapter.api.dependencies import AipContainer, get_container

logger = logging.getLogger(__name__)

router = APIRouter()


def _summarize_event(event_type: str, metadata: dict) -> str:
    """Build a one-line summary from event_type + metadata."""
    # Actor-specific summaries for the most common event types
    if event_type == "beast_heartbeat":
        overall = metadata.get("health_overall", "")
        return f"heartbeat · health={overall}" if overall else "heartbeat"
    if event_type == "beast_health_check":
        overall = metadata.get("overall", "")
        return f"health check · overall={overall}" if overall else "health check"
    if event_type == "beast_cycle_complete":
        domains = metadata.get("domains_processed", [])
        llm = metadata.get("total_llm_calls", 0)
        return f"cycle · domains={len(domains)} llm_calls={llm}"
    if event_type == "beast_corpus_maintenance":
        reembedded = metadata.get("vectors_reembedded", 0)
        stale = metadata.get("stale_vectors_found", 0)
        return f"corpus · stale={stale} reembedded={reembedded}"
    if event_type == "beast_tagging_complete":
        tagged = metadata.get("tagged", 0)
        unclass = metadata.get("unclassified", 0)
        return f"tagging · tagged={tagged} unclassified={unclass}"
    if event_type == "beast_wiki_cycle_complete":
        written = metadata.get("wiki_written", 0)
        skipped = metadata.get("wiki_skipped", 0)
        return f"wiki · written={written} skipped={skipped}"
    if event_type == "beast_entity_stale_detected":
        count = metadata.get("stale_count", 0)
        return f"stale entities · count={count}"
    if event_type == "beast_stale_generated_detected":
        count = metadata.get("count", 0)
        return f"stale generated · count={count}"
    if event_type == "sexton_vigil_start":
        return "vigil cycle started"
    if event_type == "sexton_vigil_complete":
        elapsed = metadata.get("elapsed_seconds", "")
        return f"vigil cycle complete · elapsed={elapsed}" if elapsed else "vigil cycle complete"
    if event_type == "sexton_tagging_complete":
        tagged = metadata.get("tagged", 0)
        return f"tagging · tagged={tagged}"
    if event_type == "sexton_wiki_cycle_complete":
        written = metadata.get("wiki_written", 0)
        return f"wiki · written={written}"
    if event_type == "sexton_graph_extraction_complete":
        entities = metadata.get("entities_extracted", 0)
        edges = metadata.get("edges_extracted", 0)
        return f"graph · entities={entities} edges={edges}"
    if event_type == "vigil_eval_complete":
        evaluated = metadata.get("evaluated", 0)
        degraded = metadata.get("degraded", 0)
        return f"eval · evaluated={evaluated} degraded={degraded}"
    if event_type == "ecs_transition":
        to_state = metadata.get("to_state", "")
        return f"transition → {to_state}" if to_state else "transition"
    # Generic fallback — use first metadata value if available
    if metadata:
        first_key = next(iter(metadata), "")
        first_val = metadata.get(first_key, "")
        if first_key and first_val is not None:
            val_str = str(first_val)
            if len(val_str) > 40:
                val_str = val_str[:40] + "…"
            return f"{first_key}={val_str}"
    return event_type


def _payload_preview(metadata: dict) -> str:
    """First 200 chars of JSON-serialized metadata."""
    if not metadata:
        return ""
    try:
        raw = json.dumps(metadata, default=str)
        return raw[:200] + ("…" if len(raw) > 200 else "")
    except Exception:
        return str(metadata)[:200]


@router.get("/events")
async def list_events(
    limit: int = Query(default=50, ge=1, le=500),
    actor: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    container: AipContainer = Depends(get_container),
):
    """List recent actor events from the EventStore.

    Read-only observer surface. Returns events newest-first with
    optional filters for actor and event_type.

    Args:
        limit: Max events to return (1-500, default 50)
        actor: Filter by actor name (e.g. "beast", "sexton", "vigil")
        event_type: Filter by event type prefix (e.g. "beast_heartbeat")

    Returns:
        List of event dicts with id, actor, event_type, summary,
        timestamp, and payload_preview.
    """
    if container.event_store is None:
        return {"events": [], "total": 0}

    try:
        # event_store.query() filters by exact event_type match.
        # For prefix matching (e.g. "beast_" matches all beast events),
        # we fetch more and post-filter if actor is specified.
        query_limit = limit * 3 if actor else limit
        events = await container.event_store.query(
            artifact_id=None,
            event_type=event_type,
            limit=query_limit,
        )
    except Exception as exc:
        logger.warning("event_store query failed: %s", exc)
        return {"events": [], "total": 0}

    result = []
    for ev in events:
        # Post-filter by actor if requested
        if actor and ev.actor != actor:
            continue

        metadata = ev.metadata if hasattr(ev, "metadata") and ev.metadata else {}
        summary = _summarize_event(ev.event_type, metadata)

        result.append({
            "id": ev.id,
            "actor": ev.actor,
            "event_type": ev.event_type,
            "artifact_id": ev.artifact_id,
            "summary": summary,
            "timestamp": ev.timestamp,
            "payload_preview": _payload_preview(metadata),
            "from_state": ev.from_state,
            "to_state": ev.to_state,
        })

        if len(result) >= limit:
            break

    return {"events": result, "total": len(result)}
