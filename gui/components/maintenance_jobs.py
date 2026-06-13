"""Maintenance jobs component for the Maintenance Center.

UI Cycle 12: Displays maintenance job buttons with status indicators.
Each job is an explicit DEFINER action. Honest about unavailable/not_wired states.

Import boundary: imports ONLY from gui.* — never imports from aip.orchestration.
"""

from __future__ import annotations

from typing import Any, Callable

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_INK40,
    C_INK60,
    C_MUTED,
    C_OK_FG,
    C_SURFACE,
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_MD,
    R_SM,
)


class MaintenanceJobs:
    """Renders maintenance job control buttons.

    Each button represents an explicit DEFINER action. Buttons are
    disabled when the capability is not_wired or scheduled_only.
    Status indicators show current availability.
    """

    def __init__(
        self,
        on_backfill: Callable[[], None] | None = None,
        on_rebuild_graph: Callable[[], None] | None = None,
        on_rebuild_codex: Callable[[], None] | None = None,
        on_retrieval_eval: Callable[[], None] | None = None,
        on_check_stale: Callable[[], None] | None = None,
        on_check_contradictions: Callable[[], None] | None = None,
    ) -> None:
        self._on_backfill = on_backfill
        self._on_rebuild_graph = on_rebuild_graph
        self._on_rebuild_codex = on_rebuild_codex
        self._on_retrieval_eval = on_retrieval_eval
        self._on_check_stale = on_check_stale
        self._on_check_contradictions = on_check_contradictions
        self._container: ui.column | None = None

    def render(self, capabilities: dict[str, Any], backfill_running: bool = False) -> None:
        """Render maintenance job controls from capabilities data."""
        if self._container is not None:
            self._container.clear()

        with ui.column().classes("w-full").style("gap:8px") as col:
            self._container = col

            # Section header
            ui.label("MAINTENANCE JOBS").style(
                f"font-size:11px; font-weight:600; letter-spacing:1px; "
                f"color:{C_AMBER}; text-transform:uppercase; font-family:{F_SANS};"
            )

            # Job grid
            jobs = [
                {
                    "key": "embedding_backfill",
                    "label": "Backfill Embeddings",
                    "on_click": self._on_backfill,
                    "icon": "sync",
                    "extra_status": "RUNNING" if backfill_running else None,
                },
                {
                    "key": "graph_rebuild",
                    "label": "Rebuild Graph",
                    "on_click": self._on_rebuild_graph,
                    "icon": "account_tree",
                },
                {
                    "key": "codex_rebuild",
                    "label": "Rebuild CODEX/Wiki",
                    "on_click": self._on_rebuild_codex,
                    "icon": "auto_stories",
                },
                {
                    "key": "retrieval_eval",
                    "label": "Run Retrieval Eval",
                    "on_click": self._on_retrieval_eval,
                    "icon": "science",
                },
                {
                    "key": "stale_docs_check",
                    "label": "Check Stale Docs",
                    "on_click": self._on_check_stale,
                    "icon": "schedule",
                },
                {
                    "key": "contradiction_check",
                    "label": "Check Contradictions",
                    "on_click": self._on_check_contradictions,
                    "icon": "warning",
                },
            ]

            with ui.row().classes("w-full").style("gap:8px; flex-wrap:wrap;"):
                for job in jobs:
                    cap = capabilities.get(job["key"], {})
                    available = cap.get("available", False)
                    status = cap.get("status", "unknown")
                    message = cap.get("message", "")

                    with (
                        ui.card()
                        .classes("q-pa-sm")
                        .style(
                            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
                            f"border-radius:{R_MD}; min-width:180px; max-width:220px; "
                            f"flex:1; font-family:{F_SANS};"
                        )
                    ):
                        # Job title row
                        with ui.row().classes("w-full items-center").style("gap:6px;"):
                            ui.icon(job["icon"], size="16px").style(f"color:{C_CREAM if available else C_MUTED};")
                            ui.label(job["label"]).style(
                                f"font-size:11px; font-weight:600; color:{C_CREAM if available else C_MUTED}; "
                                f"font-family:{F_SANS};"
                            )

                        # Status indicator
                        with ui.row().style("gap:4px; margin-top:4px;"):
                            if job.get("extra_status"):
                                ui.label(job["extra_status"]).style(
                                    f"font-size:9px; font-weight:600; color:{C_WARN_FG}; "
                                    f"font-family:{F_MONO}; border:1px solid {C_WARN_FG}; "
                                    f"background:{C_SURFACE}; border-radius:{R_SM}; "
                                    f"padding:0px 4px; letter-spacing:0.5px;"
                                )
                            elif status == "available":
                                ui.label("AVAILABLE").style(
                                    f"font-size:9px; font-weight:600; color:{C_OK_FG}; "
                                    f"font-family:{F_MONO}; border:1px solid {C_OK_FG}; "
                                    f"background:{C_SURFACE}; border-radius:{R_SM}; "
                                    f"padding:0px 4px; letter-spacing:0.5px;"
                                )
                            elif status == "scheduled_only":
                                ui.label("SCHEDULED ONLY").style(
                                    f"font-size:9px; font-weight:600; color:{C_AMBER}; "
                                    f"font-family:{F_MONO}; border:1px solid {C_AMBER}; "
                                    f"background:{C_SURFACE}; border-radius:{R_SM}; "
                                    f"padding:0px 4px; letter-spacing:0.5px;"
                                )
                            else:
                                ui.label("NOT WIRED").style(
                                    f"font-size:9px; font-weight:600; color:{C_MUTED}; "
                                    f"font-family:{F_MONO}; border:1px solid {C_INK60}; "
                                    f"background:{C_SURFACE}; border-radius:{R_SM}; "
                                    f"padding:0px 4px; letter-spacing:0.5px;"
                                )

                        # Run button
                        btn_label = "Run" if available else ("N/A" if status == "not_wired" else "Trigger Cycle")
                        btn_enabled = available or status == "scheduled_only"

                        ui.button(
                            btn_label,
                            on_click=lambda j=job: j["on_click"]() if j["on_click"] else None,
                        ).props("outline dense size=sm").style(
                            f"color:{C_CREAM if btn_enabled else C_MUTED}; "
                            f"border-color:{C_INK40}; font-family:{F_SANS}; font-size:10px; "
                            f"margin-top:6px;"
                        ) if btn_enabled else ui.button(btn_label).props("outline dense size=sm disabled").style(
                            f"color:{C_MUTED}; border-color:{C_INK40}; "
                            f"font-family:{F_SANS}; font-size:10px; margin-top:6px;"
                        )

                        # Message tooltip
                        if message:
                            with ui.row().style("margin-top:2px;"):
                                ui.label(message[:60]).style(
                                    f"font-size:9px; color:{C_INK60}; font-family:{F_SANS}; line-height:1.2;"
                                )
