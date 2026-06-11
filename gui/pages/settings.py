"""AIP Settings Page — Route: /settings

Placeholder: Settings — Not yet implemented.
"""

from __future__ import annotations

from nicegui import context, ui

from gui.components.layout import build_left_nav, build_right_rail, build_top_bar
from gui.state import get_session_state
from gui.theme import (
    C_CREAM,
    C_GROUND,
    C_MUTED,
    C_SURFACE,
    C_INK40,
    F_SANS,
    F_MONO,
    R_MD,
)


@ui.page("/settings")
async def settings_page():
    """Settings — Not yet implemented."""
    state = get_session_state()
    state.client = context.client

    build_top_bar(state)
    build_left_nav(state, active_page="/settings")

    with (
        ui.column()
        .classes("flex-1")
        .style(f"background:{C_GROUND}; padding:48px; overflow-y:auto; min-height:calc(100vh - 44px);")
    ):
        ui.label("Settings").style(f"font-family:{F_SANS}; font-size:28px; font-weight:700; color:{C_CREAM};")
        ui.label("Not yet implemented").style(f"font-size:14px; color:{C_MUTED}; margin-top:8px;")
        ui.label(
            "Planned for a future UI cycle.\n"
            "The Settings page will provide:\n"
            "  - API key management\n"
            "  - Model slot configuration\n"
            "  - Definer profile settings\n"
            "  - Display preferences"
        ).style(
            f"font-size:12px; color:{C_MUTED}; font-family:{F_MONO}; "
            f"margin-top:16px; white-space:pre-wrap; "
            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_MD}; padding:16px;"
        )

    build_right_rail(state)
