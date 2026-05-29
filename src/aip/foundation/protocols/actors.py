"""Vigil actor store Protocol definition.

Storage interface for the Vigil actor: canonical health monitoring,
stale detection, and health check recording.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class VigilStore(Protocol):
    """Protocol for Vigil actor storage needs (canonical health, entity consistency).

    Vigil is read-only; it detects and reports, never modifies autonomously.
    """

    async def get_canonical_health(self, artifact_id: str) -> dict | None:
        """Get health metadata for a canonical artifact.

        Returns dict with: artifact_id, last_evaluated_at, model_slot_used,
        faithfulness_score, domain_coherence_score, status (VigilHealthStatus).
        Returns None if artifact not found.
        """
        ...

    async def list_stale_canonicals(self, threshold_days: int) -> list[dict]:
        """Return canonical artifacts that have not been re-evaluated within threshold_days."""
        ...

    async def record_vigil_check(
        self,
        canonical_count: int,
        stale_count: int,
        status: "VigilHealthStatus",
    ) -> None:
        """Record the result of a Vigil health check pass."""
        ...

    async def get_last_vigil_check(self) -> dict | None:
        """Return the most recent Vigil check result, or None if never run."""
        ...


__all__ = [
    "VigilStore",
]
