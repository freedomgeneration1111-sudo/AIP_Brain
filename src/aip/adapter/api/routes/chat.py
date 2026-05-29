"""Chat WebSocket surface.

Per spec: WS /api/v1/chat/{session_id}
Message flow: message → (synthesis + ACE) → response or gate → gate_response → resume
Also: context_reset (from L4), budget exhaustion.
Mounted on the 8.1 FastAPI app.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from aip.adapter.api.dependencies import get_container

router = APIRouter()


@router.websocket("/chat/{session_id}")
async def chat_websocket(websocket: WebSocket, session_id: str):
    """WebSocket chat endpoint with DEFINER gate handling and ACE integration."""
    await websocket.accept()

    container = get_container(websocket)  # type: ignore  # in real lifespan context

    # In full impl: load ACE playbook for the session's domain (already done at session start per 8.1/8.2)
    # container.ace_playbook.load_for_domain(...) if available

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except Exception:
                await websocket.send_json({"type": "error", "content": "invalid json"})
                continue

            if msg.get("type") == "message":
                content = msg.get("content", "")
                # Simulate synthesis + possible gate (real impl runs the workflow)
                # For 8.3 scaffold we echo + demonstrate gate flow
                if "gate" in content.lower():
                    # Simulate hitting a dialog node
                    await websocket.send_json(
                        {
                            "type": "gate",
                            "gate_type": "definer_review",
                            "artifact_id": "art-demo-123",
                            "preview": "Proposed design decision...",
                        },
                    )
                else:
                    await websocket.send_json(
                        {
                            "type": "response",
                            "content": f"Echo (scaffold): {content}",
                            "artifacts": [],
                            "tokens_used": 42,
                        },
                    )
            elif msg.get("type") == "gate_response":
                approved = msg.get("approved", False)
                await websocket.send_json(
                    {
                        "type": "response",
                        "content": f"Gate {'approved' if approved else 'rejected'} (workflow resumed)",
                        "artifacts": ["art-demo-123"] if approved else [],
                        "tokens_used": 10,
                    },
                )
            else:
                await websocket.send_json({"type": "error", "content": "unknown message type"})
    except WebSocketDisconnect:
        # In real impl: write disconnect event, update SessionContext
        pass
