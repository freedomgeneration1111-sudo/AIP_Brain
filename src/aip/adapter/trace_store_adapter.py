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

                results.append(
                    {
                        "id": getattr(ev, "id", 0),
                        "session_id": getattr(ev, "actor", session_id),
                        "node_type": metadata.get("node_type", ""),
                        "failure_type": metadata.get("failure_type", ""),
                        "outcome": getattr(ev, "to_state", ""),
                        "detail": metadata.get("detail", ""),
                        "created_at": getattr(ev, "timestamp", ""),
                        **{k: v for k, v in metadata.items() if k not in ("node_type", "failure_type", "detail")},
                    }
                )
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

                results.append(
                    {
                        "id": getattr(ev, "id", 0),
                        "session_id": actor or meta_sid,
                        "node_type": metadata.get("node_type", ev_type.replace("trace:", "")),
                        "failure_type": metadata.get("failure_type", ""),
                        "outcome": getattr(ev, "to_state", ""),
                        "detail": metadata.get("detail", ""),
                        "created_at": getattr(ev, "timestamp", ""),
                        **{k: v for k, v in metadata.items() if k not in ("node_type", "failure_type", "detail")},
                    }
                )

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
                    results.append(
                        {
                            "id": getattr(ev, "id", 0),
                            "session_id": getattr(ev, "actor", ""),
                            "node_type": metadata.get("node_type", ev_type.replace("trace:", "")),
                            "failure_type": failure_type,
                            "outcome": outcome,
                            "detail": metadata.get("detail", ""),
                            "created_at": getattr(ev, "timestamp", ""),
                            **{k: v for k, v in metadata.items() if k not in ("node_type", "failure_type", "detail")},
                        }
                    )

                    if len(results) >= limit:
                        break

            return results
        except Exception as exc:
            logger.debug("TraceStoreAdapter.get_unclassified_failures failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Retrieval analytics / dashboard methods
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

        Sprint 10 additions:
          - recent_asks: Last N ask queries with timestamps and verdicts.
          - low_context_answers: Queries with few/no sources after quality gate.
          - empty_retrieval_events: Queries that returned 0 results.
          - vector_fallback_events: Queries where vector was disabled/degraded.
          - slow_channels: Channels with high p95 latency.
          - top_failing_sources: Source IDs that frequently appear in low-score results.
          - channel_health_summary: Aggregate channel health across recent queries.
          - degradation_warning_counts: How often each warning type appears.
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
        # Sprint 10: New dashboard metrics
        recent_asks: list[dict] = []
        low_context_answers: list[dict] = []
        empty_retrieval_events: list[dict] = []
        vector_fallback_events: list[dict] = []
        channel_health_summary: dict[str, dict[str, int]] = {}  # channel -> {active/degraded/failed: count}
        degradation_warning_counts: dict[str, int] = {}
        per_channel_latency: dict[str, list[float]] = {}  # channel -> [elapsed_ms, ...]

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

            # Sprint 10: Recent asks (last 20)
            prompt = meta.get("prompt", "")[:100]
            timestamp = meta.get("created_at", "")
            session_id = meta.get("session_id", "")
            recent_asks.append(
                {
                    "prompt": prompt,
                    "verdict": verdict,
                    "timestamp": timestamp,
                    "session_id": session_id,
                    "hits_after_gate": meta.get("retrieval_hits_after_gate", 0),
                }
            )

            # Sprint 10: Low-context answers (≤ 2 hits after gate)
            gate_hits = meta.get("retrieval_hits_after_gate", 0)
            try:
                gate_hits = int(gate_hits)
            except (ValueError, TypeError):
                gate_hits = 0
            if gate_hits <= 2 and verdict != "NO_RESULTS":
                low_context_answers.append(
                    {
                        "prompt": prompt,
                        "verdict": verdict,
                        "hits_after_gate": gate_hits,
                    }
                )

            # Sprint 10: Empty retrieval events
            if verdict == "NO_RESULTS" or gate_hits == 0:
                empty_retrieval_events.append(
                    {
                        "prompt": prompt,
                        "verdict": verdict,
                    }
                )

            # Sprint 10: Vector fallback events
            vector_status = meta.get("retrieval_vector_backend_status", "")
            vector_degraded = meta.get("retrieval_vector_degraded", False)
            if vector_status in ("disabled", "failed") or vector_degraded:
                vector_fallback_events.append(
                    {
                        "prompt": prompt,
                        "vector_status": vector_status,
                        "vector_degraded": bool(vector_degraded),
                    }
                )

            # Sprint 10: Channel health summary
            ch_health_raw = meta.get("retrieval_channel_health", "{}")
            try:
                ch_health = json.loads(ch_health_raw) if isinstance(ch_health_raw, str) else ch_health_raw
                for ch, health in ch_health.items():
                    if ch not in channel_health_summary:
                        channel_health_summary[ch] = {"active": 0, "degraded": 0, "failed": 0, "disabled": 0}
                    if health in channel_health_summary[ch]:
                        channel_health_summary[ch][health] += 1
            except (json.JSONDecodeError, TypeError):
                pass

            # Sprint 10: Degradation warning counts
            warnings_raw = meta.get("retrieval_degradation_warnings", "[]")
            try:
                warnings = json.loads(warnings_raw) if isinstance(warnings_raw, str) else warnings_raw
                for w in warnings:
                    degradation_warning_counts[w] = degradation_warning_counts.get(w, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass

            # Sprint 10: Per-channel latency tracking
            per_ch_ms_raw = meta.get("retrieval_per_channel_ms", "{}")
            try:
                per_ch_ms = json.loads(per_ch_ms_raw) if isinstance(per_ch_ms_raw, str) else per_ch_ms_raw
                for ch, ms in per_ch_ms.items():
                    if ch not in per_channel_latency:
                        per_channel_latency[ch] = []
                    try:
                        per_channel_latency[ch].append(float(ms))
                    except (ValueError, TypeError):
                        pass
            except (json.JSONDecodeError, TypeError):
                pass

        # Compute percentiles
        total_ms_values.sort()
        avg_ms = sum(total_ms_values) / len(total_ms_values) if total_ms_values else 0.0
        p50_ms = total_ms_values[len(total_ms_values) // 2] if total_ms_values else 0.0
        p95_idx = int(len(total_ms_values) * 0.95)
        p95_ms = total_ms_values[min(p95_idx, len(total_ms_values) - 1)] if total_ms_values else 0.0

        retry_count = sum(1 for r in rounds if r > 0)

        # Aggregate channel contributions from trace metadata
        channel_contribution_summary: dict[str, int] = {}
        for meta in ask_events:
            cc_raw = meta.get("retrieval_channel_contributions", "{}")
            try:
                cc = json.loads(cc_raw) if isinstance(cc_raw, str) else cc_raw
                if isinstance(cc, dict):
                    for ch, count in cc.items():
                        channel_contribution_summary[ch] = channel_contribution_summary.get(ch, 0) + int(count)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        # Aggregate LLM entity extraction observability
        llm_ext_calls = 0
        llm_ext_success = 0
        llm_ext_failed = 0
        llm_ext_total_ms = 0.0
        for meta in ask_events:
            llm_status = meta.get("retrieval_llm_entity_extraction_status", "not_used")
            llm_ms = meta.get("retrieval_llm_entity_extraction_ms", 0)
            if llm_status != "not_used":
                llm_ext_calls += 1
                try:
                    llm_ext_total_ms += float(llm_ms)
                except (ValueError, TypeError):
                    pass
                if llm_status == "success":
                    llm_ext_success += 1
                elif llm_status == "failed":
                    llm_ext_failed += 1

        # Sprint 10: Compute slow channels (p95 latency > threshold)
        SLOW_CHANNEL_THRESHOLD_MS = 500.0
        slow_channels: dict[str, dict] = {}
        for ch, latencies in per_channel_latency.items():
            if not latencies:
                continue
            latencies_sorted = sorted(latencies)
            ch_p50 = latencies_sorted[len(latencies_sorted) // 2]
            ch_p95_idx = int(len(latencies_sorted) * 0.95)
            ch_p95 = latencies_sorted[min(ch_p95_idx, len(latencies_sorted) - 1)]
            ch_avg = sum(latencies_sorted) / len(latencies_sorted)
            if ch_p95 > SLOW_CHANNEL_THRESHOLD_MS:
                slow_channels[ch] = {
                    "p50_ms": round(ch_p50, 2),
                    "p95_ms": round(ch_p95, 2),
                    "avg_ms": round(ch_avg, 2),
                    "sample_count": len(latencies_sorted),
                }

        result = {
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
            "channel_contribution_summary": channel_contribution_summary,
            "llm_entity_extraction": {
                "total_calls": llm_ext_calls,
                "success_count": llm_ext_success,
                "failed_count": llm_ext_failed,
                "avg_ms": round(llm_ext_total_ms / llm_ext_calls, 1) if llm_ext_calls > 0 else 0.0,
            },
            # Sprint 10: Quality dashboard metrics
            "recent_asks": recent_asks[:20],
            "low_context_answers": low_context_answers[:20],
            "empty_retrieval_events": empty_retrieval_events[:20],
            "vector_fallback_events": vector_fallback_events[:20],
            "slow_channels": slow_channels,
            "channel_health_summary": channel_health_summary,
            "degradation_warning_counts": degradation_warning_counts,
        }

        return result
