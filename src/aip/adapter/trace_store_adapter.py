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
