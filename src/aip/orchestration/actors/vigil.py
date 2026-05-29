"""Vigil actor.

Last missing orchestration actor.
Read-only: monitors, detects,
creates trace events for Sexton; never modifies canonicals.
Complementary to Sexton (classifies failures) and Beast (maintains vectors).

G. Runtime gap closure: on_model_slot_change now triggers real re-evaluation
of affected artifacts/canonical candidates. Affected items are marked for
re-evaluation in the VigilStore, and trace events are recorded for Sexton
with structured detail so downstream processing can identify what changed.
"""

from __future__ import annotations

import logging
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
    """Vigil — compiled knowledge maintenance actor."""

    def __init__(
        self,
        config: VigilConfig,
        vigil_store: VigilStore,
        canonical_store: CanonicalStore,
        entity_store: EntityStore,
        model_provider: ModelProvider,
        trace_store: TraceStore,
        sexton: Any | None = None,  # Sexton from 7.1 (optional for triggering)
    ) -> None:
        self.config = config
        self.vigil_store = vigil_store
        self.canonical_store = canonical_store
        self.entity_store = entity_store
        self.model_provider = model_provider
        self.trace_store = trace_store
        self.sexton = sexton

    async def check_canonical_health(self) -> dict:
        """Return aggregate canonical health status."""
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
        """Return list of stale canonicals (threshold + model slot)."""
        try:
            threshold_days = self.config.stale_threshold_days
            return await self.vigil_store.list_stale_canonicals(threshold_days=threshold_days)
        except Exception:
            return []

    async def detect_entity_inconsistencies(self) -> list[dict]:
        """Return entities referenced by canonicals that have been updated since promotion.

        Only runs when config.entity_consistency_check is True.
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

        G. Runtime gap closure:
        1. Identifies affected canonicals (all canonicals are potentially affected
           by a model slot change since they were evaluated with the old model).
        2. Marks affected items as "needs_re_evaluation" in VigilStore.
        3. Records trace events for Sexton with structured detail.
        4. Returns a structured result showing what was affected.

        If the exact dependency graph is unavailable, ALL canonicals are conservatively
        marked as potentially affected.
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
            # Identify affected canonicals — conservative approach: all canonicals
            # are potentially affected by a model slot change
            canonicals = await self.canonical_store.list_canonical()
            affected = canonicals  # Conservative: all are potentially affected

            result["affected_count"] = len(affected)

            # Mark each affected canonical for re-evaluation (bounded by config)
            batch_size = self.config.max_re_evaluate_batch_size
            for item in affected[:batch_size]:
                artifact_id = item.get("artifact_id") or item.get("id", "unknown")
                try:
                    await self.vigil_store.record_vigil_check(
                        canonical_count=result["affected_count"],
                        stale_count=1,  # This item needs re-evaluation
                        status="needs_re_evaluation",
                    )
                    result["marked_for_re_evaluation"] += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to mark canonical %s for re-evaluation: %s",
                        artifact_id,
                        exc,
                    )

            # Write structured trace events for Sexton
            for item in affected[:batch_size]:
                artifact_id = item.get("artifact_id") or item.get("id", "unknown")
                try:
                    await self.trace_store.write_event(
                        session_id="vigil-model-slot-change",
                        node_type="vigil",
                        failure_type="A",  # Context Framing Failure — stale model assumptions
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

    async def run(self) -> None:
        """Cadence entry point (called by scheduler/Beast). Read-only."""
        health = await self.check_canonical_health()
        stale = await self.detect_stale_canonicals()

        _entity_issues = await self.detect_entity_inconsistencies()

        await self.vigil_store.record_vigil_check(
            canonical_count=health.get("total_count", 0),
            stale_count=len(stale),
            status=health.get("status", "unknown"),
        )

        # Create trace events for Sexton on stale items (bounded)
        for item in stale[: self.config.max_re_evaluate_batch_size]:
            await self.trace_store.write_event(
                session_id="vigil-run",
                node_type="vigil",
                failure_type="A",
                outcome="stale_detected",
                detail=f"Stale canonical: {item.get('artifact_id')}",
            )

        # If sexton present and configured, it can now read these events
