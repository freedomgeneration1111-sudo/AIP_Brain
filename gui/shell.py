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


# ── PERSISTENCE & MODULE STATE ────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SELECTED_MODELS_FILE = _PROJECT_ROOT / "config" / "selected_models.json"


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


_selected_models: list[str] = _load_sel()
_role_models: dict[str, str] = {
    "synthesis": "", "evaluation": "", "sexton": "", "embedding": "",
}


def get_selected_models() -> list[str]:
    return _selected_models


def set_selected_models(models: list[str]) -> None:
    global _selected_models
    _selected_models = models
    _save_sel(models)


def get_role_model(slot: str) -> str:
    return _role_models.get(slot, "")


def set_role_model(slot: str, model: str) -> None:
    _role_models[slot] = model


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


async def load_model_slots(state: GuiState) -> list:
    try:
        slots = await state.api_client.list_model_slots()
        state.available_slots = slots
        return slots
    except Exception:
        return []


def build_model_options(slots: list) -> list[str]:
    sel = get_selected_models()
    backend = [
        s.get("model", "") for s in slots
        if s.get("model") and not s.get("model", "").startswith("<")
    ]
    opts = list(dict.fromkeys(sel + backend + ["google/gemma-3-4b-it"]))
    return [m for m in opts if m] or ["(no models — open Settings)"]


def on_chat_model_changed(model_id: str) -> None:
    state = get_state()
    state.current_role = None
    set_role_model("synthesis", model_id)
    state.reset_session()
    asyncio.create_task(
        state.api_client.update_slot_model(
            "synthesis", model_id, api_key=state.api_client.get_openrouter_api_key()
        )
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
                with state.client: _u()
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

    with ui.row().classes("w-full items-center px-4 py-2 gap-2").style(
        f"background:{C_SURFACE};border-bottom:.5px solid {C_INK40};"
    ):
        ui.label("SYSTEM STATUS").style(
            f"font-size:11px;font-weight:600;letter-spacing:1px;color:{C_AMBER};"
        )
        ui.space()
        refresh_btn = ui.button("Refresh", icon="refresh").props("dense flat").style(
            f"color:{C_MUTED};font-size:11px;"
        )

    content = ui.column().classes("w-full px-4 py-3").style(
        f"flex:1;overflow-y:auto;background:{C_GROUND};gap:0;"
    )

    def _section(title: str) -> None:
        ui.label(title).style(
            f"font-size:10px;font-weight:700;letter-spacing:2px;"
            f"color:{C_INK60};margin-top:12px;margin-bottom:4px;"
        )

    def _kv(label: str, val: str, vc: str = "") -> None:
        with ui.row().classes("w-full items-baseline").style("padding:1px 0;"):
            ui.label(label).style(
                f"font-size:11px;font-family:{F_MONO};color:{C_MUTED};min-width:240px;"
            )
            ui.label(val).style(
                f"font-size:11px;font-family:{F_MONO};color:{vc or C_CREAM};"
            )

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
            state.api_client.list_knowledge(),
            state.api_client.get_graph_stats(),
            return_exceptions=True,
        )
        health     = results[0] if not isinstance(results[0], Exception) else {}
        actors_r   = results[1] if not isinstance(results[1], Exception) else {}
        slots_list = results[2] if not isinstance(results[2], Exception) else []
        know_r     = results[3] if not isinstance(results[3], Exception) else {}
        graph_r    = results[4] if not isinstance(results[4], Exception) else {}

        items = know_r.get("items", []) if isinstance(know_r, dict) else []
        approved = sum(1 for k in items if k.get("state") == "APPROVED")
        generated = sum(1 for k in items if k.get("state") == "GENERATED")
        domains_with_wiki = len({k.get("domain", "") for k in items if k.get("domain")})

        actors = actors_r.get("actors", {}) if isinstance(actors_r, dict) else {}
        slot_map = {s.get("slot_name"): s for s in (slots_list or [])}

        content.clear()
        with content:
            _section("CORPUS")
            _kv("Total turns:", "2,766")
            _kv("Tagged:", "42  (1.5%)")
            _kv("Untagged:", "2,724")
            _kv("Bridge-tagged:", "5")
            _kv("Embedded:", "0  (Phase 1.4 pending)")
            _sep()

            _section("WIKI")
            _kv("APPROVED articles:", str(approved))
            _kv("GENERATED (pending):", str(generated),
                vc=C_WARN_FG if generated > 0 else C_CREAM)
            _kv("Domains with wiki:", str(domains_with_wiki))
            _sep()

            _section("KNOWLEDGE GRAPH")
            _kv("Entities:", str(graph_r.get("nodes", "—")))
            _kv("Edges:", str(graph_r.get("edges", "—")))
            _sep()

            _section("ACTOR SLOTS")
            for sn in ["synthesis", "beast", "vigil", "sexton", "embedding"]:
                s = slot_map.get(sn, {})
                model = s.get("model") or "[not configured]"
                configured = model and not model.startswith("<") and model != "[not configured]"
                dot_c = C_OK_FG if configured else C_MUTED
                status = "READY" if configured else "UNCONFIGURED"
                sc = C_OK_FG if configured else C_MUTED
                with ui.row().classes("w-full items-center gap-2").style("padding:1px 0;"):
                    ui.html(
                        f'<span style="font-size:10px;color:{dot_c};">'
                        f'{"●" if configured else "○"}</span>'
                    )
                    ui.label(sn).style(
                        f"font-size:11px;font-family:{F_MONO};color:{C_CREAM};min-width:90px;"
                    )
                    ui.label((model[:36] + "…") if len(model) > 36 else model).style(
                        f"font-size:11px;font-family:{F_MONO};color:{C_MUTED};flex:1;"
                    )
                    ui.label(status).style(
                        f"font-size:10px;font-family:{F_MONO};color:{sc};"
                    )
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


