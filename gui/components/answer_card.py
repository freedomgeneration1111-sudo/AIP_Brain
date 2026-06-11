"""AIP Answer Card — enhanced assistant message with status strip and actions.

Every assistant answer in the Ask Workbench is rendered as an Answer Card
that includes:
  1. The answer text (markdown)
  2. A status strip showing retrieval health (healthy, degraded, lexical only,
     no sources, direct model only, trace unavailable)
  3. An action bar with per-answer actions (Show Sources, Show Trace,
     Save as Artifact, Link Wiki, Run Model Council)

Import boundary: this module imports ONLY from gui.* (theme, state, components).
Never imports from aip.orchestration.
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_DOGFOOD_BARE,
    C_INK40,
    C_INK60,
    C_MUTED,
    C_OK_FG,
    C_RAISED,
    C_SURFACE,
    C_WARN_FG,
    F_MONO,
    R_MD,
    R_SM,
)

log = logging.getLogger("gui.components.answer_card")


def determine_answer_status(
    sources: list[dict[str, Any]] | None,
    trace_available: bool = False,
    lexical_only: bool = False,
    vector_contributed: bool = False,
    direct_model: bool = False,
    mode: str = "normal",
) -> dict[str, Any]:
    """Determine the answer status label and color from available metadata.

    Returns a dict with:
      - label: Human-readable status label
      - color: Theme color for the status
      - level: "ok" | "degraded" | "warning" | "error"
      - detail: Longer explanation text
    """
    if direct_model:
        return {
            "label": "DIRECT MODEL ONLY",
            "color": C_DOGFOOD_BARE,
            "level": "error",
            "detail": "No retrieval. No corpus. No actors. No artifact lifecycle.",
        }

    if mode == "normal":
        return {
            "label": "NORMAL MODE",
            "color": C_MUTED,
            "level": "ok",
            "detail": "Direct model response — no retrieval augmentation.",
        }

    # Augmented mode
    has_sources = bool(sources)

    if not has_sources:
        if not trace_available:
            return {
                "label": "NO SOURCES",
                "color": C_WARN_FG,
                "level": "warning",
                "detail": "Retrieval returned no sources. Answer based on model knowledge only.",
            }
        return {
            "label": "NO SOURCES",
            "color": C_WARN_FG,
            "level": "warning",
            "detail": "Retrieval returned no matching sources for this query.",
        }

    if lexical_only and not vector_contributed:
        return {
            "label": "LEXICAL ONLY",
            "color": C_AMBER,
            "level": "degraded",
            "detail": "Only lexical/FTS5 retrieval contributed. Vector search unavailable or returned no results.",
        }

    if not trace_available:
        return {
            "label": "RETRIEVAL HEALTHY",
            "color": C_OK_FG,
            "level": "ok",
            "detail": "Sources retrieved. Trace data not available for inspection.",
        }

    if vector_contributed:
        return {
            "label": "RETRIEVAL HEALTHY",
            "color": C_OK_FG,
            "level": "ok",
            "detail": "Hybrid retrieval: vector + lexical sources contributed.",
        }

    return {
        "label": "RETRIEVAL HEALTHY",
        "color": C_OK_FG,
        "level": "ok",
        "detail": "Sources retrieved via retrieval pipeline.",
    }


def add_answer_card(
    container,
    content: str,
    model: str | None = None,
    latency_ms: int | None = None,
    sources: list[dict[str, Any]] | None = None,
    trace_available: bool = False,
    lexical_only: bool = False,
    vector_contributed: bool = False,
    direct_model: bool = False,
    mode: str = "normal",
    on_show_sources: Any = None,
    on_show_trace: Any = None,
    on_save_artifact: Any = None,
    on_link_wiki: Any = None,
    on_run_model_council: Any = None,
    on_beast_counsel: Any = None,
    turn_data: dict[str, Any] | None = None,
) -> None:
    """Add an enhanced answer card to the chat container.

    The answer card includes:
    - Role/model header
    - Answer content (markdown)
    - Status strip with retrieval health indicator
    - Action bar with per-answer buttons

    Args:
        container: The NiceGUI container to add the card to
        content: The assistant's answer text
        model: Model name that generated the answer
        latency_ms: Response latency in milliseconds
        sources: List of source dicts from retrieval
        trace_available: Whether a retrieval trace was generated
        lexical_only: Whether only FTS5 contributed
        vector_contributed: Whether vector search contributed
        direct_model: Whether this was a direct model call (no backend)
        mode: Chat mode ("normal" or "augmented")
        on_show_sources: Callback for "Show Sources" action
        on_show_trace: Callback for "Show Trace" action
        on_save_artifact: Callback for "Save as Artifact" action
        on_link_wiki: Callback for "Link Wiki" action
        on_run_model_council: Callback for "Run Model Council" action
        turn_data: Dict of turn-level metadata for action handlers
    """
    sources = sources or []
    turn_data = turn_data or {}
    status = determine_answer_status(
        sources=sources,
        trace_available=trace_available,
        lexical_only=lexical_only,
        vector_contributed=vector_contributed,
        direct_model=direct_model,
        mode=mode,
    )

    with container:
        # ── Role/model header ──────────────────────────────────
        with ui.row().classes("w-full items-start").style("margin-bottom:4px;"):
            display = model or "Assistant"
            label_text = f"**{display}**"
            if latency_ms is not None:
                label_text += f"  ({latency_ms}ms)"
            ui.markdown(label_text).style(f"font-size:11px; color:{C_AMBER}; font-family:{F_MONO};")

        # ── Answer content ─────────────────────────────────────
        with ui.row().classes("w-full"):
            ui.markdown(content).style(
                f"font-size:13px; color:{C_CREAM}; background:{C_SURFACE}; "
                f"border:0.5px solid {C_INK40}; border-radius:{R_MD}; "
                f"padding:8px 12px; max-width:85%; line-height:1.5;"
            )

        # ── Status strip ───────────────────────────────────────
        with (
            ui.row()
            .classes("w-full items-center")
            .style(
                f"margin-top:4px; padding:3px 8px; "
                f"background:{C_RAISED}; border-radius:{R_SM}; "
                f"border:0.5px solid {C_INK40};"
            )
        ):
            ui.label(status["label"]).style(
                f"font-size:9px; font-weight:700; font-family:{F_MONO}; "
                f"color:{status['color']}; letter-spacing:0.5px; margin-right:8px;"
            )
            ui.label(status["detail"]).style(f"font-size:9px; color:{C_INK60}; font-family:{F_MONO};")

        # ── Action bar ─────────────────────────────────────────
        with ui.row().classes("w-full items-center").style("margin-top:2px; padding:2px 0; gap:4px;"):
            # Show Sources — available when sources exist
            if sources:
                ui.button(
                    "Sources",
                    on_click=lambda: _safe_callback(on_show_sources, turn_data),
                ).props("dense flat size=xs").style(f"color:{C_AMBER}; font-size:9px; font-family:{F_MONO};")
            else:
                ui.button("No Sources").props("dense flat size=xs disable").style(
                    f"color:{C_INK60}; font-size:9px; font-family:{F_MONO};"
                ).tooltip("not wired — no sources available")

            # Show Trace — available when trace was generated
            if trace_available:
                ui.button(
                    "Trace",
                    on_click=lambda: _safe_callback(on_show_trace, turn_data),
                ).props("dense flat size=xs").style(f"color:{C_AMBER}; font-size:9px; font-family:{F_MONO};")
            else:
                ui.button("Trace Unavailable").props("dense flat size=xs disable").style(
                    f"color:{C_INK60}; font-size:9px; font-family:{F_MONO};"
                ).tooltip("not wired — trace data not available")

            # Save as Artifact — always available (creates GENERATED artifact)
            if on_save_artifact:
                ui.button(
                    "Save Artifact",
                    on_click=lambda: _safe_callback(on_save_artifact, turn_data),
                ).props("dense flat size=xs").style(f"color:{C_OK_FG}; font-size:9px; font-family:{F_MONO};")
            else:
                ui.button("Save Artifact").props("dense flat size=xs disable").style(
                    f"color:{C_INK60}; font-size:9px; font-family:{F_MONO};"
                ).tooltip("not wired — save artifact callback not provided")

            # Beast Counsel — available when turn_data has a turn_id
            if on_beast_counsel:
                ui.button(
                    "Beast Counsel",
                    on_click=lambda: _safe_callback(on_beast_counsel, turn_data),
                ).props("dense flat size=xs").style(f"color:{C_AMBER}; font-size:9px; font-family:{F_MONO};")
            else:
                ui.button("Beast Counsel").props("dense flat size=xs disable").style(
                    f"color:{C_INK60}; font-size:9px; font-family:{F_MONO};"
                ).tooltip("not wired — beast counsel callback not provided")

            # Link Wiki — UI Cycle 8: Crosslink System now provides backend endpoint
            if on_link_wiki:
                ui.button(
                    "Link Wiki",
                    on_click=lambda: _safe_callback(on_link_wiki, turn_data),
                ).props("dense flat size=xs").style(f"color:{C_AMBER}; font-size:9px; font-family:{F_MONO};")
            else:
                ui.button("Link Wiki").props("dense flat size=xs disable").style(
                    f"color:{C_INK60}; font-size:9px; font-family:{F_MONO};"
                ).tooltip("not wired — link wiki callback not provided")

            # Run Model Council — available via Model Council panel
            if on_run_model_council:
                ui.button(
                    "Model Council",
                    on_click=lambda: _safe_callback(on_run_model_council, turn_data),
                ).props("dense flat size=xs").style(f"color:{C_AMBER}; font-size:9px; font-family:{F_MONO};")
            else:
                ui.button("Model Council").props("dense flat size=xs disable").style(
                    f"color:{C_INK60}; font-size:9px; font-family:{F_MONO};"
                ).tooltip("not wired — model council callback not provided")


def _safe_callback(callback: Any, turn_data: dict[str, Any]) -> None:
    """Safely invoke a callback, logging errors instead of crashing."""
    if callback is None:
        return
    try:
        callback(turn_data)
    except Exception as exc:
        log.error("action_callback_failed: %s", exc)
        ui.notify(f"Action failed: {exc}", color="negative")
