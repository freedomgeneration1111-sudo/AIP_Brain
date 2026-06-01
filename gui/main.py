"""AIP_Brain NiceGUI Frontend — OpenRouter Integration Pass.

This module implements the NiceGUI frontend that communicates EXCLUSIVELY
through the AIP FastAPI backend's REST and WebSocket endpoints. It does NOT:
  - Import from aip.orchestration
  - Directly access AipContainer
  - Make direct HTTP calls to Ollama or OpenRouter (except OpenRouter catalog API)
  - Read enabled_models.json directly

All chat interactions flow through:
  1. POST /api/v1/sessions → get session_id
  2. WebSocket /api/v1/chat/{session_id} → send messages, receive responses
  3. GET /api/v1/models/slots → populate model/role dropdowns
  4. PATCH /api/v1/sessions/{id} → toggle auto_save, update session flags
  5. POST /api/v1/ingest/conversation → manual ingestion trigger

OpenRouter Integration Pass:
  - .env file loaded on startup → AIP_OPENAI_API_KEY available immediately
  - API key prompt on first load if key is NOT in .env or environment
  - Model Catalog page fetches ALL models from OpenRouter API
  - Table with model name, cost, context length, date, and checkbox
  - Selected models populate a universal model dropdown
  - Chat + Actor roles all use the same model dropdown
  - All slots configured for OpenRouter (openai_compatible provider)
  - Selected models persisted to config/selected_models.json
  - Visual clarity: amber border = chat model, sidebar = role models
  - "USING INGESTED DATA" badge when in augmented mode

This follows the API-First approach: the GUI is an Adapter-layer surface
like CLI and MCP, communicating via HTTP/WebSocket rather than in-process.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from nicegui import ui

from gui.api_client import get_api_client, AipApiClient

# ---------------------------------------------------------------------------
# Load .env file IMMEDIATELY — must happen before any env var reads
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Persistence: selected models saved to JSON
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SELECTED_MODELS_FILE = _PROJECT_ROOT / "config" / "selected_models.json"


def _load_selected_models_from_disk() -> list[str]:
    """Load previously selected models from JSON file."""
    try:
        if _SELECTED_MODELS_FILE.exists():
            data = json.loads(_SELECTED_MODELS_FILE.read_text())
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save_selected_models_to_disk(models: list[str]) -> None:
    """Persist selected models to JSON file."""
    try:
        _SELECTED_MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SELECTED_MODELS_FILE.write_text(json.dumps(models, indent=2))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


class GuiState:
    """Holds GUI session state. One instance per page load."""

    def __init__(self) -> None:
        self.api_client: AipApiClient = get_api_client()
        self.session_id: str | None = None
        self.current_role: str = "beast"
        self.current_model_slot: str = "synthesis"
        self.current_mode: str = "normal"  # "normal" or "augmented"
        self.available_slots: list[dict[str, Any]] = []
        self.backend_reachable: bool = False
        self.pending_gate: dict[str, Any] | None = None
        self.auto_save: bool = True
        self.ingestion_status: str = "idle"  # "idle" | "ingesting" | "error"
        self.chunks_indexed: int = 0

    async def ensure_session(self) -> str:
        """Create a session if one doesn't exist, or return the existing one."""
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
        """Reset session state (e.g., when changing roles)."""
        self.session_id = None
        self.pending_gate = None
        self.ingestion_status = "idle"
        self.chunks_indexed = 0


# Per-page state
_state: GuiState | None = None

# Track which OpenRouter models the user has selected from the catalog.
# These populate the universal model dropdown. Loaded from disk on startup.
_selected_models: list[str] = _load_selected_models_from_disk()

# Track enabled slots (for Chat Model dropdown filtering)
_enabled_slots: dict[str, bool] = {}

# Role → model assignment: maps role names to selected OpenRouter model IDs.
_role_model_assignments: dict[str, str] = {
    "synthesis": "",
    "evaluation": "",
    "sexton": "",
    "embedding": "",
}


def is_slot_enabled(slot_name: str) -> bool:
    """Check if a model slot is enabled. Defaults to True."""
    return _enabled_slots.get(slot_name, True)


def set_slot_enabled(slot_name: str, enabled: bool) -> None:
    """Enable or disable a model slot."""
    _enabled_slots[slot_name] = enabled


def get_selected_models() -> list[str]:
    """Get the list of models selected from the OpenRouter catalog."""
    return _selected_models


def set_selected_models(models: list[str]) -> None:
    """Set the list of selected models and persist to disk."""
    global _selected_models
    _selected_models = models
    _save_selected_models_to_disk(models)


def get_role_model(slot_name: str) -> str:
    """Get the OpenRouter model ID assigned to a role/slot."""
    return _role_model_assignments.get(slot_name, "")


def set_role_model(slot_name: str, model_id: str) -> None:
    """Assign an OpenRouter model ID to a role/slot."""
    _role_model_assignments[slot_name] = model_id


def get_state() -> GuiState:
    """Get the current page's GuiState."""
    global _state
    if _state is None:
        _state = GuiState()
    return _state


# ---------------------------------------------------------------------------
# UI Helper Functions
# ---------------------------------------------------------------------------


def add_message(role: str, text: str, model: str | None = None, latency_ms: int | None = None) -> None:
    """Add a chat message bubble to the chat container."""
    with chat_container:
        with ui.row().classes("w-full"):
            if model and role == "assistant":
                display = model
            elif role == "assistant":
                display = "Assistant"
            else:
                display = "You"
            label_text = f"**{display}**"
            if latency_ms is not None:
                label_text += f"  ({latency_ms}ms)"
            ui.markdown(label_text).classes("text-sm text-grey-7")
        with ui.row().classes("w-full"):
            color = "#dcf8c6" if role == "assistant" else "#f0f0f0"
            ui.markdown(text).classes("p-2 rounded-lg").style(f"background-color: {color}; max-width: 80%;")


def add_system_message(text: str) -> None:
    """Add a system/info message to the chat container."""
    with chat_container:
        with ui.row().classes("w-full justify-center"):
            ui.label(text).classes("text-caption text-grey-6")


async def check_backend_health() -> str:
    """Check if the AIP backend is reachable. Returns status string."""
    state = get_state()
    try:
        health = await state.api_client.check_health()
        state.backend_reachable = True
        slots = health.get("model_slots", [])
        return f"Backend: OK (slots: {', '.join(slots)})"
    except Exception as exc:
        state.backend_reachable = False
        return f"Backend: UNREACHABLE — {exc}"


