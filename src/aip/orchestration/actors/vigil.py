"""Vigil actor.

Last missing orchestration actor.
Read-only: monitors, detects,
creates trace events for Sexton; never modifies canonicals.
Complementary to Sexton (classifies failures) and Beast (maintains vectors).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aip.foundation.protocols import (
    CanonicalStore,
    EntityStore,
    ModelProvider,
    TraceStore,
    VigilStore,
)
from aip.foundation.schemas import ModelSlotConfig, VigilConfig


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
            # Check for entities updated more recently than their referencing canonicals
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
    ) -> None:
        """Audit for stale assumptions on model slot upgrade."""
        if self.config.re_evaluate_on_slot_change:
            # Create trace events so Sexton can classify
            await self.trace_store.write_event(
                session_id="vigil-audit",
                node_type="vigil",
                failure_type="A",  # Missing Context (stale model assumptions)
                outcome="detected",
                detail=f"Model slot {slot_name} changed; potential stale canonicals",
            )

    async def run(self) -> None:
        """Cadence entry point (called by scheduler/Beast). Read-only."""
        health = await self.check_canonical_health()
        stale = await self.detect_stale_canonicals()

        entity_issues = await self.detect_entity_inconsistencies()

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
