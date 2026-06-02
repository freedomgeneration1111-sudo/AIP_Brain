"""Chat WebSocket surface.

WebSocket chat endpoint at /api/v1/chat/{session_id}

Phase 4 Knowledge Exploration enhancements:
- Augmented mode now routes through retrieval + context injection
  before model dispatch, producing source-grounded answers
- Sources are included in the response payload so the GUI can
  display citations and provenance
- Normal mode remains unchanged: direct model dispatch

Phase 3 Auto-Save Ingestion enhancements (preserved):
- Auto-save hook triggers ingestion after each completed chat turn
- Ingestion is non-blocking: response is sent immediately, ingestion runs as a background task
- Session auto_save flag controls whether ingestion fires (default: True)
- Ingestion status (idle/ingesting/error) tracked in session metadata
- Gate flow: triggered when session is "augmented" mode and ReviewQueueStore is available
- Trajectory regulation check after each turn (when SessionManager is available)
- Graceful degradation when model_provider, review_queue_store, or ingestion stores are unavailable

Message flow (normal): message → ModelSlotResolver.call() → response → [auto-save ingestion]
Message flow (augmented): message → retrieve sources → assemble context → ModelSlotResolver.call() → response + sources → [auto-save ingestion]
Also: context_reset (from L4), budget exhaustion, trajectory_warning.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from aip.adapter.api.dependencies import get_container
from aip.adapter.api.routes.sessions import get_session_meta, increment_turn_count

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/chat/{session_id}")
async def chat_websocket(websocket: WebSocket, session_id: str):
    """WebSocket chat endpoint with DEFINER gate handling and ModelSlotResolver routing.

    The GUI connects to this endpoint after creating a session via POST /api/v1/sessions.
    Each message is routed through the configured model slot (from session metadata),
    allowing the backend's ModelSlotResolver to dispatch to the appropriate provider.
    """
    await websocket.accept()
    logger.info("chat_ws_connected: session=%s", session_id)

    _container = get_container(websocket)  # type: ignore  # in real lifespan context

    # Look up session metadata to determine which model slot to use
    session_meta = get_session_meta(session_id)
    model_slot = "synthesis"  # default
    session_mode = "normal"  # default
    auto_save_enabled = True  # default — sessions created with auto_save=True
    if session_meta:
        model_slot = session_meta.get("model_slot", "synthesis")
        session_mode = session_meta.get("mode", "normal")
        auto_save_enabled = session_meta.get("auto_save", True)
    logger.info("chat_ws_session: session=%s slot=%s mode=%s model_provider=%s",
                session_id, model_slot, session_mode,
                "yes" if _container.model_provider is not None else "NONE")

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
                logger.info("chat_message_received", slot=effective_slot, content_len=len(content), session=session_id, mode=session_mode)

                # Route through ModelSlotResolver if available
                model_provider = _container.model_provider
                if model_provider is not None:
                    try:
                        # Build messages list for the model call
                        # Include system context from session if available
                        messages = []
                        response_sources = []  # Sources for augmented mode

                        if session_mode == "augmented" and _container.lexical_store is not None:
                            # === AUGMENTED MODE: Retrieve sources + inject context ===
                            try:
                                from aip.orchestration.ask_pipeline import (
                                    _search_sources,
                                    _assemble_context,
                                    _sanitize_fts_query,
                                )

                                # Resolve domain from session or project
                                domain = (session_meta or {}).get("domain")

                                # If project_id is set, try to resolve domain from project store
                                project_id = (session_meta or {}).get("project_id")
                                if project_id and _container.project_store is not None:
                                    try:
                                        projects = await _container.project_store.list_projects()
                                        for p in projects:
                                            if p.get("project_id") == project_id or p.get("name") == project_id:
                                                domain = p.get("domain") or domain
                                                break
                                    except Exception:
                                        logger.warning("project lookup failed", exc_info=True)
                                        pass

                                # Retrieve relevant sources
                                source_refs = await _search_sources(
                                    query=content,
                                    project_domain=domain,
                                    source_filter="all",
                                    lexical_store=_container.lexical_store,
                                    vector_store=_container.vector_store,
                                    embedding_provider=_container.embedding_provider,
                                    max_sources=10,
                                )

                                if source_refs:
                                    # Assemble context and build source-grounded system prompt
                                    context = _assemble_context(source_refs, max_sources=10)
                                    messages.append({
                                        "role": "system",
                                        "content": (
                                            f"You are AIP, a source-grounded knowledge assistant. "
                                            f"Answer the user's question based on the provided sources. "
                                            f"Cite sources using [source: <source_id>] notation. "
                                            f"If the sources do not contain enough information, say so explicitly.\n\n"
                                            f"Sources:\n{context}"
                                        ),
                                    })
                                    # Store sources for the response payload
                                    response_sources = [
                                        {
                                            "source_id": s.source_id,
                                            "source_type": s.source_type,
                                            "title": s.title,
                                            "score": s.score,
                                            "content_snippet": s.content_snippet,
                                            "domain": s.domain,
                                        }
                                        for s in source_refs
                                    ]
                                else:
                                    # No sources found — still add a note in system prompt
                                    messages.append({
                                        "role": "system",
                                        "content": (
                                            "You are AIP, a knowledge assistant. "
                                            "No relevant sources were found in the knowledge base for this query. "
                                            "Answer based on your general knowledge but note that no source material was available."
                                        ),
                                    })
                            except Exception as exc:
                                # Retrieval failed — fall back to normal mode behavior
                                import logging
                                logging.getLogger(__name__).warning(
                                    "Augmented retrieval failed, falling back to normal: %s", exc
                                )
                                if session_meta and session_meta.get("role"):
                                    role_hint = session_meta.get("role", "")
                                    messages.append({
                                        "role": "system",
                                        "content": f"You are acting in the {role_hint} role. Respond accordingly.",
                                    })
                        else:
                            # === NORMAL MODE: Direct model dispatch ===
                            if session_meta and session_meta.get("role"):
                                role_hint = session_meta.get("role", "")
                                messages.append({
                                    "role": "system",
                                    "content": f"You are acting in the {role_hint} role. Respond accordingly.",
                                })

                        messages.append({"role": "user", "content": content})

                        # Budget check before model call
                        if _container.budget_manager is not None:
                            try:
                                budget_ok = await _container.budget_manager.check_before_call(
                                    scope="session",
                                    scope_id=session_id,
                                    estimated_tokens=2000,  # rough estimate per turn
                                )
                                if not budget_ok:
                                    await websocket.send_json({
                                        "type": "error",
                                        "content": "Budget limit reached. Session token budget has been exceeded. Consider starting a new session.",
                                        "error_type": "budget_exhausted",
                                        "model_slot": effective_slot,
                                    })
                                    continue
                            except Exception as exc:
                                # Budget check failure is non-critical — log and proceed
                                import logging
                                logging.getLogger(__name__).warning("Budget check failed, proceeding: %s", exc)

                        result = await model_provider.call(effective_slot, messages)

                        # Check for error from model provider
                        if result.get("error"):
                            error_msg = result.get("error_message", "Model call failed — provider returned an error.")
                            logger.error("chat_model_provider_error", slot=effective_slot, error=error_msg, model=result.get("model", "?"), session=session_id)
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
                        latency = result.get("latency_ms", 0)
                        logger.info("chat_response_sent", slot=effective_slot, model=model_used, latency_ms=latency, content_len=len(response_content), session=session_id)

                        # Increment turn counter
                        increment_turn_count(session_id, _container)

                        # Record budget consumption
                        if _container.budget_manager is not None:
                            try:
                                tokens_used = usage.get("total_tokens", usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
                                await _container.budget_manager.record_consumption(
                                    scope="session",
                                    scope_id=session_id,
                                    tokens_used=tokens_used or 0,
                                    cost_usd=result.get("cost_usd", 0.0),
                                    model_slot=effective_slot,
                                )
                            except Exception as exc:
                                import logging
                                logging.getLogger(__name__).debug("Budget record failed: %s", exc)

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
                            "auto_save": auto_save_enabled and _container.artifact_store is not None and _container.lexical_store is not None,
                            "sources": response_sources,  # Empty in normal mode, populated in augmented mode
                            "mode": session_mode,  # Echo the mode so GUI knows how the response was generated
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

                        # Auto-save ingestion: after a successful chat turn,
                        # trigger background ingestion if auto_save is enabled
                        # and the required stores (artifact_store, lexical_store) are available.
                        if auto_save_enabled and _container.artifact_store is not None and _container.lexical_store is not None:
                            try:
                                from aip.adapter.api.routes.ingest import auto_save_chat_turn

                                domain = (session_meta or {}).get("domain", "chat")
                                asyncio.create_task(
                                    auto_save_chat_turn(
                                        session_id=session_id,
                                        user_message=content,
                                        assistant_response=response_content,
                                        container=_container,
                                        domain=domain,
                                    ),
                                    name=f"auto-save-{session_id}",
                                )
                            except Exception:
                                # Non-critical — auto-save is advisory
                                pass

                    except ValueError as exc:
                        # Slot not found or invalid
                        logger.error("chat_model_slot_error", slot=effective_slot, error=str(exc), session=session_id)
                        await websocket.send_json(
                            {
                                "type": "error",
                                "content": f"Model slot error: {exc}",
                                "model_slot": effective_slot,
                            }
                        )
                    except Exception as exc:
                        # Model call failed — send error rather than crashing
                        logger.error("chat_model_call_failed", slot=effective_slot, error=str(exc), session=session_id, exc_info=True)
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