async def load_model_slots() -> list[dict[str, Any]]:
    """Fetch model slots from the backend and update state."""
    state = get_state()
    try:
        slots = await state.api_client.list_model_slots()
        state.available_slots = slots
        state.backend_reachable = True
        return slots
    except Exception:
        state.backend_reachable = False
        return []


async def send_prompt() -> None:
    """Handle the send button click — sends message via WebSocket."""
    state = get_state()
    prompt = input_field.value.strip()
    if not prompt:
        return

    if not state.backend_reachable:
        ui.notify("Backend is not reachable. Please check that the AIP FastAPI server is running.", color="negative")
        return

    try:
        session_id = await state.ensure_session()
    except Exception as exc:
        ui.notify(f"Failed to create session: {exc}", color="negative")
        return

    add_message("user", prompt)
    input_field.value = ""

    thinking_label = ui.label("Thinking...").classes("text-grey")

    def on_response(resp: dict[str, Any]) -> None:
        thinking_label.delete()
        content = resp.get("content", "")
        model = resp.get("model", resp.get("model_slot", ""))
        latency = resp.get("latency_ms")
        tokens = resp.get("tokens_used", 0)
        auto_saved = resp.get("auto_save", False)
        sources = resp.get("sources", [])
        add_message("assistant", content, model=model, latency_ms=latency)
        if tokens > 0:
            add_system_message(f"Tokens: {tokens}")
        if sources:
            source_summary = f"Sources ({len(sources)}): "
            source_parts = []
            for s in sources[:5]:
                title = s.get("title", s.get("source_id", "?"))
                score = s.get("score", 0)
                source_parts.append(f"{title} ({score:.2f})")
            source_summary += ", ".join(source_parts)
            if len(sources) > 5:
                source_summary += f" +{len(sources) - 5} more"
            add_system_message(source_summary)
        if auto_saved:
            add_system_message("Auto-save: indexing...")
            asyncio.create_task(refresh_ingestion_status())

    def on_error(err: dict[str, Any]) -> None:
        thinking_label.delete()
        content = err.get("content", "Unknown error")
        add_system_message(f"Error: {content}")
        ui.notify(content, color="negative")

    def on_gate(gate: dict[str, Any]) -> None:
        thinking_label.delete()
        state.pending_gate = gate
        gate_type = gate.get("gate_type", "unknown")
        preview = gate.get("preview", "")
        add_system_message(f"DEFINER Gate ({gate_type}): {preview}")
        with chat_container:
            with ui.row().classes("w-full justify-center gap-2"):
                ui.button(
                    "Approve",
                    color="positive",
                    on_click=lambda: asyncio.create_task(handle_gate_response(True)),
                )
                ui.button(
                    "Reject",
                    color="negative",
                    on_click=lambda: asyncio.create_task(handle_gate_response(False)),
                )

    try:
        await state.api_client.chat_via_websocket(
            session_id=session_id,
            message=prompt,
            on_response=on_response,
            on_error=on_error,
            on_gate=on_gate,
            model_slot=state.current_model_slot,
        )
    except Exception as exc:
        thinking_label.delete()
        ui.notify(f"Communication error: {exc}", color="negative")


async def handle_gate_response(approved: bool) -> None:
    """Handle a DEFINER gate approval/rejection."""
    state = get_state()
    if state.session_id is None:
        return

    decision_text = "approved" if approved else "rejected"
    add_system_message(f"Gate {decision_text}")

    try:
        result = await state.api_client.send_gate_response(
            session_id=state.session_id,
            approved=approved,
        )
        if result.get("type") == "error":
            add_system_message(f"Gate response error: {result.get('content', 'Unknown error')}")
            ui.notify(f"Gate response failed: {result.get('content', 'Unknown error')}", color="negative")
        elif result.get("type") == "response":
            content = result.get("content", "")
            add_message("assistant", content)
    except Exception as exc:
        add_system_message(f"Gate response failed: {exc}")
        ui.notify(f"Gate response failed: {exc}", color="negative")
        return

    state.pending_gate = None


def set_mode(mode: str) -> None:
    """Set the chat mode (normal or knowledge-augmented)."""
    state = get_state()
    state.current_mode = mode
    state.reset_session()
    mode_label.text = "Chat" if mode == "normal" else "Augmented"


def on_slot_changed(slot_name: str) -> None:
    """Handle model slot selection change."""
    state = get_state()
    state.current_model_slot = slot_name
    state.reset_session()


async def trigger_actor(actor_name: str) -> None:
    """Manually trigger an actor cycle and show the result."""
    state = get_state()
    try:
        result = await state.api_client.trigger_actor_cycle(actor_name)
        triggered = result.get("triggered", False)
        if triggered:
            ui.notify(f"{actor_name.capitalize()} cycle triggered successfully", color="positive")
        else:
            error = result.get("error", "Unknown error")
            ui.notify(f"{actor_name.capitalize()} cycle failed: {error}", color="negative")
    except Exception as exc:
        ui.notify(f"Failed to trigger {actor_name}: {exc}", color="negative")


async def on_auto_save_toggled(enabled: bool) -> None:
    """Handle auto_save checkbox toggle."""
    state = get_state()
    state.auto_save = enabled
    if state.session_id is not None:
        try:
            await state.api_client.update_session(state.session_id, {"auto_save": enabled})
            status = "enabled" if enabled else "disabled"
            ui.notify(f"Auto-save {status}", color="positive" if enabled else "warning")
        except Exception as exc:
            ui.notify(f"Failed to update auto-save: {exc}", color="negative")
    else:
        status = "enabled" if enabled else "disabled"
        ui.notify(f"Auto-save will be {status} for next session", color="info")


async def refresh_ingestion_status() -> None:
    """Refresh ingestion status from the backend."""
    state = get_state()
    if state.session_id is None:
        return
    await asyncio.sleep(1.0)
    try:
        session = await state.api_client.get_session(state.session_id)
        ingestion_status = session.get("ingestion_status", "idle")
        chunks_indexed = session.get("chunks_indexed", 0)
        state.ingestion_status = ingestion_status
        state.chunks_indexed = chunks_indexed
        if ingestion_status == "ingesting":
            ingestion_label_ref.text = f"Indexing... ({chunks_indexed} chunks)"
        elif ingestion_status == "error":
            ingestion_label_ref.text = "Auto-save: error (check logs)"
        else:
            ingestion_label_ref.text = f"Indexed: {chunks_indexed} chunks" if chunks_indexed > 0 else ""
    except Exception:
        pass


