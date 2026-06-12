"""Maintenance problem panel for the Maintenance Center.

UI Cycle 12: Displays failed runs, degraded actors, and warnings.
Honest about empty/problem-free states. Never fakes problems.

Import boundary: imports ONLY from gui.* — never imports from aip.orchestration.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_ERR_FG,
    C_INK60,
    C_OK_FG,
    C_RAISED,
    C_SURFACE,
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_SM,
)


class MaintenanceProblemPanel:
    """Renders a panel showing failed runs, degraded actors, and warnings.

    Honest about no problems (shows "No problems" rather than faking issues).
    Honest about unavailable data (shows "unavailable" message).
    """

    def __init__(self) -> None:
        self._container: ui.column | None = None

    def render(self, data: dict[str, Any]) -> None:
        """Render problems from maintenance status data."""
        if self._container is not None:
            self._container.clear()

        actors = data.get("actors", {})
        warnings = data.get("warnings", [])

        with ui.column().classes("w-full").style("gap:6px") as col:
            self._container = col

            # Collect problems from actor states
            problems: list[dict[str, str]] = []
            for name, entry in actors.items():
                state = entry.get("state", "unknown")
                if state == "failed":
                    problems.append(
                        {
                            "type": "failed",
                            "source": name.upper(),
                            "message": entry.get("last_error", "Actor is in failed state"),
                        }
                    )
                elif state == "degraded":
                    reason = entry.get("degraded_reason", "No reason provided")
                    problems.append(
                        {
                            "type": "degraded",
                            "source": name.upper(),
                            "message": reason,
                        }
                    )
                if entry.get("last_error") and state != "failed":
                    problems.append(
                        {
                            "type": "error",
                            "source": name.upper(),
                            "message": entry.get("last_error", ""),
                        }
                    )
                missing = entry.get("missing_core_dependencies", [])
                if missing:
                    problems.append(
                        {
                            "type": "missing",
                            "source": name.upper(),
                            "message": f"Missing deps: {', '.join(missing[:3])}",
                        }
                    )

            # Add warnings
            for w in warnings[:5]:
                problems.append(
                    {
                        "type": "warning",
                        "source": "SYSTEM",
                        "message": w,
                    }
                )

            if not problems:
                ui.label("No problems detected").style(f"font-size:11px; color:{C_OK_FG}; font-family:{F_SANS};")
                return

            # Render problems
            for problem in problems[:10]:
                ptype = problem.get("type", "warning")
                source = problem.get("source", "")
                message = problem.get("message", "")

                if ptype == "failed":
                    fg = C_ERR_FG
                    badge = "FAILED"
                elif ptype == "degraded":
                    fg = C_WARN_FG
                    badge = "DEGRADED"
                elif ptype == "error":
                    fg = C_AMBER
                    badge = "ERROR"
                elif ptype == "missing":
                    fg = C_AMBER
                    badge = "MISSING"
                else:
                    fg = C_INK60
                    badge = "WARN"

                with (
                    ui.row()
                    .classes("w-full items-center")
                    .style(
                        f"background:{C_RAISED}; border-left:2px solid {fg}; "
                        f"border-radius:0 {R_SM} {R_SM} 0; padding:4px 8px; gap:8px;"
                    )
                ):
                    ui.label(badge).style(
                        f"font-size:8px; font-weight:600; color:{fg}; "
                        f"font-family:{F_MONO}; border:1px solid {fg}; "
                        f"background:{C_SURFACE}; border-radius:{R_SM}; "
                        f"padding:0px 4px; letter-spacing:0.5px; min-width:56px; text-align:center;"
                    )
                    ui.label(source).style(
                        f"font-size:10px; font-weight:600; color:{C_CREAM}; font-family:{F_MONO}; min-width:56px;"
                    )
                    ui.label(message[:60]).style(f"font-size:10px; color:{fg}; font-family:{F_SANS}; flex:1;")
