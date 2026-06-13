"""Document Table — corpus document list for the Corpus Workbench.

Shows documents with name/path, type, status, chunks, embedded count,
last updated, and problem indicators. Selecting a document opens detail.

Import boundary: imports ONLY from gui.* (theme, api_client).
Never imports from aip.orchestration.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_ERR_FG,
    C_INK60,
    C_MUTED,
    C_OK_FG,
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_SM,
)

log = logging.getLogger("gui.components.document_table")


class DocumentTable:
    """Document table component for the Corpus Workbench.

    Displays a list of documents (distinct source_paths) with chunk counts,
    embedding status, and problem indicators. Selecting a row triggers
    the on_select callback for document detail view.

    Handles empty corpus, backend unavailable, and error states honestly.
    """

    def __init__(
        self,
        on_select: Callable[[str], None] | None = None,
    ) -> None:
        self._on_select = on_select
        self._container: ui.column | None = None
        self._documents: list[dict[str, Any]] = []

    def render(
        self,
        data: dict[str, Any],
        search_query: str = "",
    ) -> None:
        """Render the document table with given data."""
        if self._container is not None:
            self._container.clear()

        with ui.column().classes("w-full").style("gap:8px;") as col:
            self._container = col

            items = data.get("items", [])
            total = data.get("total", 0)
            error = data.get("error", "")

            # Header row
            with (
                ui.row()
                .classes("w-full")
                .style(
                    f"font-size:11px; color:{C_MUTED}; text-transform:uppercase; "
                    f"letter-spacing:0.5px; padding:4px 8px; font-family:{F_SANS};"
                )
            ):
                ui.label("Source Path").style("flex:3; min-width:200px;")
                ui.label("Model").style("flex:1; min-width:80px;")
                ui.label("Chunks").style("flex:0.5; min-width:50px; text-align:right;")
                ui.label("Embedded").style("flex:0.5; min-width:60px; text-align:right;")
                ui.label("Problems").style("flex:0.5; min-width:50px; text-align:right;")
                ui.label("Updated").style("flex:1; min-width:100px;")

            if error:
                ui.label(f"Error loading documents: {error}").style(f"color:{C_ERR_FG}; font-size:12px; padding:12px;")
                return

            if not items:
                ui.label("No documents found in corpus.").style(
                    f"color:{C_MUTED}; font-size:13px; padding:16px; text-align:center; font-family:{F_SANS};"
                )
                if total == 0:
                    ui.label("Ingest documents using CLI or the Ingest action above.").style(
                        f"color:{C_INK60}; font-size:11px; padding:0 16px; font-family:{F_SANS};"
                    )
                return

            # Document rows
            for doc in items:
                source_path = doc.get("source_path", "unknown")
                turn_count = doc.get("turn_count", 0)
                embedded_count = doc.get("embedded_count", 0)
                unembedded_count = doc.get("unembedded_count", 0)
                embed_fail_count = doc.get("embed_fail_count", 0)
                needs_reembed = doc.get("needs_reembed_count", 0)
                source_model = doc.get("source_model", "")
                last_updated = doc.get("last_updated", "")

                # Compute status color
                if embed_fail_count > 0:
                    status_color = C_ERR_FG
                elif needs_reembed > 0:
                    status_color = C_AMBER
                elif unembedded_count > 0:
                    status_color = C_WARN_FG
                else:
                    status_color = C_OK_FG

                problem_count = embed_fail_count + needs_reembed

                with (
                    ui.row()
                    .classes("w-full")
                    .style(
                        f"padding:6px 8px; border-radius:{R_SM}; cursor:pointer; "
                        f"border:0.5px solid transparent; "
                        f"font-family:{F_SANS}; font-size:12px; "
                        f"transition:background 0.15s;"
                    )
                    .on("click", lambda sp=source_path: self._handle_select(sp))
                ):
                    # Hover effect via classes
                    ui.label(source_path).style(
                        f"flex:3; min-width:200px; color:{C_CREAM}; "
                        f"overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
                    )
                    ui.label(source_model or "-").style(
                        f"flex:1; min-width:80px; color:{C_INK60}; font-family:{F_MONO}; font-size:10px;"
                    )
                    ui.label(str(turn_count)).style(
                        f"flex:0.5; min-width:50px; text-align:right; color:{C_CREAM}; font-family:{F_MONO};"
                    )
                    # Embedded with color coding
                    (embedded_count / turn_count * 100) if turn_count > 0 else 0
                    ui.label(f"{embedded_count}/{turn_count}").style(
                        f"flex:0.5; min-width:60px; text-align:right; color:{status_color}; font-family:{F_MONO};"
                    )
                    ui.label(str(problem_count) if problem_count > 0 else "-").style(
                        f"flex:0.5; min-width:50px; text-align:right; "
                        f"color:{C_ERR_FG if problem_count > 0 else C_INK60}; "
                        f"font-family:{F_MONO};"
                    )
                    ui.label(last_updated[:16] if last_updated else "-").style(
                        f"flex:1; min-width:100px; color:{C_INK60}; font-family:{F_MONO}; font-size:10px;"
                    )

            # Footer with total count
            ui.label(f"Total: {total} documents").style(
                f"font-size:11px; color:{C_MUTED}; padding:8px; font-family:{F_SANS};"
            )

    def _handle_select(self, source_path: str) -> None:
        """Handle document row selection."""
        if self._on_select:
            self._on_select(source_path)
