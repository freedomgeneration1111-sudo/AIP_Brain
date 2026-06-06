"""AIP_Brain GUI — Fixed Shell (Tier 0).

Dev entry point:  python -m gui.shell  (port 8082, runs alongside main.py)
Final entry point after Stage 0D: replaces gui/main.py on port 8080.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from nicegui import app, context, ui

from gui.api_client import AipApiClient, get_api_client

log = logging.getLogger("gui.shell")

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# ── AIP DESIGN TOKENS  (aip_design_reference.html §2) ────────────────
# Ground layers
C_GROUND = "#0E0E0F"  # page/shell background
C_SURFACE = "#1A1D1F"  # cards, panels
C_RAISED = "#242829"  # hover states, inputs
# Structural ink (slate-teal family)
C_INK40 = "#2A3540"  # borders, edges, lines
C_INK60 = "#3D5566"  # labels, inactive text, secondary
C_MUTED = "#8FA8B8"  # body text secondary, placeholder
C_CREAM = "#F2EDE4"  # primary text on dark
# Activation (amber — use ONLY for: active tab, primary CTA, corpus node)
C_AMBER = "#B8935A"  # primary amber
C_AMBER_P = "#8C6E3A"  # pressed/hover state
# Semantic states
C_OK_BG = "#1E3A2F"  # confirmed/approved background
C_OK_FG = "#4EAA7A"  # confirmed/approved text
C_ERR_BG = "#3A1E1E"  # danger background
C_ERR_FG = "#E07070"  # danger text
C_WARN_BG = "#2A2A1A"  # caution/pending background
C_WARN_FG = "#C8A84E"  # caution/pending text
# Typography
F_SERIF = "Georgia, 'Times New Roman', serif"
F_SANS = "'Helvetica Neue', Helvetica, Arial, sans-serif"
F_MONO = "'Courier New', monospace"
# Spacing (px values as strings for style() calls)
SP_XS = "4px"
SP_SM = "8px"
SP_MD = "16px"
SP_LG = "32px"
# Border
BORDER = f"0.5px solid {C_INK40}"
# Radius
R_SM = "4px"
R_MD = "6px"
R_LG = "8px"
# Stat tile background
C_STAT_BG = "#141618"
C_STAT_BD = "#1E2428"
# Status pill colors (per aip_design_reference.html §4.4)
_PILL_STYLES = {
    "GENERATED": (C_AMBER, C_AMBER_P, "#1A1200"),
    "APPROVED": (C_OK_FG, "#1E4030", "#0E1F17"),
    "REJECTED": (C_ERR_FG, C_ERR_BG, "#1A0000"),
    "IDLE": (C_MUTED, C_INK40, "transparent"),
    "ACTIVE": (C_AMBER, C_AMBER_P, "#1A0E00"),
    "READY": (C_OK_FG, "#1E4030", "#0E1F17"),
    "UNCONFIGURED": (C_MUTED, C_INK40, "transparent"),
}


# ── BUTTON STYLE HELPERS  (aip_design_reference.html §4.5) ──────────
def btn_primary() -> str:
    return (
        f"background:{C_AMBER}; color:#0E0800; border:0.5px solid {C_AMBER}; "
        f"padding:6px 14px; border-radius:{R_SM}; font-size:11px; font-weight:500;"
    )


def btn_secondary() -> str:
    return (
        f"background:transparent; color:{C_MUTED}; border:0.5px solid {C_INK40}; "
        f"padding:6px 14px; border-radius:{R_SM}; font-size:11px; font-weight:500;"
    )


def btn_ghost() -> str:
    return (
        f"background:transparent; color:{C_INK60}; border:0.5px solid {C_STAT_BD}; "
        f"padding:6px 14px; border-radius:{R_SM}; font-size:10px;"
    )


# ── STATUS PILL HELPER  (aip_design_reference.html §4.4) ────────────
def status_pill(status: str) -> None:
    """Render an inline status pill with correct semantic colors."""
    fg, border_c, bg = _PILL_STYLES.get(status.upper(), (C_MUTED, C_INK40, "transparent"))
    ui.label(status).style(
        f"display:inline-flex; align-items:center; font-size:10px; "
        f"letter-spacing:.5px; padding:3px 8px; border-radius:{R_SM}; "
        f"border:0.5px solid {border_c}; color:{fg}; background:{bg}; "
        f"flex-shrink:0;"
    )


# ── AIP CORPUS MARK  (aip_design_reference.html §1 — canonical 24×24) ──
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
    "<!-- Center corpus node (amber, weighted 2× the peripheral nodes) -->"
    '<circle cx="12" cy="12" r="3"   fill="#B8935A"/>'
    "</svg>"
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
        self.current_project: str | None = None  # resolved from GET /api/v1/projects
        self.client = None
        # Epistemic flags — shared between Settings and Chat panels
        self.epistemic_flags: dict[str, bool] = {
            "no_flattery": True,
            "flag_uncertainty": True,
            "suggest_validation": True,
            "report_conflicts": True,
        }
        # Beast scan history — shared with Beast pop-out page
        self.beast_scan_history: list[dict[str, Any]] = []  # max 10
        # Last beast comparison result
        self.beast_last_comparison: dict[str, Any] = {}

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


# ── PERSISTENCE & MODULE STATE ────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SLOT_MODELS_FILE = _PROJECT_ROOT / "config" / "slot_models.json"
_SELECTED_MODELS_FILE = _PROJECT_ROOT / "config" / "selected_models.json"


def _load_slot_models() -> dict[str, str]:
    """Load persisted slot→model mapping from config/slot_models.json."""
    try:
        if _SLOT_MODELS_FILE.exists():
            d = json.loads(_SLOT_MODELS_FILE.read_text())
            if isinstance(d, dict):
                return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str)}
    except Exception:
        pass
    return {}


def _save_slot_models() -> None:
    """Persist current _role_models to config/slot_models.json."""
    try:
        _SLOT_MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SLOT_MODELS_FILE.write_text(json.dumps(_role_models, indent=2))
        log.debug("slot models persisted: %s", _role_models)
    except Exception as exc:
        log.warning("failed to persist slot models: %s", exc)


def _load_sel() -> list[str]:
    try:
        if _SELECTED_MODELS_FILE.exists():
            d = json.loads(_SELECTED_MODELS_FILE.read_text())
            return d if isinstance(d, list) else []
    except Exception:
        pass
    return []


def _save_sel(models: list[str]) -> None:
    try:
        _SELECTED_MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SELECTED_MODELS_FILE.write_text(json.dumps(models, indent=2))
    except Exception:
        pass


# Load persisted slot assignments on module import
_role_models: dict[str, str] = _load_slot_models()
# Ensure standard slots exist even if not in saved file
for _slot in ("synthesis", "evaluation", "sexton", "embedding", "beast", "vigil"):
    _role_models.setdefault(_slot, "")

_selected_models: list[str] = _load_sel()


def get_selected_models() -> list[str]:
    return _selected_models


def set_selected_models(models: list[str]) -> None:
    global _selected_models
    _selected_models = models
    _save_sel(models)


def get_role_model(slot: str) -> str:
    return _role_models.get(slot, "")


def set_role_model(slot: str, model: str) -> None:
    """Set a model for a slot and persist immediately."""
    _role_models[slot] = model
    _save_slot_models()


# ── BACKEND & MODEL HELPERS ───────────────────────────────────────────


async def check_backend_health(state: GuiState) -> str:
    try:
        h = await asyncio.wait_for(state.api_client.check_health(), timeout=4.0)
        state.backend_reachable = True
        return "backend OK · " + ", ".join(h.get("model_slots", []))
    except asyncio.TimeoutError:
        state.backend_reachable = False
        return "backend timeout (>4s)"
    except Exception:
        state.backend_reachable = False
        return "backend unreachable"


async def check_backend() -> None:
    """Non-blocking background health check — registered via app.on_startup.

    Runs in the event loop without blocking NiceGUI page rendering.
    Retries up to 10 times with 3-second intervals.
    """
    import httpx

    state = get_state()
    for _ in range(10):
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    "http://127.0.0.1:8000/api/v1/health",
                    timeout=2,
                )
                if r.status_code == 200:
                    state.backend_reachable = True
                    log.info("backend health check: OK")
                    return
        except Exception:
            pass
        await asyncio.sleep(3)
    state.backend_reachable = False
    log.warning("backend health check: unreachable after 10 attempts")


async def load_model_slots(state: GuiState) -> list:
    try:
        slots = await state.api_client.list_model_slots()
        state.available_slots = slots
        return slots
    except Exception:
        return []


def build_model_options(slots: list) -> list[str]:
    sel = get_selected_models()
    # Include persisted slot model assignments so they appear even before backend connects
    persisted = [m for m in _role_models.values() if m and not m.startswith("<")]
    backend = [s.get("model", "") for s in slots if s.get("model") and not s.get("model", "").startswith("<")]
    opts = list(dict.fromkeys(sel + persisted + backend + ["google/gemma-3-4b-it"]))
    return [m for m in opts if m] or ["(no models — open Settings)"]


def on_chat_model_changed(model_id: str) -> None:
    state = get_state()
    state.current_role = None
    set_role_model("synthesis", model_id)  # persists to slot_models.json
    # Also track in selected models list for build_model_options
    if model_id not in _selected_models:
        _selected_models.insert(0, model_id)
        _save_sel(_selected_models)
    state.reset_session()
    asyncio.create_task(
        state.api_client.update_slot_model("synthesis", model_id, api_key=state.api_client.get_openrouter_api_key())
    )
    ui.notify(f"Chat model → {model_id}", color="info")


async def refresh_budget_status(label, state: GuiState) -> None:
    while True:
        try:
            b = await state.api_client.get_budget_status(scope="session", scope_id="default")
            consumed = b.get("consumed_tokens", 0)
            limit = b.get("limit", 0)
            fraction = b.get("fraction_used", 0)

            def _u():
                if limit:
                    label.text = f"budget: {consumed}/{limit} ({fraction:.0%})"
                    if fraction >= 0.8:
                        label.style(f"color:{C_ERR_FG};font-size:10px;font-family:{F_MONO};")
                elif b.get("budget_manager") is False:
                    label.text = "budget: n/a"

            if state.client:
                with state.client:
                    _u()
            else:
                _u()
        except Exception:
            pass
        await asyncio.sleep(30)


async def _show_api_key_prompt() -> str | None:
    with ui.dialog().props("persistent") as dlg, ui.card().classes("p-6 min-w-[420px]"):
        ui.label("OpenRouter API Key Required").classes("text-h6")
        ui.label("AIP_Brain uses OpenRouter for all model slots.").classes("text-body2 q-mt-sm")
        inp = ui.input(placeholder="sk-or-v1-...", password=True).props("outlined dense").classes("w-full q-mt-md")
        with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
            ui.button("Skip", color="grey", on_click=lambda: dlg.submit(None))
            ui.button("Save Key", color="primary", on_click=lambda: dlg.submit(inp.value.strip()))
    return await dlg


# ── STATUS PANEL ─────────────────────────────────────────────────────


def _build_status_panel(state: GuiState) -> None:
    """STATUS tab — health, actors, slots, wiki/corpus stats."""
    import datetime as _dt

    with (
        ui.row()
        .classes("w-full items-center px-4 py-2 gap-2")
        .style(f"background:{C_SURFACE};border-bottom:.5px solid {C_INK40};")
    ):
        ui.label("SYSTEM STATUS").style(f"font-size:11px;font-weight:600;letter-spacing:1px;color:{C_AMBER};")
        ui.space()
        refresh_btn = ui.button("Refresh", icon="refresh").props("dense flat").style(f"color:{C_MUTED};font-size:11px;")

    content = ui.column().classes("w-full px-4 py-3").style(f"flex:1;overflow-y:auto;background:{C_GROUND};gap:0;")

    def _section(title: str) -> None:
        ui.label(title).style(
            f"font-size:9px;font-weight:500;letter-spacing:2px;"
            f"color:{C_MUTED};text-transform:uppercase;margin-top:12px;margin-bottom:4px;"
        )

    def _kv(label: str, val: str, vc: str = "") -> None:
        with ui.row().classes("w-full items-baseline").style("padding:1px 0;"):
            ui.label(label).style(f"font-size:11px;font-family:{F_MONO};color:{C_MUTED};min-width:240px;")
            ui.label(val).style(f"font-size:11px;font-family:{F_MONO};color:{vc or C_CREAM};")

    def _sep() -> None:
        ui.separator().style(f"background:{C_INK40};margin:4px 0;")

    async def _load() -> None:
        content.clear()
        with content:
            ui.label("Loading…").style(f"color:{C_MUTED};font-size:11px;font-family:{F_MONO};")

        results = await asyncio.gather(
            state.api_client.check_health(),
            state.api_client.get_actors_status(),
            state.api_client.list_model_slots(),
            state.api_client.list_wiki_articles(),
            state.api_client.get_graph_stats(),
            state.api_client.get_corpus_stats(),
            return_exceptions=True,
        )
        health = results[0] if not isinstance(results[0], Exception) else {}
        actors_r = results[1] if not isinstance(results[1], Exception) else {}
        slots_list = results[2] if not isinstance(results[2], Exception) else []
        know_r = results[3] if not isinstance(results[3], Exception) else {}
        graph_r = results[4] if not isinstance(results[4], Exception) else {}
        corpus_r = results[5] if not isinstance(results[5], Exception) else {}

        items = know_r.get("items", []) if isinstance(know_r, dict) else []
        approved = sum(1 for k in items if k.get("state") == "APPROVED")
        generated = sum(1 for k in items if k.get("state") == "GENERATED")
        domains_with_wiki = len({k.get("domain", "") for k in items if k.get("domain")})

        actors = actors_r.get("actors", {}) if isinstance(actors_r, dict) else {}
        slot_map = {s.get("slot_name"): s for s in (slots_list or [])}

        # Corpus stats from API (uses primary_domain, not tagging_version)
        total_turns = corpus_r.get("total_turns", 0) if isinstance(corpus_r, dict) else 0
        tagged = corpus_r.get("tagged", 0) if isinstance(corpus_r, dict) else 0
        untagged = corpus_r.get("untagged", 0) if isinstance(corpus_r, dict) else 0
        embedded = corpus_r.get("embedded", 0) if isinstance(corpus_r, dict) else 0
        pct = f"  ({100 * tagged / total_turns:.1f}%)" if total_turns > 0 else ""

        content.clear()
        with content:
            _section("CORPUS")
            _kv("Total turns:", f"{total_turns:,}")
            _kv("Tagged:", f"{tagged:,}{pct}")
            _kv("Untagged:", f"{untagged:,}")
            _kv("Embedded:", str(embedded))
            _sep()

            _section("WIKI")
            _kv("APPROVED articles:", str(approved))
            _kv("GENERATED (pending):", str(generated), vc=C_WARN_FG if generated > 0 else C_CREAM)
            _kv("Domains with wiki:", str(domains_with_wiki))
            _sep()

            _section("KNOWLEDGE GRAPH")
            _kv("Entities:", str(graph_r.get("nodes", "—")))
            _kv("Edges:", str(graph_r.get("edges", "—")))
            _sep()

            # Actor role labels per ADR-011
            _actor_roles = {
                "beast": "Active during augmented chat sessions",
                "sexton": "Background maintenance (vigil cycle)",
                "vigil": "Quality evaluation (hourly)",
            }
            _section("ACTOR SLOTS")
            # "vigil" is displayed but the model slot is named "evaluation" in ModelSlotResolver
            _slot_key = {"vigil": "evaluation"}
            for sn in ["synthesis", "beast", "vigil", "sexton", "embedding"]:
                s = slot_map.get(_slot_key.get(sn, sn), {})
                model = s.get("model") or "[not configured]"
                configured = model and not model.startswith("<") and model != "[not configured]"
                dot_c = C_OK_FG if configured else C_MUTED
                status = "READY" if configured else "UNCONFIGURED"
                sc = C_OK_FG if configured else C_MUTED
                role_label = _actor_roles.get(sn, "")
                display_name = f"{sn}" if not role_label else f"{sn}  ({role_label})"
                with ui.row().classes("w-full items-center gap-2").style("padding:1px 0;"):
                    ui.html(f'<span style="font-size:10px;color:{dot_c};">{"●" if configured else "○"}</span>')
                    ui.label(display_name).style(f"font-size:11px;font-family:{F_MONO};color:{C_CREAM};min-width:90px;")
                    ui.label((model[:36] + "…") if len(model) > 36 else model).style(
                        f"font-size:11px;font-family:{F_MONO};color:{C_MUTED};flex:1;"
                    )
                    ui.label(status).style(f"font-size:10px;font-family:{F_MONO};color:{sc};")
            _sep()

            _section("RECENT ACTOR ACTIVITY")
            for aname, ainfo in actors.items():
                if not isinstance(ainfo, dict):
                    continue
                last_ts = ainfo.get("last_cycle_time")
                interval = ainfo.get("interval_seconds", "—")
                if last_ts:
                    try:
                        ts = _dt.datetime.fromtimestamp(last_ts).strftime("%H:%M")
                    except Exception:
                        ts = "—"
                else:
                    ts = "never"
                _kv(
                    f"{aname.upper()}:",
                    f"{ts}  ·  interval {interval}s",
                )
            _sep()

            _section("OVERALL")
            h = health if isinstance(health, dict) else {}
            st = h.get("status", "—")
            _kv("Status:", st, vc=C_OK_FG if st == "ok" else C_ERR_FG)
            _kv("Uptime:", f"{h.get('uptime_seconds', 0):.0f}s")
            _kv("DB writable:", str(h.get("db_writable", "—")))
            _kv("Budget:", h.get("budget_status", "—"))

    refresh_btn.on("click", lambda: asyncio.create_task(_load()))
    asyncio.create_task(_load())


# ── SETTINGS PANEL ───────────────────────────────────────────────────


def _build_settings_panel(state: GuiState) -> None:
    """SETTINGS tab — DEFINER profile editor and epistemic flags."""
    with (
        ui.column()
        .classes("w-full")
        .style(f"flex:1;overflow-y:auto;background:{C_GROUND};padding:24px 32px;gap:24px;")
    ):

        # ── DEFINER PROFILE section ──
        ui.label("DEFINER PROFILE").style(
            f"font-size:13px;font-weight:700;letter-spacing:1px;color:{C_AMBER};"
        )
        ui.label(
            "Edit the DEFINER profile injected into augmented chat system prompts. "
            "Changes take effect on next request (5-min cache)."
        ).style(f"font-size:11px;color:{C_MUTED};line-height:1.4;")

        profile_textarea = ui.textarea(
            value="Loading...",
        ).props("outlined rows=12").classes("w-full").style(
            f"font-size:12px;font-family:{F_MONO};"
            f"background:{C_RAISED};color:{C_CREAM};"
        )

        save_profile_btn = ui.button("Save Profile").props("unelevated dense").style(
            f"background:{C_AMBER};color:#000;font-weight:600;font-size:12px;"
            f"padding:6px 16px;border-radius:4px;"
        )

        profile_status = ui.label("").style(
            f"font-size:11px;color:{C_MUTED};font-family:{F_MONO};"
        )

        async def _load_profile() -> None:
            r = await state.api_client.get_definer_profile()
            profile_textarea.value = r.get("content", "")
            if r.get("missing"):
                profile_status.set_text(
                    f"File not found: {r.get('path', '?')} — Save to create it"
                )
                profile_status.style(f"color:{C_WARN_FG};")
            else:
                profile_status.set_text(f"Path: {r.get('path', '?')}")
                profile_status.style(f"color:{C_MUTED};")

        async def _save_profile() -> None:
            content = profile_textarea.value
            r = await state.api_client.save_definer_profile(content)
            if r.get("ok"):
                ui.notify(
                    "Profile saved — takes effect on next request",
                    color="positive",
                    timeout=4000,
                )
                profile_status.set_text(f"Saved to: {r.get('path', '?')}")
                profile_status.style(f"color:{C_OK_FG};")
            else:
                ui.notify(
                    f"Save failed: {r.get('error', 'unknown error')}",
                    color="negative",
                    timeout=6000,
                )
                profile_status.set_text(f"Save failed: {r.get('error', '?')}")
                profile_status.style(f"color:{C_ERR_FG};")

        save_profile_btn.on("click", lambda: asyncio.create_task(_save_profile()))
        asyncio.create_task(_load_profile())

        ui.separator().style(f"background:{C_INK40};")

        # ── EPISTEMIC FLAGS section ──
        ui.label("EPISTEMIC FLAGS").style(
            f"font-size:13px;font-weight:700;letter-spacing:1px;color:{C_AMBER};"
        )
        ui.label(
            "Control which epistemic guardrails are active. Unchecking a flag "
            "removes that sentence from Engineering mode; for other modes, "
            "checking a flag adds the sentence."
        ).style(f"font-size:11px;color:{C_MUTED};line-height:1.4;")

        flag_no_flattery = ui.checkbox(
            "No flattery — suppress sycophantic preambles",
            value=True,
            on_change=lambda e: _update_flag("no_flattery", e.value),
        ).style(f"color:{C_CREAM};font-size:12px;")
        flag_uncertainty = ui.checkbox(
            "Flag uncertainty — require explicit confidence statements",
            value=True,
            on_change=lambda e: _update_flag("flag_uncertainty", e.value),
        ).style(f"color:{C_CREAM};font-size:12px;")
        flag_validation = ui.checkbox(
            "Suggest validation — prompt model to note what needs empirical confirmation",
            value=True,
            on_change=lambda e: _update_flag("suggest_validation", e.value),
        ).style(f"color:{C_CREAM};font-size:12px;")
        flag_conflicts = ui.checkbox(
            "Report conflicts — surface conflicting evidence rather than resolving artificially",
            value=True,
            on_change=lambda e: _update_flag("report_conflicts", e.value),
        ).style(f"color:{C_CREAM};font-size:12px;")

        def _update_flag(key: str, val: bool) -> None:
            """Update live flag state in GuiState (UI IS the live state)."""
            state.epistemic_flags[key] = val

        flag_status = ui.label("").style(
            f"font-size:11px;color:{C_MUTED};font-family:{F_MONO};"
        )

        async def _load_flags() -> None:
            r = await state.api_client.get_epistemic_flags()
            flags = r.get("flags", {})
            flag_no_flattery.value = flags.get("no_flattery", True)
            flag_uncertainty.value = flags.get("flag_uncertainty", True)
            flag_validation.value = flags.get("suggest_validation", True)
            flag_conflicts.value = flags.get("report_conflicts", True)
            # Sync live state
            state.epistemic_flags.update(flags)
            src = r.get("source", "defaults")
            flag_status.set_text(f"Source: {src}")

        async def _save_flags() -> None:
            r = await state.api_client.save_epistemic_flags(
                no_flattery=flag_no_flattery.value,
                flag_uncertainty=flag_uncertainty.value,
                suggest_validation=flag_validation.value,
                report_conflicts=flag_conflicts.value,
            )
            if r.get("ok"):
                ui.notify("Flags saved", color="positive", timeout=2000)
                flag_status.set_text("Saved")
                flag_status.style(f"color:{C_OK_FG};")
            else:
                ui.notify(
                    f"Save failed: {r.get('error', '?')}",
                    color="negative",
                    timeout=4000,
                )

        save_flags_btn = ui.button("Save Flags").props("unelevated dense").style(
            f"background:{C_AMBER};color:#000;font-weight:600;font-size:12px;"
            f"padding:6px 16px;border-radius:4px;"
        )
        save_flags_btn.on("click", lambda: asyncio.create_task(_save_flags()))
        asyncio.create_task(_load_flags())


# ── REVIEW PANEL ─────────────────────────────────────────────────────


def _build_review_panel(state: GuiState) -> None:
    """REVIEW tab — beast_wiki artifacts in GENERATED state, approve/reject."""

    with (
        ui.row()
        .classes("w-full items-center px-4 py-2 gap-2")
        .style(f"background:{C_SURFACE};border-bottom:.5px solid {C_INK40};")
    ):
        ui.label("REVIEW").style(f"font-size:11px;font-weight:600;letter-spacing:1px;color:{C_AMBER};")
        count_lbl = ui.label("").style(
            f"font-size:10px;background:#1A1200;color:{C_AMBER};"
            f"padding:1px 6px;border-radius:3px;font-family:{F_MONO};"
            f"border:0.5px solid {C_AMBER_P};"
        )
        ui.space()
        approve_all_btn = (
            ui.button("APPROVE ALL", icon="done_all").props("dense flat").style(f"color:{C_OK_FG};font-size:11px;")
        )
        refresh_btn = ui.button("Refresh", icon="refresh").props("dense flat").style(f"color:{C_MUTED};font-size:11px;")

    content = (
        ui.column()
        .classes("w-full px-4 py-3 gap-3")
        .style(f"flex:1;overflow-y:auto;background:{C_GROUND};min-height:0;")
    )

    async def _approve_all() -> None:
        try:
            result = await state.api_client.approve_all_reviews()
            count = result.get("approved", 0)
            ui.notify(f"Approved {count} artifacts", color="positive")
            asyncio.create_task(_load())
        except Exception as exc:
            ui.notify(f"Approve all failed: {exc}", color="negative")

    async def _load() -> None:
        content.clear()
        count_lbl.text = ""
        with content:
            ui.label("Loading…").style(f"color:{C_MUTED};font-size:11px;font-family:{F_MONO};")

        reviews, knowledge = await asyncio.gather(
            state.api_client.list_pending_reviews(),
            state.api_client.list_knowledge(state="GENERATED"),
            return_exceptions=True,
        )
        review_items = reviews if isinstance(reviews, list) else []
        know_items = knowledge.get("items", []) if isinstance(knowledge, dict) else []

        # Merge: reviews queue + knowledge with GENERATED state
        seen_ids: set[str] = set()
        cards: list[dict] = []
        for r in review_items:
            rid = r.get("artifact_id") or r.get("id", "")
            seen_ids.add(rid)
            cards.append({"id": rid, "source": "review_queue", **r})
        for k in know_items:
            kid = k.get("knowledge_id") or k.get("id", "")
            if kid not in seen_ids:
                cards.append(
                    {
                        "id": kid,
                        "source": "knowledge",
                        "domain": k.get("domain", ""),
                        "state": k.get("state", ""),
                        "content": k.get("content", ""),
                        "artifact_type": "wiki",
                        **k,
                    }
                )

        count_lbl.text = f"{len(cards)} pending" if cards else ""

        content.clear()
        if not cards:
            with content:
                ui.label("No items pending review.").style(f"color:{C_MUTED};font-size:12px;padding:24px;")
            return

        for card in cards:
            _render_review_card(content, card, state, _load)

    def _render_review_card(parent, card: dict, state: GuiState, reload_fn) -> None:
        artifact_id = card.get("id", "")
        domain = card.get("domain", "—")
        artifact_type = card.get("artifact_type", card.get("type", "artifact"))
        content_text = card.get("content", "")
        preview = (content_text[:300] + "…") if len(content_text) > 300 else content_text

        expanded_state: list[bool] = [False]

        with parent:
            with (
                ui.card()
                .classes("w-full")
                .style(f"background:{C_SURFACE};border:0.5px solid {C_RAISED};border-radius:{R_LG};padding:0;")
            ):
                # Header bar
                with (
                    ui.row()
                    .classes("w-full items-center px-3 py-2 gap-2")
                    .style(f"border-bottom:0.5px solid {C_INK40};")
                ):
                    ui.label(f"[{domain.upper()}]").style(f"font-size:10px;font-family:{F_MONO};color:{C_AMBER};")
                    uid_short = artifact_id[:20] + "…" if len(artifact_id) > 20 else artifact_id
                    ui.label(uid_short).style(f"font-size:10px;font-family:{F_MONO};color:{C_MUTED};")
                    ui.space()
                    ui.label(artifact_type).style(
                        f"font-size:9px;background:{C_INK40};color:{C_MUTED};padding:1px 5px;border-radius:3px;"
                    )

                # Preview content
                preview_col = ui.column().classes("w-full px-3 py-2")
                with preview_col:
                    ui.label(preview or "(empty)").style(f"font-size:12px;color:{C_CREAM};line-height:1.5;")

                # Expanded content (hidden by default)
                full_col = ui.column().classes("w-full px-3 py-2").style("display:none;")
                with full_col:
                    ui.markdown(content_text or "(no content)").style(
                        f"font-size:12px;color:{C_CREAM};line-height:1.6;"
                    )

                # Action bar
                with (
                    ui.row().classes("w-full items-center px-3 py-2 gap-2").style(f"border-top:0.5px solid {C_INK40};")
                ):
                    approve_btn = ui.button("APPROVE").style(btn_primary()).props("dense flat")
                    reject_btn = ui.button("REJECT").style(btn_secondary()).props("dense flat")
                    ui.space()
                    expand_btn = ui.button("EXPAND ↓").props("dense flat").style(btn_ghost())

                async def _approve(aid: str = artifact_id) -> None:
                    try:
                        await state.api_client.approve_review(aid)
                        ui.notify(f"Approved: {aid[:16]}…", color="positive")
                        asyncio.create_task(reload_fn())
                    except Exception as exc:
                        ui.notify(f"Approve failed: {exc}", color="negative")

                async def _reject(aid: str = artifact_id) -> None:
                    try:
                        await state.api_client.reject_review(aid)
                        ui.notify(f"Rejected: {aid[:16]}…", color="warning")
                        asyncio.create_task(reload_fn())
                    except Exception as exc:
                        ui.notify(f"Reject failed: {exc}", color="negative")

                def _expand(fc=full_col, pc=preview_col, es=expanded_state, btn=expand_btn) -> None:
                    es[0] = not es[0]
                    if es[0]:
                        fc.style("display:block;")
                        pc.style("display:none;")
                        btn.text = "COLLAPSE ↑"
                    else:
                        fc.style("display:none;")
                        pc.style("display:block;")
                        btn.text = "EXPAND ↓"

                approve_btn.on("click", lambda: asyncio.create_task(_approve()))
                reject_btn.on("click", lambda: asyncio.create_task(_reject()))
                expand_btn.on("click", _expand)

    approve_all_btn.on("click", lambda: asyncio.create_task(_approve_all()))
    refresh_btn.on("click", lambda: asyncio.create_task(_load()))
    asyncio.create_task(_load())


# ── CORPUS PANEL ─────────────────────────────────────────────────────


def _build_corpus_panel(state: GuiState) -> None:
    """CORPUS tab — stats bar, domain distribution, source browser."""

    with (
        ui.row()
        .classes("w-full items-center px-4 py-2 gap-2")
        .style(f"background:{C_SURFACE};border-bottom:.5px solid {C_INK40};")
    ):
        ui.label("CORPUS").style(f"font-size:11px;font-weight:600;letter-spacing:1px;color:{C_AMBER};")
        ui.space()
        refresh_btn = ui.button("Refresh", icon="refresh").props("dense flat").style(f"color:{C_MUTED};font-size:11px;")

    # Stats bar
    stats_row = (
        ui.row()
        .classes("w-full items-center px-4 py-2 gap-4")
        .style(f"background:{C_SURFACE};border-bottom:.5px solid {C_INK40};")
    )

    content = (
        ui.column()
        .classes("w-full px-4 py-2 gap-3")
        .style(f"flex:1;overflow-y:auto;background:{C_GROUND};min-height:0;")
    )

    async def _load() -> None:
        stats_row.clear()
        content.clear()

        corpus_stats = await state.api_client.get_corpus_stats()
        if isinstance(corpus_stats, Exception):
            corpus_stats = {}

        total_turns = corpus_stats.get("total_turns", 0) if isinstance(corpus_stats, dict) else 0
        tagged = corpus_stats.get("tagged", 0) if isinstance(corpus_stats, dict) else 0
        untagged = corpus_stats.get("untagged", 0) if isinstance(corpus_stats, dict) else 0
        embedded = corpus_stats.get("embedded", 0) if isinstance(corpus_stats, dict) else 0
        domains = corpus_stats.get("domains", []) if isinstance(corpus_stats, dict) else []
        top_turns = corpus_stats.get("top_turns", []) if isinstance(corpus_stats, dict) else []

        def _stat(label: str, val: str) -> None:
            with (
                ui.row()
                .classes("items-baseline gap-1")
                .style(f"background:{C_STAT_BG};border:0.5px solid {C_STAT_BD};border-radius:{R_MD};padding:6px 10px;")
            ):
                ui.label(val).style(f"font-size:18px;font-weight:500;color:{C_CREAM};letter-spacing:-0.5px;")
                ui.label(label).style(f"font-size:9px;letter-spacing:1.5px;color:{C_MUTED};text-transform:uppercase;")

        with stats_row:
            _stat("turns", f"{total_turns:,}")
            ui.label("·").style(f"color:{C_INK60};")
            _stat("tagged", str(tagged))
            ui.label("·").style(f"color:{C_INK60};")
            _stat("untagged", str(untagged))
            ui.label("·").style(f"color:{C_INK60};")
            _stat("embedded", str(embedded))

        # Build domain data from corpus stats (primary source of truth)
        by_domain: dict[str, int] = {d["name"]: d["count"] for d in domains if isinstance(d, dict)}

        with content:
            if not domains and not top_turns:
                ui.label("No corpus turns indexed yet. Run: aip ingest file <path>").style(
                    f"color:{C_MUTED};font-size:12px;padding:16px;"
                )
                return

            # Domain distribution table
            ui.label("DOMAIN DISTRIBUTION").style(
                f"font-size:10px;font-weight:700;letter-spacing:2px;color:{C_MUTED};margin-bottom:4px;"
            )
            with (
                ui.element("table")
                .classes("w-full")
                .style(f"border-collapse:collapse;font-family:{F_MONO};font-size:11px;color:{C_MUTED};")
            ):
                with ui.element("thead"):
                    with ui.element("tr").style(f"color:{C_MUTED};border-bottom:0.5px solid {C_INK40};"):
                        for col in ["Domain", "Turns"]:
                            ui.element("th").style(
                                f"text-align:left;padding:3px 8px;font-weight:500;"
                                f"font-size:10px;letter-spacing:.5px;color:{C_MUTED};"
                            ).text = col
                with ui.element("tbody"):
                    for domain_name, count in sorted(by_domain.items(), key=lambda x: -x[1]):
                        with ui.element("tr").style(f"border-bottom:.5px solid {C_INK40};cursor:pointer;"):
                            ui.element("td").style(f"padding:3px 8px;color:{C_AMBER};").text = domain_name
                            ui.element("td").style(f"padding:3px 8px;color:{C_CREAM};").text = f"{count:,}"

            ui.separator().style(f"background:{C_INK40};margin:12px 0;")

            # Top turns by importance
            ui.label("TOP TURNS BY IMPORTANCE").style(
                f"font-size:10px;font-weight:700;letter-spacing:2px;color:{C_MUTED};margin-bottom:4px;"
            )
            for turn in top_turns[:10]:
                domain = turn.get("primary_domain", "—")
                source_model = turn.get("source_model", "—")
                user_text = turn.get("user_text", "")
                importance = turn.get("importance", 0)
                preview = (user_text[:100] + "…") if len(user_text) > 100 else user_text
                with (
                    ui.card()
                    .classes("w-full")
                    .style(
                        f"background:{C_SURFACE};border:0.5px solid {C_RAISED};"
                        f"border-radius:{R_MD};padding:8px 12px;margin-bottom:4px;"
                    )
                ):
                    with ui.row().classes("w-full items-center gap-2"):
                        ui.label(f"[{domain}]").style(f"font-size:10px;color:{C_AMBER};font-family:{F_MONO};")
                        ui.label(source_model).style(
                            f"font-size:10px;background:{C_INK40};color:{C_MUTED};padding:1px 5px;border-radius:3px;"
                        )
                        ui.space()
                        ui.label(f"importance: {importance:.2f}").style(
                            f"font-size:10px;color:{C_INK60};font-family:{F_MONO};"
                        )
                    if preview:
                        ui.label(preview).style(f"font-size:12px;color:{C_CREAM};margin-top:2px;")

    refresh_btn.on("click", lambda: asyncio.create_task(_load()))
    asyncio.create_task(_load())


# ── WIKI PANEL ───────────────────────────────────────────────────────


def _build_wiki_panel(state: GuiState) -> None:
    """WIKI tab — two-pane domain navigator + article reader."""

    with ui.row().classes("w-full").style(f"flex:1;min-height:0;background:{C_GROUND};"):
        # ── LEFT PANE — domain navigator ──
        with ui.column().style(
            f"width:260px;min-width:260px;background:{C_SURFACE};"
            f"border-right:0.5px solid {C_INK40};overflow-y:auto;padding:8px 0;"
        ):
            with ui.row().classes("w-full items-center px-3 py-2 gap-2"):
                ui.label("DOMAINS").style(f"font-size:10px;font-weight:700;letter-spacing:2px;color:{C_MUTED};")
                ui.space()
                refresh_btn = ui.button(icon="refresh").props("dense flat round").style(f"color:{C_MUTED};")

            search_inp = (
                ui.input(placeholder="search…")
                .props("dense borderless")
                .style(
                    f"font-size:11px;color:{C_CREAM};background:{C_RAISED};"
                    f"border:.5px solid {C_INK40};border-radius:3px;"
                    "padding:2px 8px;margin:0 8px;width:calc(100% - 16px);"
                )
            )

            domain_list = ui.column().classes("w-full").style("padding:4px 0;")

        # ── RIGHT PANE — article reader ──
        reader = ui.column().classes("flex-1 px-5 py-4").style(f"overflow-y:auto;background:{C_GROUND};min-height:0;")
        with reader:
            ui.label("Select a domain to view articles.").style(f"color:{C_MUTED};font-size:12px;")

    # State: all knowledge items, current domain filter
    _all_items: list[dict] = []
    _current_domain: list[str] = [""]

    def _render_domain_list(filter_text: str = "") -> None:
        domain_list.clear()
        by_domain: dict[str, list] = {}
        for item in _all_items:
            d = item.get("domain", "—") or "—"
            by_domain.setdefault(d, []).append(item)

        for domain, items in sorted(by_domain.items()):
            if filter_text and filter_text.lower() not in domain.lower():
                continue
            approved = sum(1 for i in items if i.get("state") == "APPROVED")
            pending = sum(1 for i in items if i.get("state") == "GENERATED")
            count_text = f"{len(items)} article{'s' if len(items) != 1 else ''}"
            badge = "✅" if approved == len(items) and len(items) > 0 else ("⏳" if pending > 0 else "")

            active = _current_domain[0] == domain
            bg = C_RAISED if active else "transparent"

            with domain_list:
                with (
                    ui.row()
                    .classes("w-full items-center px-3 py-2 gap-1")
                    .style(
                        f"cursor:pointer;background:{bg};"
                        f"border-left:2px solid {'#B8935A' if active else 'transparent'};"
                    )
                    .on("click", lambda d=domain: _show_domain(d))
                ):
                    ui.label(domain).style(
                        f"font-size:11px;color:{C_AMBER if active else C_CREAM};"
                        "flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                    )
                    ui.label(badge + " " + count_text).style(
                        f"font-size:10px;color:{C_MUTED};font-family:{F_MONO};white-space:nowrap;"
                    )

    def _show_domain(domain: str) -> None:
        _current_domain[0] = domain
        _render_domain_list()
        reader.clear()
        domain_items = [i for i in _all_items if i.get("domain") == domain]
        if not domain_items:
            with reader:
                ui.label(f"No articles for {domain}.").style(f"color:{C_MUTED};font-size:12px;")
            return
        with reader:
            ui.label(domain).style(
                f"font-family:{F_SERIF};font-size:20px;font-weight:700;color:{C_CREAM};margin-bottom:4px;"
            )
            for item in domain_items:
                _render_article_card(item)

    def _render_article_card(item: dict) -> None:
        kid = item.get("knowledge_id") or item.get("id", "")
        st = item.get("state", "")
        content_text = item.get("content", "") or ""
        created = item.get("created_at", "")[:10] if item.get("created_at") else "—"
        approved_at = item.get("approved_at", "")[:10] if item.get("approved_at") else "—"
        word_count = len(content_text.split()) if content_text else 0

        expanded_state: list[bool] = [False]

        with (
            ui.card()
            .classes("w-full")
            .style(
                f"background:{C_SURFACE};border:0.5px solid {C_RAISED};"
                f"border-radius:{R_LG};padding:0;margin-bottom:12px;"
            )
        ):
            with ui.row().classes("w-full items-center px-3 py-2 gap-2").style(f"border-bottom:.5px solid {C_INK40};"):
                st_color = C_OK_FG if st == "APPROVED" else C_WARN_FG
                ui.label(st or "—").style(
                    f"font-size:9px;background:{C_OK_BG if st == 'APPROVED' else C_WARN_BG};"
                    f"color:{st_color};padding:1px 6px;border-radius:3px;"
                    "font-family:{F_MONO};font-weight:700;"
                )
                uid_short = (kid[:24] + "…") if len(kid) > 24 else kid
                ui.label(uid_short).style(f"font-size:10px;color:{C_MUTED};font-family:{F_MONO};")
                ui.space()
                ui.label(f"{word_count} words").style(f"font-size:10px;color:{C_MUTED};font-family:{F_MONO};")

            preview_col = ui.column().classes("w-full px-3 py-2")
            with preview_col:
                preview = (content_text[:300] + "…") if len(content_text) > 300 else content_text
                ui.label(preview or "(empty)").style(f"font-size:12px;color:{C_CREAM};line-height:1.5;")

            full_col = ui.column().classes("w-full px-3 py-2").style("display:none;")
            with full_col:
                ui.markdown(content_text or "(no content)").style(f"font-size:13px;color:{C_CREAM};line-height:1.7;")

            with ui.row().classes("w-full items-center px-3 py-2 gap-2").style(f"border-top:.5px solid {C_INK40};"):
                ui.label(f"generated: {created}" + (f"  ·  approved: {approved_at}" if st == "APPROVED" else "")).style(
                    f"font-size:10px;color:{C_MUTED};font-family:{F_MONO};"
                )
                ui.space()
                expand_btn = ui.button("EXPAND ↓").props("dense flat").style(btn_ghost())

            def _toggle(fc=full_col, pc=preview_col, es=expanded_state, btn=expand_btn) -> None:
                es[0] = not es[0]
                if es[0]:
                    fc.style("display:block;")
                    pc.style("display:none;")
                    btn.text = "COLLAPSE ↑"
                else:
                    fc.style("display:none;")
                    pc.style("display:block;")
                    btn.text = "EXPAND ↓"

            expand_btn.on("click", _toggle)

    async def _load() -> None:
        domain_list.clear()
        reader.clear()
        _all_items.clear()
        _current_domain[0] = ""

        with domain_list:
            ui.label("Loading…").style(f"color:{C_MUTED};font-size:11px;font-family:{F_MONO};padding:8px;")

        result = await state.api_client.list_wiki_articles()
        if isinstance(result, dict):
            _all_items.extend(result.get("items", []))

        _render_domain_list()
        with reader:
            if _all_items:
                ui.label("Select a domain.").style(f"color:{C_MUTED};font-size:12px;")
            else:
                ui.label("No wiki articles yet.").style(f"color:{C_MUTED};font-size:12px;")

    search_inp.on("keyup", lambda e: _render_domain_list(search_inp.value))
    refresh_btn.on("click", lambda: asyncio.create_task(_load()))
    asyncio.create_task(_load())


# ── GRAPH PANEL ──────────────────────────────────────────────────────


def _build_graph_panel(state: GuiState) -> None:
    """GRAPH tab — launches the standalone Cytoscape graph-viz in a new browser tab.

    The /graph-viz page renders perfectly as a standalone page. NiceGUI iframe
    embedding collapses due to flex container height resolution. Simplest correct
    fix: open in new tab. Node detail panel retained for future direct integration.
    """
    _gv_url = f"{state.api_client.base_url.rstrip('/')}/graph-viz"

    with (
        ui.column()
        .classes("w-full items-center justify-center")
        .style(f"flex:1;background:{C_GROUND};gap:24px;padding:48px;")
    ):
        ui.icon("hub", size="64px").style(f"color:{C_AMBER};")
        ui.label("AIP Knowledge Graph").style(f"font-family:{F_SERIF};font-size:22px;font-weight:700;color:{C_CREAM};")

        async def _load_stats() -> None:
            try:
                stats = await state.api_client.get_graph_stats()
                node_count = stats.get("nodes", 0)
                edge_count = stats.get("edges", 0)
                nodes_by_type = stats.get("nodes_by_type", {})
                type_summary = "  ·  ".join(f"{v} {k}" for k, v in nodes_by_type.items())
                stat_label.set_text(f"{node_count} entities  ·  {edge_count} edges  ·  {type_summary}")
            except Exception:
                stat_label.set_text("Graph data unavailable")

        stat_label = ui.label("Loading…").style(f"font-size:12px;color:{C_MUTED};font-family:{F_MONO};")
        asyncio.create_task(_load_stats())

        ui.button(
            "Open Graph Visualization",
            icon="open_in_new",
            on_click=lambda: ui.navigate.to(_gv_url, new_tab=True),
        ).props("unelevated").style(
            f"background:{C_AMBER};color:#000;font-weight:600;font-size:13px;padding:10px 28px;border-radius:6px;"
        )

        ui.label("Opens in a new tab · Cytoscape.js · filterable by domain, type, confidence").style(
            f"font-size:11px;color:{C_INK60};font-family:{F_MONO};"
        ).style("margin-top:-12px;")


# ── COHORT PALETTE ────────────────────────────────────────────────────

_COHORT_PALETTE: dict[str, str] = {
    "claude": "#C8956A",
    "deepseek": "#5B8DEF",
    "openai": "#6DCB5A",
    "gemini": "#7C6AE8",
    "grok": "#E87040",
    # Fallback for models not in known tags
    "default": "#B8935A",
}


def _model_accent_color(model_id: str) -> str:
    """Return a per-model accent color based on the model_id's tag.

    Checks known provider tags in _COHORT_PALETTE; falls back to
    'default' (C_AMBER) if no tag matches.
    """
    mid_lower = model_id.lower()
    for tag, color in _COHORT_PALETTE.items():
        if tag == "default":
            continue
        if tag in mid_lower:
            return color
    return _COHORT_PALETTE["default"]


# ── UNIFIED CHAT PANEL ──────────────────────────────────────────────────


def _build_unified_chat_panel(
    state: GuiState,
    slots: list,
    opts: list[str],
) -> None:
    """Unified chat panel — augment toggle determines BARE vs AUGMENTED mode.

    Per AIP_UNIFIED_CHAT_SPEC §Mode Logic:
      - augment OFF + 1 model = BARE (direct LLM call)
      - augment ON + 1 model = AUGMENTED (backend retrieval + synthesis)
      - >1 model selected = COHORT (parallel dispatch to all selected models)
    """
    # Track augment state (toggle drives dispatch path)
    augment_on: list[bool] = [state.current_mode == "augmented"]

    # Chat mode picker (per AIP_UNIFIED_CHAT_SPEC §Chat Mode Picker)
    _CHAT_MODES = ["Engineering", "Research", "Ideation", "Teaching"]
    # Auto-detection keywords (per spec)
    _MODE_KEYWORDS: dict[str, list[str]] = {
        "Ideation": ["brainstorm", "ideate", "blue sky", "what if", "imagine", "riff on"],
        "Research": ["what does the literature say", "find sources", "what's the evidence", "find evidence"],
        "Teaching": ["explain for", "simplify", "how would i teach", "help me explain"],
    }

    # Epistemic flag sentences (each flag maps to a specific sentence)
    _FLAG_SENTENCES: dict[str, str] = {
        "flag_uncertainty": "When uncertain, say so and quantify the uncertainty if possible.",
        "suggest_validation": "Suggest validation steps for claims that require empirical confirmation.",
        "no_flattery": "Do not flatter. Do not pad. Get to the substance.",
        "report_conflicts": (
            "Report conflicting evidence when it exists. "
            "Do not resolve genuine uncertainty artificially."
        ),
    }

    # Base mode texts (Engineering includes all 4 flag sentences by default;
    # other modes do NOT include them — flags are additive for non-Engineering)
    _MODE_BASES: dict[str, str] = {
        "Engineering": (
            "You are operating in Engineering/Synthesis mode.\n"
            "Respond with precision over coverage. Flag assumptions explicitly."
        ),
        "Research": (
            "You are operating in Research mode.\n"
            "Prioritize source citation and epistemic traceability.\n"
            "Distinguish: what is established, what is contested, what is speculative.\n"
            "Prefer precise claims over broad ones."
        ),
        "Ideation": (
            "You are operating in Ideation mode.\n"
            "Expand possibilities before narrowing. Defer judgment — generate first, evaluate later.\n"
            "Surface unexpected connections across domains. Speculate freely but label speculation.\n"
            "Volume of ideas matters more than precision here. Follow threads wherever they lead."
        ),
        "Teaching": (
            "You are operating in Teaching mode.\n"
            "The DEFINER is preparing material for others (students, community members, collaborators).\n"
            "Structure for clarity and progressive disclosure.\n"
            "Use concrete examples. Avoid jargon unless it will be explained.\n"
            "Flag where simplification sacrifices precision."
        ),
    }

    def _build_mode_modifier(mode: str, flags: dict[str, bool]) -> str:
        """Build the mode modifier text with epistemic flags applied.

        Engineering mode: base includes all 4 sentences by default;
        unchecking a flag REMOVES that sentence from the modifier.
        Other modes: base does NOT include flag sentences;
        checking a flag ADDS that sentence to the modifier.
        """
        base = _MODE_BASES.get(mode, "")
        if not base:
            return ""
        parts = [base]
        for flag_key, sentence in _FLAG_SENTENCES.items():
            is_checked = flags.get(flag_key, True)
            if mode == "Engineering":
                # Engineering: include sentence only if flag is checked
                if is_checked:
                    parts.append(sentence)
            else:
                # Non-Engineering: include sentence only if flag is checked (additive)
                if is_checked:
                    parts.append(sentence)
        return "\n".join(parts)
    current_chat_mode: list[str] = ["Engineering"]

    with (
        ui.row()
        .classes("w-full items-center px-4 py-2 gap-2")
        .style(f"background:{C_SURFACE};border-bottom:.5px solid {C_INK40};")
    ):
        # Mode status chip
        mode_chip = ui.label("BARE").style(
            f"font-size:9px;font-weight:700;letter-spacing:1px;"
            f"padding:2px 8px;border-radius:3px;"
            f"background:{C_INK40};color:{C_CREAM};"
        )
        # Augment toggle
        ui.switch(
            value=augment_on[0],
            on_change=lambda e: _on_augment_toggle(e.value),
        ).props("dense").style("color:{C_AMBER};")
        ui.label("AUGMENT").style(
            f"font-size:10px;color:{C_MUTED};font-weight:500;letter-spacing:0.5px;"
        )
        # Separator
        ui.label("·").style(f"color:{C_INK60};font-size:12px;")
        # Chat mode picker
        mode_select = (
            ui.select(
                _CHAT_MODES,
                value=current_chat_mode[0],
                on_change=lambda e: _on_mode_change(e.value),
            )
            .props("dense")
            .classes("min-w-[130px]")
        )
        ui.space()
        # Model selector: multi-select from enabled_models (library) when available,
        # falls back to static slot-based options. Multiple selection triggers COHORT mode.
        cur = get_role_model("synthesis")
        if not cur or cur not in opts:
            cur = opts[0] if opts else ""
        # Track selected models for cohort dispatch
        selected_model_ids: list[list[str]] = [[cur]]
        model_select = (
            ui.select(opts, value=[cur], multiple=True, on_change=lambda e: _on_model_select(e.value))
            .props("dense use-chips")
            .classes("min-w-[260px]")
            .style("max-width:500px;")
        )

    async def _load_model_library() -> None:
        """Load models from /api/v1/models/library and update selector."""
        try:
            models = await state.api_client.list_model_library(enabled_only=True)
            if models:
                # Merge with existing opts (library takes priority)
                combined = dict.fromkeys([m["model_id"] for m in models] + opts)
                model_select.options = list(combined.keys())
                # For multi-select: keep current selection if still valid
                current = selected_model_ids[0]
                valid = [m for m in current if m in model_select.options]
                if not valid and model_select.options:
                    valid = [model_select.options[0]]
                selected_model_ids[0] = valid
                model_select.value = valid
                # Store library for display names
                state._model_library = models  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("model_library_load_failed: %s", exc)

    def _on_model_select(selected: list[str]) -> None:
        """Handle model selection change (multi-select)."""
        if not selected:
            # Don't allow empty selection — keep at least one
            selected = selected_model_ids[0] or [opts[0]] if opts else []
            if selected:
                model_select.value = selected
        # Cap at 5 models
        if len(selected) > 5:
            selected = selected[:5]
            model_select.value = selected
            ui.notify("Maximum 5 models per cohort", color="warning", timeout=3000)
        selected_model_ids[0] = selected
        # Update primary synthesis slot
        if selected:
            set_role_model("synthesis", selected[0])
        _update_mode_chip()

    def _update_mode_chip() -> None:
        """Update mode status chip based on model count and augment toggle."""
        n = len(selected_model_ids[0])
        if n > 1:
            mode_chip.text = f"COHORT \u00b7 {n} models"
            mode_chip.style(
                f"font-size:9px;font-weight:700;letter-spacing:1px;"
                f"padding:2px 8px;border-radius:3px;"
                f"background:#7C6AE8;color:{C_CREAM};"
            )
        elif augment_on[0]:
            mode_chip.text = "AUGMENTED"
            mode_chip.style(
                f"font-size:9px;font-weight:700;letter-spacing:1px;"
                f"padding:2px 8px;border-radius:3px;"
                f"background:{C_AMBER};color:{C_GROUND};"
            )
        else:
            mode_chip.text = "BARE"
            mode_chip.style(
                f"font-size:9px;font-weight:700;letter-spacing:1px;"
                f"padding:2px 8px;border-radius:3px;"
                f"background:{C_INK40};color:{C_CREAM};"
            )

    def _on_mode_change(mode: str) -> None:
        current_chat_mode[0] = mode

    def _detect_mode(text: str) -> str | None:
        """Scan text for auto-detection keywords. Returns mode name or None."""
        lower = text.lower()
        for mode_name, keywords in _MODE_KEYWORDS.items():
            for kw in keywords:
                if kw in lower:
                    return mode_name
        return None

    def _on_augment_toggle(val: bool) -> None:
        augment_on[0] = val
        if val:
            state.current_mode = "augmented"
        else:
            state.current_mode = "normal"
        _update_mode_chip()
        state.reset_session()

    # Beast pane state
    beast_visible: list[bool] = [True]

    # Main content area: flex row with conversation + Beast pane
    with ui.row().classes("w-full").style(f"flex:1;min-height:0;"):

        # ── LEFT: Conversation thread ──
        msgs = (
            ui.column().classes("w-full px-4 py-2").style(
                f"flex:1;overflow-y:auto;background:{C_GROUND};min-height:320px;"
            )
        )
        with msgs:
            ok = state.backend_reachable and state.api_client.has_openrouter_api_key()
            ui.label(
                ("Connected" if state.backend_reachable else "Offline")
                + f" · {len(slots)} slot(s)"
                + (" · API key set" if state.api_client.has_openrouter_api_key() else " · API key missing")
            ).style(f"color:{C_OK_FG if ok else C_WARN_FG};font-size:11px;padding:8px;")

        # ── RIGHT: Beast pane (collapsible sidebar) ──
        beast_col = ui.column().style(
            f"width:320px;min-width:0;background:{C_SURFACE};"
            f"border-left:.5px solid {C_INK40};overflow-y:auto;padding:0;"
        )
        with beast_col:
            # Beast pane header
            with ui.row().classes("w-full items-center px-3 py-2 gap-2").style(
                f"border-bottom:.5px solid {C_INK40};background:{C_RAISED};"
            ):
                ui.label("BEAST").style(
                    f"font-size:11px;font-weight:600;letter-spacing:1px;color:{C_AMBER};"
                )
                ui.space()
                # Pop-out button — opens standalone Beast pane in new tab
                ui.button(
                    icon="open_in_new",
                    on_click=lambda: ui.navigate.to("/beast-pane", new_tab=True),
                ).props("dense flat").style(
                    f"color:{C_INK60};font-size:10px;"
                ).tooltip("Open Beast pane in new tab")
                # Collapse button
                ui.button(icon="close", on_click=lambda: _toggle_beast(False)).props(
                    "dense flat"
                ).style(f"color:{C_INK60};font-size:10px;")

            beast_content = ui.column().classes("w-full px-3 py-2").style("gap:4px;")

    # Toggle button (shown when Beast pane is collapsed)
    beast_toggle_btn = ui.button(
        icon="visibility",
        on_click=lambda: _toggle_beast(True),
    ).props("dense flat").style(
        f"color:{C_AMBER};position:absolute;right:12px;top:60px;z-index:10;"
    )
    beast_toggle_btn.set_visibility(False)

    def _toggle_beast(show: bool) -> None:
        beast_visible[0] = show
        if show:
            beast_col.style(
                f"width:320px;min-width:0;background:{C_SURFACE};"
                f"border-left:.5px solid {C_INK40};overflow-y:auto;padding:0;"
            )
            beast_toggle_btn.set_visibility(False)
        else:
            beast_col.style("width:0;min-width:0;padding:0;overflow:hidden;border:none;")
            beast_toggle_btn.set_visibility(True)

    async def _beast_scan(query: str) -> None:
        """Fire Beast scan and render results in the pane (AIP-G-02)."""
        with beast_content:
            ui.label("Scanning corpus...").style(
                f"color:{C_MUTED};font-size:10px;font-family:{F_MONO};"
            )
        scan = await state.api_client.beast_scan(query=query)
        beast_content.clear()
        with beast_content:
            if scan.get("error"):
                ui.label("corpus unavailable").style(
                    f"color:{C_ERR_FG};font-size:10px;font-family:{F_MONO};"
                )
                return
            domain = scan.get("domain")
            confidence = scan.get("confidence", 0)
            if domain:
                ui.label(f"Domain: {domain}").style(
                    f"font-size:11px;font-weight:600;color:{C_AMBER};"
                )
                ui.label(f"Confidence: {confidence}").style(
                    f"font-size:10px;color:{C_MUTED};font-family:{F_MONO};"
                )
            # Top turns
            turns = scan.get("top_turns", [])
            if turns:
                ui.label("TOP TURNS").style(
                    f"font-size:9px;font-weight:700;letter-spacing:1px;"
                    f"color:{C_MUTED};margin-top:4px;"
                )
                for t in turns[:5]:
                    with ui.row().classes("w-full").style("gap:2px;"):
                        ui.label(f"· [{t.get('domain', '?')}]").style(
                            f"font-size:9px;color:{C_AMBER};font-family:{F_MONO};"
                        )
                    snippet = t.get("snippet", "")[:80]
                    if snippet:
                        ui.label(snippet + "...").style(
                            f"font-size:10px;color:{C_MUTED};line-height:1.3;"
                        )
            # Domain neighbors
            neighbors = scan.get("neighbors", [])
            if neighbors:
                ui.label("DOMAIN NEIGHBORS").style(
                    f"font-size:9px;font-weight:700;letter-spacing:1px;"
                    f"color:{C_MUTED};margin-top:4px;"
                )
                for n in neighbors[:5]:
                    ui.label(
                        f"{n.get('source')} → {n.get('target')}"
                    ).style(
                        f"font-size:10px;color:{C_CREAM};font-family:{F_MONO};"
                    )
            # Wiki coverage
            wiki = scan.get("wiki_coverage")
            if wiki:
                ui.label("WIKI").style(
                    f"font-size:9px;font-weight:700;letter-spacing:1px;"
                    f"color:{C_MUTED};margin-top:4px;"
                )
                status = wiki.get("status", "?")
                wc = wiki.get("word_count", 0)
                ui.label(f"{domain}: {status} ({wc}w)").style(
                    f"font-size:10px;"
                    f"color:{C_OK_FG if status == 'APPROVED' else C_WARN_FG};"
                )

        # Append to scan history for Beast pop-out (max 10)
        if not scan.get("error"):
            state.beast_scan_history.append(scan)
            if len(state.beast_scan_history) > 10:
                state.beast_scan_history.pop(0)

    async def _beast_compare(query: str, responses: list[dict]) -> None:
        """Fire Beast cohort comparison and render in the Beast pane.

        Per spec: non-blocking, fires AFTER all model cards are rendered.
        Never delays rendering.
        """
        from datetime import datetime

        timestamp = datetime.now().strftime("%H:%M:%S")
        with beast_content:
            ui.label("Comparing responses...").style(
                f"color:{C_MUTED};font-size:10px;font-family:{F_MONO};"
            )
        cmp = await state.api_client.beast_compare(
            query=query,
            responses=responses,
        )
        beast_content.clear()
        with beast_content:
            if cmp.get("error") or not cmp.get("comparison_text"):
                ui.label("comparison unavailable").style(
                    f"color:{C_ERR_FG};font-size:10px;font-family:{F_MONO};"
                )
                return
            ui.label(f"COHORT COMPARISON \u00b7 {timestamp}").style(
                "font-size:9px;font-weight:700;letter-spacing:1px;"
                "color:#7C6AE8;margin-bottom:2px;"
            )
            ui.markdown(cmp["comparison_text"]).style(
                f"font-size:11px;color:{C_CREAM};line-height:1.5;"
            )

        # Store last comparison for Beast pop-out page
        if cmp.get("comparison_text"):
            cmp["timestamp"] = timestamp
            state.beast_last_comparison = cmp

    with (
        ui.row()
        .classes("w-full items-center px-4 py-2 gap-2")
        .style(f"border-top:.5px solid {C_INK40};background:{C_SURFACE};")
    ):
        fld = ui.input(placeholder="Ask anything...").props("outlined dense").classes("flex-grow")
        btn = ui.button("SEND").props("flat").style(btn_primary())

    def _msg(role: str, text: str, model: str | None = None, lat: int | None = None) -> None:
        with msgs:
            with ui.row().classes("w-full"):
                lbl = (model or "Assistant") if role == "assistant" else "You"
                ui.html(
                    f'<span style="color:{C_MUTED};font-size:11px;font-weight:600;">'
                    f"{lbl}"
                    f"{f'  ({lat}ms)' if lat else ''}"
                    f"</span>"
                )
            with ui.row().classes("w-full"):
                ui.markdown(text).style(
                    f"background:{C_SURFACE if role == 'assistant' else C_RAISED};"
                    f"border:0.5px solid {C_INK40};border-radius:{R_LG};"
                    f"padding:8px 10px;max-width:80%;font-size:13px;color:{C_CREAM};"
                ).classes("aip-msg-bubble")

    def _sys(text: str) -> None:
        with msgs:
            with ui.row().classes("w-full justify-center"):
                ui.label(text).style(f"color:{C_MUTED};font-size:10px;font-family:{F_MONO};padding:2px 0;")

    async def _send() -> None:
        prompt = fld.value.strip()
        if not prompt:
            return

        # Auto-detect chat mode from keywords (per spec §Chat Mode Picker)
        detected = _detect_mode(prompt)
        if detected and detected != current_chat_mode[0]:
            current_chat_mode[0] = detected
            mode_select.value = detected
            ui.notify(f"Switched to {detected} mode", color="info", timeout=3000)

        # Build the mode modifier with epistemic flags applied
        mode_modifier = _build_mode_modifier(
            current_chat_mode[0], state.epistemic_flags
        )

        # Determine dispatch path based on model count
        active_models = selected_model_ids[0]
        is_cohort = len(active_models) > 1

        _msg("user", prompt)
        fld.value = ""
        with msgs:
            think = ui.label(
                f"Waiting for {len(active_models)} model(s)..."
                if is_cohort
                else "Thinking..."
            ).style(f"color:{C_MUTED};font-size:12px;")

        if is_cohort:
            # ── COHORT MODE: parallel dispatch to all selected models ──
            try:
                r = await state.api_client.cohort_dispatch(
                    query=prompt,
                    model_ids=active_models,
                    augmented=augment_on[0],
                    mode_modifier=mode_modifier,
                )
                think.delete()
                if r.get("error"):
                    _sys(f"cohort error: {r.get('error', '?')}")
                    ui.notify(f"Cohort dispatch failed: {r.get('error', '?')}", color="negative")
                    return
                responses = r.get("responses", [])
                if not responses:
                    _sys("cohort returned no responses")
                    return
                # Render per-model response cards with distinct left-border colors
                with msgs:
                    for resp in responses:
                        mid = resp.get("model_id", "?")
                        accent = _model_accent_color(mid)
                        dname = resp.get("display_name", mid)
                        with ui.row().classes("w-full"):
                            ui.html(
                                f'<span style="color:{accent};font-size:11px;'
                                f'font-weight:600;">{dname}</span>'
                            )
                        with (
                            ui.row()
                            .classes("w-full")
                        ):
                            text = resp.get("response_text") or resp.get("error", "(no response)")
                            ui.markdown(text).style(
                                f"background:{C_SURFACE};"
                                f"border:0.5px solid {C_INK40};"
                                f"border-left:3px solid {accent};"
                                f"border-radius:{R_LG};"
                                f"padding:8px 10px;max-width:80%;"
                                f"font-size:13px;color:{C_CREAM};"
                            ).classes("aip-msg-bubble")
                        lat = resp.get("elapsed_ms", 0)
                        if lat:
                            _sys(f"{dname}: {lat}ms")
                # Fire Beast corpus scan + cohort comparison (non-blocking, AFTER response)
                if beast_visible[0]:
                    asyncio.create_task(_beast_scan(prompt))
                    # Build comparison payload from successful responses
                    cmp_responses = [
                        {"model_id": r.get("model_id", "?"), "response_text": r.get("response_text", "")}
                        for r in responses
                        if r.get("response_text")
                    ]
                    if len(cmp_responses) > 1:
                        asyncio.create_task(_beast_compare(prompt, cmp_responses))
            except Exception as exc:
                think.delete()
                _sys(f"cohort failed: {exc}")
                ui.notify(f"Cohort dispatch failed: {exc}", color="negative")

        elif augment_on[0]:
            # ── AUGMENTED MODE: POST to /api/v1/ask via backend ──
            # Check that we have a valid project before asking
            if not state.current_project:
                think.delete()
                _sys("No project found. Run: aip project create --name default")
                ui.notify(
                    "No project found. Run: aip project create --name default",
                    color="warning",
                    timeout=8000,
                )
                return
            try:
                r = await state.api_client.augmented_ask(
                    query=prompt,
                    project_name=state.current_project,
                    model_slot="synthesis",  # Always pass slot name, not model ID
                    system_prompt_modifier=mode_modifier,
                )
                think.delete()
                status = r.get("status", "")
                answer = r.get("answer", "")
                errors = r.get("errors", [])
                # Handle NO_PROJECT specifically with actionable guidance
                if status == "NO_PROJECT":
                    _sys(
                        f"Project '{state.current_project}' not found. "
                        f"Run: aip project create --name {state.current_project}"
                    )
                    ui.notify(
                        f"Project '{state.current_project}' not found",
                        color="warning",
                        timeout=6000,
                    )
                    # Invalidate cached project so next attempt re-resolves
                    state.current_project = None
                    return
                if status != "OK" or errors:
                    err_text = "; ".join(errors) if errors else f"status={status}"
                    _sys(f"ask error: {err_text}")
                    ui.notify(f"Ask failed: {err_text}", color="negative")
                if answer:
                    model_label = r.get("model_provider", "") or r.get("model_slot", "augmented")
                    _msg("assistant", answer, model=model_label)
                # Display retrieved domain + source turns
                sources = r.get("sources", [])
                if sources:
                    with msgs:
                        with ui.row().classes("w-full"):
                            ui.label(f"Retrieved {len(sources)} source(s)").style(
                                f"font-size:10px;color:{C_AMBER};font-family:{F_MONO};"
                            )
                        for src in sources[:10]:
                            domain = src.get("domain", "")
                            src_type = src.get("source_type", "")
                            title = src.get("title", "")
                            snippet = src.get("content_snippet", "")
                            score = src.get("score", 0)
                            with (
                                ui.row()
                                .classes("w-full aip-source-card")
                                .style(
                                    f"background:{C_SURFACE};border:0.5px solid {C_INK40};"
                                    f"border-radius:{R_SM};padding:6px 10px;margin:2px 0;"
                                    f"max-width:85%;"
                                )
                            ):
                                with ui.column().classes("w-full").style("gap:2px;"):
                                    hdr_parts = []
                                    if domain:
                                        hdr_parts.append(f"[{domain}]")
                                    if src_type:
                                        hdr_parts.append(src_type)
                                    if title:
                                        hdr_parts.append(title)
                                    if score:
                                        hdr_parts.append(f"score:{score:.2f}")
                                    ui.label(" ".join(hdr_parts)).style(
                                        f"font-size:10px;color:{C_AMBER};font-family:{F_MONO};"
                                    ).classes("aip-domain-chip")
                                    if snippet:
                                        ui.label(snippet[:200] + ("..." if len(snippet) > 200 else "")).style(
                                            f"font-size:11px;color:{C_MUTED};line-height:1.4;"
                                        )
                if not answer and not sources:
                    _sys("no answer or sources returned")
            except Exception as exc:
                think.delete()
                _sys(f"augmented ask failed: {exc}")
                ui.notify(f"Augmented ask failed: {exc}", color="negative")
        else:
            # ── BARE MODE: direct OpenRouter (existing path) ──
            model = get_role_model("synthesis")
            if not model or model.startswith("("):
                sel = get_selected_models()
                model = sel[0] if sel else ""
            if not model or model.startswith("("):
                opts_now = build_model_options(state.available_slots)
                model = opts_now[0] if opts_now and not opts_now[0].startswith("(") else ""
            if not model:
                think.delete()
                ui.notify("No model — open Settings", color="warning")
                return
            try:
                r = await state.api_client.chat_direct_openrouter(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    api_key=state.api_client.get_openrouter_api_key(),
                )
                think.delete()
                if r.get("error"):
                    _sys(f"error: {r.get('content', '?')}")
                    ui.notify(r.get("content", ""), color="negative")
                else:
                    _msg("assistant", r.get("content", ""), model=r.get("model", model), lat=r.get("latency_ms"))
                    if r.get("tokens_used", 0):
                        _sys(f"tokens: {r['tokens_used']}")
                    # Fire Beast corpus scan (non-blocking, AFTER response)
                    if beast_visible[0]:
                        asyncio.create_task(_beast_scan(prompt))
            except Exception as exc:
                think.delete()
                _sys(f"failed: {exc}")
                ui.notify(f"failed: {exc}", color="negative")

    async def _send_ctx() -> None:
        try:
            if state.client:
                with state.client:
                    await _send()
            else:
                await _send()
        except Exception as exc:
            try:
                ui.notify(f"send failed: {exc}", color="negative", timeout=8000)
            except Exception:
                pass

    fld.on("keydown.enter", lambda: asyncio.create_task(_send_ctx()))
    btn.on("click", lambda: asyncio.create_task(_send_ctx()))

    # Load model library in background (non-blocking)
    asyncio.create_task(_load_model_library())


# ── SHELL PAGE ────────────────────────────────────────────────────────


@ui.page("/")
async def main_page() -> None:
    ui.page_title("AIP_Brain")
    state = get_state()
    state.client = context.client

    ui.add_head_html(
        f"<style>"
        # ── Global: force minimum text contrast on dark backgrounds ──
        f"body,.q-page,.q-layout{{background:{C_GROUND}!important;color:{C_MUTED}!important}}"
        # Tab bar
        f".q-tab{{padding:14px 16px;font-size:13px;font-weight:500;color:{C_MUTED};border-bottom:2px solid transparent;}}"
        f".q-tab--active{{color:{C_CREAM};border-bottom:2px solid {C_AMBER};}}"
        f".q-tabs{{border-bottom:0.5px solid {C_INK40};}}"
        f".q-tab__label{{font-size:13px;font-weight:500;font-family:{F_SANS}}}"
        f".q-tabs__arrow{{color:{C_INK60}}}"
        # ── Markdown content: cream text on dark backgrounds ──
        f".q-markdown,.q-markdown p,.q-markdown span,.q-markdown li,.q-markdown strong,"
        f".q-markdown em,.q-markdown h1,.q-markdown h2,.q-markdown h3,.q-markdown h4,"
        f".q-markdown h5,.q-markdown h6{{color:{C_CREAM}!important}}"
        f".q-markdown code,.q-markdown pre{{color:{C_AMBER}!important;background:{C_RAISED}!important}}"
        # ── Tables: minimum C_INK60 text on dark backgrounds ──
        f"table,table td,table th{{color:{C_MUTED}!important}}"
        # ── Input/select/textarea: visible text ──
        f".q-field__native,.q-field__input,.q-field__label,.q-field__control,"
        f"input,textarea,select,.q-input,.q-textarea,.q-select{{color:{C_CREAM}!important}}"
        f".q-placeholder{{color:{C_INK60}!important}}"
        # ── Cards and panels: inherit muted text ──
        f".q-card,.q-card-section,.q-panel{{color:{C_MUTED}}}"
        # ── Buttons: ensure ghost buttons visible ──
        f".q-btn{{color:{C_MUTED}}}"
        # ── Chat message bubbles: cream text ──
        f".aip-msg-bubble,.aip-msg-bubble p,.aip-msg-bubble span,"
        f".aip-msg-bubble strong,.aip-msg-bubble em,.aip-msg-bubble li{{color:{C_CREAM}!important}}"
        # ── Source citations in augmented responses ──
        f".aip-source-card,.aip-source-card *{{color:{C_MUTED}!important}}"
        f".aip-source-card .aip-domain-chip{{color:{C_AMBER}!important}}"
        f"</style>"
    )

    # Non-blocking: render shell immediately, defer API key prompt + backend load
    backend_status = "connecting..."
    slots: list = []
    opts = build_model_options(slots)

    async def _deferred_backend_load() -> None:
        """Load backend health + slots + project after the page has rendered.

        Also checks API key status via the backend endpoint and shows
        the prompt if needed — deferred so it does not block NiceGUI's
        page rendering.
        """
        nonlocal backend_status
        bs = await check_backend_health(state)
        loaded_slots = await load_model_slots(state)
        for s in loaded_slots:
            sn, m = s.get("slot_name"), s.get("model")
            if sn and m and not str(m).startswith("<"):
                _role_models[sn] = m
        # Persist any new slots from backend
        _save_slot_models()
        state.available_slots = loaded_slots
        # Resolve a valid project for the AUGMENTED ask endpoint
        try:
            projects = await state.api_client.list_projects()
            if projects:
                # Use the first available project's name or id
                first = projects[0]
                state.current_project = first.get("name") or first.get("project_id") or first.get("id")
                log.info("resolved project for augmented ask: %s", state.current_project)
            else:
                log.warning("no projects found — augmented ask will show guidance")
        except Exception as exc:
            log.warning("failed to list projects: %s", exc)
        # Check API key status via backend endpoint (not just local env var)
        # This catches keys configured in TOML or .env that the backend
        # picks up but the GUI process doesn't see locally.
        try:
            key_status = await state.api_client.check_api_key_status()
            if key_status.get("has_any_key"):
                # Backend has a key — propagate to local state so
                # has_openrouter_api_key() returns True for UI checks.
                # Read the actual key from env (set by backend's .env loader).
                if not state.api_client.has_openrouter_api_key():
                    # Backend has the key but GUI doesn't know it locally.
                    # The backend's dotenv loader set AIP_OPENAI_API_KEY in the
                    # backend process, but our process may not have it.
                    # Set a flag so the UI knows a key exists server-side.
                    state.api_client._openrouter_api_key = "__backend_configured__"
                log.info("api key status: backend has key (slots=%s)", key_status.get("slots"))
            else:
                log.warning("api key status: no key configured on backend")
                # No key anywhere — show the prompt
                key = await _show_api_key_prompt()
                if key:
                    state.api_client.set_openrouter_api_key(key)
                    ui.notify("API key saved!", color="positive", position="top")
        except Exception as exc:
            log.warning("failed to check api key status: %s", exc)
            # Fallback: check locally (env var)
            if not state.api_client.has_openrouter_api_key():
                key = await _show_api_key_prompt()
                if key:
                    state.api_client.set_openrouter_api_key(key)
                    ui.notify("API key saved!", color="positive", position="top")
        # Update footer label with backend status
        try:
            budget_lbl.text = bs
            dot_color = C_OK_FG if state.backend_reachable else C_ERR_FG
            status_dot.set_content(
                f'<span style="display:inline-block;width:6px;height:6px;'
                f'border-radius:50%;background:{dot_color};"></span>'
            )
        except Exception:
            pass

    asyncio.create_task(_deferred_backend_load())

    def _on_tab(name: str) -> None:
        if name == "chat" and state.current_mode != "normal":
            state.current_mode = "normal"
            state.reset_session()

    # ── TOPBAR ──────────────────────────────────────────────────────
    with ui.header().style(
        f"background:{C_GROUND};border-bottom:0.5px solid {C_INK40};padding:0 12px;min-height:40px;"
    ):
        with ui.row().classes("items-center w-full gap-1").style("height:40px"):
            ui.html(_AIP_MARK)
            ui.label("AIP").style(
                f"font-family:{F_SERIF};font-size:18px;font-weight:700;"
                f"color:{C_CREAM};letter-spacing:-0.5px;margin-right:10px;"
            )

            tabs = (
                ui.tabs(value="chat", on_change=lambda e: _on_tab(e.value))
                .props("dense no-caps align=left")
                .style(f"color:{C_MUTED};")
            )
            with tabs:
                ui.tab("chat", label="CHAT")
                ui.tab("review", label="REVIEW")
                ui.tab("wiki", label="WIKI")
                ui.tab("corpus", label="CORPUS")
                ui.tab("graph", label="GRAPH")
                ui.tab("status", label="STATUS")
                ui.tab("settings", label="SETTINGS")

            ui.space()

            key_set = state.api_client.has_openrouter_api_key()
            ui.icon("vpn_key", size="xs").style(f"color:{'#4EAA7A' if key_set else '#E07070'};cursor:pointer;").on(
                "click", lambda: asyncio.create_task(_show_api_key_prompt())
            ).tooltip("API key set" if key_set else "API key missing — click to set")
            ui.icon("settings", size="xs").style(f"color:{C_MUTED};cursor:pointer;").tooltip("Model catalog — stage 0C")

    # ── CONTENT PANELS ──────────────────────────────────────────────
    with ui.tab_panels(tabs, value="chat").classes("w-full").style(f"flex:1;background:{C_GROUND};min-height:0;"):
        with ui.tab_panel("chat"):
            _build_unified_chat_panel(state, slots, opts)
        with ui.tab_panel("review"):
            _build_review_panel(state)
        with ui.tab_panel("wiki"):
            _build_wiki_panel(state)
        with ui.tab_panel("corpus"):
            _build_corpus_panel(state)
        with ui.tab_panel("graph"):
            _build_graph_panel(state)
        with ui.tab_panel("status"):
            _build_status_panel(state)
        with ui.tab_panel("settings"):
            _build_settings_panel(state)

    # ── STATUS BAR ──────────────────────────────────────────────────
    with ui.footer().style(f"background:{C_GROUND};border-top:{BORDER};height:28px;padding:0 16px;"):
        with ui.row().classes("w-full items-center gap-3").style("height:28px"):
            status_dot = ui.html(
                f'<span style="display:inline-block;width:6px;height:6px;'
                f'border-radius:50%;background:{C_INK60};"></span>'
            )
            budget_lbl = ui.label(backend_status).style(f"color:{C_MUTED};font-size:11px;font-family:{F_MONO};")
            ui.space()
            ui.label("aip_brain · AIP v0.1").style(f"color:{C_MUTED};font-size:11px;font-family:{F_MONO};")

    asyncio.create_task(refresh_budget_status(budget_lbl, state))


# Register non-blocking backend health check on app startup
app.on_startup(check_backend)


# ── BEAST PANE POP-OUT PAGE ───────────────────────────────────────────


@ui.page("/beast-pane")
async def beast_pane_page() -> None:
    """Standalone Beast pane — full-width, auto-refreshing every 30s.

    Opened via the [↗] pop-out button in the Beast pane sidebar.
    Shows scan history and last cohort comparison.
    Per AIP-G-02: shows real data only — never fake on failure.
    """
    state = get_state()
    ui.page_title("AIP_Brain — Beast Pane")

    ui.add_head_html(
        f"<style>"
        f"body,.q-page,.q-layout{{background:{C_GROUND}!important;color:{C_MUTED}!important}}"
        f".q-markdown,.q-markdown p,.q-markdown span,.q-markdown li,"
        f".q-markdown strong,.q-markdown em{{color:{C_CREAM}!important}}"
        f"</style>"
    )

    with (
        ui.column()
        .classes("w-full")
        .style(
            f"min-height:100vh;background:{C_GROUND};padding:24px 32px;gap:20px;"
        )
    ):
        # Header
        with ui.row().classes("w-full items-center gap-3"):
            ui.label("BEAST PANE").style(
                f"font-size:16px;font-weight:700;letter-spacing:2px;color:{C_AMBER};"
            )
            ui.label("(standalone)").style(
                f"font-size:11px;color:{C_MUTED};font-family:{F_MONO};"
            )
            ui.space()
            refresh_btn = ui.button(
                "Refresh", icon="refresh"
            ).props("dense flat").style(f"color:{C_MUTED};font-size:11px;")

        content = ui.column().classes("w-full").style("gap:16px;")

        async def _refresh() -> None:
            content.clear()
            with content:
                # Last cohort comparison
                cmp = state.beast_last_comparison
                if cmp and cmp.get("comparison_text"):
                    ts = cmp.get("timestamp", "?")
                    ui.label(f"LAST COHORT COMPARISON · {ts}").style(
                        "font-size:11px;font-weight:700;letter-spacing:1px;"
                        "color:#7C6AE8;margin-bottom:4px;"
                    )
                    ui.markdown(cmp["comparison_text"]).style(
                        f"font-size:13px;color:{C_CREAM};line-height:1.5;"
                        f"background:{C_SURFACE};padding:12px 16px;"
                        f"border:0.5px solid {C_INK40};border-radius:{R_LG};"
                    )
                else:
                    ui.label("No cohort comparison yet").style(
                        f"font-size:12px;color:{C_MUTED};font-family:{F_MONO};"
                    )

                ui.separator().style(f"background:{C_INK40};margin:8px 0;")

                # Scan history
                history = state.beast_scan_history
                if history:
                    ui.label(f"SCAN HISTORY ({len(history)})").style(
                        f"font-size:11px;font-weight:700;letter-spacing:1px;"
                        f"color:{C_AMBER};margin-bottom:4px;"
                    )
                    for scan in reversed(history):
                        domain = scan.get("domain", "—")
                        confidence = scan.get("confidence", 0)
                        with (
                            ui.card()
                            .classes("w-full")
                            .style(
                                f"background:{C_SURFACE};"
                                f"border:0.5px solid {C_INK40};"
                                f"border-radius:{R_LG};padding:12px 16px;"
                            )
                        ):
                            ui.label(f"Domain: {domain}").style(
                                f"font-size:12px;font-weight:600;color:{C_AMBER};"
                            )
                            ui.label(f"Confidence: {confidence}").style(
                                f"font-size:10px;color:{C_MUTED};"
                                f"font-family:{F_MONO};"
                            )
                            # Top turns
                            turns = scan.get("top_turns", [])
                            if turns:
                                ui.label("TOP TURNS").style(
                                    f"font-size:9px;font-weight:700;"
                                    f"letter-spacing:1px;color:{C_MUTED};"
                                )
                                for t in turns[:5]:
                                    snippet = t.get("snippet", "")[:80]
                                    d = t.get("domain", "?")
                                    ui.label(f"· [{d}] {snippet}").style(
                                        f"font-size:10px;color:{C_CREAM};"
                                        f"line-height:1.3;"
                                    )
                else:
                    ui.label("No scan history yet — send a message to start").style(
                        f"font-size:12px;color:{C_MUTED};font-family:{F_MONO};"
                    )

        refresh_btn.on("click", lambda: asyncio.create_task(_refresh()))
        await _refresh()

        # Auto-refresh every 30 seconds
        ui.timer(30.0, lambda: asyncio.create_task(_refresh()))


ui.run(title="AIP_Brain", port=8080, reload=True)
