"""AIP Source Panel — drawer/modal for displaying retrieval sources.

Shows source title/path, snippet, score, and channel for each source
retrieved during an augmented answer. If no sources are available,
shows an honest empty/unavailable state.

Import boundary: this module imports ONLY from gui.* (theme, state).
Never imports from aip.orchestration.
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_GROUND,
    C_INK40,
    C_INK60,
    C_MUTED,
    C_OK_FG,
    C_RAISED,
    C_WARN_FG,
    F_MONO,
    R_MD,
)

log = logging.getLogger("gui.components.source_panel")


class SourcePanel:
    """Manages a source detail drawer/modal for the Ask Workbench.

    Usage:
        panel = SourcePanel()
        # ... later, when user clicks "Show Sources":
        panel.show_sources(sources_list)
    """

    def __init__(self) -> None:
        self._drawer: Any = None
        self._sources: list[dict[str, Any]] = []

    def show_sources(self, sources: list[dict[str, Any]]) -> None:
        """Open a right drawer showing the source list.

        Args:
            sources: List of source dicts with keys:
                source_id, source_type, title, score,
                content_snippet, domain
        """
        self._sources = sources

        # Close existing drawer if any
        self.close()

        with ui.right_drawer().style(
            f"background:{C_GROUND}; border-left:0.5px solid {C_INK40}; "
            f"width:380px; min-width:380px; padding:16px; overflow-y:auto;"
        ) as drawer:
            self._drawer = drawer

            # Header
            with (
                ui.row()
                .classes("w-full items-center")
                .style(f"border-bottom:0.5px solid {C_INK40}; padding-bottom:8px; margin-bottom:12px;")
            ):
                ui.label(f"Sources ({len(sources)})").style(
                    f"font-size:13px; font-weight:700; color:{C_AMBER}; font-family:{F_MONO}; letter-spacing:0.5px;"
                )
                ui.space()
                ui.button("Close", on_click=self.close).props("dense flat size=sm").style(
                    f"color:{C_MUTED}; font-size:10px;"
                )

            if not sources:
                ui.label("No sources available for this answer.").style(
                    f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO}; padding:16px; text-align:center;"
                )
                return

            # Source list
            for i, src in enumerate(sources, 1):
                self._render_source_card(i, src)

    def _render_source_card(self, index: int, src: dict[str, Any]) -> None:
        """Render a single source card."""
        source_id = src.get("source_id", "?")
        source_type = src.get("source_type", "unknown")
        title = src.get("title", "Untitled")
        score = src.get("score", 0)
        snippet = src.get("content_snippet", "")
        domain = src.get("domain", "")

        # Score color
        if isinstance(score, (int, float)) and score >= 0.8:
            score_color = C_OK_FG
        elif isinstance(score, (int, float)) and score >= 0.5:
            score_color = C_AMBER
        else:
            score_color = C_WARN_FG

        with (
            ui.card()
            .classes("w-full")
            .style(
                f"background:{C_RAISED}; border:0.5px solid {C_INK40}; "
                f"border-radius:{R_MD}; margin-bottom:8px; padding:8px 12px;"
            )
        ):
            # Source header: index + title + score
            with ui.row().classes("w-full items-center").style("margin-bottom:4px;"):
                ui.label(f"#{index}").style(
                    f"font-size:10px; font-weight:700; color:{C_AMBER}; font-family:{F_MONO}; margin-right:6px;"
                )
                ui.label(title[:80]).style(
                    f"font-size:11px; font-weight:600; color:{C_CREAM}; font-family:{F_MONO}; flex:1;"
                )
                if isinstance(score, (int, float)):
                    ui.label(f"{score:.2f}").style(
                        f"font-size:10px; font-weight:600; color:{score_color}; font-family:{F_MONO};"
                    )

            # Source metadata row
            with ui.row().classes("w-full").style("margin-bottom:4px;"):
                ui.label(f"type: {source_type}").style(
                    f"font-size:9px; color:{C_INK60}; font-family:{F_MONO}; margin-right:8px;"
                )
                if domain:
                    ui.label(f"domain: {domain}").style(f"font-size:9px; color:{C_INK60}; font-family:{F_MONO};")

            # Snippet
            if snippet:
                ui.label(snippet[:300]).style(
                    f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO}; line-height:1.4; margin-top:4px;"
                )

            # Source ID (small, for reference)
            ui.label(f"id: {source_id}").style(f"font-size:8px; color:{C_INK60}; font-family:{F_MONO}; margin-top:2px;")

    def close(self) -> None:
        """Close the source panel drawer."""
        if self._drawer is not None:
            try:
                self._drawer.close()
            except Exception:
                pass
            self._drawer = None