async def refresh_budget_status(label_ref, state: GuiState) -> None:
    """Fetch budget status and update the footer label."""
    while True:
        try:
            budget = await state.api_client.get_budget_status(scope="session", scope_id="default")
            consumed = budget.get("consumed_tokens", 0)
            limit = budget.get("limit", 0)
            fraction = budget.get("fraction_used", 0)
            if limit > 0:
                pct = f"{fraction:.0%}"
                label_ref.text = f"Budget: {consumed}/{limit} ({pct})"
                if fraction >= 0.8:
                    label_ref.classes("text-[10px] text-negative", remove="text-grey-6 text-black")
                else:
                    label_ref.classes("text-[10px] text-grey-6", remove="text-negative text-black")
            elif budget.get("budget_manager") is False:
                label_ref.text = "Budget: n/a"
        except Exception:
            label_ref.text = ""
        await asyncio.sleep(30)


# ---------------------------------------------------------------------------
# API Key Prompt — BLOCKING dialog that MUST be resolved before proceeding
# ---------------------------------------------------------------------------


async def show_api_key_prompt() -> str | None:
    """Show a BLOCKING dialog asking for the OpenRouter API key.

    This dialog cannot be dismissed without entering a key or explicitly
    skipping. Returns the key if provided, None if skipped.
    """
    with ui.dialog().props("persistent") as dialog, ui.card().classes("p-6 min-w-[480px]"):
        ui.icon("vpn_key", size="lg", color="amber").classes("q-mb-sm")
        ui.label("OpenRouter API Key Required").classes("text-h5 text-weight-bold")
        ui.label(
            "AIP_Brain uses OpenRouter for ALL model slots (chat, beast, vigil, embed). "
            "Enter your OpenRouter API key to get started. "
            "You can get one at openrouter.ai/keys"
        ).classes("text-body2 q-mt-sm")
        key_input = ui.input(
            placeholder="sk-or-v1-...",
            password=True,
        ).props("outlined dense").classes("w-full q-mt-md")
        with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
            ui.button("Skip (limited functionality)", color="grey", on_click=lambda: dialog.submit(None))
            ui.button("Save Key", color="primary", on_click=lambda: dialog.submit(key_input.value.strip()))

    result = await dialog
    return result


# ---------------------------------------------------------------------------
# Model Dropdown Builder — builds universal dropdown from selected models
# ---------------------------------------------------------------------------


def build_model_options(slots: list[dict[str, Any]]) -> list[str]:
    """Build the universal model dropdown options list.

    Priority:
    1. Models selected from OpenRouter catalog (persisted in selected_models.json)
    2. Models currently configured in backend slots
    3. Fallback message
    """
    selected = get_selected_models()
    backend_models = [
        s.get("model", "")
        for s in slots
        if s.get("model") and not s.get("model", "").startswith("<")
    ]
    all_options = list(dict.fromkeys(selected + backend_models))
    if not all_options:
        all_options = ["(no models — go to Models page to select)"]
    return all_options


# ---------------------------------------------------------------------------
# Page Definitions
# ---------------------------------------------------------------------------


