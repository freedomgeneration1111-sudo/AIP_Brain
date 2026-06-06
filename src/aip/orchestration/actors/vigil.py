"""Vigil actor — quality evaluation (ADR-011).

Vigil is the quality assurance actor. It evaluates synthesis outputs,
monitors for quality degradation, and proposes profile amendments when
it notices systematic patterns.

Per ADR-011:
- Vigil evaluates augmented chat responses for source citation quality
- Vigil flags responses that make claims not supported by retrieved sources
- Vigil proposes DEFINER profile amendments when synthesis patterns drift
- Vigil monitors Beast wiki articles for factual consistency
- Vigil generates quality evaluation reports as reviewable artifacts

What Vigil does NOT do (per ADR-011):
- Background maintenance (→ Sexton)
- Context assembly (→ Beast)
- Any write operations to corpus_turns or artifacts without DEFINER review

The previous Vigil implementation contained maintenance operations
(canonical health checks, stale detection, entity inconsistency checks,
model slot change handling) that are now outside Vigil's scope per ADR-011.
Those operations are retained as private methods for backward compatibility
but are no longer called from run_cycle().

Model slot: evaluation-capable model (e.g., openai/gpt-oss-20b:free).
Cadence: Hourly — evaluates last N synthesis responses since previous run.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from aip.foundation.protocols import (
    CanonicalStore,
    EntityStore,
    ModelProvider,
    TraceStore,
    VigilStore,
)
from aip.foundation.schemas import ModelSlotConfig, VigilConfig

logger = logging.getLogger(__name__)


class Vigil:
    """Vigil — quality evaluation actor (ADR-011).

    Evaluates synthesis quality on an hourly cadence. Reads the last N
    ask_response artifacts since the previous vigil run and checks each
    for source citation quality. Writes an evaluation summary as a
    GENERATED artifact for DEFINER review.

    Legacy maintenance methods (check_canonical_health, detect_stale_canonicals,
    detect_entity_inconsistencies, on_model_slot_change) are retained for
    backward compatibility but are NOT part of the ADR-011 vigil cycle.
    """

    def __init__(
        self,
        config: VigilConfig,
        vigil_store: VigilStore,
        canonical_store: CanonicalStore,
        entity_store: EntityStore,
        model_provider: ModelProvider,
        trace_store: TraceStore,
        sexton: Any | None = None,  # Sexton (optional for triggering)
        artifact_store: Any = None,  # for writing evaluation artifacts
        ecs_store: Any = None,  # for ECS transitions on evaluation artifacts
        event_store: Any = None,  # for emitting vigil events
        corpus_turn_store: Any = None,  # for reading ask_response artifacts
    ) -> None:
        self.config = config
        self.vigil_store = vigil_store
        self.canonical_store = canonical_store
        self.entity_store = entity_store
        self.model_provider = model_provider
        self.trace_store = trace_store
        self.sexton = sexton
        self._artifacts = artifact_store
        self._ecs = ecs_store
        self._events = event_store
        self._corpus_turns = corpus_turn_store
        self._last_eval_time: float | None = None

    # ------------------------------------------------------------------
    # ADR-011 Quality Evaluation Cycle
    # ------------------------------------------------------------------

    async def run_cycle(self) -> dict:
        """Execute a Vigil quality evaluation cycle (ADR-011).

        Reads the last N ask_response artifacts since the previous vigil
        run, checks each for source citation quality, and writes an
        evaluation summary as a GENERATED artifact.

        Returns a summary dict with evaluation results.
        """
        cycle_start = time.monotonic()

        # TODO: Quality evaluation not yet fully implemented (ADR-011).
        # The following is a placeholder that logs the vigil eval cycle
        # and records the check. Full implementation requires:
        # 1. Reading ask_response artifacts since last_eval_time
        # 2. Evaluating source citation quality for each response
        # 3. Flagging responses with unsupported claims
        # 4. Writing evaluation summary as GENERATED artifact
        # 5. Proposing profile amendments if systematic drift detected

        logger.info("vigil_eval_start")

        # Record the vigil check (legacy compatibility)
        try:
            await self.vigil_store.record_vigil_check(
                canonical_count=0,
                stale_count=0,
                status="quality_evaluation_pending",
            )
        except Exception as exc:
            logger.warning("vigil_store_record_failed", error=str(exc))

        # Emit vigil event
        if self._events is not None:
            try:
                await self._events.emit(
                    event_type="vigil_eval_start",
                    artifact_id="system",
                    metadata={
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "last_eval": self._last_eval_time,
                    },
                )
            except Exception:
                pass

        elapsed = time.monotonic() - cycle_start
        self._last_eval_time = time.time()

        result = {
            "status": "quality_evaluation_pending",
            "evaluated_count": 0,
            "flagged_count": 0,
            "cycle_elapsed_seconds": round(elapsed, 3),
            "last_eval_time": self._last_eval_time,
            "note": "Quality evaluation not yet implemented — see ADR-011 TODO",
        }

        # Emit completion event
        if self._events is not None:
            try:
                await self._events.emit(
                    event_type="vigil_eval_complete",
                    artifact_id="system",
                    metadata=result,
                )
            except Exception:
                pass

        logger.info("vigil_eval_complete", status=result["status"])
        return result

    # ------------------------------------------------------------------
    # Legacy methods (retained for backward compatibility)
    # These are NOT part of the ADR-011 vigil cycle.
    # ------------------------------------------------------------------

    async def check_canonical_health(self) -> dict:
        """Return aggregate canonical health status.

        .. deprecated:: ADR-011
           Canonical health monitoring is not part of Vigil's quality
           evaluation role. Retained for backward compatibility.
        """
        try:
            canonicals = await self.canonical_store.list_canonical()
            total = len(canonicals)
            stale = await self.detect_stale_canonicals()
            return {
                "total_count": total,
                "stale_count": len(stale),
                "healthy_count": total - len(stale),
                "degraded_count": len(stale),
                "status": "healthy" if len(stale) == 0 else "degraded",
            }
        except Exception:
            return {
                "total_count": 0,
                "stale_count": 0,
                "healthy_count": 0,
                "degraded_count": 0,
                "status": "unknown",
            }

    async def detect_stale_canonicals(self) -> list[dict]:
        """Return list of stale canonicals (threshold + model slot).

        .. deprecated:: ADR-011
           Stale canonical detection is a maintenance function (→ Sexton).
           Retained for backward compatibility.
        """
        try:
            threshold_days = self.config.stale_threshold_days
            return await self.vigil_store.list_stale_canonicals(threshold_days=threshold_days)
        except Exception:
            return []

    async def detect_entity_inconsistencies(self) -> list[dict]:
        """Return entities referenced by canonicals that have been updated since promotion.

        .. deprecated:: ADR-011
           Entity consistency checking is a maintenance function (→ Sexton).
           Retained for backward compatibility.
        """
        if not self.config.entity_consistency_check:
            return []
        try:
            entities = await self.entity_store.list_entities()
            inconsistencies = []
            for entity in entities:
                entity_id = entity.get("entity_id") or entity.get("id")
                if entity_id:
                    entity_data = await self.entity_store.get_entity(entity_id)
                    if entity_data and entity_data.get("updated_since_canonical"):
                        inconsistencies.append(entity_data)
            return inconsistencies
        except Exception:
            return []

    async def on_model_slot_change(
        self,
        slot_name: str,
        old_config: ModelSlotConfig,
        new_config: ModelSlotConfig,
    ) -> dict:
        """React to model slot change with real re-evaluation marking.

        .. deprecated:: ADR-011
           Model slot change handling is a maintenance function.
           Retained for backward compatibility.

        Identifies affected canonicals, marks them for re-evaluation in
        VigilStore, and records trace events for Sexton.
        """
        result = {
            "slot_name": slot_name,
            "old_model": getattr(old_config, "model", "unknown"),
            "new_model": getattr(new_config, "model", "unknown"),
            "affected_count": 0,
            "marked_for_re_evaluation": 0,
            "trace_events_written": 0,
        }

        if not self.config.re_evaluate_on_slot_change:
            result["skipped_reason"] = "re_evaluate_on_slot_change is False"
            return result

        try:
            canonicals = await self.canonical_store.list_canonical()
            affected = canonicals
            result["affected_count"] = len(affected)

            batch_size = self.config.max_re_evaluate_batch_size
            for item in affected[:batch_size]:
                artifact_id = item.get("artifact_id") or item.get("id", "unknown")
                try:
                    await self.vigil_store.record_vigil_check(
                        canonical_count=result["affected_count"],
                        stale_count=1,
                        status="needs_re_evaluation",
                    )
                    result["marked_for_re_evaluation"] += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to mark canonical %s for re-evaluation: %s",
                        artifact_id,
                        exc,
                    )

            for item in affected[:batch_size]:
                artifact_id = item.get("artifact_id") or item.get("id", "unknown")
                try:
                    await self.trace_store.write_event(
                        session_id="vigil-model-slot-change",
                        node_type="vigil",
                        failure_type="A",
                        outcome="needs_re_evaluation",
                        detail=(
                            f"Model slot '{slot_name}' changed from "
                            f"'{getattr(old_config, 'model', 'unknown')}' to "
                            f"'{getattr(new_config, 'model', 'unknown')}'. "
                            f"Canonical '{artifact_id}' may have stale evaluations "
                            f"and needs re-evaluation."
                        ),
                    )
                    result["trace_events_written"] += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to write trace event for slot change on %s: %s",
                        artifact_id,
                        exc,
                    )

        except Exception as exc:
            logger.error("Error during model slot change re-evaluation: %s", exc)
            result["error"] = str(exc)

        return result

    # ------------------------------------------------------------------
    # Legacy run() entry point — redirects to run_cycle()
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Cadence entry point (called by scheduler). Delegates to run_cycle().

        .. note:: ADR-011
           The legacy run() performed maintenance operations (canonical health,
           stale detection, entity inconsistencies). Those are now deprecated.
           This method now delegates to run_cycle() for quality evaluation.
        """
        await self.run_cycle()
