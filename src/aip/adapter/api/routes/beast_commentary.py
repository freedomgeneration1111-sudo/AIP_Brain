"""Beast Commentary endpoints for the Ask Workbench.

Provides turn-level Beast commentary generation and retrieval:
  - GET  /api/v1/turns/{turn_id}/beast-commentary
  - POST /api/v1/turns/{turn_id}/beast-commentary/run

Beast commentary is ADVISORY ONLY. Beast may suggest actions but must
never silently execute them. No auto-approve, no auto-export, no wiki
mutation, no config changes.

Commentary is stored as GENERATED artifacts via the VersionedArtifactStore
with ECS state management. Commentary generation uses the Beast provider
(ModelSlotResolver "beast" slot) if available; otherwise returns an honest
"unavailable" state.

Layer discipline: This module imports ONLY from adapter and foundation.
Store access is through the container, not via direct orchestration imports.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aip.adapter.api.dependencies import AipContainer, get_container

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Valid commentary modes
# ---------------------------------------------------------------------------

VALID_MODES = {"continuity", "critique", "strategy", "librarian", "risk"}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class BeastCommentaryRequest(BaseModel):
    """Request body for Beast commentary generation."""

    session_id: str = ""
    mode: str = "continuity"  # continuity, critique, strategy, librarian, risk
    question_text: str = ""
    answer_text: str = ""
    sources: list[dict] = []
    trace_available: bool = False
    lexical_only: bool = False
    vector_contributed: bool = False


class BeastCommentaryResponse(BaseModel):
    """Response model for Beast commentary."""

    id: str = ""
    turn_id: str = ""
    session_id: str = ""
    mode: str = ""
    summary: str = ""
    critique: str = ""
    continuity_notes: str = ""
    risk_notes: str = ""
    suggested_actions: list[dict] = []  # Each: {"action": str, "target": str, "advisory_only": True}
    suggested_wiki_links: list[str] = []
    suggested_artifacts: list[str] = []
    model_comparison: str = ""
    retrieval_notes: str = ""
    source_notes: str = ""
    created_at: str = ""
    status: str = "available"  # available, not_available, unavailable, not_wired, error
    persistence: str = "available"  # available, not_available
    error: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _commentary_artifact_id(turn_id: str, mode: str = "") -> str:
    """Deterministic artifact ID for Beast commentary on a given turn + mode.

    Pattern: ``beast:commentary:{sha256(turn_id:mode)[:16]}``

    The mode is included in the hash input so that different commentary
    modes (continuity, critique, strategy, librarian, risk) produce
    distinct artifact IDs on the same turn. This prevents one mode from
    overwriting another.
    """
    key = f"{turn_id}:{mode}" if mode else turn_id
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"beast:commentary:{digest}"


def _load_soul_text() -> str:
    """Load Beast soul from data/beast_soul.md.

    Returns empty string if the file is missing or unreadable.
    Per AIP-G-02: never raise, never fake.
    """
    soul_path = Path("data/beast_soul.md")
    try:
        if soul_path.exists():
            text = soul_path.read_text(encoding="utf-8").strip()
            if text:
                return text
    except Exception as exc:
        logger.warning("beast_soul_load_failed", path=str(soul_path), error=str(exc))
    return ""


def _prepend_soul(system_prompt: str, soul_text: str) -> str:
    """Prepend soul text to a system prompt (same pattern as Beast actor)."""
    if soul_text:
        return f"{soul_text}\n\n---\n\n{system_prompt}"
    return system_prompt


def _not_available_response(turn_id: str, **overrides: Any) -> BeastCommentaryResponse:
    """Build a standard 'not available' response with honest defaults."""
    defaults = {
        "id": "",
        "turn_id": turn_id,
        "session_id": "",
        "mode": "",
        "summary": "No commentary yet for this turn",
        "status": "not_available",
        "persistence": "available",
    }
    defaults.update(overrides)
    return BeastCommentaryResponse(**defaults)


# ---------------------------------------------------------------------------
# GET endpoint — retrieve existing commentary
# ---------------------------------------------------------------------------


@router.get(
    "/turns/{turn_id}/beast-commentary",
    response_model=BeastCommentaryResponse,
)
async def get_beast_commentary(
    turn_id: str,
    mode: str = "continuity",
    container: AipContainer = Depends(get_container),
):
    """Retrieve existing Beast commentary for a turn + mode.

    Returns the commentary artifact if one exists for the given mode,
    or an honest ``not_available`` status if no commentary has been
    generated yet for that mode. Returns ``unavailable`` if persistence
    (artifact_store) is not wired.

    The ``mode`` query parameter selects which commentary mode to
    retrieve. Different modes produce distinct artifacts, so switching
    modes does not overwrite or return stale data from another mode.
    """
    # Normalize mode
    mode = mode.strip().lower() if mode else "continuity"

    # No artifact store — honest degradation
    if container.artifact_store is None:
        return _not_available_response(
            turn_id,
            mode=mode,
            status="unavailable",
            persistence="not_available",
            summary="Artifact store not available — cannot retrieve commentary",
        )

    artifact_id = _commentary_artifact_id(turn_id, mode)

    try:
        content, metadata = await container.artifact_store.read_with_metadata(artifact_id)
    except KeyError:
        # No commentary yet for this turn+mode — honest, not fake
        return _not_available_response(turn_id, mode=mode)
    except Exception as exc:
        logger.error(
            "beast_commentary_read_failed",
            artifact_id=artifact_id,
            error=str(exc),
        )
        return _not_available_response(
            turn_id,
            mode=mode,
            status="error",
            error=f"Failed to read commentary artifact: {exc}",
        )

    # Parse stored commentary content (JSON)
    try:
        commentary_data = json.loads(content) if content else {}
    except (json.JSONDecodeError, TypeError):
        # Content might be plain text from an older format
        commentary_data = {"summary": content}

    return BeastCommentaryResponse(
        id=artifact_id,
        turn_id=turn_id,
        session_id=commentary_data.get("session_id", metadata.get("session_id", "")),
        mode=commentary_data.get("mode", metadata.get("mode", mode)),
        summary=commentary_data.get("summary", ""),
        critique=commentary_data.get("critique", ""),
        continuity_notes=commentary_data.get("continuity_notes", ""),
        risk_notes=commentary_data.get("risk_notes", ""),
        suggested_actions=commentary_data.get("suggested_actions", []),
        suggested_wiki_links=commentary_data.get("suggested_wiki_links", []),
        suggested_artifacts=commentary_data.get("suggested_artifacts", []),
        model_comparison=commentary_data.get("model_comparison", ""),
        retrieval_notes=commentary_data.get("retrieval_notes", ""),
        source_notes=commentary_data.get("source_notes", ""),
        created_at=metadata.get("created_at", ""),
        status="available",
        persistence="available",
    )


# ---------------------------------------------------------------------------
# POST endpoint — generate commentary
# ---------------------------------------------------------------------------


@router.post(
    "/turns/{turn_id}/beast-commentary/run",
    response_model=BeastCommentaryResponse,
)
async def run_beast_commentary(
    turn_id: str,
    request: BeastCommentaryRequest,
    container: AipContainer = Depends(get_container),
):
    """Generate Beast commentary for a turn.

    Uses the Beast provider (ModelSlotResolver "beast" slot) to produce
    structured advisory commentary. Commentary is persisted as a GENERATED
    artifact — it requires DEFINER review before approval.

    Returns ``not_wired`` if no model provider or Beast slot is available.
    Returns ``unavailable`` if persistence is not wired.
    Never auto-approves, auto-exports, or mutates wiki/config.
    """
    # Validate mode
    mode = request.mode.strip().lower()
    if mode not in VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{request.mode}'. Valid modes: {sorted(VALID_MODES)}",
        )

    # No model provider — honest degradation
    if container.model_provider is None:
        return _not_available_response(
            turn_id,
            session_id=request.session_id,
            mode=mode,
            status="not_wired",
            summary="Beast commentary generation requires a configured model provider",
        )

    # No artifact store — can generate but can't persist
    if container.artifact_store is None:
        return _not_available_response(
            turn_id,
            session_id=request.session_id,
            mode=mode,
            status="unavailable",
            persistence="not_available",
            summary="Artifact store not available — cannot persist commentary",
        )

    # --- Build prompts ---
    soul_text = _load_soul_text()

    mode_descriptions = {
        "continuity": "Assess how well this answer connects to prior turns and established knowledge. Flag gaps in reasoning or context continuity.",
        "critique": "Critically evaluate the answer's strengths and weaknesses. Identify unsupported claims, logical fallacies, or missing perspectives.",
        "strategy": "Suggest strategic next steps for the DEFINER. What should be explored further? What decisions need to be made?",
        "librarian": "Evaluate source quality and coverage. Suggest additional sources, wiki links, or knowledge gaps that should be addressed.",
        "risk": "Identify potential risks, failure modes, or unintended consequences. Flag assumptions that could be wrong.",
    }

    system_prompt = (
        "You are AIP Beast, the corpus intelligence actor. You provide turn-level "
        "advisory commentary for the Ask Workbench. Your commentary is ADVISORY ONLY — "
        "you may suggest actions but they MUST NOT be executed without DEFINER approval.\n\n"
        f"Commentary mode: {mode}\n"
        f"Focus: {mode_descriptions.get(mode, mode_descriptions['continuity'])}\n\n"
        "IMPORTANT CONSTRAINTS:\n"
        "- All suggested actions are ADVISORY ONLY and require DEFINER approval\n"
        "- Never auto-approve, auto-export, mutate wiki, change config, or change model slots\n"
        "- Every suggested action must include advisory_only: true and requires_DEFINER_approval: true\n"
        "- Be honest about uncertainty — flag weak signals explicitly\n"
        "- Do not fabricate sources, links, or artifacts that you cannot verify\n\n"
        "Respond with a JSON object containing these fields:\n"
        "{\n"
        '  "summary": "2-3 sentence overview of your assessment",\n'
        '  "critique": "Critical evaluation of the answer quality",\n'
        '  "continuity_notes": "How this connects to prior context and knowledge",\n'
        '  "risk_notes": "Potential risks or failure modes identified",\n'
        '  "suggested_actions": [{"action": "description", "target": "what to act on", '
        '"advisory_only": true, "requires_DEFINER_approval": true}],\n'
        '  "suggested_wiki_links": ["wiki page titles that would be relevant"],\n'
        '  "suggested_artifacts": ["artifact IDs or types to review"],\n'
        '  "retrieval_notes": "Assessment of retrieval quality and coverage",\n'
        '  "source_notes": "Assessment of source quality and gaps"\n'
        "}\n\n"
        "If you cannot confidently assess a field, leave it as an empty string or empty list. "
        "Do not fabricate content."
    )

    system_prompt = _prepend_soul(system_prompt, soul_text)

    # Build user prompt with turn context
    sources_text = ""
    if request.sources:
        sources_text = "\n\nSources:\n"
        for i, src in enumerate(request.sources[:10], 1):
            sources_text += f"  {i}. {src.get('title', src.get('id', 'unknown'))}: "
            sources_text += f"{src.get('snippet', src.get('content', ''))[:200]}\n"

    retrieval_meta = ""
    if request.trace_available:
        retrieval_meta += "- Trace data is available for this turn\n"
    if request.lexical_only:
        retrieval_meta += "- Retrieval was lexical-only (no vector search)\n"
    if request.vector_contributed:
        retrieval_meta += "- Vector search contributed to retrieval\n"

    user_prompt = f"""Analyze this turn and provide Beast commentary.