@ui.page("/")
async def main_page():
    """Main chat page — AIP_Brain frontend."""
    global chat_container, input_field, mode_label, ingestion_label_ref

    ui.page_title("AIP_Brain")
    state = get_state()

    # ---- STEP 1: API Key Check — BLOCKING ----
    # This MUST happen before anything else. If the key is missing,
    # show a blocking dialog. The user cannot proceed without entering
    # a key or explicitly skipping.
    if not state.api_client.has_openrouter_api_key():
        key = await show_api_key_prompt()
        if key:
            state.api_client.set_openrouter_api_key(key)
            ui.notify("API key saved! All OpenRouter calls will use it.", color="positive", position="top")
        else:
            ui.notify("No API key set. Model catalog and chat will not work. You can set it later via Models page.", color="warning", position="top")

    # ---- STEP 2: Load backend data ----
    backend_status = await check_backend_health()
    slots = await load_model_slots()

    # ---- STEP 3: Build universal model dropdown ----
    all_model_options = build_model_options(slots)

    # Determine current chat model — prefer assigned model, then first available
    current_chat_model = get_role_model("synthesis")
    if not current_chat_model or current_chat_model not in all_model_options:
        # Try to use the backend's configured synthesis model
        for s in slots:
            if s.get("slot_name") == "synthesis" and s.get("model", "") and not s["model"].startswith("<"):
                current_chat_model = s["model"]
                break
    if not current_chat_model or current_chat_model not in all_model_options:
        current_chat_model = all_model_options[0] if all_model_options else ""

    # Build slot model map for sidebar defaults
    slot_models = {s["slot_name"]: s.get("model", "") for s in slots}

    # ---- HEADER ----
    with ui.header(elevated=True).classes("bg-primary text-white items-center q-pa-xs"):
        ui.label("AIP_Brain").classes("text-h6 q-ml-sm")
        ui.button("Chat", on_click=lambda: set_mode("normal")).props("flat text-color=white dense")
        ui.button("Augmented", on_click=lambda: set_mode("augmented")).props(
            "flat text-color=yellow-3 dense outline"
        ).classes("q-mr-xs")
        mode_label = ui.label("Chat").classes("q-ml-xs text-caption text-white")
        # Augmented mode indicator
        ui.badge("USING INGESTED DATA", color="amber").classes(
            "text-[9px] q-ml-xs"
        ).bind_visibility_from(state, "current_mode", backward=lambda m: m == "augmented")
        ui.space()

        # --- Chat Model selector (amber bordered) ---
        with ui.row().classes(
            "items-center q-pa-xs rounded-borders"
        ).style("background: rgba(255,255,255,0.15); border: 2px solid #FFC107; border-radius: 6px;"):
            ui.icon("chat", size="xs").classes("text-amber q-mr-xs")
            ui.label("Chat Model").classes("text-caption text-amber text-weight-bold q-mr-xs")
            chat_model_select = ui.select(
                all_model_options,
                value=current_chat_model,
                on_change=lambda e: on_chat_model_changed(e.value),
            ).classes("min-w-[180px] text-black").props("dense")

        ui.checkbox(
            "Auto-save", value=True,
            on_change=lambda e: asyncio.create_task(on_auto_save_toggled(e.value)),
        ).classes("q-ml-xs text-caption text-white")
        ui.space()

        # API key indicator
        key_set = state.api_client.has_openrouter_api_key()
        ui.button(
            icon="vpn_key",
            color="positive" if key_set else "negative",
            on_click=lambda: asyncio.create_task(show_key_dialog_and_update()),
        ).props("flat dense round").tooltip("OpenRouter API Key" if key_set else "API Key Missing — Click to set")

        ui.button("Models", on_click=lambda: ui.navigate.to("/models")).props("flat text-color=white dense")
        with ui.row().classes("items-center gap-0"):
            ui.button(icon="storage", on_click=lambda: ui.navigate.to("/vector")).props("flat text-color=white dense round")
            ui.button(icon="account_tree", on_click=lambda: ui.navigate.to("/graph")).props("flat text-color=white dense round")
            ui.button(icon="menu_book", on_click=lambda: ui.navigate.to("/wiki")).props("flat text-color=white dense round")
            ui.button(icon="source", on_click=lambda: ui.navigate.to("/sources")).props("flat text-color=white dense round")
            ui.button(icon="rate_review", on_click=lambda: ui.navigate.to("/review")).props("flat text-color=white dense round")

    # ---- RIGHT DRAWER — Actor Roles (compact) ----
    with ui.right_drawer(fixed=True).classes("q-pa-xs bg-grey-2").style("width: 240px;"):
        with ui.row().classes("w-full items-center no-wrap"):
            ui.label("Actor Roles").classes("text-subtitle2 text-weight-bold")
            ui.space()
            ui.badge("OpenRouter", color="deep-orange").classes("text-[9px]")

        if state.backend_reachable:
            try:
                actors_data = await state.api_client.get_actors_status()
                actors = actors_data.get("actors", {})
            except Exception:
                actors = {}
        else:
            actors = {}

        # Each actor gets its own model dropdown from the universal model list
        actor_defs = [
            ("synthesis", "Beast", "brown", "rgba(121,85,72,0.08)", "beast"),
            ("evaluation", "Vigil", "indigo", "rgba(63,81,181,0.08)", "vigil"),
            ("embedding", "Embed", "teal", "rgba(0,150,136,0.08)", "embedding"),
            ("sexton", "Sexton", "grey", "rgba(0,0,0,0.04)", "sexton"),
        ]

        for slot_name, label, border_color, bg_color, actor_key in actor_defs:
            with ui.row().classes(
                "w-full items-center no-wrap q-px-xs q-py-none q-mt-xs rounded-borders"
            ).style(f"background: {bg_color}; border-left: 3px solid {border_color};"):
                actor = actors.get(actor_key, {})
                actor_init = actor.get("initialized", False)
                ui.icon(
                    "check_circle" if actor_init else "cancel",
                    color="positive" if actor_init else "negative",
                    size="xs",
                )
                ui.label(label).classes("text-[11px] text-weight-bold q-mr-xs")
                # Model dropdown for this role
                current_role_model = get_role_model(slot_name)
                role_default = slot_models.get(slot_name, "")
                role_value = current_role_model or role_default or (all_model_options[0] if all_model_options else "")
                if role_value not in all_model_options:
                    role_value = all_model_options[0] if all_model_options else ""
                ui.select(
                    all_model_options,
                    value=role_value,
                    on_change=lambda e, sn=slot_name: on_role_model_changed(sn, e.value),
                ).props("dense").classes("flex-grow text-[11px]")

        # Trigger buttons (compact)
        ui.separator().classes("q-my-xs")
        with ui.row().classes("w-full gap-1"):
            ui.button("Run B", color="brown",
                      on_click=lambda: asyncio.create_task(trigger_actor("beast"))).props("size=xs dense")
            ui.button("Run V", color="indigo",
                      on_click=lambda: asyncio.create_task(trigger_actor("vigil"))).props("size=xs dense")
            ui.button("Run S", color="teal",
                      on_click=lambda: asyncio.create_task(trigger_actor("sexton"))).props("size=xs dense")

        # Slot details — collapsible
        ui.separator().classes("q-my-xs")
        with ui.expansion("Slot Details", group="slots").classes("w-full text-[11px]"):
            if slots:
                for s in slots:
                    model_name = s.get("model", "N/A")
                    provider = s.get("provider", "?")
                    ui.label(f"{s['slot_name']}: {model_name} ({provider})").classes("text-[11px] q-mb-none")
            else:
                ui.label("No slots loaded — backend may not be running").classes("text-[11px]")

    # ---- CHAT AREA ----
    chat_container = ui.column().classes("w-full max-w-3xl mx-auto q-px-md q-py-sm").style("min-height: 400px;")

    if not state.backend_reachable:
        with chat_container:
            ui.label("AIP Backend is not reachable. Please ensure the FastAPI server is running at " + state.api_client.base_url).classes(
                "text-negative text-weight-medium q-pa-md"
            )
            ui.label("Start the backend with: uvicorn aip.adapter.api.app:create_app --factory --port 8000").classes("text-caption q-px-md")
    else:
        with chat_container:
            api_key_status = "API key: Set" if state.api_client.has_openrouter_api_key() else "API key: MISSING"
            selected_count = len(get_selected_models())
            ui.label(
                f"Connected to AIP Backend. {len(slots)} slot(s). {api_key_status}. "
                f"{selected_count} model(s) selected from catalog."
            ).classes("text-positive text-caption q-pa-sm" if state.api_client.has_openrouter_api_key() else "text-warning text-caption q-pa-sm")

    # ---- INPUT AREA ----
    with ui.row().classes("w-full max-w-3xl mx-auto items-center q-pa-sm gap-2"):
        input_field = ui.input(placeholder="Ask anything...").props("outlined dense").classes("flex-grow")
        input_field.on("keydown.enter", lambda: asyncio.create_task(send_prompt()))
        ui.button("Send", on_click=lambda: asyncio.create_task(send_prompt()), color="primary").props("icon=send")

    # ---- FOOTER ----
    with ui.footer().classes("bg-grey-2 q-pa-xs items-center"):
        ui.label("aip_brain").classes("text-[10px] text-grey-6")
        ingestion_label_ref = ui.label("").classes("text-[10px] text-grey-6")
        budget_label_ref = ui.label("").classes("text-[10px] text-grey-6")
        ui.space()
        ui.label(backend_status).classes("text-[10px] text-grey-6")
        ci_status = "CI" if any(s.get("model", "").startswith("<") for s in slots) else "Live"
        ui.label(f"{ci_status}").classes("text-[10px] text-grey-6")

    asyncio.create_task(refresh_budget_status(budget_label_ref, state))


