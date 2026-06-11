"""Retrieval Query Panel — query input and channel toggles for the Retrieval Lab.

Provides:
- Query text input
- Channel toggle checkboxes (Lexical, Vector, Graph, Wiki/CODEX, Procedural, Corpus)
- Result limit slider
- Include trace toggle
- Run Retrieval Test button
"""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from nicegui import ui

from gui.theme import (
    C_CREAM,
    C_GROUND,
    C_SURFACE,
    C_RAISED,
    C_INK40,
    C_MUTED,
    C_OK_FG,
    C_AMBER,
    C_ERR_FG,
    F_SANS,
    F_MONO,
    R_MD,
    R_SM,
)


# Channel definitions: key, label, default_enabled
CHANNEL_DEFS: list[tuple[str, str, bool]] = [
    ("fts", "Lexical (FTS5)", True),
    ("vector", "Vector", True),
    ("graph", "Graph", False),
    ("wiki", "Wiki/CODEX", False),
    ("procedural", "Procedural", False),
    ("corpus", "Corpus", True),
]


class RetrievalQueryPanel:
    """Renders the query input, channel toggles, and controls for the Retrieval Lab."""

    def __init__(self, on_run: Callable[[str, list[str], int, bool], Awaitable[None]]) -> None:
        """Initialize the query panel.

        Args:
            on_run: Async callback when the user clicks "Run Test".
                Receives (query, selected_channels, limit, include_trace).
        """
        self._on_run = on_run
        self._query_input: ui.input | None = None
        self._channel_toggles: dict[str, ui.checkbox] = {}
        self._limit_input: ui.number | None = None
        self._trace_toggle: ui.checkbox | None = None
        self._run_button: ui.button | None = None
        self._running: bool = False

    def render(self) -> None:
        """Render the query panel."""
        with (
            ui.card()
            .classes("w-full p-4")
            .style(f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; border-radius:{R_MD};")
        ):
            # Title
            ui.label("Retrieval Test").style(f"font-family:{F_SANS}; font-size:16px; font-weight:700; color:{C_CREAM};")
            ui.label("Test retrieval quality independently of answer synthesis").style(
                f"font-size:11px; color:{C_MUTED}; font-family:{F_SANS}; margin-bottom:8px;"
            )

            # Query input
            with ui.row().classes("w-full items-center gap-2"):
                self._query_input = (
                    ui.input(
                        placeholder="Enter a test query...",
                    )
                    .classes("flex-1")
                    .style(
                        f"font-family:{F_MONO}; font-size:13px; "
                        f"background:{C_GROUND}; border:0.5px solid {C_INK40}; "
                        f"border-radius:{R_SM};"
                    )
                    .props("outlined dense")
                )

                self._run_button = (
                    ui.button(
                        "Run Test",
                        on_click=self._handle_run,
                    )
                    .style(
                        f"font-family:{F_SANS}; font-weight:600; "
                        f"background:{C_OK_FG}; color:white; "
                        f"border-radius:{R_SM}; padding:6px 16px;"
                    )
                    .props("unelevated")
                )

            # Channel toggles
            with ui.row().classes("w-full mt-2 gap-2 flex-wrap items-center"):
                ui.label("Channels:").style(
                    f"font-size:11px; color:{C_MUTED}; font-family:{F_SANS}; font-weight:600; margin-right:4px;"
                )
                for ch_key, ch_label, default in CHANNEL_DEFS:
                    toggle = ui.checkbox(ch_label, value=default).style(
                        f"font-size:11px; color:{C_CREAM}; font-family:{F_SANS};"
                    )
                    self._channel_toggles[ch_key] = toggle

            # Controls row
            with ui.row().classes("w-full mt-2 gap-4 items-center"):
                # Result limit
                with ui.row().classes("items-center gap-1"):
                    ui.label("Limit:").style(f"font-size:10px; color:{C_MUTED}; font-family:{F_SANS};")
                    self._limit_input = (
                        ui.number(value=20, min=1, max=100)
                        .style(
                            f"font-family:{F_MONO}; font-size:11px; width:60px; "
                            f"background:{C_GROUND}; border:0.5px solid {C_INK40}; "
                            f"border-radius:{R_SM};"
                        )
                        .props("outlined dense")
                    )

                # Include trace
                self._trace_toggle = ui.checkbox("Include Trace", value=True).style(
                    f"font-size:10px; color:{C_CREAM}; font-family:{F_SANS};"
                )

                # Warning banner
                ui.label("No answer synthesis — retrieval diagnostic only").style(
                    f"font-size:9px; color:{C_AMBER}; font-family:{F_MONO}; margin-left:auto;"
                )

    async def _handle_run(self) -> None:
        """Handle the Run Test button click."""
        if self._running:
            return
        self._running = True
        try:
            if self._run_button is not None:
                self._run_button.text = "Running..."
                self._run_button.disable()

            query = ""
            if self._query_input is not None:
                query = (self._query_input.value or "").strip()

            selected_channels = [ch_key for ch_key, toggle in self._channel_toggles.items() if toggle.value]

            limit = 20
            if self._limit_input is not None and self._limit_input.value is not None:
                limit = int(self._limit_input.value)

            include_trace = True
            if self._trace_toggle is not None:
                include_trace = self._trace_toggle.value

            await self._on_run(query, selected_channels, limit, include_trace)
        finally:
            self._running = False
            if self._run_button is not None:
                self._run_button.text = "Run Test"
                self._run_button.enable()

    def set_query(self, query: str) -> None:
        """Set the query input value programmatically."""
        if self._query_input is not None:
            self._query_input.set_value(query)
