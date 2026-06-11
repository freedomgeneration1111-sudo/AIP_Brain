"""Artifact State Badge — renders ECS state as a styled pill.

Maps ECS states and derived states (NEEDS_REVISION, EXPORTED) to
semantic colors from the AIP design system.

States:
  - GENERATED: amber (pending review)
  - REVIEWED: amber (reviewed but not approved)
  - APPROVED: green (canonical)
  - REJECTED: red (rejected)
  - SUPERSEDED: muted (terminal)
  - FAILED: red (failed)
  - NEEDS_REVISION: warning amber (verdict, not ECS state)
  - EXPORTED: green with check (event, not ECS state)
  - UNKNOWN: muted

Import boundary: imports only gui.theme (no aip.* imports).
"""

from __future__ import annotations

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_AMBER_P,
    C_CREAM,
    C_ERR_FG,
    C_ERR_BG,
    C_INK40,
    C_INK60,
    C_MUTED,
    C_OK_BG,
    C_OK_FG,
    C_WARN_FG,
    F_MONO,
    R_SM,
)

# State badge configuration: (foreground, background, border, label)
_STATE_BADGE_CONFIG = {
    "GENERATED": (C_AMBER, C_AMBER_P, "#1A1200", "GENERATED"),
    "REVIEWED": (C_AMBER, "#2A2A1A", "#1A1200", "REVIEWED"),
    "APPROVED": (C_OK_FG, C_OK_BG, "#1E4030", "APPROVED"),
    "REJECTED": (C_ERR_FG, C_ERR_BG, "#3A1E1E", "REJECTED"),
    "SUPERSEDED": (C_MUTED, "transparent", C_INK40, "SUPERSEDED"),
    "FAILED": (C_ERR_FG, C_ERR_BG, "#3A1E1E", "FAILED"),
    "NEEDS_REVISION": (C_WARN_FG, "#2A2A1A", "#2A2A1A", "NEEDS REVISION"),
    "EXPORTED": (C_OK_FG, C_OK_BG, "#1E4030", "EXPORTED"),
    "UNKNOWN": (C_MUTED, "transparent", C_INK40, "UNKNOWN"),
}


def render_artifact_state_badge(
    ecs_state: str,
    *,
    has_needs_revision: bool = False,
    has_export: bool = False,
    compact: bool = False,
) -> None:
    """Render an artifact state badge as a styled label.

    If has_needs_revision is True and the artifact is in GENERATED state,
    the badge shows "NEEDS REVISION" instead of "GENERATED".

    If has_export is True and the artifact is APPROVED, the badge shows
    "EXPORTED" instead of "APPROVED".

    Parameters:
        ecs_state: The ECS state string (GENERATED, APPROVED, etc.)
        has_needs_revision: Whether artifact has a NEEDS_REVISION verdict
        has_export: Whether artifact has an export event
        compact: Use smaller font size
    """
    # Determine effective display state
    effective_state = ecs_state.upper()

    # Derived state overrides for display
    if has_needs_revision and effective_state == "GENERATED":
        effective_state = "NEEDS_REVISION"
    elif has_export and effective_state == "APPROVED":
        effective_state = "EXPORTED"

    # Get badge config
    fg, bg, border, label = _STATE_BADGE_CONFIG.get(
        effective_state,
        _STATE_BADGE_CONFIG["UNKNOWN"],
    )

    font_size = "8px" if compact else "9px"
    padding = "2px 6px" if compact else "3px 8px"
    letter_spacing = "0.5px" if compact else "0.5px"

    ui.label(label).style(
        f"display:inline-flex; align-items:center; "
        f"font-size:{font_size}; font-weight:700; font-family:{F_MONO}; "
        f"letter-spacing:{letter_spacing}; text-transform:uppercase; "
        f"padding:{padding}; border-radius:{R_SM}; "
        f"border:0.5px solid {border}; color:{fg}; background:{bg}; "
        f"flex-shrink:0;"
    )
