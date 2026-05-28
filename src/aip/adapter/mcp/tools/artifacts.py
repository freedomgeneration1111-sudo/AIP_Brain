"""aip_artifact_approve MCP tool  — admin, uses Ecs + Canonical + Gate (already enforced by server)."""

from __future__ import annotations

from typing import Any


async def aip_artifact_approve(container: Any, artifact_id: str) -> dict:
    """Approve artifact for canonical promotion. Gate already checked by server."""
    if not container.ecs_store or not container.canonical_store:
        return {"approved": False, "reason": "stores not wired"}
    try:
        await container.ecs_store.transition(
            artifact_id=artifact_id,
            from_state="REVIEWED",
            to_state="APPROVED",
            actor="definer",
            reason="MCP tool approval",
        )
        content = await container.artifact_store.read(artifact_id) if container.artifact_store else ""
        await container.canonical_store.write_canonical(artifact_id, {"content": content}, approved_by="definer")
        return {"approved": True, "canonical_written": True, "artifact_id": artifact_id}
    except Exception as e:
        return {"approved": False, "reason": str(e)}
