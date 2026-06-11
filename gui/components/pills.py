"""AIP Status Pills — re-exports from theme + dogfood mode pill."""

from __future__ import annotations

from nicegui import ui

from gui.theme import (
    C_DOGFOOD_BARE,
    C_DOGFOOD_DEGRADED,
    C_DOGFOOD_FULL,
    C_INK40,
    C_MUTED,
    R_SM,
    status_pill,  # noqa: F401 — re-export
)

# Dogfood mode -> (fg color, bg color)
_DOGFOOD_PILL_COLORS = {
    "FULL": (C_DOGFOOD_FULL, "#0E1F17"),
    "DEGRADED": (C_DOGFOOD_DEGRADED, "#1A1A0E"),
    "BARE": (C_DOGFOOD_BARE, "#1A0E0E"),
    "DIRECT MODEL ONLY": (C_DOGFOOD_BARE, "#1A0E0E"),
}


def dogfood_mode_pill(mode: str) -> None:
    """Render a pill showing dogfood mode: FULL / DEGRADED / BARE / DIRECT MODEL ONLY."""
    fg, bg = _DOGFOOD_PILL_COLORS.get(mode, (C_MUTED, "transparent"))
    ui.label(mode).style(
        f"display:inline-flex; align-items:center; font-size:10px; "
        f"letter-spacing:.5px; padding:3px 8px; border-radius:{R_SM}; "
        f"border:0.5px solid {fg}; color:{fg}; background:{bg}; "
        f"flex-shrink:0; font-weight:600;"
    )
