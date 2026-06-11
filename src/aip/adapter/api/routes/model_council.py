"""Model Council — multi-model comparison report endpoint.

Provides:
  POST /api/v1/beast/compare-models

The Model Council lets the DEFINER compare multiple model outputs for a
prompt/turn/context, then receive a Beast-style synthesis of convergence,
disagreements, risks, and recommended decision.

Reports are ADVISORY ONLY. No auto-approve, no auto-export, no wiki
mutation, no config changes, no model slot changes.

If fewer than two text-generation model slots are configured, returns an
honest ``insufficient_models`` state. If one model fails, returns a
partial/degraded report rather than total failure. If Beast synthesis
is unavailable, returns per-model results with conclusion status
``unavailable`` rather than a fake conclusion.

Layer discipline: This module imports ONLY from adapter and foundation.
Store access is through the container, not via direct orchestration imports.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from aip.adapter.api.dependencies import AipContainer, get_container

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Slots that should NOT be used for text generation comparison
# ---------------------------------------------------------------------------

_EXCLUDED_SLOTS = {"embedding"}

# Default text-generation slots to use for comparison if caller doesn't specify
_DEFAULT_COMPARISON_SLOTS = ["synthesis", "evaluation", "beast"]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class PerModelResult(BaseModel):
    """Per-model result within a Model Council comparison."""

    model_slot: str = ""
    model_id: str = ""
    provider: str = ""
    status: str = "pending"  # pending, completed, failed, excluded
    answer: str = ""
    error: str = ""
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None


class ModelCouncilRequest(BaseModel):
    """Request body for Model Council comparison."""

    prompt: str
    turn_id: str = ""
    session_id: str = ""
    existing_answer: str = ""
    sources: list[dict] = []
    selected_model_slots: list[str] = Field(default_factory=list)
    save_as_artifact: bool = False


class ModelCouncilResponse(BaseModel):
    """Response model for Model Council comparison report."""

    id: str = ""
    status: str = "pending"  # pending, completed, partial, insufficient_models, unavailable, error
    prompt: str = ""
    turn_id: str = ""
    session_id: str = ""
    selected_models: list[PerModelResult] = []
    convergence: str = ""
    disagreements: str = ""
    unique_contributions: str = ""
    risks: str = ""
    beast_conclusion: str = ""
    recommended_decision: str = ""
    degraded_models: list[str] = []
    failed_models: list[str] = []
    artifact_id: str = ""
    created_at: str = ""
    advisory_only: bool = True
    requires_DEFINER_approval: bool = True
    error: str = ""
    # Synthesis status — separate from overall status
    synthesis_status: str = "pending"  # pending, completed, unavailable, failed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _council_artifact_id(turn_id: str, session_id: str) -> str:
    """Deterministic artifact ID for Model Council report.

    Pattern: ``council:report:{sha256(turn_id:session_id)[:16]}``
    """
    key = f"{turn_id}:{session_id}" if session_id else turn_id or "no-turn"
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"council:report:{digest}"


def _load_soul_text() -> str:
    """Load Beast soul from data/beast_soul.md.

    Returns empty string if the file is missing or unreadable.
    """
    soul_path = Path("data/beast_soul.md")
    try:
        if soul_path.exists():
            text = soul_path.read_text(encoding="utf-8").strip()
            if text:
                return text
    except Exception as exc:
        logger.warning("council_soul_load_failed path=%s error=%s", str(soul_path), str(exc))
    return ""


def _prepend_soul(system_prompt: str, soul_text: str) -> str:
    """Prepend soul text to a system prompt."""
    if soul_text:
        return f"{soul_text}\n\n---\n\n{system_prompt}"
    return system_prompt


def _resolve_comparison_slots(
    model_provider: Any,
    requested_slots: list[str] | None = None,
) -> list[str]:
    """Determine which slots to use for comparison.

    Filters out embedding and non-dict slots. If caller specifies slots,
    uses those (after filtering). Otherwise uses default text-generation
    slots that are actually configured.
    """
    try:
        available = model_provider.list_slots()
    except Exception:
        available = []

    # Filter to only dict-typed slots (exclude ci_mode flags etc.)
    configured_slots = []
    for s in available:
        try:
            cfg = model_provider._resolve_slot_config(s)
            if isinstance(cfg, dict):
                configured_slots.append(s)
        except Exception:
            logger.debug("slot_config_resolve_failed slot=%s", s)
            pass

    # Remove excluded slots
    usable = [s for s in configured_slots if s not in _EXCLUDED_SLOTS]

    if requested_slots:
        # Use caller's selection, filtered to actually configured + usable
        return [s for s in requested_slots if s in usable]

    # Default: use configured text-generation slots from our default list
    defaults_in_config = [s for s in _DEFAULT_COMPARISON_SLOTS if s in usable]
    if defaults_in_config:
        return defaults_in_config

    # Fallback: use any usable slots
    return usable


# ---------------------------------------------------------------------------
# POST endpoint — run model comparison
# ---------------------------------------------------------------------------

@router.post(
    "/beast/compare-models",
    response_model=ModelCouncilResponse,
)
async def compare_models(
    request: ModelCouncilRequest,
    container: AipContainer = Depends(get_container),
):
    """Run a multi-model comparison and produce an advisory Model Council report.

    Calls multiple configured model slots with the same prompt, then uses
    the Beast/synthesis model to synthesize a structured advisory report
    covering convergence, disagreements, unique contributions, risks,
    and recommended decision.

    Returns ``insufficient_models`` if fewer than two text-generation
    model slots are configured. Returns ``partial`` if some models fail.
    Never auto-approves, auto-exports, mutates wiki, or changes config.
    """
    now = datetime.now(timezone.utc).isoformat()
    artifact_id = _council_artifact_id(request.turn_id, request.session_id)

    # --- No model provider — honest degradation ---
    if container.model_provider is None:
        return ModelCouncilResponse(
            id=artifact_id,
            status="insufficient_models",
            prompt=request.prompt[:500],
            turn_id=request.turn_id,
            session_id=request.session_id,
            error="No model provider configured — cannot run Model Council",
            created_at=now,
            synthesis_status="unavailable",
        )

    # --- Resolve which slots to compare ---
    comparison_slots = _resolve_comparison_slots(
        container.model_provider, request.selected_model_slots
    )

    if len(comparison_slots) < 2:
        return ModelCouncilResponse(
            id=artifact_id,
            status="insufficient_models",
            prompt=request.prompt[:500],
            turn_id=request.turn_id,
            session_id=request.session_id,
            selected_models=[
                PerModelResult(
                    model_slot=s,
                    model_id=_safe_model_id(container.model_provider, s),
                    provider=_safe_provider(container.model_provider, s),
                    status="excluded",
                )
                for s in comparison_slots
            ],
            error=(
                f"Insufficient text-generation model slots for comparison. "
                f"Found {len(comparison_slots)} usable slot(s): {comparison_slots}. "
                f"Need at least 2. Embedding slot is excluded from text generation."
            ),
            created_at=now,
            synthesis_status="unavailable",
        )

    # --- Build the user prompt ---
    sources_text = ""
    if request.sources:
        sources_text = "\n\nContext/Sources:\n"
        for i, src in enumerate(request.sources[:10], 1):
            sources_text += f"  {i}. {src.get('title', src.get('id', 'unknown'))}: "
            sources_text += f"{src.get('snippet', src.get('content', ''))[:200]}\n"

    existing_answer_block = ""
    if request.existing_answer:
        existing_answer_block = f"\n\nExisting Answer:\n{request.existing_answer[:3000]}\n"

    user_prompt = f"""{request.prompt[:4000]}{sources_text}{existing_answer_block}"""

    # --- Call each model slot concurrently ---
    per_model_tasks = {}
    for slot_name in comparison_slots:
        per_model_tasks[slot_name] = _call_model_slot(
            container.model_provider, slot_name, user_prompt
        )

    # Run all model calls concurrently
    results_map: dict[str, dict] = {}
    task_keys = list(per_model_tasks.keys())
    task_coros = [per_model_tasks[k] for k in task_keys]
    task_results = await asyncio.gather(*task_coros, return_exceptions=True)

    for slot_name, result in zip(task_keys, task_results):
        if isinstance(result, Exception):
            results_map[slot_name] = {
                "content": "",
                "model": "",
                "usage": {},
                "latency_ms": 0,
                "cost_usd": 0.0,
                "error": True,
                "error_message": str(result),
            }
        else:
            results_map[slot_name] = result

    # --- Build per-model results ---
    per_model_results: list[PerModelResult] = []
    degraded_models: list[str] = []
    failed_models: list[str] = []
    successful_count = 0

    for slot_name in comparison_slots:
        r = results_map.get(slot_name, {})
        model_id = r.get("model", _safe_model_id(container.model_provider, slot_name))
        provider = _safe_provider(container.model_provider, slot_name)
        usage = r.get("usage", {})
        is_error = r.get("error", False)

        if is_error:
            failed_models.append(slot_name)
            per_model_results.append(PerModelResult(
                model_slot=slot_name,
                model_id=model_id,
                provider=provider,
                status="failed",
                error=r.get("error_message", "Model call failed"),
                latency_ms=r.get("latency_ms"),
            ))
        else:
            successful_count += 1
            per_model_results.append(PerModelResult(
                model_slot=slot_name,
                model_id=model_id,
                provider=provider,
                status="completed",
                answer=r.get("content", ""),
                latency_ms=r.get("latency_ms"),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                cost_usd=r.get("cost_usd"),
            ))

    # --- Determine overall status ---
    if successful_count == 0:
        overall_status = "error"
    elif successful_count < len(comparison_slots):
        overall_status = "partial"
        degraded_models = [s for s in comparison_slots if s not in failed_models]
    else:
        overall_status = "completed"

    # --- Beast synthesis ---
    synthesis_status = "pending"
    convergence = ""
    disagreements = ""
    unique_contributions = ""
    risks = ""
    beast_conclusion = ""
    recommended_decision = ""

    if successful_count >= 2:
        # Build the synthesis prompt with per-model answers
        answers_block = ""
        for pm in per_model_results:
            if pm.status == "completed":
                answers_block += f"\n## {pm.model_slot} ({pm.model_id})\n{pm.answer[:2000]}\n"

        soul_text = _load_soul_text()

        synthesis_system_prompt = (
            "You are AIP Beast, the corpus intelligence actor, acting as Model Council "
            "synthesizer. You are given multiple model responses to the same prompt and "
            "must produce a structured advisory synthesis.\n\n"
            "Your synthesis is ADVISORY ONLY — it must never be treated as canonical "
            "without DEFINER review and approval.\n\n"
            "IMPORTANT CONSTRAINTS:\n"
            "- All recommendations are ADVISORY ONLY and require DEFINER approval\n"
            "- Never auto-approve, auto-export, mutate wiki, change config, or change model slots\n"
            "- Be honest about uncertainty — flag weak signals explicitly\n"
            "- Do not fabricate convergence, disagreements, or risks that aren't evident\n\n"
            "Respond with a JSON object containing these fields:\n"
            "{\n"
            '  "convergence": "Where the models agree and why",\n'
            '  "disagreements": "Where the models disagree and the substance of disagreement",\n'
            '  "unique_contributions": "What each model contributed that others did not",\n'
            '  "risks": "Risks identified from the comparison",\n'
            '  "beast_conclusion": "Your overall assessment and reasoning",\n'
            '  "recommended_decision": "Your advisory recommendation for the DEFINER"\n'
            "}\n\n"
            "If you cannot confidently assess a field, leave it as an empty string. "
            "Do not fabricate content."
        )

        synthesis_system_prompt = _prepend_soul(synthesis_system_prompt, soul_text)

        synthesis_user_prompt = f"""Synthesize these model responses into a structured advisory report.

