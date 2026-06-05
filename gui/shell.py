"""AIP_Brain GUI — Fixed Shell (Tier 0).

Dev entry point:  python -m gui.shell  (port 8082, runs alongside main.py)
Final entry point after Stage 0D: replaces gui/main.py on port 8080.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from nicegui import context, ui
from gui.api_client import get_api_client, AipApiClient

log = logging.getLogger("gui.shell")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# ── AIP DESIGN TOKENS  (aip_design_reference.html §2) ────────────────
C_GROUND   = '#0E0E0F'
C_SURFACE  = '#1A1D1F'
C_RAISED   = '#242829'
C_INK40    = '#2A3540'
C_INK60    = '#3D5566'
C_MUTED    = '#8FA8B8'
C_CREAM    = '#F2EDE4'
C_AMBER    = '#B8935A'
C_AMBER_P  = '#8C6E3A'
C_OK_BG    = '#1E3A2F'
C_OK_FG    = '#4EAA7A'
C_ERR_BG   = '#3A1E1E'
C_ERR_FG   = '#E07070'
C_WARN_BG  = '#2A2A1A'
C_WARN_FG  = '#C8A84E'
F_SERIF    = "Georgia, 'Times New Roman', serif"
F_SANS     = "'Helvetica Neue', Helvetica, Arial, sans-serif"
F_MONO     = "'Courier New', monospace"

# ── AIP CORPUS MARK  (aip_design_reference.html §1) ──────────────────
_AIP_MARK = (
    '<svg width="20" height="20" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
    '<line x1="4" y1="4" x2="12" y2="4" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="12" y1="4" x2="20" y2="4" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="4" y1="12" x2="12" y2="12" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="12" y1="12" x2="20" y2="12" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="4" y1="20" x2="12" y2="20" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="12" y1="20" x2="20" y2="20" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="4" y1="4" x2="4" y2="12" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="4" y1="12" x2="4" y2="20" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="12" y1="4" x2="12" y2="12" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="12" y1="12" x2="12" y2="20" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="20" y1="4" x2="20" y2="12" stroke="#2A3540" stroke-width="1"/>'
    '<line x1="20" y1="12" x2="20" y2="20" stroke="#2A3540" stroke-width="1"/>'
    '<circle cx="4"  cy="4"  r="1.5" fill="#3D5566"/>'
    '<circle cx="12" cy="4"  r="1.5" fill="#3D5566"/>'
    '<circle cx="20" cy="4"  r="1.5" fill="#3D5566"/>'
    '<circle cx="4"  cy="12" r="1.5" fill="#3D5566"/>'
    '<circle cx="20" cy="12" r="1.5" fill="#3D5566"/>'
    '<circle cx="4"  cy="20" r="1.5" fill="#3D5566"/>'
    '<circle cx="12" cy="20" r="1.5" fill="#3D5566"/>'
    '<circle cx="20" cy="20" r="1.5" fill="#3D5566"/>'
    '<circle cx="12" cy="12" r="3"   fill="#B8935A"/>'
    '</svg>'
)

# ── STATE ─────────────────────────────────────────────────────────────

class GuiState:
    """Module-level session state — one instance, persists across tab switches."""

    def __init__(self) -> None:
        self.api_client: AipApiClient = get_api_client()
        self.session_id: str | None = None
        self.current_role: str | None = None
        self.current_model_slot: str = "synthesis"
        self.current_mode: str = "normal"
        self.available_slots: list[dict[str, Any]] = []
        self.backend_reachable: bool = False
        self.pending_gate: dict[str, Any] | None = None
        self.auto_save: bool = True
        self.ingestion_status: str = "idle"
        self.chunks_indexed: int = 0
        self.client = None

    async def ensure_session(self) -> str:
        if self.session_id is not None:
            return self.session_id
        result = await self.api_client.create_session(
            role=self.current_role,
            model_slot=self.current_model_slot,
            mode=self.current_mode,
        )
        self.session_id = result["id"]
        return self.session_id

    def reset_session(self) -> None:
        self.session_id = None
        self.pending_gate = None
        self.ingestion_status = "idle"
        self.chunks_indexed = 0


_state: GuiState | None = None


def get_state() -> GuiState:
    global _state
    if _state is None:
        _state = GuiState()
    return _state


# ── SHELL PAGE ────────────────────────────────────────────────────────

@ui.page("/")
async def main_page() -> None:
    ui.page_title("AIP_Brain")
    state = get_state()
    state.client = context.client

    ui.add_head_html(
        f"<style>body,.q-page,.q-layout{{background:{C_GROUND}!important}}"
        f".q-tab__label{{font-size:11px;letter-spacing:.5px;font-family:{F_SANS}}}"
        f".q-tabs__arrow{{color:{C_INK60}}}</style>"
    )

    # ── TOPBAR ──────────────────────────────────────────────────────
    with ui.header().style(
        f"background:{C_GROUND};border-bottom:0.5px solid {C_INK40};"
        "padding:0 12px;min-height:40px;"
    ):
        with ui.row().classes("items-center w-full gap-1").style("height:40px"):
            ui.html(_AIP_MARK)
            ui.label("AIP").style(
                f"font-family:{F_SERIF};font-size:15px;font-weight:700;"
                f"color:{C_AMBER};letter-spacing:2px;margin-right:10px;"
            )

            tabs = ui.tabs(value="chat").props(
                "dense no-caps indicator-color=amber align=left"
            ).style(f"color:{C_MUTED};")
            with tabs:
                ui.tab("chat",      label="CHAT")
                ui.tab("augmented", label="AUGMENTED")
                ui.tab("cohort",    label="COHORT")
                ui.tab("review",    label="REVIEW")
                ui.tab("wiki",      label="WIKI")
                ui.tab("corpus",    label="CORPUS")
                ui.tab("graph",     label="GRAPH")
                ui.tab("status",    label="STATUS")

            ui.space()

            key_set = state.api_client.has_openrouter_api_key()
            ui.icon("vpn_key", size="xs").style(
                f"color:{'#4EAA7A' if key_set else '#E07070'};cursor:pointer;"
            ).tooltip("OpenRouter API key set" if key_set else "API key missing — click to set")
            ui.icon("settings", size="xs").style(
                f"color:{C_INK60};cursor:pointer;"
            ).tooltip("Model catalog & settings")

    # ── CONTENT PANELS ──────────────────────────────────────────────
    with ui.tab_panels(tabs, value="chat").classes("w-full").style(
        f"flex:1;background:{C_GROUND};min-height:0;"
    ):
        with ui.tab_panel("chat"):
            ui.label("CHAT — wired in stage 0B").style(
                f"color:{C_MUTED};padding:24px;font-family:{F_MONO};font-size:12px;"
            )
        with ui.tab_panel("augmented"):
            ui.label("AUGMENTED — wired in stage 0B").style(
                f"color:{C_MUTED};padding:24px;font-family:{F_MONO};font-size:12px;"
            )
        with ui.tab_panel("cohort"):
            ui.label("COHORT — tier 9 scaffold").style(
                f"color:{C_MUTED};padding:24px;font-family:{F_MONO};font-size:12px;"
            )
        with ui.tab_panel("review"):
            ui.label("REVIEW — wired in stage 0C").style(
                f"color:{C_MUTED};padding:24px;font-family:{F_MONO};font-size:12px;"
            )
        with ui.tab_panel("wiki"):
            ui.label("WIKI — wired in stage 0C").style(
                f"color:{C_MUTED};padding:24px;font-family:{F_MONO};font-size:12px;"
            )
        with ui.tab_panel("corpus"):
            ui.label("CORPUS — wired in stage 0C").style(
                f"color:{C_MUTED};padding:24px;font-family:{F_MONO};font-size:12px;"
            )
        with ui.tab_panel("graph"):
            ui.label("GRAPH — wired in stage 0C").style(
                f"color:{C_MUTED};padding:24px;font-family:{F_MONO};font-size:12px;"
            )
        with ui.tab_panel("status"):
            ui.label("STATUS — wired in stage 0C").style(
                f"color:{C_MUTED};padding:24px;font-family:{F_MONO};font-size:12px;"
            )

    # ── STATUS BAR ──────────────────────────────────────────────────
    with ui.footer().style(
        f"background:{C_GROUND};border-top:0.5px solid {C_INK40};"
        "padding:3px 12px;min-height:26px;"
    ):
        with ui.row().classes("w-full items-center gap-3"):
            ui.html(
                f'<span style="display:inline-block;width:6px;height:6px;'
                f'border-radius:50%;background:{C_INK60};"></span>'
            )
            _status = ui.label("initialising...").style(
                f"color:#555;font-size:10px;font-family:{F_MONO};"
            )
            ui.space()
            ui.label("aip_brain · AIP v0.1").style(
                f"color:{C_INK60};font-size:10px;font-family:{F_MONO};"
            )


ui.run(title="AIP_Brain", port=8082, reload=True)