# ── REVIEW PANEL ─────────────────────────────────────────────────────

def _build_review_panel(state: GuiState) -> None:
    """REVIEW tab — beast_wiki artifacts in GENERATED state, approve/reject."""

    with ui.row().classes("w-full items-center px-4 py-2 gap-2").style(
        f"background:{C_SURFACE};border-bottom:.5px solid {C_INK40};"
    ):
        ui.label("REVIEW").style(
            f"font-size:11px;font-weight:600;letter-spacing:1px;color:{C_AMBER};"
        )
        count_lbl = ui.label("").style(
            f"font-size:10px;background:{C_WARN_BG};color:{C_WARN_FG};"
            "padding:1px 6px;border-radius:3px;font-family:{F_MONO};"
        )
        ui.space()
        refresh_btn = ui.button("Refresh", icon="refresh").props("dense flat").style(
            f"color:{C_MUTED};font-size:11px;"
        )

    content = ui.column().classes("w-full px-4 py-3 gap-3").style(
        f"flex:1;overflow-y:auto;background:{C_GROUND};min-height:0;"
    )

    async def _load() -> None:
        content.clear()
        count_lbl.text = ""
        with content:
            ui.label("Loading…").style(
                f"color:{C_MUTED};font-size:11px;font-family:{F_MONO};"
            )

        reviews, knowledge = await asyncio.gather(
            state.api_client.list_pending_reviews(),
            state.api_client.list_knowledge(state="GENERATED"),
            return_exceptions=True,
        )
        review_items = reviews if isinstance(reviews, list) else []
        know_items = (
            knowledge.get("items", [])
            if isinstance(knowledge, dict)
            else []
        )

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
                cards.append({
                    "id": kid,
                    "source": "knowledge",
                    "domain": k.get("domain", ""),
                    "state": k.get("state", ""),
                    "content": k.get("content", ""),
                    "artifact_type": "wiki",
                    **k,
                })

        count_lbl.text = f"{len(cards)} pending" if cards else ""

        content.clear()
        if not cards:
            with content:
                ui.label("No items pending review.").style(
                    f"color:{C_MUTED};font-size:12px;padding:24px;"
                )
            return

        for card in cards:
            _render_review_card(content, card, state, _load)

    def _render_review_card(
        parent, card: dict, state: GuiState, reload_fn
    ) -> None:
        artifact_id = card.get("id", "")
        domain = card.get("domain", "—")
        artifact_type = card.get("artifact_type", card.get("type", "artifact"))
        content_text = card.get("content", "")
        preview = (content_text[:300] + "…") if len(content_text) > 300 else content_text

        expanded_state: list[bool] = [False]

        with parent:
            with ui.card().classes("w-full").style(
                f"background:{C_SURFACE};border:1px solid {C_INK40};"
                "border-radius:4px;padding:0;"
            ):
                # Header bar
                with ui.row().classes("w-full items-center px-3 py-2 gap-2").style(
                    f"border-bottom:0.5px solid {C_INK40};"
                ):
                    ui.label(f"[{domain.upper()}]").style(
                        f"font-size:10px;font-family:{F_MONO};color:{C_AMBER};"
                    )
                    uid_short = artifact_id[:20] + "…" if len(artifact_id) > 20 else artifact_id
                    ui.label(uid_short).style(
                        f"font-size:10px;font-family:{F_MONO};color:{C_MUTED};"
                    )
                    ui.space()
                    ui.label(artifact_type).style(
                        f"font-size:9px;background:{C_INK40};color:{C_MUTED};"
                        "padding:1px 5px;border-radius:3px;"
                    )

                # Preview content
                preview_col = ui.column().classes("w-full px-3 py-2")
                with preview_col:
                    ui.label(preview or "(empty)").style(
                        f"font-size:12px;color:{C_CREAM};line-height:1.5;"
                    )

                # Expanded content (hidden by default)
                full_col = ui.column().classes("w-full px-3 py-2").style("display:none;")
                with full_col:
                    ui.markdown(content_text or "(no content)").style(
                        f"font-size:12px;color:{C_CREAM};line-height:1.6;"
                    )

                # Action bar
                with ui.row().classes("w-full items-center px-3 py-2 gap-2").style(
                    f"border-top:0.5px solid {C_INK40};"
                ):
                    approve_btn = ui.button("APPROVE").style(
                        f"background:{C_OK_BG};color:{C_OK_FG};"
                        "font-size:10px;letter-spacing:.5px;font-weight:700;"
                    ).props("dense")
                    reject_btn = ui.button("REJECT").style(
                        f"background:{C_ERR_BG};color:{C_ERR_FG};"
                        "font-size:10px;letter-spacing:.5px;font-weight:700;"
                    ).props("dense")
                    ui.space()
                    expand_btn = ui.button("EXPAND ↓").props("dense flat").style(
                        f"color:{C_MUTED};font-size:10px;"
                    )

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

    refresh_btn.on("click", lambda: asyncio.create_task(_load()))
    asyncio.create_task(_load())


