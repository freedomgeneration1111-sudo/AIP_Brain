"""Crosslink System — Link Editor Dialog.

A simple dialog for creating a new knowledge link.
For v1, object picker is manual:
  - object_type dropdown
  - object_id input
  - relation_type dropdown
  - confidence input
  - notes

If richer object search is not available, we do not fake it.

Import boundary: this module imports ONLY from gui.* (theme).
Never imports from aip.orchestration.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_GROUND,
    C_INK40,
    C_INK60,
    C_RAISED,
    C_SURFACE,
    C_WARN_FG,
    F_MONO,
    R_MD,
    R_SM,
)

log = logging.getLogger("gui.components.link_editor")

# Object types matching the backend
OBJECT_TYPES = [
    "source_document",
    "chunk",
    "conversation_turn",
    "retrieval_trace",
    "beast_commentary",
    "wiki_article",
    "artifact",
    "review_event",
    "actor_event",
    "model_comparison_report",
]

# Relation types matching the backend
RELATION_TYPES = [
    "supports",
    "contradicts",
    "summarizes",
    "extends",
    "mentions",
    "depends_on",
    "implements",
    "supersedes",
    "related_to",
    "generated_from",
    "reviewed_by",
    "approved_by",
]


class LinkEditorDialog:
    """Dialog for creating a new knowledge link.

    For v1, the object picker is manual — the user selects the target
    object type from a dropdown and enters the object ID.
    No autocomplete or search is provided since no object search API
    exists yet.

    Usage:
        editor = LinkEditorDialog(
            source_type="wiki_article",
            source_id="wiki:test:article:20260101",
            api_client=api_client,
            on_submit=callback,
        )
        editor.open_create()
    """

    def __init__(
        self,
        source_type: str,
        source_id: str,
        api_client: Any,
        on_submit: Callable[[], None] | None = None,
    ) -> None:
        self._source_type = source_type
        self._source_id = source_id
        self._api_client = api_client
        self._on_submit = on_submit
        self._dialog: Any = None

    def open_create(self) -> None:
        """Open the link creation dialog."""
        self._dialog = (
            ui.dialog()
            .props("maximized=false")
            .classes("")
            .style(f"background:{C_GROUND}; border:1px solid {C_INK40}; border-radius:{R_MD}; min-width:420px;")
        )

        with self._dialog:
            with (
                ui.card()
                .classes("w-full")
                .style(f"background:{C_GROUND}; border:none; padding:0; max-width:500px; min-width:400px;")
            ):
                # Header
                with (
                    ui.row()
                    .classes("w-full items-center")
                    .style(f"padding:12px 16px; border-bottom:0.5px solid {C_INK40};")
                ):
                    ui.label("CREATE LINK").style(
                        f"font-size:13px; font-weight:700; font-family:{F_MONO}; color:{C_AMBER}; letter-spacing:1px;"
                    )
                    ui.space()
                    ui.button(icon="close", on_click=self._close).props("dense flat size=xs").style(f"color:{C_INK60};")

                # Advisory label
                with (
                    ui.row()
                    .classes("w-full items-center")
                    .style(f"padding:4px 16px; border-bottom:0.5px solid {C_INK40};")
                ):
                    ui.label("Links default to SUGGESTED — requires DEFINER approval").style(
                        f"font-size:9px; font-weight:600; font-family:{F_MONO}; "
                        f"color:{C_WARN_FG}; letter-spacing:0.3px;"
                    )

                # Form
                with ui.column().classes("w-full").style("padding:16px; gap:12px;"):
                    # Source (pre-filled, read-only)
                    ui.label("SOURCE").style(
                        f"font-size:8px; font-weight:700; font-family:{F_MONO}; color:{C_INK60}; letter-spacing:0.5px;"
                    )
                    with ui.row().classes("w-full").style("gap:8px;"):
                        ui.label(self._source_type).style(
                            f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO}; "
                            f"background:{C_RAISED}; border:0.5px solid {C_INK40}; "
                            f"border-radius:{R_SM}; padding:4px 8px;"
                        )
                        display_id = self._source_id[:32] + "..." if len(self._source_id) > 32 else self._source_id
                        ui.label(display_id).style(
                            f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; "
                            f"background:{C_RAISED}; border:0.5px solid {C_INK40}; "
                            f"border-radius:{R_SM}; padding:4px 8px; "
                            f"max-width:250px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                        )

                    # Target object type
                    ui.label("TARGET TYPE").style(
                        f"font-size:8px; font-weight:700; font-family:{F_MONO}; "
                        f"color:{C_INK60}; letter-spacing:0.5px; margin-top:4px;"
                    )
                    target_type_select = (
                        ui.select(
                            options=OBJECT_TYPES,
                            value="wiki_article",
                        )
                        .props("dense outlined dark")
                        .classes("w-full")
                        .style(f"font-size:10px; font-family:{F_MONO};")
                    )

                    # Target object ID
                    ui.label("TARGET ID").style(
                        f"font-size:8px; font-weight:700; font-family:{F_MONO}; "
                        f"color:{C_INK60}; letter-spacing:0.5px; margin-top:4px;"
                    )
                    target_id_input = (
                        ui.input(placeholder="Enter object ID...")
                        .classes("w-full")
                        .style(
                            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
                            f"border-radius:{R_SM}; padding:6px 8px; color:{C_CREAM}; "
                            f"font-size:10px; font-family:{F_MONO};"
                        )
                    )

                    # Relation type
                    ui.label("RELATION TYPE").style(
                        f"font-size:8px; font-weight:700; font-family:{F_MONO}; "
                        f"color:{C_INK60}; letter-spacing:0.5px; margin-top:4px;"
                    )
                    relation_type_select = (
                        ui.select(
                            options=RELATION_TYPES,
                            value="related_to",
                        )
                        .props("dense outlined dark")
                        .classes("w-full")
                        .style(f"font-size:10px; font-family:{F_MONO};")
                    )

                    # Confidence
                    ui.label("CONFIDENCE (0.0 - 1.0)").style(
                        f"font-size:8px; font-weight:700; font-family:{F_MONO}; "
                        f"color:{C_INK60}; letter-spacing:0.5px; margin-top:4px;"
                    )
                    confidence_input = (
                        ui.input(value="1.0")
                        .classes("w-full")
                        .style(
                            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
                            f"border-radius:{R_SM}; padding:6px 8px; color:{C_CREAM}; "
                            f"font-size:10px; font-family:{F_MONO};"
                        )
                    )

                    # Notes
                    ui.label("NOTES (optional)").style(
                        f"font-size:8px; font-weight:700; font-family:{F_MONO}; "
                        f"color:{C_INK60}; letter-spacing:0.5px; margin-top:4px;"
                    )
                    notes_input = (
                        ui.textarea(placeholder="Optional notes...")
                        .classes("w-full")
                        .style(
                            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
                            f"border-radius:{R_SM}; padding:6px 8px; color:{C_CREAM}; "
                            f"font-size:10px; font-family:{F_MONO}; min-height:60px;"
                        )
                    )

                    # Provenance
                    ui.label("PROVENANCE").style(
                        f"font-size:8px; font-weight:700; font-family:{F_MONO}; "
                        f"color:{C_INK60}; letter-spacing:0.5px; margin-top:4px;"
                    )
                    provenance_input = (
                        ui.input(value="manual")
                        .classes("w-full")
                        .style(
                            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
                            f"border-radius:{R_SM}; padding:6px 8px; color:{C_CREAM}; "
                            f"font-size:10px; font-family:{F_MONO};"
                        )
                    )

                    # Submit button
                    ui.button(
                        "Create Link (Suggested)",
                        on_click=lambda: asyncio.ensure_future(
                            _submit(
                                api_client=self._api_client,
                                source_type=self._source_type,
                                source_id=self._source_id,
                                target_type=target_type_select.value,
                                target_id=target_id_input.value.strip(),
                                relation_type=relation_type_select.value,
                                confidence=float(confidence_input.value or "1.0"),
                                notes=notes_input.value.strip(),
                                provenance=provenance_input.value.strip() or "manual",
                                dialog=self._dialog,
                                on_submit=self._on_submit,
                            )
                        ),
                    ).props("dense unelevated").style(
                        f"margin-top:8px; color:{C_GROUND}; background:{C_AMBER}; "
                        f"border-radius:{R_SM}; font-size:10px; font-family:{F_MONO}; "
                        f"font-weight:600; padding:6px 16px;"
                    )

        self._dialog.open()

    def _close(self) -> None:
        """Close the dialog."""
        if self._dialog is not None:
            try:
                self._dialog.close()
            except Exception:
                pass
            self._dialog = None


async def _submit(
    *,
    api_client: Any,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    relation_type: str,
    confidence: float,
    notes: str,
    provenance: str,
    dialog: Any,
    on_submit: Callable[[], None] | None,
) -> None:
    """Submit the create link form."""
    if not target_id:
        ui.notify("Target ID is required", color="negative")
        return

    if not source_id:
        ui.notify("Source ID is missing", color="negative")
        return

    try:
        confidence_val = max(0.0, min(1.0, confidence))
    except (ValueError, TypeError):
        confidence_val = 1.0

    result = await api_client.create_knowledge_link(
        source_type=source_type,
        source_id=source_id,
        target_type=target_type,
        target_id=target_id,
        relation_type=relation_type,
        confidence=confidence_val,
        provenance=provenance or "manual",
        notes=notes,
    )

    if result.get("id"):
        ui.notify(
            f"Link created: {result['id'][:24]}... — status: SUGGESTED, requires DEFINER approval",
            color="positive",
            timeout=6000,
        )
        # Close dialog
        try:
            dialog.close()
        except Exception:
            pass
        # Trigger callback
        if on_submit:
            on_submit()
    else:
        error = result.get("error", result.get("detail", "unknown error"))
        ui.notify(f"Create link failed: {error}", color="negative")
