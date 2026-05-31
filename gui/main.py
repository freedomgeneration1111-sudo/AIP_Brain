"""AIP_Brain NiceGUI Frontend — Phase 1 Communication Bridge.

This module implements the NiceGUI frontend that communicates EXCLUSIVELY
through the AIP FastAPI backend's REST and WebSocket endpoints. It does NOT:
  - Import from aip.orchestration
  - Directly access AipContainer
  - Make direct HTTP calls to Ollama or OpenRouter
  - Read enabled_models.json directly

All chat interactions flow through:
  1. POST /api/v1/sessions → get session_id
  2. WebSocket /api/v1/chat/{session_id} → send messages, receive responses
  3. GET /api/v1/models/slots → populate model/role dropdowns

This follows the API-First approach: the GUI is an Adapter-layer surface
like CLI and MCP, communicating via HTTP/WebSocket rather than in-process.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nicegui import ui

from gui.api_client import get_api_client, AipApiClient

# ---------------------------------------------------------------------------
# State management — kept simple for Phase 1
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


# Per-page state — initialized in the page function
_state: GuiState | None = None


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

    # Check if backend is reachable
    if not state.backend_reachable:
        ui.notify("Backend is not reachable. Please check that the AIP FastAPI server is running.", color="negative")
        return

    # Ensure we have a session
    try:
        session_id = await state.ensure_session()
    except Exception as exc:
        ui.notify(f"Failed to create session: {exc}", color="negative")
        return

    # Display user message
    add_message("user", prompt)
    input_field.value = ""

    # Show thinking indicator
    thinking_label = ui.label("Thinking...").classes("text-grey")

    # Define response callbacks
    def on_response(resp: dict[str, Any]) -> None:
        thinking_label.delete()
        content = resp.get("content", "")
        model = resp.get("model", resp.get("model_slot", ""))
        latency = resp.get("latency_ms")
        tokens = resp.get("tokens_used", 0)
        add_message("assistant", content, model=model, latency_ms=latency)
        if tokens > 0:
            add_system_message(f"Tokens: {tokens}")

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
        # Show approval buttons
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

    # Send via WebSocket
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

    add_system_message(f"Gate {'approved' ✓ if approved else 'rejected ✗'}")

    result = await state.api_client.send_gate_response(
        session_id=state.session_id,
        approved=approved,
    )

    if result.get("type") == "response":
        content = result.get("content", "")
        add_message("assistant", content)

    state.pending_gate = None


def set_mode(mode: str) -> None:
    """Set the chat mode (normal or knowledge-augmented)."""
    state = get_state()
    state.current_mode = mode
    # Reset session when changing modes
    state.reset_session()
    mode_label.text = "Normal Chat" if mode == "normal" else "Knowledge Augmented"


def on_role_changed(role: str) -> None:
    """Handle role selection change in the sidebar."""
    state = get_state()
    state.current_role = role
    # Map role to default model slot
    role_to_slot = {
        "beast": "synthesis",
        "vigil": "evaluation",
        "embedding": "embedding",
    }
    state.current_model_slot = role_to_slot.get(role, "synthesis")
    # Reset session since role/slot changed
    state.reset_session()
    # Update the model slot display in the header
    model_select_label.text = f"Slot: {state.current_model_slot}"


def on_slot_changed(slot_name: str) -> None:
    """Handle model slot selection change."""
    state = get_state()
    state.current_model_slot = slot_name
    state.reset_session()
    model_select_label.text = f"Slot: {slot_name}"


# ---------------------------------------------------------------------------
# Page Definitions
# ---------------------------------------------------------------------------


@ui.page("/")
async def main_page():
    """Main chat page — AIP_Brain frontend."""
    global chat_container, input_field, mode_label, model_select_label

    ui.page_title("AIP_Brain")
    state = get_state()

    # Load backend data on page load
    backend_status = await check_backend_health()
    slots = await load_model_slots()

    # Build slot names for dropdowns
    slot_names = [s["slot_name"] for s in slots] if slots else ["synthesis", "evaluation", "sexton", "embedding"]
    slot_models = {s["slot_name"]: s.get("model", f"<{s['slot_name']}>") for s in slots}

    # ---- HEADER ----
    with ui.header(elevated=True).classes("bg-primary text-white items-center q-pa-sm"):
        ui.label("AIP_Brain").classes("text-h6 q-ml-md")
        ui.button("Normal Chat", on_click=lambda: set_mode("normal")).props("flat text-color=white")
        ui.button("Knowledge Augmented", on_click=lambda: set_mode("augmented"), color="yellow").props("flat outline")
        mode_label = ui.label("Normal Chat").classes("q-ml-sm text-weight-medium text-white")
        ui.space()
        model_select_label = ui.label(f"Slot: {state.current_model_slot}").classes("q-mr-sm text-white text-weight-medium")
        slot_select = ui.select(
            slot_names,
            value=state.current_model_slot,
            on_change=lambda e: on_slot_changed(e.value),
        ).classes("min-w-[180px] text-black")
        ui.checkbox("Auto-save", value=True, on_change=lambda e: setattr(state, "auto_save", e.value)).classes("q-ml-sm text-white")
        ui.space()
        ui.button("Models & Roles", on_click=lambda: ui.navigate.to("/models"), color="secondary").props("flat")
        ui.space()
        with ui.row().classes("items-center gap-1"):
            ui.button("Vector", icon="storage", on_click=lambda: ui.notify("Vector — Phase 2+")).props("flat text-color=white")
            ui.button("Graph", icon="account_tree", on_click=lambda: ui.notify("Graph — Phase 2+")).props("flat text-color=white")
            ui.button("Wiki", icon="menu_book", on_click=lambda: ui.notify("Wiki — Phase 2+")).props("flat text-color=white")
            ui.button("Sources", icon="source", on_click=lambda: ui.notify("Sources — Phase 2+")).props("flat text-color=white")

    # ---- RIGHT DRAWER — Role Assignments ----
    with ui.right_drawer(fixed=True).classes("q-pa-md bg-grey-1"):
        ui.label("Role Assignments").classes("text-h6 q-mb-md")

        ui.label("Beast (Chat/LLM)").classes("text-weight-medium")
        beast_select = ui.select(
            slot_names,
            value="synthesis",
            on_change=lambda e: None,  # Individual role slot selection — Phase 2 wiring
        ).classes("q-mb-sm")

        ui.label("Vigil (Evaluation)").classes("text-weight-medium")
        vigil_select = ui.select(
            slot_names,
            value="evaluation",
            on_change=lambda e: None,  # Phase 2 wiring
        ).classes("q-mb-sm")

        ui.label("Embedding").classes("text-weight-medium")
        embed_select = ui.select(
            slot_names,
            value="embedding",
            on_change=lambda e: None,  # Phase 2 wiring
        ).classes("q-mb-md")

        # Active role selector — this determines which role is used for chat
        ui.label("Active Role for Chat").classes("text-weight-medium q-mt-md")
        ui.select(
            ["beast", "vigil", "embedding"],
            value=state.current_role,
            on_change=lambda e: on_role_changed(e.value),
        ).classes("q-mb-md")

        ui.button(
            "Save Roles",
            color="primary",
            on_click=lambda: ui.notify("Role configuration saved (in-memory). Backend integration in Phase 2."),
        )

        # Show current slot details
        ui.separator().classes("q-my-md")
        ui.label("Current Slot Details").classes("text-weight-medium")
        if slots:
            for s in slots:
                with ui.expansion(f"{s['slot_name']} ({s.get('provider', '?')})", group="slots").classes("w-full"):
                    ui.label(f"Model: {s.get('model', 'N/A')}").classes("text-caption")
                    ui.label(f"Provider: {s.get('provider', 'N/A')}").classes("text-caption")
                    if s.get("base_url"):
                        ui.label(f"Base URL: {s['base_url']}").classes("text-caption")

    # ---- CHAT AREA ----
    chat_container = ui.column().classes("w-full max-w-3xl mx-auto q-pa-md").style("min-height: 400px;")

    # Show backend status message
    if not state.backend_reachable:
        with chat_container:
            ui.label("⚠ AIP Backend is not reachable. Please ensure the FastAPI server is running at " + state.api_client.base_url).classes(
                "text-negative text-weight-medium q-pa-md"
            )
            ui.label("Start the backend with: uvicorn aip.adapter.api.app:create_app --factory --port 8000").classes("text-caption q-px-md")
    else:
        with chat_container:
            ui.label(f"Connected to AIP Backend. {len(slots)} model slot(s) available.").classes("text-positive text-caption q-pa-sm")

    # ---- INPUT AREA ----
    with ui.row().classes("w-full max-w-3xl mx-auto items-center q-pa-sm gap-2"):
        input_field = ui.input(placeholder="Ask anything...").props("outlined dense").classes("flex-grow")
        input_field.on("keydown.enter", lambda: asyncio.create_task(send_prompt()))
        ui.button("Send", on_click=lambda: asyncio.create_task(send_prompt()), color="primary").props("icon=send")

    # ---- FOOTER ----
    with ui.footer().classes("bg-grey-2 q-pa-xs items-center"):
        ui.label("AIP_Brain • API-First").classes("text-caption text-black")
        ui.space()
        ui.label(backend_status).classes("text-caption text-black")
        ui.space()
        ci_status = "CI Mode" if any(s.get("model", "").startswith("<") for s in slots) else "Live"
        ui.label(f"Mode: {ci_status}").classes("text-caption text-black")


@ui.page("/models")
async def model_catalog_page():
    """Model catalog page — shows available slots from the backend."""
    ui.page_title("Model Catalog")
    state = get_state()

    ui.label("Model Catalog").classes("text-h4 q-my-md")
    ui.label("Model slots configured in the AIP backend. These appear in role assignments and chat.").classes(
        "text-subtitle2 q-mb-md"
    )

    # Load slots from backend
    slots = await load_model_slots()

    if not state.backend_reachable:
        ui.label("⚠ Backend not reachable — cannot load model slots").classes("text-negative")
        ui.label(f"Ensure the AIP FastAPI server is running at {state.api_client.base_url}").classes("text-caption")
    elif not slots:
        ui.label("No model slots configured in the backend.").classes("text-warning")
    else:
        # Display slots in a table
        columns = [
            {"name": "slot_name", "label": "Slot", "field": "slot_name"},
            {"name": "provider", "label": "Provider", "field": "provider"},
            {"name": "model", "label": "Model", "field": "model"},
            {"name": "base_url", "label": "Base URL", "field": "base_url"},
            {"name": "fallback", "label": "Has Fallback", "field": "has_fallback"},
        ]
        rows = [
            {
                "slot_name": s["slot_name"],
                "provider": s.get("provider", "?"),
                "model": s.get("model", "?"),
                "base_url": s.get("base_url", "N/A") or "N/A",
                "has_fallback": "Yes" if s.get("has_fallback") else "No",
            }
            for s in slots
        ]
        ui.table(columns=columns, rows=rows, row_key="slot_name").classes("w-full")

        # Show CI mode warning if applicable
        ci_slots = [s for s in slots if s.get("model", "").startswith("<")]
        if ci_slots:
            ui.label(f"Note: {len(ci_slots)} slot(s) are in CI/fixture mode. Set model names in aip.config.toml or environment variables for live models.").classes(
                "text-warning q-mt-md"
            )

    ui.button("Back to Chat", on_click=lambda: ui.navigate.to("/"), color="grey").classes("q-mt-md")


# ---------------------------------------------------------------------------
# Application startup
# ---------------------------------------------------------------------------

ui.run(title="AIP_Brain", port=8080, reload=True)
