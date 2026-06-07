"""TraceStoreAdapter — adapts QueryableEventStore to the TraceStore protocol.

The orchestration layer (Sexton, L4, perf, etc.) uses the TraceStore protocol
which has a different write_event signature than EventStore:

  TraceStore.write_event(session_id, node_type, failure_type, outcome, detail=None)
  EventStore.write_event(event_type, actor, artifact_id, from_state=None, to_state=None, **kwargs)

This adapter translates TraceStore calls into EventStore calls so that a single
QueryableEventStore can serve both protocols. All TraceStore method calls are
mapped to the underlying EventStore's schema.

Additionally, the adapter provides the TraceStore-specific query methods
(get_unclassified_failures, query_events, get_recent_events) by mapping
them to the EventStore's query() method with appropriate filters.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aip.foundation.protocols import EventStore

logger = logging.getLogger(__name__)


class TraceStoreAdapter:
    """Adapts an EventStore implementation to satisfy the TraceStore protocol.

    Translation rules:
      - write_event(session_id, node_type, failure_type, outcome, ...)
        → event_store.write_event(
            event_type=f"trace:{node_type}",
            actor=session_id,
            artifact_id="",
            from_state=None,
            to_state=outcome,
            node_type=node_type,
            failure_type=failure_type,
            detail=detail,
            **extra_kwargs,
          )
      - query_events(session_id, ...) → event_store.query(artifact_id="", event_type=f"trace:", ...)
      - get_recent_events(session_id, ...) → event_store.query(artifact_id="", ...)
      - get_unclassified_failures(limit) → event_store.query(event_type="trace:", ...) + filter
    """

    def __init__(self, event_store: EventStore) -> None:
        self._event_store = event_store

    async def write_event(
        self,
        session_id: str,
        node_type: str,
        failure_type: str,
        outcome: str,
        detail: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Write a trace event by translating to EventStore.write_event."""
        try:
            await self._event_store.write_event(
                event_type=f"trace:{node_type}",
                actor=session_id,
                artifact_id=kwargs.get("artifact_id", ""),
                from_state=None,
                to_state=outcome,
                node_type=node_type,
                failure_type=failure_type,
                detail=detail or "",
                # Pass through any extra kwargs (e.g., intervention_applied, intervention_type)
                **{k: v for k, v in kwargs.items() if k != "artifact_id"},
            )
        except Exception as exc:
            logger.debug("TraceStoreAdapter.write_event failed: %s", exc)

    async def query_events(
        self,
        session_id: str,
        node_type: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query trace events for a session.

        Translates to EventStore.query() with appropriate filters,
        then maps the Event objects back to trace-style dicts.
        """
        try:
            event_type_filter = f"trace:{node_type}" if node_type else None
            events = await self._event_store.query(
                artifact_id=None,
                event_type=event_type_filter,
                limit=limit,
            )
            # Convert Event objects to trace-style dicts
            results = []
            for ev in events:
                metadata = ev.metadata if hasattr(ev, "metadata") else {}
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}

                # Filter by session_id (stored as actor in EventStore)
                if session_id and getattr(ev, "actor", None) != session_id:
                    # Also check metadata for session_id
                    meta_sid = metadata.get("session_id", "")
                    if meta_sid != session_id:
                        continue

                results.append({
                    "id": getattr(ev, "id", 0),
                    "session_id": getattr(ev, "actor", session_id),
                    "node_type": metadata.get("node_type", ""),
                    "failure_type": metadata.get("failure_type", ""),
                    "outcome": getattr(ev, "to_state", ""),
                    "detail": metadata.get("detail", ""),
                    "created_at": getattr(ev, "timestamp", ""),
                    **{k: v for k, v in metadata.items()
                       if k not in ("node_type", "failure_type", "detail")},
                })
            return results
        except Exception as exc:
            logger.debug("TraceStoreAdapter.query_events failed: %s", exc)
            return []

    async def get_recent_events(
        self,
        session_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """Return recent trace events for a session (most recent first).

        Maps to EventStore.query() and filters by session_id.
        """
        try:
            events = await self._event_store.query(limit=limit * 3)
            results = []
            for ev in events:
                metadata = ev.metadata if hasattr(ev, "metadata") else {}
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}

                # Only include trace events (event_type starts with "trace:")
                ev_type = getattr(ev, "event_type", "")
                if not ev_type.startswith("trace:"):
                    continue

                # Filter by session_id
                actor = getattr(ev, "actor", "")
                meta_sid = metadata.get("session_id", "")
                if session_id and actor != session_id and meta_sid != session_id:
                    continue

                results.append({
                    "id": getattr(ev, "id", 0),
                    "session_id": actor or meta_sid,
                    "node_type": metadata.get("node_type", ev_type.replace("trace:", "")),
                    "failure_type": metadata.get("failure_type", ""),
                    "outcome": getattr(ev, "to_state", ""),
                    "detail": metadata.get("detail", ""),
                    "created_at": getattr(ev, "timestamp", ""),
                    **{k: v for k, v in metadata.items()
                       if k not in ("node_type", "failure_type", "detail")},
                })

                if len(results) >= limit:
                    break

            return results
        except Exception as exc:
            logger.debug("TraceStoreAdapter.get_recent_events failed: %s", exc)
            return []

    async def get_unclassified_failures(self, limit: int = 100) -> list[dict]:
        """Return recent unclassified failures (failure_type empty, outcome == 'failure').

        Maps to EventStore.query() with outcome filter.
        """
        try:
            events = await self._event_store.query(limit=limit * 5)
            results = []
            for ev in events:
                metadata = ev.metadata if hasattr(ev, "metadata") else {}
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}

                # Only include trace events
                ev_type = getattr(ev, "event_type", "")
                if not ev_type.startswith("trace:"):
                    continue

                # Check outcome == 'failure' and no failure_type yet
                outcome = getattr(ev, "to_state", "")
                failure_type = metadata.get("failure_type", "")

                if outcome == "failure" and not failure_type:
                    results.append({
                        "id": getattr(ev, "id", 0),
                        "session_id": getattr(ev, "actor", ""),
                        "node_type": metadata.get("node_type", ev_type.replace("trace:", "")),
                        "failure_type": failure_type,
                        "outcome": outcome,
                        "detail": metadata.get("detail", ""),
                        "created_at": getattr(ev, "timestamp", ""),
                        **{k: v for k, v in metadata.items()
                           if k not in ("node_type", "failure_type", "detail")},
                    })

                    if len(results) >= limit:
                        break

            return results
        except Exception as exc:
            logger.debug("TraceStoreAdapter.get_unclassified_failures failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Sprint 5.7: Retrieval analytics / dashboard methods
    # ------------------------------------------------------------------

    async def get_dashboard_summary(self, limit: int = 500) -> dict[str, Any]:
        """Compute a lightweight dashboard summary from recent retrieval traces.

        Returns a dict with:
          - total_ask_queries: Number of ask_query events in the sample.
          - avg_retrieval_ms: Mean total_elapsed_ms across retrieval rounds.
          - p50_retrieval_ms / p95_retrieval_ms: Latency percentiles.
          - retry_rate: Fraction of queries that needed more than one round.
          - channel_usage: Mapping of channel name → number of dispatches.
          - avg_hits_before_fusion / avg_hits_after_fusion / avg_hits_after_gate.
          - quality_gate_verdicts: Mapping of verdict → count.

        This is a read-only aggregation suitable for the dashboard API endpoint.
        """
        try:
            events = await self._event_store.query(limit=limit * 3)
        except Exception as exc:
            logger.debug("TraceStoreAdapter.get_dashboard_summary query failed: %s", exc)
            return {"error": str(exc), "total_ask_queries": 0}

        # Filter to ask_query events that have retrieval metadata
        ask_events = []
        for ev in events:
            ev_type = getattr(ev, "event_type", "")
            if ev_type != "ask_query":
                continue
            metadata = ev.metadata if hasattr(ev, "metadata") else {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            # Check if this event has retrieval trace data
            if "retrieval_total_ms" in metadata or "retrieval_verdict" in metadata:
                ask_events.append(metadata)

        if not ask_events:
            return {
                "total_ask_queries": 0,
                "message": "No retrieval-traced ask queries found",
            }

        # Compute metrics
        total_ms_values: list[float] = []
        rounds: list[int] = []
        channel_counts: dict[str, int] = {}
        hits_before: list[int] = []
        hits_after: list[int] = []
        hits_gate: list[int] = []
        verdict_counts: dict[str, int] = {}

        for meta in ask_events:
            # Latency
            total_ms = meta.get("retrieval_total_ms")
            if total_ms is not None:
                try:
                    total_ms_values.append(float(total_ms))
                except (ValueError, TypeError):
                    pass

            # Rounds
            round_num = meta.get("retrieval_round")
            if round_num is not None:
                try:
                    rounds.append(int(round_num))
                except (ValueError, TypeError):
                    pass

            # Channel usage
            channels_raw = meta.get("retrieval_channels", "[]")
            try:
                channels = json.loads(channels_raw) if isinstance(channels_raw, str) else channels_raw
                for ch in channels:
                    channel_counts[ch] = channel_counts.get(ch, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass

            # Hit counts
            for key, lst in [
                ("retrieval_hits_before_fusion", hits_before),
                ("retrieval_hits_after_fusion", hits_after),
                ("retrieval_hits_after_gate", hits_gate),
            ]:
                val = meta.get(key)
                if val is not None:
                    try:
                        lst.append(int(val))
                    except (ValueError, TypeError):
                        pass

            # Verdict
            verdict = meta.get("retrieval_verdict", "UNKNOWN")
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

        # Compute percentiles
        total_ms_values.sort()
        avg_ms = sum(total_ms_values) / len(total_ms_values) if total_ms_values else 0.0
        p50_ms = total_ms_values[len(total_ms_values) // 2] if total_ms_values else 0.0
        p95_idx = int(len(total_ms_values) * 0.95)
        p95_ms = total_ms_values[min(p95_idx, len(total_ms_values) - 1)] if total_ms_values else 0.0

        retry_count = sum(1 for r in rounds if r > 0)

        return {
            "total_ask_queries": len(ask_events),
            "avg_retrieval_ms": round(avg_ms, 2),
            "p50_retrieval_ms": round(p50_ms, 2),
            "p95_retrieval_ms": round(p95_ms, 2),
            "retry_rate": round(retry_count / len(rounds), 4) if rounds else 0.0,
            "retry_count": retry_count,
            "channel_usage": channel_counts,
            "avg_hits_before_fusion": round(sum(hits_before) / len(hits_before), 1) if hits_before else 0,
            "avg_hits_after_fusion": round(sum(hits_after) / len(hits_after), 1) if hits_after else 0,
            "avg_hits_after_gate": round(sum(hits_gate) / len(hits_gate), 1) if hits_gate else 0,
            "quality_gate_verdicts": verdict_counts,
        }