# ── CHAT PANEL ────────────────────────────────────────────────────────

def _build_chat_panel(
    mode: str,
    state: GuiState,
    slots: list,
    opts: list[str],
) -> None:
    """Build chat panel. Send uses direct OpenRouter in 0B; WebSocket wired in 0C."""
    is_aug = mode == "augmented"

    with ui.row().classes("w-full items-center px-4 py-2 gap-2").style(
        f"background:{C_SURFACE};border-bottom:.5px solid {C_INK40};"
    ):
        ui.label("AUGMENTED" if is_aug else "CHAT").style(
            f"font-size:11px;font-weight:600;letter-spacing:1px;"
            f"color:{C_AMBER if is_aug else C_CREAM};"
        )
        if is_aug:
            ui.label("INGESTED DATA").style(
                f"font-size:9px;background:{C_AMBER};color:{C_GROUND};"
                "padding:1px 6px;border-radius:3px;font-weight:700;"
            )
        ui.space()
        cur = get_role_model("synthesis")
        if not cur or cur not in opts:
            cur = opts[0] if opts else ""
        ui.select(opts, value=cur, on_change=lambda e: on_chat_model_changed(e.value)).props(
            "dense"
        ).classes("min-w-[200px]")

    msgs = ui.column().classes("w-full px-4 py-2").style(
        f"flex:1;overflow-y:auto;background:{C_GROUND};min-height:320px;"
    )
    with msgs:
        ok = state.backend_reachable and state.api_client.has_openrouter_api_key()
        ui.label(
            ("Connected" if state.backend_reachable else "Offline")
            + f" · {len(slots)} slot(s)"
            + (" · API key set" if state.api_client.has_openrouter_api_key() else " · API key missing")
        ).style(f"color:{C_OK_FG if ok else C_WARN_FG};font-size:11px;padding:8px;")

    with ui.row().classes("w-full items-center px-4 py-2 gap-2").style(
        f"border-top:.5px solid {C_INK40};background:{C_SURFACE};"
    ):
        fld = ui.input(placeholder="Ask anything...").props("outlined dense").classes("flex-grow")
        btn = ui.button("SEND").style(
            f"background:{C_INK60};color:{C_CREAM};font-size:11px;letter-spacing:.5px;"
        )

    def _msg(role: str, text: str, model: str | None = None, lat: int | None = None) -> None:
        with msgs:
            with ui.row().classes("w-full"):
                lbl = (model or "Assistant") if role == "assistant" else "You"
                ui.markdown(f"**{lbl}**" + (f"  ({lat}ms)" if lat else "")).style(
                    f"color:{C_MUTED};font-size:11px;"
                )
            with ui.row().classes("w-full"):
                ui.markdown(text).style(
                    f"background:{C_SURFACE if role == 'assistant' else C_RAISED};"
                    f"border:.5px solid {C_INK40};border-radius:4px;"
                    f"padding:8px 10px;max-width:80%;font-size:13px;color:{C_CREAM};"
                )

    def _sys(text: str) -> None:
        with msgs:
            with ui.row().classes("w-full justify-center"):
                ui.label(text).style(
                    f"color:{C_MUTED};font-size:10px;font-family:{F_MONO};padding:2px 0;"
                )

    async def _send() -> None:
        prompt = fld.value.strip()
        if not prompt:
            return
        model = get_role_model("synthesis")
        if not model or model.startswith("("):
            sel = get_selected_models()
            model = sel[0] if sel else ""
        if not model or model.startswith("("):
            opts_now = build_model_options(state.available_slots)
            model = opts_now[0] if opts_now and not opts_now[0].startswith("(") else ""
        if not model:
            ui.notify("No model — open Settings", color="warning")
            return
        _msg("user", prompt)
        fld.value = ""
        with msgs:
            think = ui.label("Thinking...").style(f"color:{C_MUTED};font-size:12px;")
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

    # API key check — blocking prompt if missing
    if not state.api_client.has_openrouter_api_key():
        key = await _show_api_key_prompt()
        if key:
            state.api_client.set_openrouter_api_key(key)
            ui.notify("API key saved!", color="positive", position="top")

    # Backend load — hard-capped at 4 s
    backend_status = await check_backend_health(state)
    slots = await load_model_slots(state)
    for s in slots:
        sn, m = s.get("slot_name"), s.get("model")
        if sn and m and not str(m).startswith("<"):
            _role_models[sn] = m
    opts = build_model_options(slots)

    def _on_tab(name: str) -> None:
        if name == "augmented" and state.current_mode != "augmented":
            state.current_mode = "augmented"
            state.reset_session()
        elif name == "chat" and state.current_mode != "normal":
            state.current_mode = "normal"
            state.reset_session()

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

            tabs = ui.tabs(value="chat", on_change=lambda e: _on_tab(e.value)).props(
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
            ).on("click", lambda: asyncio.create_task(_show_api_key_prompt())).tooltip(
                "API key set" if key_set else "API key missing — click to set"
            )
            ui.icon("settings", size="xs").style(
                f"color:{C_INK60};cursor:pointer;"
            ).tooltip("Model catalog — stage 0C")

    # ── CONTENT PANELS ──────────────────────────────────────────────
    with ui.tab_panels(tabs, value="chat").classes("w-full").style(
        f"flex:1;background:{C_GROUND};min-height:0;"
    ):
        with ui.tab_panel("chat"):
            _build_chat_panel("normal", state, slots, opts)
        with ui.tab_panel("augmented"):
            _build_chat_panel("augmented", state, slots, opts)
        with ui.tab_panel("cohort"):
            ui.label("COHORT — tier 9 scaffold").style(
                f"color:{C_MUTED};padding:24px;font-family:{F_MONO};font-size:12px;"
            )
        with ui.tab_panel("review"):
            _build_review_panel(state)
        with ui.tab_panel("wiki"):
            ui.label("WIKI — stage 0C").style(
                f"color:{C_MUTED};padding:24px;font-family:{F_MONO};font-size:12px;"
            )
        with ui.tab_panel("corpus"):
            ui.label("CORPUS — stage 0C").style(
                f"color:{C_MUTED};padding:24px;font-family:{F_MONO};font-size:12px;"
            )
        with ui.tab_panel("graph"):
            ui.label("GRAPH — stage 0C").style(
                f"color:{C_MUTED};padding:24px;font-family:{F_MONO};font-size:12px;"
            )
        with ui.tab_panel("status"):
            _build_status_panel(state)

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
            budget_lbl = ui.label(backend_status).style(
                f"color:#555;font-size:10px;font-family:{F_MONO};"
            )
            ui.space()
            ui.label("aip_brain · AIP v0.1").style(
                f"color:{C_INK60};font-size:10px;font-family:{F_MONO};"
            )

    asyncio.create_task(refresh_budget_status(budget_lbl, state))


ui.run(title="AIP_Brain", port=8082, reload=True)
