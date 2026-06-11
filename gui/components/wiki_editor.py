"""Wiki Editor component — create and edit wiki articles.

Provides a modal dialog for creating new wiki articles and editing
existing ones. All create/edit actions are explicit DEFINER actions.

Key sovereignty guarantees:
  - Create always sets state to GENERATED (never auto-approved)
  - Edit creates a new version but does NOT change ECS state
  - No silent mutation
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_ERR_FG,
    C_GROUND,
    C_INK40,
    C_INK60,
    C_MUTED,
    C_OK_FG,
    C_RAISED,
    C_SURFACE,
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_MD,
    R_SM,
)

log = logging.getLogger("gui.components.wiki_editor")


class WikiEditorDialog:
    """Modal dialog for creating or editing a wiki article.

    Usage:
        dialog = WikiEditorDialog(on_submit=my_save_function)
        # Call dialog.open_create() or dialog.open_edit(article) to show
    """

    def __init__(
        self,
        *,
        on_submit: Callable[[dict[str, Any]], None],
    ) -> None:
        self._on_submit = on_submit
        self._article: dict[str, Any] | None = None
        self._is_edit: bool = False

        # Build dialog
        self._dialog = ui.dialog().props("persistent")
        with self._dialog, ui.card().style(
            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_MD}; min-width:560px; max-width:700px;"
        ):
            # Header
            with ui.row().classes("w-full items-center").style(
                f"padding:12px 16px; border-bottom:0.5px solid {C_INK40};"
            ):
                self._header_label = ui.label("Create Article").style(
                    f"font-size:14px; font-weight:600; color:{C_AMBER}; "
                    f"font-family:{F_SANS}; letter-spacing:0.5px;"
                )
                ui.space()
                ui.button(icon="close", on_click=self._close).props(
                    "flat dense unelevated round"
                ).style(f"color:{C_MUTED};")

            # Form
            with ui.column().style("padding:16px; gap:12px;"):
                # Title
                ui.label("Title").style(
                    f"font-size:9px; font-weight:600; letter-spacing:1px; "
                    f"color:{C_INK60}; text-transform:uppercase;"
                )
                self._title_input = ui.input(
                    placeholder="Article title"
                ).classes("w-full").style(
                    f"background:{C_GROUND}; border:0.5px solid {C_INK40}; "
                    f"border-radius:{R_SM}; padding:8px 12px; color:{C_CREAM}; "
                    f"font-family:{F_SANS}; font-size:13px;"
                )

                # Domain
                ui.label("Domain").style(
                    f"font-size:9px; font-weight:600; letter-spacing:1px; "
                    f"color:{C_INK60}; text-transform:uppercase;"
                )
                self._domain_input = ui.input(
                    placeholder="Domain classification (e.g. aip_loom)"
                ).classes("w-full").style(
                    f"background:{C_GROUND}; border:0.5px solid {C_INK40}; "
                    f"border-radius:{R_SM}; padding:8px 12px; color:{C_CREAM}; "
                    f"font-family:{F_MONO}; font-size:12px;"
                )

                # Summary
                ui.label("Summary").style(
                    f"font-size:9px; font-weight:600; letter-spacing:1px; "
                    f"color:{C_INK60}; text-transform:uppercase;"
                )
                self._summary_input = ui.textarea(
                    placeholder="Brief summary of the article"
                ).classes("w-full").style(
                    f"background:{C_GROUND}; border:0.5px solid {C_INK40}; "
                    f"border-radius:{R_SM}; padding:8px 12px; color:{C_CREAM}; "
                    f"font-family:{F_SANS}; font-size:12px; min-height:60px;"
                )

                # Tags
                ui.label("Tags (comma-separated)").style(
                    f"font-size:9px; font-weight:600; letter-spacing:1px; "
                    f"color:{C_INK60}; text-transform:uppercase;"
                )
                self._tags_input = ui.input(
                    placeholder="tag1, tag2, tag3"
                ).classes("w-full").style(
                    f"background:{C_GROUND}; border:0.5px solid {C_INK40}; "
                    f"border-radius:{R_SM}; padding:8px 12px; color:{C_CREAM}; "
                    f"font-family:{F_MONO}; font-size:12px;"
                )

                # Body
                ui.label("Content").style(
                    f"font-size:9px; font-weight:600; letter-spacing:1px; "
                    f"color:{C_INK60}; text-transform:uppercase;"
                )
                self._body_input = ui.textarea(
                    placeholder="Article body content..."
                ).classes("w-full").style(
                    f"background:{C_GROUND}; border:0.5px solid {C_INK40}; "
                    f"border-radius:{R_SM}; padding:8px 12px; color:{C_CREAM}; "
                    f"font-family:{F_MONO}; font-size:12px; min-height:150px;"
                )

                # Sovereignty notice
                self._notice_label = ui.label(
                    "New articles are created as GENERATED — requiring DEFINER review before approval."
                ).style(
                    f"font-size:10px; color:{C_WARN_FG}; font-family:{F_MONO}; "
                    f"background:#1A1A0E; border:0.5px solid {C_WARN_FG}; "
                    f"border-radius:{R_SM}; padding:6px 10px;"
                )

                # Error label
                self._error_label = ui.label("").style(
                    f"font-size:11px; color:{C_ERR_FG}; font-family:{F_MONO}; display:none;"
                )

            # Action buttons
            with ui.row().classes("w-full justify-end").style(
                f"padding:12px 16px; border-top:0.5px solid {C_INK40}; gap:8px;"
            ):
                ui.button("Cancel", on_click=self._close).props(
                    "flat dense unelevated"
                ).style(
                    f"color:{C_MUTED}; border:0.5px solid {C_INK40}; "
                    f"border-radius:{R_SM}; font-size:11px; font-family:{F_MONO}; padding:6px 16px;"
                )
                self._submit_btn = ui.button("Create Article", on_click=self._submit).props(
                    "flat dense unelevated"
                ).style(
                    f"color:{C_GROUND}; background:{C_AMBER}; "
                    f"border-radius:{R_SM}; font-size:11px; font-family:{F_MONO}; "
                    f"font-weight:600; padding:6px 20px;"
                )

    def open_create(self) -> None:
        """Open the dialog in create mode."""
        self._is_edit = False
        self._article = None
        self._header_label.text = "Create Article"
        self._title_input.value = ""
        self._domain_input.value = ""
        self._summary_input.value = ""
        self._tags_input.value = ""
        self._body_input.value = ""
        self._notice_label.text = (
            "New articles are created as GENERATED — requiring DEFINER review before approval."
        )
        self._submit_btn.text = "Create Article"
        self._error_label.style("display:none;")
        self._dialog.open()

    def open_edit(self, article: dict[str, Any]) -> None:
        """Open the dialog in edit mode with article data."""
        self._is_edit = True
        self._article = article
        self._header_label.text = "Edit Article"
        self._title_input.value = article.get("title", "")
        self._domain_input.value = article.get("domain", "")
        self._summary_input.value = article.get("summary", "")
        tags = article.get("tags", [])
        self._tags_input.value = ", ".join(tags)
        self._body_input.value = article.get("body", "")
        self._notice_label.text = (
            "Editing creates a new version. ECS state is NOT changed — "
            "separate review/approve action required."
        )
        self._submit_btn.text = "Save Changes"
        self._error_label.style("display:none;")
        self._dialog.open()

    def _close(self) -> None:
        """Close the dialog."""
        self._dialog.close()

    def _submit(self) -> None:
        """Validate and submit the form."""
        title = self._title_input.value.strip()
        if not title:
            self._error_label.text = "Title is required."
            self._error_label.style(f"font-size:11px; color:{C_ERR_FG}; font-family:{F_MONO};")
            return

        domain = self._domain_input.value.strip()
        summary = self._summary_input.value.strip()
        body = self._body_input.value.strip()
        tags_str = self._tags_input.value.strip()
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

        payload: dict[str, Any] = {
            "title": title,
            "domain": domain,
            "summary": summary,
            "body": body,
            "tags": tags,
        }

        if self._is_edit and self._article:
            payload["article_id"] = self._article.get("id", "")
            payload["mode"] = "edit"
        else:
            payload["mode"] = "create"

        self._close()
        self._on_submit(payload)