Question:
{request.question_text[:2000]}

Answer:
{request.answer_text[:3000]}
{sources_text}
{retrieval_meta}

Provide your commentary as structured JSON."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # --- Call Beast provider ---
    try:
        result = await container.model_provider.call("beast", messages)
        raw_content = result.get("content", "").strip()

        # Check for provider errors
        if result.get("error"):
            error_msg = result.get("error_message", "Beast provider returned an error")
            logger.error(
                "beast_commentary_provider_error",
                turn_id=turn_id,
                error=error_msg,
            )
            return _not_available_response(
                turn_id,
                session_id=request.session_id,
                mode=mode,
                status="error",
                error=f"Beast provider error: {error_msg}",
            )

    except Exception as exc:
        logger.error(
            "beast_commentary_call_failed",
            turn_id=turn_id,
            error=str(exc),
            exc_info=True,
        )
        return _not_available_response(
            turn_id,
            session_id=request.session_id,
            mode=mode,
            status="error",
            error=f"Failed to call Beast provider: {exc}",
        )

    # --- Parse LLM response ---
    commentary_data: dict[str, Any] = {}
    if raw_content:
        # Try to extract JSON from the response
        # The LLM might wrap it in markdown code blocks
        json_str = raw_content
        if "```json" in json_str:
            json_str = json_str.split("```json", 1)[-1].split("```", 1)[0]
        elif "```" in json_str:
            json_str = json_str.split("```", 1)[-1].split("```", 1)[0]

        try:
            commentary_data = json.loads(json_str.strip())
            if not isinstance(commentary_data, dict):
                commentary_data = {"summary": raw_content[:500]}
        except (json.JSONDecodeError, TypeError):
            # Fallback: treat raw content as summary
            logger.warning(
                "beast_commentary_json_parse_failed",
                turn_id=turn_id,
                content_preview=raw_content[:200],
            )
            commentary_data = {"summary": raw_content[:500]}

    # Ensure all suggested actions have advisory_only and requires_DEFINER_approval
    suggested_actions = commentary_data.get("suggested_actions", [])
    for action in suggested_actions:
        if isinstance(action, dict):
            action["advisory_only"] = True
            action["requires_DEFINER_approval"] = True

    # Build the full commentary data for storage
    now = datetime.now(timezone.utc).isoformat()
    artifact_id = _commentary_artifact_id(turn_id, mode)

    full_commentary = {
        "turn_id": turn_id,
        "session_id": request.session_id,
        "mode": mode,
        "summary": commentary_data.get("summary", ""),
        "critique": commentary_data.get("critique", ""),
        "continuity_notes": commentary_data.get("continuity_notes", ""),
        "risk_notes": commentary_data.get("risk_notes", ""),
        "suggested_actions": suggested_actions,
        "suggested_wiki_links": commentary_data.get("suggested_wiki_links", []),
        "suggested_artifacts": commentary_data.get("suggested_artifacts", []),
        "model_comparison": commentary_data.get("model_comparison", ""),
        "retrieval_notes": commentary_data.get("retrieval_notes", ""),
        "source_notes": commentary_data.get("source_notes", ""),
        "generated_at": now,
    }

    # --- Persist as GENERATED artifact (NOT APPROVED — never auto-approve) ---
    try:
        artifact_metadata = {
            "artifact_type": "beast_commentary",
            "turn_id": turn_id,
            "session_id": request.session_id,
            "mode": mode,
        }

        await container.artifact_store.write(
            id=artifact_id,
            content=json.dumps(full_commentary, ensure_ascii=False),
            metadata=artifact_metadata,
        )
        logger.info(
            "beast_commentary_artifact_created",
            artifact_id=artifact_id,
            turn_id=turn_id,
            mode=mode,
        )
    except Exception as exc:
        logger.error(
            "beast_commentary_artifact_write_failed",
            artifact_id=artifact_id,
            turn_id=turn_id,
            error=str(exc),
            exc_info=True,
        )
        # Return the commentary anyway, but note persistence failure
        return BeastCommentaryResponse(
            id=artifact_id,
            turn_id=turn_id,
            session_id=request.session_id,
            mode=mode,
            summary=full_commentary.get("summary", ""),
            critique=full_commentary.get("critique", ""),
            continuity_notes=full_commentary.get("continuity_notes", ""),
            risk_notes=full_commentary.get("risk_notes", ""),
            suggested_actions=suggested_actions,
            suggested_wiki_links=full_commentary.get("suggested_wiki_links", []),
            suggested_artifacts=full_commentary.get("suggested_artifacts", []),
            model_comparison=full_commentary.get("model_comparison", ""),
            retrieval_notes=full_commentary.get("retrieval_notes", ""),
            source_notes=full_commentary.get("source_notes", ""),
            created_at=now,
            status="error",
            persistence="not_available",
            error=f"Commentary generated but not persisted: {exc}",
        )

    # --- ECS transition to GENERATED (NOT APPROVED — never auto-approve) ---
    if container.ecs_store is not None:
        try:
            await container.ecs_store.transition(
                artifact_id=artifact_id,
                from_state=None,
                to_state="GENERATED",
                actor="beast:commentary",
                reason="Beast commentary generated — requires DEFINER review before approval",
            )
            logger.info(
                "beast_commentary_ecs_transition",
                artifact_id=artifact_id,
                state="GENERATED",
            )
        except Exception as exc:
            logger.warning(
                "beast_commentary_ecs_transition_failed",
                artifact_id=artifact_id,
                error=str(exc),
            )
            # Artifact was created but ECS transition failed — still return success
            # since the artifact exists; just note the ECS issue.

    return BeastCommentaryResponse(
        id=artifact_id,
        turn_id=turn_id,
        session_id=request.session_id,
        mode=mode,
        summary=full_commentary.get("summary", ""),
        critique=full_commentary.get("critique", ""),
        continuity_notes=full_commentary.get("continuity_notes", ""),
        risk_notes=full_commentary.get("risk_notes", ""),
        suggested_actions=suggested_actions,
        suggested_wiki_links=full_commentary.get("suggested_wiki_links", []),
        suggested_artifacts=full_commentary.get("suggested_artifacts", []),
        model_comparison=full_commentary.get("model_comparison", ""),
        retrieval_notes=full_commentary.get("retrieval_notes", ""),
        source_notes=full_commentary.get("source_notes", ""),
        created_at=now,
        status="available",
        persistence="available",
    )
