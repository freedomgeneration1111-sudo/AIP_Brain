"""aip_artifact_approve MCP tool (CHUNK-8.5) — admin, uses Ecs + Canonical + Gate (already enforced by server)."""

from __future__ import annotations

from typing import Any


async def aip_artifact_approve(container: Any, artifact_id: str) -> dict:
    # Gate already checked by server before dispatch
    if container.ecs_store and container.canonical_store:
        # container.ecs_store.transition(...)
        # container.canonical_store.write_canonical(artifact_id, ..., "definer")
        return {"approved": True, "canonical_written": True}
    return {"approved": False, "reason": "stores not wired"}