Original Prompt:
{request.prompt[:2000]}
{answers_block}

Provide your synthesis as structured JSON."""

        synthesis_messages = [
            {"role": "system", "content": synthesis_system_prompt},
            {"role": "user", "content": synthesis_user_prompt},
        ]

        try:
            synth_result = await container.model_provider.call("beast", synthesis_messages)
            synth_content = synth_result.get("content", "").strip()

            if synth_result.get("error"):
                synthesis_status = "failed"
                logger.error(
                    "council_synthesis_provider_error error=%s",
                    synth_result.get("error_message", "unknown"),
                )
            elif synth_content:
                # Parse JSON from synthesis response
                json_str = synth_content
                if "```json" in json_str:
                    json_str = json_str.split("```json", 1)[-1].split("```", 1)[0]
                elif "```" in json_str:
                    json_str = json_str.split("```", 1)[-1].split("```", 1)[0]

                try:
                    synth_data = json.loads(json_str.strip())
                    if isinstance(synth_data, dict):
                        convergence = synth_data.get("convergence", "")
                        disagreements = synth_data.get("disagreements", "")
                        unique_contributions = synth_data.get("unique_contributions", "")
                        risks = synth_data.get("risks", "")
                        beast_conclusion = synth_data.get("beast_conclusion", "")
                        recommended_decision = synth_data.get("recommended_decision", "")
                        synthesis_status = "completed"
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "council_synthesis_json_parse_failed content_preview=%s",
                        synth_content[:200],
                    )
                    beast_conclusion = synth_content[:500]
                    synthesis_status = "completed"
        except Exception as exc:
            logger.error("council_synthesis_call_failed error=%s", str(exc), exc_info=True)
            synthesis_status = "failed"
    elif successful_count == 1:
        # Only one model succeeded — can't really compare
        synthesis_status = "unavailable"
        beast_conclusion = (
            "Only one model responded successfully. "
            "Comparison requires at least two successful model responses. "
            "Per-model results are available for individual review."
        )
    else:
        synthesis_status = "unavailable"

    # --- Build full response ---
    response = ModelCouncilResponse(
        id=artifact_id,
        status=overall_status,
        prompt=request.prompt[:500],
        turn_id=request.turn_id,
        session_id=request.session_id,
        selected_models=per_model_results,
        convergence=convergence,
        disagreements=disagreements,
        unique_contributions=unique_contributions,
        risks=risks,
        beast_conclusion=beast_conclusion,
        recommended_decision=recommended_decision,
        degraded_models=degraded_models,
        failed_models=failed_models,
        created_at=now,
        advisory_only=True,
        requires_DEFINER_approval=True,
        synthesis_status=synthesis_status,
    )

    # --- Save as artifact if requested ---
    if request.save_as_artifact and container.artifact_store is not None:
        try:
            report_data = json.dumps(response.model_dump(), ensure_ascii=False, default=str)
            artifact_metadata = {
                "artifact_type": "model_council_report",
                "turn_id": request.turn_id,
                "session_id": request.session_id,
                "comparison_slots": ",".join(comparison_slots),
                "status": overall_status,
            }
            await container.artifact_store.write(
                id=artifact_id,
                content=report_data,
                metadata=artifact_metadata,
            )
            response.artifact_id = artifact_id

            # ECS transition to GENERATED (NOT APPROVED — never auto-approve)
            if container.ecs_store is not None:
                try:
                    await container.ecs_store.transition(
                        artifact_id=artifact_id,
                        from_state=None,
                        to_state="GENERATED",
                        actor="model_council",
                        reason="Model Council report generated — requires DEFINER review",
                    )
                except Exception as exc:
                    logger.warning(
                        "council_ecs_transition_failed artifact_id=%s error=%s",
                        artifact_id, str(exc),
                    )

            logger.info(
                "council_artifact_saved artifact_id=%s status=%s",
                artifact_id, overall_status,
            )
        except Exception as exc:
            logger.error(
                "council_artifact_write_failed artifact_id=%s error=%s",
                artifact_id, str(exc), exc_info=True,
            )
            # Don't fail the whole response — just note the save failure
            response.error = f"Report generated but artifact save failed: {exc}"

    return response


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _call_model_slot(
    model_provider: Any,
    slot_name: str,
    user_prompt: str,
) -> dict:
    """Call a single model slot with the given prompt.

    Returns the raw result dict from model_provider.call().
    """
    messages = [
        {"role": "user", "content": user_prompt},
    ]
    return await model_provider.call(slot_name, messages)


def _safe_model_id(model_provider: Any, slot_name: str) -> str:
    """Safely resolve model ID for a slot without raising."""
    try:
        resolved = model_provider._resolve_slot_config(slot_name)
        return resolved.get("model", f"<{slot_name}>")
    except Exception:
        return f"<{slot_name}>"


def _safe_provider(model_provider: Any, slot_name: str) -> str:
    """Safely resolve provider name for a slot without raising."""
    try:
        resolved = model_provider._resolve_slot_config(slot_name)
        return resolved.get("provider", "unknown")
    except Exception:
        return "unknown"
