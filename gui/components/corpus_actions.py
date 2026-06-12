"""Corpus Actions — action buttons for the Corpus Workbench.

Provides Ingest, Backfill, and Retry Failed actions.
Actions with missing backend support show unavailable/not_wired honestly.

Import boundary: imports ONLY from gui.* (theme, api_client).
Never imports from aip.orchestration.
"""

from __future__ import annotations

import logging
from typing import Callable

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_INK40,
    C_MUTED,
    C_OK_FG,
    F_SANS,
)

log = logging.getLogger("gui.components.corpus_actions")


class CorpusActions:
    """Corpus action buttons for the Corpus Workbench.

    Provides:
      - Ingest File (explicit DEFINER action)
      - Run Embedding Backfill (explicit DEFINER action)
      - Retry Failed Embeds (explicit DEFINER action)

    All actions are explicit — no silent mutation.
    Actions with missing backend support show unavailable/not_wired honestly.
    """

    def __init__(
        self,
        on_ingest: Callable[[], None] | None = None,
        on_backfill: Callable[[], None] | None = None,
        on_retry_failed: Callable[[], None] | None = None,
    ) -> None:
        self._on_ingest = on_ingest
        self._on_backfill = on_backfill
        self._on_retry_failed = on_retry_failed
        self._container: ui.row | None = None

    def render(
        self,
        backfill_running: bool = False,
        has_embedding_provider: bool = True,
    ) -> None:
        """Render the action buttons."""
        if self._container is not None:
            self._container.clear()

        with ui.row().classes("w-full").style("gap:8px; padding:4px 0; flex-wrap:wrap;") as row:
            self._container = row

            # Ingest button
            ingest_label = "Ingest File"
            ui.button(ingest_label, on_click=self._handle_ingest).props("outline dense size=sm").style(
                f"color:{C_CREAM}; border-color:{C_INK40}; font-family:{F_SANS};"
            )

            # Backfill button
            if backfill_running:
                ui.button("Backfill Running...").props("outline dense size=sm disabled").style(
                    f"color:{C_MUTED}; border-color:{C_INK40}; font-family:{F_SANS};"
                )
            elif not has_embedding_provider:
                ui.button("Backfill (unavailable)").props("outline dense size=sm disabled").style(
                    f"color:{C_MUTED}; border-color:{C_INK40}; font-family:{F_SANS};"
                ).tooltip("No embedding provider configured. Configure an embedding model slot in Settings.")
            else:
                ui.button("Run Embedding Backfill", on_click=self._handle_backfill).props(
                    "outline dense size=sm"
                ).style(f"color:{C_OK_FG}; border-color:{C_INK40}; font-family:{F_SANS};").tooltip(
                    "Explicit DEFINER action. Starts embedding backfill for unembedded chunks."
                )

            # Retry failed button
            ui.button("Retry Failed Embeds", on_click=self._handle_retry_failed).props("outline dense size=sm").style(
                f"color:{C_AMBER}; border-color:{C_INK40}; font-family:{F_SANS};"
            ).tooltip("Explicit DEFINER action. Clears failure counters so failed turns will be retried.")

    def _handle_ingest(self) -> None:
        if self._on_ingest:
            self._on_ingest()

    def _handle_backfill(self) -> None:
        if self._on_backfill:
            self._on_backfill()

    def _handle_retry_failed(self) -> None:
        if self._on_retry_failed:
            self._on_retry_failed()
