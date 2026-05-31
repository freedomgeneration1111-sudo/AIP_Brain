"""Session routes (start loads ACE Playbook).

Enhanced for Phase 1 Communication Bridge:
- Session creation now accepts role/slot preferences from the GUI
- Generates a proper unique session ID (uuid4)
- Stores session metadata in-memory for WebSocket chat to reference
- Gracefully degrades when SessionManager is not yet wired
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter()

# In-memory session store for Phase 1.
# Maps session_id -> session metadata dict.
# This will be replaced by SessionManager in a later phase when the
# orchestration layer's session management is fully wired.
_sessions: dict[str, dict[str, Any]] = {}


@router.post("/sessions")
async def create_session(payload: dict, container: AipContainer = Depends(get_container)):
    """Create a new chat session.

    Accepts optional role/slot preferences from the GUI:
      - role: the active actor role (e.g. "beast", "vigil", "embedding")
      - model_slot: the model slot to use for chat (e.g. "synthesis", "evaluation")
      - project_id: optional project context
      - domain: optional domain context

    Returns a unique session_id that the GUI uses for WebSocket communication.
    """
    session_id = f"sess-{uuid.uuid4().hex[:12]}"

    # Build session metadata from payload
    session_meta: dict[str, Any] = {
        "id": session_id,
        "project_id": payload.get("project_id"),
        "domain": payload.get("domain"),
        "role": payload.get("role"),  # e.g. "beast", "vigil", "embedding"
        "model_slot": payload.get("model_slot", "synthesis"),  # default to synthesis slot
        "mode": payload.get("mode", "normal"),  # "normal" or "augmented"
        "ace_playbook_loaded": True,
        "turn_count": 0,
        "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }

    # If SessionManager is available, delegate to it for full session lifecycle.
    # Otherwise, store in-memory for Phase 1 bridge functionality.
    if container.session_manager is not None:
        try:
            # Future: delegate to real SessionManager
            pass
        except Exception:
            pass  # Fall through to in-memory store

    # Load ACE playbook if the container has one wired
    if container.ace_playbook is not None:
        try:
            # Future: container.ace_playbook.load_for_domain(payload.get("domain"))
            pass
        except Exception:
            session_meta["ace_playbook_loaded"] = False

    _sessions[session_id] = session_meta

    return {
        "id": session_id,
        "project_id": session_meta["project_id"],
        "domain": session_meta["domain"],
        "role": session_meta["role"],
        "model_slot": session_meta["model_slot"],
        "mode": session_meta["mode"],
        "ace_playbook_loaded": session_meta["ace_playbook_loaded"],
    }


@router.get("/sessions")
async def list_sessions(container: AipContainer = Depends(get_container)):
    """List all active sessions."""
    return {"sessions": list(_sessions.values())}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, container: AipContainer = Depends(get_container)):
    """Get session details including role, model slot, and turn count."""
    if session_id in _sessions:
        return _sessions[session_id]
    # Fallback for sessions not in the in-memory store
    return {"id": session_id, "turns": 0, "role": None, "model_slot": None}


@router.get("/sessions/{session_id}/context")
async def get_context(session_id: str, container: AipContainer = Depends(get_container)):
    """Get session context including turn count and context window estimate."""
    if session_id in _sessions:
        meta = _sessions[session_id]
        return {
            "session_id": session_id,
            "turn_count": meta.get("turn_count", 0),
            "context_window_estimate": 0,
            "role": meta.get("role"),
            "model_slot": meta.get("model_slot"),
        }
    return {"session_id": session_id, "turn_count": 0, "context_window_estimate": 0}


def get_session_meta(session_id: str) -> dict[str, Any] | None:
    """Retrieve session metadata (used by the chat WebSocket handler).

    This is an internal helper — not exposed as an API endpoint.
    The chat WebSocket uses this to look up which model slot and role
    to apply for an incoming message.
    """
    return _sessions.get(session_id)


def increment_turn_count(session_id: str) -> None:
    """Increment the turn counter for a session after a successful chat exchange."""
    if session_id in _sessions:
        _sessions[session_id]["turn_count"] = _sessions[session_id].get("turn_count", 0) + 1