async def show_key_dialog_and_update():
    """Show the API key dialog from the header key icon."""
    state = get_state()
    with ui.dialog() as dialog, ui.card().classes("p-6 min-w-[480px]"):
        ui.label("OpenRouter API Key").classes("text-h6")
        current = state.api_client.get_openrouter_api_key() or ""
        masked = current[:8] + "..." + current[-4:] if len(current) > 12 else "(not set)"
        ui.label(f"Current: {masked}").classes("text-caption q-mt-xs")
        key_input = ui.input(placeholder="sk-or-v1-...", password=True).props("outlined dense").classes("w-full q-mt-md")
        with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
            ui.button("Cancel", color="grey", on_click=lambda: dialog.submit(None))
            ui.button("Save", color="primary", on_click=lambda: dialog.submit(key_input.value.strip()))

    result = await dialog
    if result:
        state.api_client.set_openrouter_api_key(result)
        ui.notify("API key updated! It will be used for all OpenRouter calls.", color="positive", position="top")


def on_chat_model_changed(model_id: str) -> None:
    """Handle chat model selection change from the header dropdown.

    Updates the GUI state AND pushes the model change to the backend
    so that the next chat message uses the selected model.
    """
    state = get_state()
    # The chat always uses the "synthesis" slot
    set_role_model("synthesis", model_id)
    state.reset_session()
    # Push to backend — set AIP_SYNTHESIS_MODEL env var via API
    api_key = state.api_client.get_openrouter_api_key()
    asyncio.create_task(
        state.api_client.update_slot_model("synthesis", model_id, api_key=api_key)
    )
    ui.notify(f"Chat model → {model_id}", color="info")


def on_role_model_changed(slot_name: str, model_id: str) -> None:
    """Handle model change for an actor role in the sidebar.

    Updates the GUI state AND pushes the model change to the backend
    so that the next actor call uses the selected model.
    """
    set_role_model(slot_name, model_id)
    state = get_state()
    api_key = state.api_client.get_openrouter_api_key()
    asyncio.create_task(
        state.api_client.update_slot_model(slot_name, model_id, api_key=api_key)
    )
    ui.notify(f"{slot_name.capitalize()} → {model_id}", color="info")


# ---------------------------------------------------------------------------
# Model Catalog Page — OpenRouter Model Browser
# ---------------------------------------------------------------------------


