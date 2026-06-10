"""
L4 Context Reset Protocol Foundation (spec delta)

Implements the response path from Architecture Rev 5.2, consuming the
detection foundation from (TrajectoryMonitor).

- Deterministic evaluation of monitor signals.
- Logs intervention event (step 4) via injected TraceStore using existing
  write_event + **kwargs to populate intervention_applied / intervention_type.
- Surfaces ResetRecommendation for caller to execute model "progress summary"
  (step 2), provisional commit (step 3), DEFINER surface (step 5), and fresh
  session (step 6) using normal L5 paths.
- Zero tokens inside L4 logic. All stores injected only (no direct construction).
- Every trigger carries model_gen_assumption.

This is the smallest useful foundation for the Context Reset Protocol.
Full Sexton, UI surface, and advanced L4b metrics remain out of scope.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from aip.foundation.protocols import ArtifactStore, TraceStore
from aip.foundation.schemas import TrajectorySignal
from aip.orchestration.l4.monitor import TrajectoryMonitor
from aip.orchestration.workflow.context import WorkflowContext

logger = logging.getLogger(__name__)


@dataclass
class ResetRecommendation:
    """
    Structured output when L4 decides a context reset / trajectory correction
    is warranted.

    The caller (WorkflowEngine, script node, or external orchestrator) is
    responsible for acting on .action (e.g. synthesize progress summary via
    existing agent node, then commit via ArtifactStore, then start fresh
    session seeded with the summary).
    """

    session_id: str
    signals: list[TrajectorySignal]
    action: str = "context_reset"
    reason: str = (
        "2-of-3 trajectory signals (D/F/combined) detected in session window; Context Reset Protocol required."
    )
    model_gen_assumption: str | None = (
        "L4 treats D and/or F signals as sufficient grounds for a context reset. "
        "The assumption is that trajectory degeneration under context pressure "
        "is best addressed by summarizing progress and starting a fresh session."
    )


class L4ResetCoordinator:
    """
    Deterministic coordinator for the Context Reset Protocol.

    Usage (typical, via WorkflowContext injection or direct test construction):
        coordinator = L4ResetCoordinator(
            trajectory_monitor=monitor,
            trace_store=trace_store,
            artifact_store=artifact_store,  # optional for foundation
        )
        recs = await coordinator.check_and_log_reset(session_id="sess_123")

    Never constructs stores. Never calls models. Pure decision + logging.
    """

    def __init__(
        self,
        trajectory_monitor: TrajectoryMonitor,
        trace_store: TraceStore,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._monitor = trajectory_monitor
        self._trace_store = trace_store
        self._artifact_store = artifact_store

    async def check_and_log_reset(self, session_id: str) -> list[ResetRecommendation]:
        """
        Run detection, and if L4-relevant signals are present, log the
        intervention event (step 4) and return recommendation(s).

        Returns [] for clean sessions (no action).
        Safe to call; defensive on store errors.
        """
        if not session_id:
            return []

        try:
            signals = await self._monitor.detect(session_id=session_id)
        except Exception:
            return []

        if not signals:
            return []

        # Filter to the signals that matter (D / F / combined proxy)
        # Updated signal_type values to match schema's TrajectorySignalType
        relevant = [
            s
            for s in signals
            if s.signal_type in ("loop", "anxiety", "failure_streak", "loop_d", "context_anxiety_f", "combined_2of3")
        ]
        if not relevant:
            return []

        rec = ResetRecommendation(
            session_id=session_id,
            signals=relevant,
        )

        # Step 4: Log reset event with intervention fields
        # Uses **kwargs passthrough (all current fakes + noops support this;
        # concrete writers target the full trace_events schema).
        try:
            await self._trace_store.write_event(
                session_id=session_id,
                node_type="L4",
                failure_type=None,
                outcome="intervention",
                detail=f"Context reset triggered by signals: {[s.signal_type for s in relevant]}",
                intervention_applied=1,
                intervention_type="context_reset",
            )
        except Exception as exc:
            logger.debug("Trace write for L4 reset event failed: %s", exc)

        return [rec]


# activation helper (minimal reusable pattern)
# Provides the documented way for ScriptNodes or custom workflow code
# to invoke L4 and surface recommendations to DEFINER via the existing
# emit_event + DialogNode machinery.


async def check_l4_and_surface_if_needed(
    context: WorkflowContext,
    session_id: str,
) -> list[ResetRecommendation]:
    """
    Convenience helper for use inside ScriptNodes or workflow code.

    Retrieves the injected L4ResetCoordinator (if present), runs detection,
    logs any intervention, and if recommendations are produced, emits a
    structured "l4_reset_recommended" event that can be consumed by a
    DialogNode for DEFINER review (step 5).

    Returns the list of recommendations (empty if no action required).
    Fully deterministic, zero tokens, respects injection model.
    """
    coordinator = context.get_protocol("l4_coordinator")
    if coordinator is None:
        return []

    try:
        recs = await coordinator.check_and_log_reset(session_id=session_id)
    except Exception:
        return []

    if not recs:
        return []

    # Emit event for DEFINER surface using the standard mechanism
    payload = {
        "session_id": session_id,
        "action": "context_reset",
        "recommendations": [
            {
                "signal_types": [s.signal_type for s in r.signals],
                "reason": r.reason,
                "model_gen_assumption": r.model_gen_assumption,
            }
            for r in recs
        ],
    }
    context.emit_event("l4_reset_recommended", payload)
    return recs


# thin runtime integration helper (node-level L4 + Sexton)

from aip.orchestration.sexton import Sexton  # noqa: E402 -- lazy import to avoid circular dependency


async def run_l4_and_sexton_check(
    context: WorkflowContext,
    session_id: str,
    also_run_sexton: bool = True,
) -> dict[str, Any]:
    """
    Thin helper for use from ScriptNodes or inside workflow node logic.

    Calls the injected L4ResetCoordinator (via the 3.3 helper) and,
    optionally, a Sexton instance constructed from the trace_store in the context.

    Emits the standard "l4_reset_recommended" event when L4 recommends action.
    Returns a dict with recommendations and any Sexton classifications.

    This makes the full L4 (incl. L4b) + Sexton stack callable from within
    running workflow nodes (the main integration gap after 3.5).
    """
    from .reset import check_l4_and_surface_if_needed  # local to avoid circular

    l4_recs = await check_l4_and_surface_if_needed(context, session_id)

    sexton_classifications: list[dict] = []
    if also_run_sexton:
        trace_store = context.get_protocol("trace_store")
        if trace_store is not None:
            sexton = Sexton(trace_store=trace_store)
            try:
                sexton_classifications = await sexton.classify_recent_failures(limit=50)
            except Exception as exc:
                logger.debug("Sexton classification in L4 check failed: %s", exc)

    return {
        "l4_recommendations": l4_recs,
        "sexton_classifications": sexton_classifications,
    }
