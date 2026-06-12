"""AIP Design Tokens — extracted from shell.py (lines 28-75).

All visual constants for the AIP_Brain Operator Console.
Reference: aip_design_reference.html SS2-SS4.5

This module is the SOLE source of truth for colors, fonts, spacing,
border, and radius tokens.  Every GUI component should import from
here rather than hard-coding hex values.
"""

from __future__ import annotations

from nicegui import ui

# ── GROUND LAYERS (aip_design_reference.html SS2) ────────────────────
C_GROUND = "#0E0E0F"  # page/shell background
C_SURFACE = "#1A1D1F"  # cards, panels
C_RAISED = "#242829"  # hover states, inputs

# ── STRUCTURAL INK (slate-teal family) ────────────────────────────────
C_INK40 = "#2A3540"  # borders, edges, lines
C_INK60 = "#3D5566"  # labels, inactive text, secondary
C_MUTED = "#8FA8B8"  # body text secondary, placeholder
C_CREAM = "#F2EDE4"  # primary text on dark

# ── ACTIVATION (amber — use ONLY for: active tab, primary CTA, corpus node) ─
C_AMBER = "#B8935A"  # primary amber
C_AMBER_P = "#8C6E3A"  # pressed/hover state

# ── SEMANTIC STATES ───────────────────────────────────────────────────
C_OK_BG = "#1E3A2F"  # confirmed/approved background
C_OK_FG = "#4EAA7A"  # confirmed/approved text
C_ERR_BG = "#3A1E1E"  # danger background
C_ERR_FG = "#E07070"  # danger text
C_WARN_BG = "#2A2A1A"  # caution/pending background
C_WARN_FG = "#C8A84E"  # caution/pending text

# ── DOGFOOD MODE COLORS ──────────────────────────────────────────────
C_DOGFOOD_FULL = "#4EAA7A"  # green for FULL DOGFOOD
C_DOGFOOD_DEGRADED = "#C8A84E"  # amber for DEGRADED
C_DOGFOOD_BARE = "#E07070"  # red for BARE / DIRECT MODEL ONLY

# ── TYPOGRAPHY ────────────────────────────────────────────────────────
F_SERIF = "Georgia, 'Times New Roman', serif"
F_SANS = "'Helvetica Neue', Helvetica, Arial, sans-serif"
F_MONO = "'Courier New', monospace"

# ── SPACING (px values as strings for style() calls) ──────────────────
SP_XS = "4px"
SP_SM = "8px"
SP_MD = "16px"
SP_LG = "32px"

# ── BORDER ────────────────────────────────────────────────────────────
BORDER = f"0.5px solid {C_INK40}"

# ── RADIUS ────────────────────────────────────────────────────────────
R_SM = "4px"
R_MD = "6px"
R_LG = "8px"

# ── STAT TILE BACKGROUND ─────────────────────────────────────────────
C_STAT_BG = "#141618"
C_STAT_BD = "#1E2428"

# ── STATUS PILL COLORS (per aip_design_reference.html SS4.4) ─────────
_PILL_STYLES = {
    "GENERATED": (C_AMBER, C_AMBER_P, "#1A1200"),
    "APPROVED": (C_OK_FG, "#1E4030", "#0E1F17"),
    "REJECTED": (C_ERR_FG, C_ERR_BG, "#1A0000"),
    "IDLE": (C_MUTED, C_INK40, "transparent"),
    "ACTIVE": (C_AMBER, C_AMBER_P, "#1A0E00"),
    "READY": (C_OK_FG, "#1E4030", "#0E1F17"),
    "UNCONFIGURED": (C_MUTED, C_INK40, "transparent"),
}


# ── BUTTON STYLE HELPERS (aip_design_reference.html SS4.5) ───────────


def btn_primary() -> str:
    """Style string for primary (amber) buttons."""
    return (
        f"background:{C_AMBER}; color:#0E0800; border:0.5px solid {C_AMBER}; "
        f"padding:6px 14px; border-radius:{R_SM}; font-size:11px; font-weight:500;"
    )


def btn_secondary() -> str:
    """Style string for secondary (ghost bordered) buttons."""
    return (
        f"background:transparent; color:{C_MUTED}; border:0.5px solid {C_INK40}; "
        f"padding:6px 14px; border-radius:{R_SM}; font-size:11px; font-weight:500;"
    )


def btn_ghost() -> str:
    """Style string for ghost (minimal) buttons."""
    return (
        f"background:transparent; color:{C_INK60}; border:0.5px solid {C_STAT_BD}; "
        f"padding:6px 14px; border-radius:{R_SM}; font-size:10px;"
    )


# ── STATUS PILL HELPER (aip_design_reference.html SS4.4) ─────────────


def status_pill(status: str) -> None:
    """Render an inline status pill with correct semantic colors."""
    fg, border_c, bg = _PILL_STYLES.get(status.upper(), (C_MUTED, C_INK40, "transparent"))
    ui.label(status).style(
        f"display:inline-flex; align-items:center; font-size:10px; "
        f"letter-spacing:.5px; padding:3px 8px; border-radius:{R_SM}; "
        f"border:0.5px solid {border_c}; color:{fg}; background:{bg}; "
        f"flex-shrink:0;"
    )


# ── AIP CORPUS MARK (aip_design_reference.html SS1 — canonical 24x24) ─

_AIP_MARK = (
    '<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
    "<!-- Orthogonal edges -->"
    '<line x1="4" y1="4"  x2="12" y2="4"  stroke="#2A3540" stroke-width="1"/>'
    '<line x1="12" y1="4" x2="20" y2="4"  stroke="#2A3540" stroke-width="1"/>'
    '<line x1="4" y1="12" x2="12" y2="12" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="12" y1="12" x2="20" y2="12" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="4" y1="20" x2="12" y2="20" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="12" y1="20" x2="20" y2="20" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="4"  y1="4"  x2="4"  y2="12" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="4"  y1="12" x2="4"  y2="20" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="12" y1="4"  x2="12" y2="12" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="12" y1="12" x2="12" y2="20" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="20" y1="4"  x2="20" y2="12" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="20" y1="12" x2="20" y2="20" stroke="#2A3540" stroke-width="1"/>'
    "<!-- 8 peripheral nodes (slate-teal) -->"
    '<circle cx="4"  cy="4"  r="1.5" fill="#3D5566"/>'
    '<circle cx="12" cy="4"  r="1.5" fill="#3D5566"/>'
    '<circle cx="20" cy="4"  r="1.5" fill="#3D5566"/>'
    '<circle cx="4"  cy="12" r="1.5" fill="#3D5566"/>'
    '<circle cx="20" cy="12" r="1.5" fill="#3D5566"/>'
    '<circle cx="4"  cy="20" r="1.5" fill="#3D5566"/>'
    '<circle cx="12" cy="20" r="1.5" fill="#3D5566"/>'
    '<circle cx="20" cy="20" r="1.5" fill="#3D5566"/>'
    "<!-- Center corpus node (amber, weighted 2x the peripheral nodes) -->"
    '<circle cx="12" cy="12" r="3"   fill="#B8935A"/>'
    "</svg>"
)
