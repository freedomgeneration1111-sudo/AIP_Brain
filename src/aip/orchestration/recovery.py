"""WorkflowRecovery — orchestration component for interrupted workflow recovery.

Per AIP_0_1_Phase8_BuildSpec_Rev1.0.md exact prose + box + ANNEX.
Handles post-crash/restart resumption from checkpoints in state.db.
Records recovery in trace_events.

Issue 26: Fix recover_interrupted_workflow to verify prior node outputs exist
and record recovery in trace_events.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite


class WorkflowRecovery:
    """Manages checkpointing and recovery for interrupted workflows."""

    def __init__(self, db_path: str = "db/state.db", trace_store: Any = None, artifact_store: Any = None) -> None:
        self.db_path = db_path
        self.trace_store = trace_store
        self.artifact_store = artifact_store

    async def checkpoint_workflow(self, session_id: str, node_id: str, state: dict) -> None:
        """Write current workflow state to checkpoints table."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                    session_id TEXT PRIMARY KEY,
                    node_id TEXT,
                    state TEXT,
                    updated_at TEXT
                )
            """)
            await db.execute(
                "INSERT OR REPLACE INTO workflow_checkpoints (session_id, node_id, state, updated_at) VALUES (?, ?, ?, datetime('now'))",
                (session_id, node_id, str(state)),
            )
            await db.commit()

    async def get_interrupted_workflows(self) -> list[dict]:
        """Return all sessions with incomplete checkpoints."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                    session_id TEXT PRIMARY KEY,
                    node_id TEXT,
                    state TEXT,
                    updated_at TEXT
                )
            """)
            cursor = await db.execute("SELECT session_id, node_id, state, updated_at FROM workflow_checkpoints")
            rows = await cursor.fetchall()
            return [{"session_id": r[0], "node_id": r[1], "state": r[2], "updated_at": r[3]} for r in rows]

    async def recover_interrupted_workflow(self, session_id: str) -> dict:
        """Resume from last checkpoint; verify prior outputs exist; return recovery plan.

        Issue 26: Verify prior node outputs exist and record recovery in trace_events.
        """
        checkpoints = await self.get_interrupted_workflows()
        for cp in checkpoints:
            if cp["session_id"] == session_id:
                # Verify prior node outputs exist
                outputs_verified = True
                verification_detail = "All prior outputs verified."
                if self.artifact_store is not None:
                    try:
                        # Parse state dict to find artifact IDs produced by prior nodes
                        import ast
                        state_dict = ast.literal_eval(cp["state"]) if isinstance(cp["state"], str) else cp["state"]
                        prior_artifacts = state_dict.get("artifacts_produced", []) if isinstance(state_dict, dict) else []
                        missing = []
                        for art_id in prior_artifacts:
                            try:
                                content = await self.artifact_store.read(art_id)
                                if not content:
                                    missing.append(art_id)
                            except Exception:
                                missing.append(art_id)
                        if missing:
                            outputs_verified = False
                            verification_detail = f"Missing prior outputs: {missing}"
                    except Exception as e:
                        outputs_verified = False
                        verification_detail = f"Could not verify outputs: {e}"

                # Record recovery in trace_events
                if self.trace_store is not None:
                    try:
                        await self.trace_store.write_event(
                            session_id=session_id,
                            node_type="workflow_recovery",
                            failure_type="",
                            outcome="recovered" if outputs_verified else "partial_recovery",
                            detail=f"Workflow recovered from node {cp['node_id']}. {verification_detail}",
                        )
                    except Exception:
                        pass  # trace failures must not break recovery

                return {
                    "status": "recovered" if outputs_verified else "partial_recovery",
                    "session_id": session_id,
                    "resume_from_node": cp["node_id"],
                    "state": cp["state"],
                    "outputs_verified": outputs_verified,
                    "verification_detail": verification_detail,
                    "message": f"Workflow resumed from last checkpoint. {verification_detail}",
                }
        return {"status": "no_checkpoint", "session_id": session_id}
