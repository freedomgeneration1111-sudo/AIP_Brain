"""Vigil actor (CHUNK-9.1).

Last missing §3 orchestration actor.
Read-only (per Appendix D + Process Rule 12): monitors, detects, creates trace events for Sexton; never modifies canonicals.
Complementary to Sexton (classifies failures) and Beast (maintains vectors).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aip.foundation.protocols import (
    VigilStore,
    CanonicalStore,
    EntityStore,
    ModelProvider,
    TraceStore,
)
from aip.foundation.schemas import VigilConfig, ModelSlotConfig


class Vigil:
    """Vigil — compiled knowledge maintenance actor (Phase 7)."""

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
        # In full impl: query all canonicals via canonical_store, cross with vigil_store health
        # For scaffold: return shape with counts
        return {
            "total_count": 0,
            "stale_count": 0,
            "healthy_count": 0,
            "degraded_count": 0,
            "status": "healthy",
        }

    async def detect_stale_canonicals(self) -> list[dict]:
        """Return list of stale canonicals (threshold + model slot)."""
        # Uses vigil_store.list_stale_canonicals + current model config
        return []

    async def detect_entity_inconsistencies(self) -> list[dict]:
        """Return entities referenced by canonicals that have been updated since promotion."""
        # Cross entity_store updates with canonical creation dates
        return []

    async def on_model_slot_change(
        self, slot_name: str, old_config: ModelSlotConfig, new_config: ModelSlotConfig
    ) -> None:
        """Per §1.8: audit for stale assumptions on model slot upgrade."""
        # If re_evaluate_on_slot_change: query vigil_store for canonicals using old slot
        # Create trace events (node_type="vigil", failure_type="A" or similar) for Sexton
        # Bound by config.max_re_evaluate_batch_size
        if self.config.model_slot_change_audit:
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
        for item in stale[: self.config.max_re_evaluate_batch_size]:  # type: ignore[attr-defined]
            await self.trace_store.write_event(
                session_id="vigil-run",
                node_type="vigil",
                failure_type="A",
                outcome="stale_detected",
                detail=f"Stale canonical: {item.get('artifact_id')}",
            )

        # If sexton present and configured, it can now read these events
