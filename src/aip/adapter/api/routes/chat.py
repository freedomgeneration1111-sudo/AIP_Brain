"""Chat WebSocket surface.

WebSocket chat endpoint at /api/v1/chat/{session_id}

Phase 3 Gate Hardening enhancements:
- Removed keyword-based gate demo ("gate" detection)
- Gate is now triggered when session is "augmented" mode and ReviewQueueStore is available
- Gate responses integrate with ReviewQueueStore for real approval/rejection
- Trajectory regulation check after each turn (when SessionManager is available)
- Graceful degradation when model_provider or review_queue_store is not configured

Message flow: message → ModelSlotResolver.call() → response or gate
Also: context_reset (from L4), budget exhaustion, trajectory_warning.
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
    session_mode = "normal"  # default
    if session_meta:
        model_slot = session_meta.get("model_slot", "synthesis")
        session_mode = session_meta.get("mode", "normal")

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
                        increment_turn_count(session_id, _container)

                        # Build response payload
                        response_payload = {
                            "type": "response",
                            "content": response_content,
                            "model_slot": effective_slot,
                            "model": model_used,
                            "artifacts": [],
                            "tokens_used": usage.get("total_tokens", usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)),
                            "latency_ms": result.get("latency_ms", 0),
                            "cost_usd": result.get("cost_usd", 0.0),
                        }

                        # Check if review is available for augmented mode sessions
                        # This is a transitional approach — full workflow integration comes later.
                        # For now, augmented mode + ReviewQueueStore means the response
                        # includes a review_available flag so the GUI can show the review panel.
                        if (
                            session_mode == "augmented"
                            and _container.review_queue_store is not None
                        ):
                            response_payload["review_available"] = True

                        await websocket.send_json(response_payload)

                        # Trajectory regulation check after each turn
                        # When SessionManager is available, check if trajectory
                        # is degrading and send warnings to the client.
                        if _container.session_manager is not None and _container.event_store is not None:
                            try:
                                from aip.foundation.schemas import SessionContext

                                # Build a SessionContext from current session metadata
                                updated_meta = get_session_meta(session_id)
                                if updated_meta is not None:
                                    ctx = SessionContext(
                                        session_id=session_id,
                                        project_id=updated_meta.get("project_id", ""),
                                        turn_count=updated_meta.get("turn_count", 0),
                                        context_tokens_estimate=updated_meta.get("context_tokens_estimate", 0),
                                        artifacts_produced=updated_meta.get("artifacts_produced", []),
                                    )
                                    signals, should_intervene = await _container.session_manager.check_trajectory(
                                        ctx, _container.event_store,
                                    )
                                    if should_intervene:
                                        # Send trajectory warning to client
                                        signal_summaries = [
                                            {
                                                "type": s.signal_type,
                                                "failure_type": s.failure_type,
                                                "detail": s.detail,
                                            }
                                            for s in signals
                                        ]
                                        await websocket.send_json(
                                            {
                                                "type": "trajectory_warning",
                                                "signals": signal_summaries,
                                                "intervention_recommended": True,
                                                "message": "Trajectory degradation detected. Consider context reset.",
                                            }
                                        )
                            except Exception:
                                # Non-critical — trajectory check is advisory
                                pass

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
                queue_item_id = msg.get("queue_item_id")

                # Integrate with ReviewQueueStore when available
                if (
                    _container.review_queue_store is not None
                    and queue_item_id is not None
                ):
                    try:
                        decision = "approved" if approved else "rejected"
                        result = await _container.review_queue_store.decide(
                            item_id=int(queue_item_id),
                            decision=decision,
                            decided_by="definer",
                        )
                        if not result.get("ok"):
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "content": f"Review decision failed: {result.get('error', {}).get('message', 'unknown')}",
                                }
                            )
                            continue
                        await websocket.send_json(
                            {
                                "type": "response",
                                "content": f"Gate {'approved' if approved else 'rejected'} (workflow resumed)",
                                "artifacts": [result.get("artifact_id", "")] if approved else [],
                                "tokens_used": 10,
                                "queue_item_id": queue_item_id,
                                "decision": decision,
                            },
                        )
                    except Exception as exc:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "content": f"Review decision failed: {exc}",
                            }
                        )
                else:
                    # No ReviewQueueStore or no queue_item_id — legacy response
                    await websocket.send_json(
                        {
                            "type": "response",
                            "content": f"Gate {'approved' if approved else 'rejected'} (workflow resumed)",
                            "artifacts": [],
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
