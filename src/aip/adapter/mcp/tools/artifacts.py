"""aip_artifact_approve MCP tool — admin, uses Ecs + Canonical + Gate.

Per spec: approval through MCP follows the same path as REST/CLI.
Gate enforcement happens in server.py before this function is called.
This function performs the actual ECS transition + canonical write.

The caller (server.py) is responsible for:
- Autonomy gate check
- Artifact existence check
- ECS state validation (must be REVIEWED)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def aip_artifact_approve(container: Any, artifact_id: str) -> dict:
    """Approve artifact for canonical promotion.

    Returns {"approved": True, "canonical_written": bool, "artifact_id": str}
    on success, or {"approved": False, "reason": str} on failure.

    This function does NOT auto-approve — it requires the artifact to be in
    REVIEWED state. The server.py dispatch validates this before calling.
    """
    if not container.ecs_store or not container.canonical_store:
        return {"approved": False, "reason": "Required stores (ECS, Canonical) not wired"}

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
        logger.info("MCP approved artifact %s and wrote canonical", artifact_id)
        return {"approved": True, "canonical_written": True, "artifact_id": artifact_id}
    except Exception as e:
        reason = str(e)
        logger.error("MCP artifact approve failed for %s: %s", artifact_id, reason)
        return {"approved": False, "reason": reason}
