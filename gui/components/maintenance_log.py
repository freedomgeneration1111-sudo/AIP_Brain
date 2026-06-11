"""Maintenance log panel for the Maintenance Center.

UI Cycle 12: Displays recent maintenance events from the event store.
Honest empty state when event store is unavailable. Never fakes logs.

Import boundary: imports ONLY from gui.* — never imports from aip.orchestration.
"""

from __future__ import annotations

from typing import Any

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
    F_MONO,
    F_SANS,
    R_MD,
)


class MaintenanceLog:
    """Renders a scrollable panel of recent maintenance log events.

    Shows event type, actor, timestamp, and metadata for each entry.
    Honest about unavailable/empty states.
    """

    def __init__(self) -> None:
        self._container: ui.column | None = None

    def render(self, data: dict[str, Any]) -> None:
        """Render maintenance log entries from the logs response."""
        if self._container is not None:
            self._container.clear()

        available = data.get("available", False)
        logs = data.get("logs", [])
        message = data.get("message", "")

        with ui.column().classes("w-full").style("gap:4px") as col:
            self._container = col

            if not available:
                ui.label(message or "Event store not available").style(
                    f"font-size:11px; color:{C_ERR_FG}; font-family:{F_SANS};"
                )
                return

            if not logs:
                ui.label("No recent maintenance events").style(
                    f"font-size:11px; color:{C_MUTED}; font-family:{F_SANS};"
                )
                return

            # Log header
            with (
                ui.row()
                .classes("w-full")
                .style(
                    f"background:{C_SURFACE}; border-bottom:1px solid {C_INK40}; "
                    f"padding:4px 8px; border-radius:{R_MD} {R_MD} 0 0;"
                )
            ):
                for header, width in [
                    ("TIME", "80px"),
                    ("ACTOR", "60px"),
                    ("EVENT", "150px"),
                    ("DETAIL", "flex:1"),
                ]:
                    ui.label(header).style(
                        f"font-size:8px; font-weight:600; color:{C_INK60}; "
                        f"letter-spacing:0.5px; min-width:{width}; "
                        f"text-transform:uppercase; font-family:{F_MONO};"
                    )

            # Log rows (scrollable, max 10 visible)
            for entry in logs[:30]:
                event_type = entry.get("event_type", "")
                actor = entry.get("actor", "")
                timestamp = entry.get("timestamp", "")
                metadata = entry.get("metadata", {})
                from_state = entry.get("from_state")
                to_state = entry.get("to_state")

                # Determine color from event type
                if "error" in event_type.lower() or "fail" in event_type.lower():
                    row_fg = C_ERR_FG
                elif "health" in event_type.lower() or "heartbeat" in event_type.lower():
                    row_fg = C_OK_FG
                elif "start" in event_type.lower() or "complete" in event_type.lower():
                    row_fg = C_CREAM
                else:
                    row_fg = C_INK60

                # Format timestamp
                time_str = ""
                if timestamp:
                    if "T" in str(timestamp):
                        time_str = str(timestamp).split("T")[1][:8]
                    else:
                        time_str = str(timestamp)[:8]

                # Format detail
                detail_parts = []
                if from_state and to_state:
                    detail_parts.append(f"{from_state} -> {to_state}")
                if metadata:
                    for k, v in list(metadata.items())[:2]:
                        detail_parts.append(f"{k}={v}")
                detail = " | ".join(detail_parts) if detail_parts else ""

                with (
                    ui.row()
                    .classes("w-full items-center")
                    .style(
                        f"background:{C_RAISED}; border-bottom:0.5px solid {C_INK40}; padding:2px 8px; min-height:22px;"
                    )
                ):
                    ui.label(time_str).style(f"font-size:9px; color:{C_INK60}; font-family:{F_MONO}; min-width:80px;")
                    ui.label(actor.upper()[:6]).style(
                        f"font-size:9px; color:{row_fg}; font-family:{F_MONO}; font-weight:600; min-width:60px;"
                    )
                    ui.label(event_type[:24]).style(
                        f"font-size:9px; color:{row_fg}; font-family:{F_MONO}; min-width:150px;"
                    )
                    ui.label(detail[:50]).style(f"font-size:9px; color:{C_INK60}; font-family:{F_MONO}; flex:1;")