@ui.page("/models")
async def model_catalog_page():
    """Model catalog — browse all OpenRouter models, select which to use."""
    ui.page_title("Model Catalog — AIP_Brain")
    state = get_state()

    # ---- STEP 1: API Key Check — BLOCKING ----
    if not state.api_client.has_openrouter_api_key():
        key = await show_api_key_prompt()
        if key:
            state.api_client.set_openrouter_api_key(key)
            ui.notify("API key saved! Loading models...", color="positive", position="top")
        else:
            ui.notify("No API key — model catalog requires an OpenRouter key.", color="warning", position="top")

    # ---- HEADER ----
    with ui.header().classes("bg-primary text-white items-center q-pa-xs"):
        ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props("flat text-color=white dense round")
        ui.label("Model Catalog").classes("text-h6")
        ui.space()
        # API key status indicator
        key_status = "Key: Set" if state.api_client.has_openrouter_api_key() else "Key: Missing"
        key_color = "positive" if state.api_client.has_openrouter_api_key() else "negative"
        ui.badge(key_status, color=key_color).classes("text-[10px] q-mr-sm")
        ui.button(icon="vpn_key", on_click=lambda: asyncio.create_task(show_key_dialog_and_reload())).props(
            "flat text-color=white dense round"
        )
        ui.button(icon="chat", on_click=lambda: ui.navigate.to("/")).props("flat text-color=white dense round")

    # ---- API KEY WARNING (if still missing after prompt) ----
    if not state.api_client.has_openrouter_api_key():
        with ui.card().classes("w-full max-w-4xl mx-auto q-mt-md q-pa-md").style("border: 2px solid #F44336;"):
            ui.icon("warning", size="lg", color="negative")
            ui.label("OpenRouter API Key Required").classes("text-h6 text-negative")
            ui.label(
                "You need an OpenRouter API key to browse and select models. "
                "Get one at openrouter.ai/keys. Click the key icon above to enter it."
            ).classes("text-caption q-mt-xs")
        return  # Don't show the rest of the page without a key

    # ---- MAIN CONTENT ----
    content_area = ui.column().classes("w-full max-w-6xl mx-auto q-pa-md")

    # Selected models summary
    selected_summary = ui.label(f"Selected: {len(get_selected_models())} models").classes("text-subtitle2 q-mb-sm")

    # Model table container
    table_container = ui.column().classes("w-full")

    # Load button
    with ui.row().classes("w-full items-center gap-2 q-mb-md"):
        ui.button("Load OpenRouter Models", icon="cloud_download", color="primary",
                  on_click=lambda: asyncio.create_task(load_openrouter_models()))
        ui.button("Clear Selection", color="grey",
                  on_click=lambda: clear_selection())
        ui.button("Apply to Chat", color="positive",
                  on_click=lambda: ui.navigate.to("/"))
        # Search filter
        filter_input = ui.input(placeholder="Filter models...", on_change=lambda e: asyncio.create_task(load_openrouter_models(filter_text=e.value))).props(
            "outlined dense clearable"
        ).classes("q-ml-md flex-grow")

    async def show_key_dialog_and_reload():
        """Show key dialog and reload models after saving."""
        with ui.dialog() as dialog, ui.card().classes("p-6 min-w-[480px]"):
            ui.label("OpenRouter API Key").classes("text-h6")
            current = state.api_client.get_openrouter_api_key() or ""
            masked = current[:8] + "..." + current[-4:] if len(current) > 12 else "(not set)"
            ui.label(f"Current: {masked}").classes("text-caption q-mt-xs")
            key_input = ui.input(placeholder="sk-or-v1-...", password=True).props("outlined dense").classes("w-full q-mt-md")
            with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
                ui.button("Cancel", color="grey", on_click=lambda: dialog.submit(None))
                ui.button("Save", color="primary", on_click=lambda: dialog.submit(key_input.value.strip()))

        result = await dialog
        if result:
            state.api_client.set_openrouter_api_key(result)
            ui.notify("API key updated!", color="positive", position="top")
            await load_openrouter_models()

    async def load_openrouter_models(filter_text: str = ""):
        """Fetch models from OpenRouter and display them in a table."""
        table_container.clear()
        with table_container:
            ui.label("Fetching models from OpenRouter...").classes("text-grey")
            ui.spinner("dots", size="lg", color="primary")

        try:
            api_key = state.api_client.get_openrouter_api_key()
            models = await state.api_client.list_openrouter_models(api_key=api_key)

            table_container.clear()
            with table_container:
                if not models:
                    ui.label("No models found. Check your API key.").classes("text-warning")
                    return

                # Apply text filter
                if filter_text:
                    filter_lower = filter_text.lower()
                    models = [m for m in models if filter_lower in m.get("id", "").lower()]

                # Sort models: free first, then by prompt cost ascending
                def sort_key(m):
                    pricing = m.get("pricing", {})
                    prompt_cost = float(pricing.get("prompt", "1") or "1")
                    is_free = prompt_cost == 0
                    return (0 if is_free else 1, prompt_cost)

                models.sort(key=sort_key)

                ui.label(f"{len(models)} text models available from OpenRouter").classes("text-caption q-mb-sm")

                # Build table data
                columns = [
                    {"name": "select", "label": "", "field": "select", "align": "center", "sortable": False},
                    {"name": "model_id", "label": "Model ID", "field": "model_id", "align": "left", "sortable": True},
                    {"name": "prompt_cost", "label": "Prompt $/1M tok", "field": "prompt_cost", "align": "right", "sortable": True},
                    {"name": "completion_cost", "label": "Completion $/1M tok", "field": "completion_cost", "align": "right", "sortable": True},
                    {"name": "context", "label": "Context", "field": "context", "align": "right", "sortable": True},
                    {"name": "updated", "label": "Updated", "field": "updated", "align": "left", "sortable": True},
                ]

                selected = get_selected_models()
                rows = []
                for m in models:
                    pricing = m.get("pricing", {})
                    prompt_cost = float(pricing.get("prompt", "0") or "0")
                    comp_cost = float(pricing.get("completion", "0") or "0")
                    context = m.get("context_length", 0) or 0
                    mid = m.get("id", "")
                    updated = m.get("updated", "") or ""

                    # Format cost display
                    if prompt_cost == 0:
                        cost_str = "FREE"
                        comp_str = "FREE"
                    else:
                        cost_str = f"${prompt_cost:.4f}"
                        comp_str = f"${comp_cost:.4f}"

                    rows.append({
                        "select": mid in selected,
                        "model_id": mid,
                        "prompt_cost": cost_str,
                        "completion_cost": comp_str,
                        "context": f"{context:,}" if context else "?",
                        "updated": updated[:10] if updated else "?",
                    })

                # Use ui.table with selection
                selected_models_set = set(selected)

                table = ui.table(
                    columns=columns,
                    rows=rows,
                    row_key="model_id",
                    selection="multiple",
                    selected=[r for r in rows if r["model_id"] in selected_models_set],
                ).classes("w-full").props('flat bordered dense virtual-scroll')

                # Style free models in green
                table.add_slot('body-cell-prompt_cost', '''
                    <q-td :props="props">
                        <span :class="props.value === 'FREE' ? 'text-positive text-weight-bold' : ''">
                            {{ props.value }}
                        </span>
                    </q-td>
                ''')

                # Style model IDs that are selected
                table.add_slot('body-cell-model_id', '''
                    <q-td :props="props">
                        <span :class="props.row.select ? 'text-weight-bold text-primary' : ''">
                            {{ props.value }}
                        </span>
                    </q-td>
                ''')

                def on_selection_change(e):
                    selected_ids = [r["model_id"] for r in e.args["selected"]]
                    set_selected_models(selected_ids)
                    selected_summary.text = f"Selected: {len(selected_ids)} models"

                table.on('selection', on_selection_change)

        except Exception as exc:
            table_container.clear()
            with table_container:
                ui.label(f"Failed to load models: {exc}").classes("text-negative q-pa-md")
                ui.label("Make sure your API key is valid and you have internet access.").classes("text-caption")

    def clear_selection():
        """Clear all selected models."""
        set_selected_models([])
        selected_summary.text = "Selected: 0 models"
        ui.notify("Selection cleared", color="info")
        # Reload the table to reflect the cleared selection
        asyncio.create_task(load_openrouter_models())

    def on_save_key(key_value: str):
        """Save the API key from the input field."""
        if key_value and key_value.strip():
            state.api_client.set_openrouter_api_key(key_value.strip())
            ui.notify("API key saved!", color="positive")
            # Reload the page to refresh the key status
            asyncio.create_task(load_openrouter_models())

    # Load backend slots info
    slots = await load_model_slots()

    # Show currently configured slots
    with content_area:
        # Selected models counter
        selected_summary

        # Currently configured slots
        if slots:
            ui.label("Currently Configured Slots (backend)").classes("text-subtitle2 q-mb-sm")
            with ui.row().classes("w-full gap-2 q-mb-md flex-wrap"):
                for s in slots:
                    model_name = s.get("model", "?")
                    provider = s.get("provider", "?")
                    is_ci = model_name.startswith("<")
                    slot_name = s.get("slot_name", "?")
                    role_model = get_role_model(slot_name)
                    display_model = role_model or model_name

                    with ui.card().classes("min-w-[160px] flex-1").style(
                        "border-left: 3px solid #FF5722;"
                    ):
                        with ui.card_section():
                            ui.label(slot_name).classes("text-weight-bold text-[12px]")
                            ui.label(display_model).classes(
                                "text-[11px] text-positive" if not is_ci else "text-[11px] text-warning"
                            )
                            ui.label(f"via {provider}").classes("text-[10px] text-grey-7")
        else:
            ui.label("No slots configured in backend (check config/aip.config.toml)").classes("text-warning q-mb-md")

        # Table load area
        table_container

    # Auto-load models if API key is available
    if state.api_client.has_openrouter_api_key():
        await load_openrouter_models()

    ui.button("Back to Chat", on_click=lambda: ui.navigate.to("/"), color="grey").classes("q-mt-md")


# ---------------------------------------------------------------------------
# Phase 4: Knowledge Exploration Pages
# ---------------------------------------------------------------------------


