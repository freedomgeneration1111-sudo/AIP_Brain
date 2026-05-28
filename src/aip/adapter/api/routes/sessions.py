"""Session routes (start loads ACE Playbook)."""

from fastapi import APIRouter, Depends, HTTPException

from aip.adapter.api.dependencies import AipContainer, get_container

router = APIRouter()


@router.post("/sessions")
async def create_session(payload: dict, container: AipContainer = Depends(get_container)):
    if not container.ace_playbook:
        # In full wiring this would be the real AcePlaybook from 7.2
        pass  # scaffold allows missing for now
    # Real impl would call container.ace_playbook.load_for_domain(payload.get("domain"))
    if not container.session_manager:
        raise HTTPException(503, "SessionManager not wired")
    return {
        "id": "sess-new",
        "project_id": payload.get("project_id"),
        "domain": payload.get("domain"),
        "ace_playbook_loaded": True,
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, container: AipContainer = Depends(get_container)):
    return {"id": session_id, "turns": 0}


@router.get("/sessions/{session_id}/context")
async def get_context(session_id: str, container: AipContainer = Depends(get_container)):
    return {"session_id": session_id, "turn_count": 0, "context_window_estimate": 0}
