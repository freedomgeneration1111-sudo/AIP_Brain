"""Actor status table component for the Maintenance Center.

UI Cycle 12: Displays Beast, Vigil, and Sexton actor status in a
structured table with state, scheduling, timing, results, and actions.
Honest about uninitialized, degraded, and failed states.

Import boundary: imports ONLY from gui.* — never imports from aip.orchestration.
"""

from __future__ import annotations

from typing import Any, Callable

from nicegui import ui

from gui.theme import (
    C_CREAM,
    C_ERR_FG,
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


def _state_pill(state: str) -> None:
    """Render a compact state pill for an actor state."""
    colors = {
        "active": (C_OK_FG, C_OK_FG, C_SURFACE),
        "degraded": (C_WARN_FG, C_WARN_FG, C_SURFACE),
        "failed": (C_ERR_FG, C_ERR_FG, C_SURFACE),
        "not_configured": (C_MUTED, C_MUTED, C_SURFACE),
        "instantiated": (C_WARN_FG, C_WARN_FG, C_SURFACE),
        "unknown": (C_INK60, C_INK60, C_SURFACE),
    }
    fg, border, bg = colors.get(state, (C_INK60, C_INK60, C_SURFACE))
    ui.label(state.upper()).style(
        f"font-size:9px; font-weight:600; font-family:{F_MONO}; "
        f"color:{fg}; border:1px solid {border}; background:{bg}; "
        f"border-radius:{R_SM}; padding:1px 6px; letter-spacing:0.5px;"
    )


def _format_time(value: Any) -> str:
    """Format a time value for display."""
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        if value > 1e15:  # epoch ms
            from datetime import datetime, timezone

            try:
                dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
                return dt.strftime("%H:%M:%S")
            except Exception:
                return str(value)
        return str(value)
    s = str(value)
    # Trim ISO timestamps to time-only for compact display
    if "T" in s:
        try:
            return s.split("T")[1][:8]
        except Exception:
            pass
    return s[:20]


class ActorStatusTable:
    """Renders a table of actor statuses with run buttons.

    Displays Beast, Vigil, and Sexton status including:
    - name, state, initialized, scheduled, running
    - last run, next run, last result, last error
    - actions: Run, View Logs
    """

    def __init__(
        self,
        on_run_actor: Callable[[str], None] | None = None,
        on_view_logs: Callable[[str], None] | None = None,
    ) -> None:
        self._on_run_actor = on_run_actor
        self._on_view_logs = on_view_logs
        self._container: ui.column | None = None

    def render(self, data: dict[str, Any]) -> None:
        """Render the actor status table from maintenance status data."""
        if self._container is not None:
            self._container.clear()

        actors = data.get("actors", {})
        if not actors:
            with ui.column().classes("w-full") as col:
                self._container = col
                ui.label("No actor data available").style(f"font-size:12px; color:{C_MUTED}; font-family:{F_SANS};")
            return

        with ui.column().classes("w-full").style("gap:0") as col:
            self._container = col

            # Table header
            with (
                ui.row()
                .classes("w-full")
                .style(
                    f"background:{C_SURFACE}; border-bottom:1px solid {C_INK40}; "
                    f"border-radius:{R_MD} {R_MD} 0 0; padding:8px 12px; "
                    f"font-family:{F_SANS};"
                )
            ):
                for header, width in [
                    ("ACTOR", "80px"),
                    ("STATE", "100px"),
                    ("INIT", "50px"),
                    ("LAST RUN", "80px"),
                    ("LAST RESULT", "120px"),
                    ("LAST ERROR", "140px"),
                    ("CYCLES", "60px"),
                    ("ACTIONS", "130px"),
                ]:
                    ui.label(header).style(
                        f"font-size:9px; font-weight:600; color:{C_INK60}; "
                        f"letter-spacing:0.5px; min-width:{width}; "
                        f"text-transform:uppercase;"
                    )

            # Table rows
            actor_order = ["beast", "vigil", "sexton"]
            for actor_name in actor_order:
                entry = actors.get(actor_name, {})
                with (
                    ui.row()
                    .classes("w-full items-center")
                    .style(
                        f"background:{C_RAISED}; border-bottom:0.5px solid {C_INK40}; "
                        f"padding:6px 12px; font-family:{F_SANS}; min-height:36px;"
                    )
                ):
                    # Actor name
                    ui.label(actor_name.upper()).style(
                        f"font-size:11px; font-weight:700; color:{C_CREAM}; font-family:{F_MONO}; min-width:80px;"
                    )

                    # State pill
                    with ui.row().style("min-width:100px;"):
                        state = entry.get("state", "unknown")
                        _state_pill(state)

                    # Initialized
                    init = entry.get("initialized", False)
                    ui.label("Y" if init else "N").style(
                        f"font-size:11px; color:{C_OK_FG if init else C_ERR_FG}; "
                        f"font-family:{F_MONO}; min-width:50px; font-weight:600;"
                    )

                    # Last run
                    last_run = entry.get("last_run_at")
                    ui.label(_format_time(last_run)).style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; min-width:80px;"
                    )

                    # Last result
                    last_result = entry.get("last_result")
                    result_text = str(last_result)[:24] if last_result else "—"
                    ui.label(result_text).style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; min-width:120px;"
                    )

                    # Last error
                    last_error = entry.get("last_error")
                    if last_error:
                        ui.label(str(last_error)[:30]).style(
                            f"font-size:10px; color:{C_ERR_FG}; font-family:{F_MONO}; min-width:140px;"
                        )
                    else:
                        ui.label("—").style(f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; min-width:140px;")

                    # Cycles
                    cycle_count = entry.get("cycle_count", 0)
                    ui.label(str(cycle_count)).style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; min-width:60px;"
                    )

                    # Actions
                    with ui.row().style("min-width:130px; gap:4px;"):
                        run_supported = entry.get("run_now_supported", True) and init
                        btn_label = "Run" if run_supported else "N/A"
                        btn = (
                            ui.button(btn_label, on_click=lambda n=actor_name: self._handle_run(n))
                            .props("outline dense size=sm")
                            .style(
                                f"color:{C_CREAM if run_supported else C_MUTED}; "
                                f"border-color:{C_INK40}; font-family:{F_SANS}; font-size:10px;"
                            )
                        )
                        if not run_supported:
                            btn.props("disabled")
                            btn.tooltip("Actor not initialized or run-now not supported")

                        ui.button("Logs", on_click=lambda n=actor_name: self._handle_view_logs(n)).props(
                            "outline dense size=sm"
                        ).style(f"color:{C_CREAM}; border-color:{C_INK40}; font-family:{F_SANS}; font-size:10px;")

    def _handle_run(self, actor_name: str) -> None:
        if self._on_run_actor:
            self._on_run_actor(actor_name)

    def _handle_view_logs(self, actor_name: str) -> None:
        if self._on_view_logs:
            self._on_view_logs(actor_name)
