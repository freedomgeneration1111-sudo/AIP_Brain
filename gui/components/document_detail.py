"""Document Detail — document detail panel for the Corpus Workbench.

Shows metadata, chunks/embedding status, warnings/errors, and
links to related wiki/artifacts if available.

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

log = logging.getLogger("gui.components.document_detail")


class DocumentDetail:
    """Document detail panel for the Corpus Workbench.

    Shows full metadata for a selected document (source_path):
    - Source info (path, model, account)
    - Chunk summary (total, embedded, unembedded, coverage)
    - Errors/problems
    - Sample turns

    Handles not_found and unavailable states honestly.
    """

    def __init__(
        self,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._on_close = on_close
        self._container: ui.column | None = None

    def render(self, detail: dict[str, Any]) -> None:
        """Render the document detail panel."""
        if self._container is not None:
            self._container.clear()

        with (
            ui.column()
            .classes("w-full")
            .style(
                f"background:{C_RAISED}; border:0.5px solid {C_INK40}; border-radius:{R_MD}; padding:16px; gap:12px;"
            ) as col
        ):
            self._container = col

            # Header with close button
            with ui.row().classes("w-full").style("align-items:center;"):
                ui.label("Document Detail").style(
                    f"font-size:16px; font-weight:700; color:{C_CREAM}; font-family:{F_SANS}; flex:1;"
                )
                if self._on_close:
                    ui.button("Close", on_click=self._on_close).props("flat dense size=sm").style(f"color:{C_MUTED};")

            # Not found state
            if detail.get("not_found"):
                source_path = detail.get("source_path", "unknown")
                error = detail.get("error", "")
                ui.label(f"Document not found: {source_path}").style(
                    f"color:{C_ERR_FG}; font-size:13px; font-family:{F_SANS};"
                )
                if error:
                    ui.label(f"Error: {error}").style(f"color:{C_MUTED}; font-size:11px; font-family:{F_MONO};")
                return

            # Source info
            source_path = detail.get("source_path", "unknown")
            ui.label(source_path).style(
                f"font-size:13px; color:{C_CREAM}; font-family:{F_MONO}; word-break:break-all; font-weight:600;"
            )

            with ui.row().style("gap:12px; flex-wrap:wrap;"):
                _meta_item("Source Model", detail.get("source_model", "-"))
                _meta_item("Account", detail.get("source_account", "-"))
                _meta_item("First Turn", detail.get("first_turn_at", "-")[:16])
                _meta_item("Last Updated", detail.get("last_updated", "-")[:16])
                _meta_item("Conversations", str(detail.get("conversation_count", 0)))
                _meta_item("Total Words", str(detail.get("total_word_count", 0)))

            # Domains
            domains = detail.get("primary_domains", [])
            if domains:
                with ui.row().style("gap:4px; flex-wrap:wrap;"):
                    for d in domains:
                        ui.label(d).style(
                            f"font-size:10px; padding:2px 6px; "
                            f"background:{C_SURFACE}; color:{C_CREAM}; "
                            f"border-radius:{R_SM}; border:0.5px solid {C_INK40}; "
                            f"font-family:{F_MONO};"
                        )

            # Chunk / Embedding summary
            turn_count = detail.get("turn_count", 0)
            embedded_count = detail.get("embedded_count", 0)
            unembedded_count = detail.get("unembedded_count", 0)
            embed_fail_count = detail.get("embed_fail_count", 0)
            needs_reembed = detail.get("needs_reembed_count", 0)
            embed_coverage = detail.get("embed_coverage", 0.0)

            ui.html("<hr>").style(f"border-color:{C_INK40}; margin:4px 0;")
            ui.label("Chunks & Embeddings").style(
                f"font-size:13px; font-weight:600; color:{C_CREAM}; font-family:{F_SANS};"
            )

            with ui.row().style("gap:16px; flex-wrap:wrap;"):
                coverage_color = C_OK_FG if embed_coverage > 80 else (C_AMBER if embed_coverage > 20 else C_ERR_FG)
                ui.label(f"Coverage: {embed_coverage:.1f}%").style(
                    f"font-size:14px; font-weight:700; color:{coverage_color}; font-family:{F_MONO};"
                )
                _meta_item("Total", str(turn_count))
                _meta_item("Embedded", str(embedded_count))
                _meta_item("Unembedded", str(unembedded_count))
                if embed_fail_count > 0:
                    ui.label(f"Failed: {embed_fail_count}").style(
                        f"font-size:11px; color:{C_ERR_FG}; font-family:{F_MONO};"
                    )
                if needs_reembed > 0:
                    ui.label(f"Needs Re-embed: {needs_reembed}").style(
                        f"font-size:11px; color:{C_AMBER}; font-family:{F_MONO};"
                    )

            # Embedding models
            models = detail.get("embedding_models", [])
            if models:
                with ui.row().style("gap:4px;"):
                    ui.label("Models:").style(f"font-size:10px; color:{C_MUTED};")
                    for m in models:
                        ui.label(m).style(
                            f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; "
                            f"background:{C_SURFACE}; padding:1px 4px; "
                            f"border-radius:{R_SM};"
                        )

            # Errors
            errors = detail.get("errors", [])
            if errors:
                ui.html("<hr>").style(f"border-color:{C_INK40}; margin:4px 0;")
                ui.label(f"Errors ({len(errors)})").style(
                    f"font-size:13px; font-weight:600; color:{C_ERR_FG}; font-family:{F_SANS};"
                )
                for err in errors[:5]:
                    turn_id = err.get("turn_id", "?")
                    fail_count = err.get("fail_count", 0)
                    last_error = err.get("last_error", "")
                    with ui.row().style("gap:8px;"):
                        ui.label(f"{turn_id[:16]}").style(f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};")
                        ui.label(f"x{fail_count}: {last_error[:80]}").style(
                            f"font-size:10px; color:{C_ERR_FG}; font-family:{F_MONO};"
                        )

            # Sample turns
            sample_turns = detail.get("sample_turns", [])
            if sample_turns:
                ui.html("<hr>").style(f"border-color:{C_INK40}; margin:4px 0;")
                ui.label(f"Sample Turns ({len(sample_turns)})").style(
                    f"font-size:13px; font-weight:600; color:{C_CREAM}; font-family:{F_SANS};"
                )
                for t in sample_turns:
                    embedded = "embedded" if t.get("embedded") else "not embedded"
                    domain = t.get("primary_domain", "")
                    ui.label(
                        f"#{t.get('turn_index', '?')} — {domain or 'no domain'} — "
                        f"{embedded} — {t.get('word_count', 0)} words"
                    ).style(f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};")


def _meta_item(label: str, value: str) -> None:
    """Render a small metadata label/value pair."""
    with ui.row().style("gap:4px; align-items:baseline;"):
        ui.label(f"{label}:").style(f"font-size:10px; color:{C_MUTED}; font-family:{F_SANS};")
        ui.label(value).style(f"font-size:11px; color:{C_CREAM}; font-family:{F_MONO};")