@ui.page("/vector")
async def vector_search_page():
    """Vector search panel — search across all indexed content."""
    ui.page_title("Vector Search — AIP_Brain")
    state = get_state()

    ui.label("Vector Search").classes("text-h4 q-my-md")
    ui.label("Search across all indexed content using lexical (FTS5) and vector (semantic) search.").classes(
        "text-subtitle2 q-mb-md"
    )

    with ui.row().classes("w-full items-center gap-2 q-mb-md"):
        search_input = ui.input(placeholder="Search for content...").props("outlined dense").classes("flex-grow")
        search_input.on("keydown.enter", lambda: asyncio.create_task(do_search()))
        ui.button("Search", on_click=lambda: asyncio.create_task(do_search()), icon="search", color="primary")

    results_container = ui.column().classes("w-full")

    async def do_search():
        query = search_input.value.strip()
        if not query:
            ui.notify("Please enter a search query", color="warning")
            return

        results_container.clear()
        with results_container:
            ui.label("Searching...").classes("text-grey")

        try:
            result = await state.api_client.ask_retrieve(question=query, max_sources=20)
            sources = result.get("sources", [])
            results_container.clear()
            with results_container:
                if not sources:
                    ui.label("No results found.").classes("text-warning q-pa-md")
                else:
                    ui.label(f"{len(sources)} result(s)").classes("text-weight-medium q-mb-sm")
                    for src in sources:
                        with ui.card().classes("w-full q-mb-sm"):
                            with ui.card_section():
                                title = src.get("title", src.get("source_id", "Untitled"))
                                score = src.get("score", 0)
                                domain = src.get("domain", "")
                                content_preview = src.get("content_preview", src.get("chunk_text", ""))

                                with ui.row().classes("items-center gap-2"):
                                    ui.label(title).classes("text-weight-medium")
                                    ui.badge(f"Score: {score:.2f}", color="blue")
                                    if domain:
                                        ui.badge(domain, color="teal")

                                if content_preview:
                                    ui.label(content_preview[:500] + ("..." if len(content_preview) > 500 else "")).classes("text-caption q-mt-xs")
        except Exception as exc:
            results_container.clear()
            with results_container:
                ui.label(f"Search failed: {exc}").classes("text-negative")


@ui.page("/graph")
async def ecs_graph_page():
    """ECS State Graph page — visualize artifact states and transitions."""
    ui.page_title("ECS Graph — AIP_Brain")
    state = get_state()

    ui.label("ECS State Graph").classes("text-h4 q-my-md")

    graph_container = ui.column().classes("w-full")

    async def load_graph():
        graph_container.clear()
        with graph_container:
            ui.label("Loading ECS graph...").classes("text-grey")

        try:
            graph_data = await state.api_client.get_ecs_graph()
            graph_container.clear()
            with graph_container:
                states = graph_data.get("states", {})
                transitions = graph_data.get("transitions", [])
                artifact_counts = graph_data.get("artifact_counts", {})

                if not states:
                    ui.label("No ECS data available.").classes("text-warning q-pa-md")
                    return

                ui.label(f"States: {len(states)}, Transitions: {len(transitions)}").classes("text-weight-medium q-mb-md")

                with ui.row().classes("w-full gap-4 flex-wrap"):
                    for state_name, count in artifact_counts.items():
                        with ui.card().classes("min-w-[100px]"):
                            with ui.card_section():
                                ui.label(state_name).classes("text-weight-bold")
                                ui.label(str(count)).classes("text-h6 text-primary")

                if transitions:
                    ui.label("Recent Transitions").classes("text-subtitle2 q-mt-md q-mb-sm")
                    for t in transitions[:20]:
                        from_state = t.get("from_state", "?")
                        to_state = t.get("to_state", "?")
                        artifact = t.get("artifact_id", "?")
                        timestamp = t.get("timestamp", "")
                        ui.label(f"{artifact}: {from_state} → {to_state} ({timestamp})").classes("text-caption")

        except Exception as exc:
            graph_container.clear()
            with graph_container:
                ui.label(f"Failed to load: {exc}").classes("text-negative")

    await load_graph()
    ui.button("Refresh", on_click=lambda: asyncio.create_task(load_graph()), icon="refresh", color="primary").classes("q-mt-md")
    ui.button("Back to Chat", on_click=lambda: ui.navigate.to("/"), color="grey").classes("q-mt-md")


@ui.page("/wiki")
async def wiki_page():
    """Wiki / Knowledge Browser page."""
    ui.page_title("Knowledge Browser — AIP_Brain")
    state = get_state()

    ui.label("Knowledge Browser").classes("text-h4 q-my-md")
    ui.label("Browse compiled knowledge items. Search or filter by domain.").classes("text-subtitle2 q-mb-md")

    with ui.row().classes("w-full items-center gap-2 q-mb-md"):
        search_input = ui.input(placeholder="Search knowledge...").props("outlined dense").classes("flex-grow")
        search_input.on("keydown.enter", lambda: asyncio.create_task(search_knowledge()))
        ui.button("Search", on_click=lambda: asyncio.create_task(search_knowledge()), icon="search", color="primary")

    knowledge_container = ui.column().classes("w-full")

    async def search_knowledge():
        query = search_input.value.strip()
        if not query:
            await load_all_knowledge()
            return

        knowledge_container.clear()
        with knowledge_container:
            ui.label("Searching...").classes("text-grey")

        try:
            result = await state.api_client.search_knowledge(q=query, limit=20)
            items = result.get("items", result.get("results", []))
            knowledge_container.clear()
            with knowledge_container:
                if not items:
                    ui.label("No knowledge items found.").classes("text-warning q-pa-md")
                else:
                    ui.label(f"{len(items)} result(s)").classes("text-weight-medium q-mb-sm")
                    for item in items:
                        with ui.card().classes("w-full q-mb-sm"):
                            with ui.card_section():
                                kid = item.get("id", "?")
                                title = item.get("title", kid)
                                domain = item.get("domain", "")
                                confidence = item.get("confidence", 0)
                                content = item.get("content", item.get("summary", ""))

                                with ui.row().classes("items-center gap-2"):
                                    ui.label(title).classes("text-weight-medium")
                                    if domain:
                                        ui.badge(domain, color="blue")
                                    ui.badge(f"Conf: {confidence:.2f}", color="green")

                                if content:
                                    ui.label(content[:500] + ("..." if len(content) > 500 else "")).classes("text-caption q-mt-xs")
        except Exception as exc:
            knowledge_container.clear()
            with knowledge_container:
                ui.label(f"Search failed: {exc}").classes("text-negative")

    async def load_all_knowledge():
        knowledge_container.clear()
        with knowledge_container:
            ui.label("Loading knowledge items...").classes("text-grey")

        try:
            result = await state.api_client.list_knowledge()
            items = result.get("items", [])
            knowledge_container.clear()
            with knowledge_container:
                if not items:
                    ui.label("No knowledge items yet.").classes("text-info q-pa-md")
                else:
                    ui.label(f"{len(items)} knowledge item(s)").classes("text-weight-medium q-mb-sm")
                    for item in items:
                        with ui.card().classes("w-full q-mb-sm"):
                            with ui.card_section():
                                kid = item.get("id", "?")
                                title = item.get("title", kid)
                                domain = item.get("domain", "")
                                state_val = item.get("state", "")
                                confidence = item.get("confidence", 0)

                                with ui.row().classes("items-center gap-2"):
                                    ui.label(title).classes("text-weight-medium")
                                    if domain:
                                        ui.badge(domain, color="blue")
                                    if state_val:
                                        ui.badge(state_val, color="orange")
                                    ui.badge(f"Conf: {confidence:.2f}", color="green")
        except Exception as exc:
            knowledge_container.clear()
            with knowledge_container:
                ui.label(f"Failed to load: {exc}").classes("text-negative")

    await load_all_knowledge()
    ui.button("Back to Chat", on_click=lambda: ui.navigate.to("/"), color="grey").classes("q-mt-md")


