"""Chat WebSocket surface.

WebSocket chat endpoint at /api/v1/chat/{session_id}

Phase 1 Communication Bridge enhancements:
- Looks up session metadata (role, model_slot) from the sessions store
- Routes messages through ModelSlotResolver instead of echo
- Handles streaming-style responses (chunked via WebSocket JSON frames)
- Maintains gate flow for DEFINER review interactions
- Graceful degradation when model_provider is not configured

Message flow: message → ModelSlotResolver.call() → response or gate
Also: context_reset (from L4), budget exhaustion.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from aip.adapter.api.dependencies import get_container
from aip.adapter.api.routes.sessions import get_session_meta, increment_turn_count

router = APIRouter()


@router.websocket("/chat/{session_id}")
async def chat_websocket(websocket: WebSocket, session_id: str):
    """WebSocket chat endpoint with DEFINER gate handling and ModelSlotResolver routing.

    The GUI connects to this endpoint after creating a session via POST /api/v1/sessions.
    Each message is routed through the configured model slot (from session metadata),
    allowing the backend's ModelSlotResolver to dispatch to the appropriate provider.
    """
    await websocket.accept()

    _container = get_container(websocket)  # type: ignore  # in real lifespan context

    # Look up session metadata to determine which model slot to use
    session_meta = get_session_meta(session_id)
    model_slot = "synthesis"  # default
    if session_meta:
        model_slot = session_meta.get("model_slot", "synthesis")

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
                # Allow per-message slot override
                override_slot = msg.get("model_slot")
                effective_slot = override_slot or model_slot

                # Check for gate trigger keywords (demo — real impl comes from workflow)
                if "gate" in content.lower():
                    await websocket.send_json(
                        {
                            "type": "gate",
                            "gate_type": "definer_review",
                            "artifact_id": "art-demo-123",
                            "preview": "Proposed design decision...",
                        },
                    )
                    continue

                # Route through ModelSlotResolver if available
                model_provider = _container.model_provider
                if model_provider is not None:
                    try:
                        # Build messages list for the model call
                        # Include system context from session if available
                        messages = []
                        if session_meta and session_meta.get("role"):
                            role_hint = session_meta.get("role", "")
                            messages.append(
                                {
                                    "role": "system",
                                    "content": f"You are acting in the {role_hint} role. Respond accordingly.",
                                }
                            )
                        messages.append({"role": "user", "content": content})

                        result = await model_provider.call(effective_slot, messages)

                        # Check for error from model provider
                        if result.get("error"):
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "content": result.get(
                                        "error_message",
                                        "Model call failed — provider returned an error.",
                                    ),
                                    "model_slot": effective_slot,
                                }
                            )
                            continue

                        response_content = result.get("content", "")
                        model_used = result.get("model", effective_slot)
                        usage = result.get("usage", {})

                        # Increment turn counter
                        increment_turn_count(session_id)

                        await websocket.send_json(
                            {
                                "type": "response",
                                "content": response_content,
                                "model_slot": effective_slot,
                                "model": model_used,
                                "artifacts": [],
                                "tokens_used": usage.get("total_tokens", usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)),
                                "latency_ms": result.get("latency_ms", 0),
                                "cost_usd": result.get("cost_usd", 0.0),
                            }
                        )
                    except ValueError as exc:
                        # Slot not found or invalid
                        await websocket.send_json(
                            {
                                "type": "error",
                                "content": f"Model slot error: {exc}",
                                "model_slot": effective_slot,
                            }
                        )
                    except Exception as exc:
                        # Model call failed — send error rather than crashing
                        await websocket.send_json(
                            {
                                "type": "error",
                                "content": f"Model call failed: {exc}",
                                "model_slot": effective_slot,
                            }
                        )
                else:
                    # No model provider configured — return degradation notice
                    await websocket.send_json(
                        {
                            "type": "response",
                            "content": f"[No model provider configured] Echo: {content}",
                            "model_slot": effective_slot,
                            "model": "none",
                            "artifacts": [],
                            "tokens_used": 0,
                        }
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

            elif msg.get("type") == "ping":
                # Keepalive / latency check
                await websocket.send_json({"type": "pong"})

            else:
                await websocket.send_json({"type": "error", "content": f"unknown message type: {msg.get('type')}"})

    except WebSocketDisconnect:
        # Client disconnected — normal flow
        pass
    except Exception as exc:
        # Unexpected error during WebSocket communication
        # In production, this would log to the event store
        try:
            await websocket.send_json({"type": "error", "content": f"WebSocket error: {exc}"})
        except Exception:
            pass  # Connection already broken