@ui.page("/sources")
async def sources_page():
    """Sources page — list and browse indexed sources."""
    ui.page_title("Sources — AIP_Brain")
    state = get_state()

    ui.label("Indexed Sources").classes("text-h4 q-my-md")

    sources_container = ui.column().classes("w-full")

    async def load_sources():
        sources_container.clear()
        with sources_container:
            ui.label("Loading sources...").classes("text-grey")

        try:
            result = await state.api_client.list_sources()
            items = result.get("sources", result.get("items", []))
            stats = await state.api_client.get_sources_stats()

            sources_container.clear()
            with sources_container:
                # Stats summary
                total_sources = stats.get("total_sources", len(items))
                total_chunks = stats.get("total_chunks", 0)
                ui.label(f"Total: {total_sources} sources, {total_chunks} chunks").classes("text-weight-medium q-mb-md")

                if not items:
                    ui.label("No sources indexed yet.").classes("text-info q-pa-md")
                else:
                    for src in items:
                        with ui.card().classes("w-full q-mb-sm"):
                            with ui.card_section():
                                sid = src.get("id", src.get("source_id", "?"))
                                title = src.get("title", sid)
                                domain = src.get("domain", "")
                                chunk_count = src.get("chunk_count", 0)

                                with ui.row().classes("items-center gap-2"):
                                    ui.label(title).classes("text-weight-medium")
                                    if domain:
                                        ui.badge(domain, color="blue")
                                    ui.badge(f"{chunk_count} chunks", color="teal")
        except Exception as exc:
            sources_container.clear()
            with sources_container:
                ui.label(f"Failed to load: {exc}").classes("text-negative")

    await load_sources()
    ui.button("Back to Chat", on_click=lambda: ui.navigate.to("/"), color="grey").classes("q-mt-md")


# ---------------------------------------------------------------------------
# Phase 5: Review Queue Page
# ---------------------------------------------------------------------------


@ui.page("/review")
async def review_queue_page():
    """Review Queue page — approve or reject artifacts pending review."""
    ui.page_title("Review Queue — AIP_Brain")
    state = get_state()

    ui.label("Review Queue").classes("text-h4 q-my-md")
    ui.label("Artifacts pending DEFINER review. Approve to promote to canonical, reject to mark as failed.").classes("text-subtitle2 q-mb-md")

    review_container = ui.column().classes("w-full")

    async def load_reviews():
        review_container.clear()
        with review_container:
            ui.label("Loading pending reviews...").classes("text-grey")
        try:
            items = await state.api_client.list_pending_reviews()
            review_container.clear()
            with review_container:
                if not items:
                    ui.label("No pending reviews.").classes("text-positive q-pa-md")
                else:
                    ui.label(f"{len(items)} item(s) pending review").classes("text-weight-medium q-mb-sm")
                    for item in items:
                        with ui.card().classes("w-full q-mb-sm"):
                            with ui.card_section():
                                artifact_id = item.get("artifact_id", item.get("id", "?"))
                                domain = item.get("domain", "")
                                state_val = item.get("state", item.get("ecs_state", "REVIEWED"))
                                summary = item.get("summary", item.get("content_preview", ""))

                                ui.label(f"Artifact: {artifact_id}").classes("text-weight-medium")
                                with ui.row().classes("gap-2 q-mb-sm"):
                                    ui.badge(state_val, color="warning")
                                    if domain:
                                        ui.badge(domain, color="blue")

                                if summary:
                                    ui.label(summary[:500] + ("..." if len(summary) > 500 else "")).classes("text-caption q-mb-sm")

                                with ui.row().classes("gap-2"):
                                    ui.button(
                                        "Approve",
                                        color="positive",
                                        on_click=lambda aid=artifact_id: asyncio.create_task(
                                            handle_review_decision(aid, True)
                                        ),
                                    ).props("size=sm dense")
                                    ui.button(
                                        "Reject",
                                        color="negative",
                                        on_click=lambda aid=artifact_id: asyncio.create_task(
                                            handle_review_decision(aid, False)
                                        ),
                                    ).props("size=sm dense")
        except Exception as exc:
            review_container.clear()
            with review_container:
                ui.label(f"Failed to load reviews: {exc}").classes("text-negative")

    async def handle_review_decision(artifact_id: str, approved: bool):
        try:
            if approved:
                result = await state.api_client.approve_review(artifact_id)
            else:
                result = await state.api_client.reject_review(artifact_id)

            new_state = result.get("new_state", "unknown")
            canonical_written = result.get("canonical_written", False)
            decision_text = "Approved" if approved else "Rejected"

            ui.notify(
                f"{decision_text}: {artifact_id} → {new_state}" +
                (f" (canonical written)" if canonical_written else ""),
                color="positive" if approved else "warning",
            )
            await load_reviews()
        except Exception as exc:
            ui.notify(f"Review decision failed: {exc}", color="negative")

    await load_reviews()
    ui.button("Back to Chat", on_click=lambda: ui.navigate.to("/"), color="grey").classes("q-mt-md")


# ---------------------------------------------------------------------------
# Application startup
# ---------------------------------------------------------------------------

ui.run(title="AIP_Brain", port=8080, reload=True)
