"""AIP_Brain NiceGUI Frontend — Phase 5 Hardening & Polish.

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
  4. PATCH /api/v1/sessions/{id} → toggle auto_save, update session flags
  5. POST /api/v1/ingest/conversation → manual ingestion trigger

Phase 5 enhancements:
  - Budget monitoring in footer and sidebar
  - Gate response error handling
  - Review Queue NiceGUI page
  - Connection status indicator

Phase 4 preserved:
  - Vector search panel, ECS graph, Wiki browser, Sources browser
  - Augmented mode with source citations

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
            auto_save=self.auto_save,
        )
        self.session_id = result["id"]
        return self.session_id

    def reset_session(self) -> None:
        """Reset session state (e.g., when changing roles)."""
        self.session_id = None
        self.pending_gate = None
        self.ingestion_status = "idle"
        self.chunks_indexed = 0


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
        auto_saved = resp.get("auto_save", False)
        sources = resp.get("sources", [])
        mode = resp.get("mode", "normal")
        add_message("assistant", content, model=model, latency_ms=latency)
        if tokens > 0:
            add_system_message(f"Tokens: {tokens}")
        if sources:
            # Display source citations for augmented mode
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
            # Schedule a status refresh after a short delay
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
    """Handle auto_save checkbox toggle — persist to backend session.

    When auto_save is toggled, we:
    1. Update local state immediately
    2. If a session exists, PATCH the backend to persist the flag
    3. If no session yet, the flag will be sent on session creation
    """
    state = get_state()
    state.auto_save = enabled

    if state.session_id is not None:
        try:
            await state.api_client.update_session(
                state.session_id, {"auto_save": enabled}
            )
            status = "enabled" if enabled else "disabled"
            ui.notify(f"Auto-save {status}", color="positive" if enabled else "warning")
        except Exception as exc:
            ui.notify(f"Failed to update auto-save: {exc}", color="negative")
    else:
        status = "enabled" if enabled else "disabled"
        ui.notify(f"Auto-save will be {status} for next session", color="info")


async def refresh_ingestion_status() -> None:
    """Refresh ingestion status from the backend and update the footer label.

    Called after each chat response to show auto-save progress.
    Waits a short delay for the background ingestion to make progress.
    """
    state = get_state()
    if state.session_id is None:
        return

    # Wait a moment for the ingestion to start
    await asyncio.sleep(1.0)

    try:
        session = await state.api_client.get_session(state.session_id)
        ingestion_status = session.get("ingestion_status", "idle")
        chunks_indexed = session.get("chunks_indexed", 0)
        state.ingestion_status = ingestion_status
        state.chunks_indexed = chunks_indexed

        # Update the footer label
        if ingestion_status == "ingesting":
            ingestion_label_ref.text = f"Indexing... ({chunks_indexed} chunks)"
        elif ingestion_status == "error":
            ingestion_label_ref.text = "Auto-save: error (check logs)"
        else:
            ingestion_label_ref.text = f"Indexed: {chunks_indexed} chunks" if chunks_indexed > 0 else ""
    except Exception:
        # Non-critical — just don't update the label
        pass


async def refresh_budget_status(label_ref, state: GuiState) -> None:
    """Fetch budget status and update the footer label."""
    try:
        budget = await state.api_client.get_budget_status(scope="session", scope_id="default")
        consumed = budget.get("consumed_tokens", 0)
        limit = budget.get("limit", 0)
        fraction = budget.get("fraction_used", 0)
        if limit > 0:
            pct = f"{fraction:.0%}"
            remaining = limit - consumed
            label_ref.text = f"Budget: {consumed}/{limit} ({pct}) — {remaining} remaining"
            if fraction >= 0.8:
                label_ref.classes("text-caption text-negative", remove="text-black")
            else:
                label_ref.classes("text-caption text-black", remove="text-negative")
        elif budget.get("budget_manager") is False:
            label_ref.text = "Budget: not configured"
    except Exception:
        label_ref.text = ""


# ---------------------------------------------------------------------------
# Page Definitions
# ---------------------------------------------------------------------------


@ui.page("/")
async def main_page():
    """Main chat page — AIP_Brain frontend."""
    global chat_container, input_field, mode_label, model_select_label, ingestion_label_ref

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
        ui.checkbox("Auto-save", value=True, on_change=lambda e: asyncio.create_task(on_auto_save_toggled(e.value))).classes("q-ml-sm text-white")
        ui.space()
        ui.button("Models & Roles", on_click=lambda: ui.navigate.to("/models"), color="secondary").props("flat")
        ui.space()
        with ui.row().classes("items-center gap-1"):
            ui.button("Vector", icon="storage", on_click=lambda: ui.navigate.to("/vector")).props("flat text-color=white")
            ui.button("Graph", icon="account_tree", on_click=lambda: ui.navigate.to("/graph")).props("flat text-color=white")
            ui.button("Wiki", icon="menu_book", on_click=lambda: ui.navigate.to("/wiki")).props("flat text-color=white")
            ui.button("Sources", icon="source", on_click=lambda: ui.navigate.to("/sources")).props("flat text-color=white")
            ui.button("Review", icon="rate_review", on_click=lambda: ui.navigate.to("/review")).props("flat text-color=white")

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
            on_click=lambda: ui.notify("Role configuration saved (in-memory)."),
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

        # Actor status section
        ui.separator().classes("q-my-md")
        ui.label("Actor Status").classes("text-weight-medium")
        if state.backend_reachable:
            try:
                actors_data = await state.api_client.get_actors_status()
                actors = actors_data.get("actors", {})

                for actor_name in ["beast", "vigil", "sexton"]:
                    actor = actors.get(actor_name, {})
                    initialized = actor.get("initialized", False)
                    status_icon = "🟢" if initialized else "🔴"
                    status_text = "Active" if initialized else "Not initialized"

                    with ui.row().classes("w-full items-center gap-1"):
                        ui.label(f"{status_icon} {actor_name.capitalize()}").classes("text-caption text-weight-medium")
                        ui.label(f"— {status_text}").classes("text-caption")

                    # Show health details if available
                    if initialized:
                        health = actor.get("health")
                        if health and isinstance(health, dict):
                            overall = health.get("overall", "unknown")
                            ui.label(f"  Health: {overall}").classes("text-caption text-grey-7")
                        if actor_name == "sexton":
                            unclassified = actor.get("unclassified_count", 0)
                            ui.label(f"  Unclassified: {unclassified}").classes("text-caption text-grey-7")
                        if actor_name == "vigil" and health:
                            status = health.get("status", "unknown")
                            ui.label(f"  Canonicals: {status}").classes("text-caption text-grey-7")

                # Trigger buttons for manual actor cycles
                ui.separator().classes("q-my-sm")
                with ui.row().classes("w-full gap-1"):
                    ui.button("Run Beast", size="sm", color="brown",
                              on_click=lambda: asyncio.create_task(trigger_actor("beast")))
                    ui.button("Run Vigil", size="sm", color="indigo",
                              on_click=lambda: asyncio.create_task(trigger_actor("vigil")))
                    ui.button("Run Sexton", size="sm", color="teal",
                              on_click=lambda: asyncio.create_task(trigger_actor("sexton")))
            except Exception:
                ui.label("Could not load actor status").classes("text-caption text-warning")
        else:
            ui.label("Backend unreachable — no actor info").classes("text-caption text-warning")

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
        ingestion_label_ref = ui.label("").classes("text-caption text-black")
        ui.space()
        budget_label_ref = ui.label("").classes("text-caption text-black")
        ui.space()
        ui.label(backend_status).classes("text-caption text-black")
        ui.space()
        ci_status = "CI Mode" if any(s.get("model", "").startswith("<") for s in slots) else "Live"
        ui.label(f"Mode: {ci_status}").classes("text-caption text-black")

    # Load initial budget status
    asyncio.create_task(refresh_budget_status(budget_label_ref, state))


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

    # Search input
    with ui.row().classes("w-full items-center gap-2 q-mb-md"):
        search_input = ui.input(placeholder="Search for content...").props("outlined dense").classes("flex-grow")
        search_input.on("keydown.enter", lambda: asyncio.create_task(do_search()))
        ui.button("Search", on_click=lambda: asyncio.create_task(do_search()), icon="search", color="primary")

    # Results area
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
            # Use the retrieve endpoint (no model call, just source retrieval)
            result = await state.api_client.ask_retrieve(question=query, max_sources=20)
            sources = result.get("sources", [])

            results_container.clear()
            with results_container:
                if not sources:
                    ui.label("No results found.").classes("text-warning q-pa-md")
                else:
                    ui.label(f"Found {len(sources)} result(s)").classes("text-caption q-mb-sm")
                    for i, s in enumerate(sources, 1):
                        with ui.card().classes("w-full q-mb-sm"):
                            with ui.card_section():
                                title = s.get("title", s.get("source_id", "Unknown"))
                                score = s.get("score", 0)
                                source_type = s.get("source_type", "unknown")
                                domain = s.get("domain", "")
                                snippet = s.get("content_snippet", "")
                                ui.label(f"#{i} — {title}").classes("text-weight-medium")
                                with ui.row().classes("gap-2"):
                                    ui.badge(source_type, color="blue").classes("text-xs")
                                    ui.badge(f"Score: {score:.3f}", color="green").classes("text-xs")
                                    if domain:
                                        ui.badge(domain, color="orange").classes("text-xs")
                                ui.label(snippet[:300] + ("..." if len(snippet) > 300 else "")).classes(
                                    "text-caption q-mt-sm"
                                )
        except Exception as exc:
            results_container.clear()
            with results_container:
                ui.label(f"Search failed: {exc}").classes("text-negative")

    ui.button("Back to Chat", on_click=lambda: ui.navigate.to("/"), color="grey").classes("q-mt-md")


@ui.page("/graph")
async def ecs_graph_page():
    """ECS Graph panel — visualize artifact lifecycle states."""
    ui.page_title("ECS Graph — AIP_Brain")
    state = get_state()

    ui.label("Artifact Lifecycle Graph").classes("text-h4 q-my-md")
    ui.label("Visualize the ECS state machine and artifact distribution across states.").classes(
        "text-subtitle2 q-mb-md"
    )

    graph_container = ui.column().classes("w-full")

    try:
        graph_data = await state.api_client.get_ecs_graph()
        transitions = graph_data.get("transitions", {})
        all_states = graph_data.get("all_states", [])
        distribution = graph_data.get("distribution", {})

        with graph_container:
            # State machine visualization
            ui.label("State Transitions").classes("text-h6 q-mb-sm")
            with ui.card().classes("w-full q-mb-md"):
                with ui.card_section():
                    for from_state, to_states in transitions.items():
                        count = distribution.get(from_state, 0)
                        with ui.row().classes("items-center gap-2 q-mb-xs"):
                            ui.badge(f"{from_state} ({count})", color="blue")
                            ui.label("→").classes("text-weight-bold")
                            if to_states:
                                for to_state in to_states:
                                    ui.badge(to_state, color="green" if to_state == "APPROVED" else "grey")
                            else:
                                ui.label("(terminal)").classes("text-italic text-grey")

            # Artifact browser by state
            ui.label("Artifacts by State").classes("text-h6 q-mb-sm")
            with ui.row().classes("w-full gap-2 q-mb-md"):
                for s in all_states:
                    count = distribution.get(s, 0)
                    color = "positive" if s == "APPROVED" else "warning" if s == "GENERATED" else "grey"
                    ui.button(f"{s} ({count})", color=color, size="sm",
                              on_click=lambda state_name=s: asyncio.create_task(show_artifacts(state_name)))

            # Artifact list area
            artifact_list_container = ui.column().classes("w-full")

            async def show_artifacts(state_name: str):
                artifact_list_container.clear()
                with artifact_list_container:
                    ui.label(f"Loading artifacts in {state_name}...").classes("text-grey")
                try:
                    result = await state.api_client.list_ecs_artifacts(state=state_name)
                    artifact_ids = result.get("artifact_ids", [])
                    count = result.get("count", 0)
                    artifact_list_container.clear()
                    with artifact_list_container:
                        ui.label(f"{count} artifact(s) in {state_name}").classes("text-weight-medium q-mb-sm")
                        for aid in artifact_ids[:50]:
                            with ui.row().classes("items-center gap-2 q-mb-xs"):
                                ui.label(aid).classes("text-caption font-mono")
                                ui.button("Details", size="xs", color="blue",
                                          on_click=lambda artifact_id=aid: asyncio.create_task(
                                              show_artifact_details(artifact_id)))
                except Exception as exc:
                    artifact_list_container.clear()
                    with artifact_list_container:
                        ui.label(f"Failed to load: {exc}").classes("text-negative")

            async def show_artifact_details(artifact_id: str):
                try:
                    detail = await state.api_client.get_ecs_artifact(artifact_id)
                    current = detail.get("current_state", "Unknown")
                    history = detail.get("history", [])
                    ui.notify(f"{artifact_id}: {current} ({len(history)} transitions)")
                except Exception as exc:
                    ui.notify(f"Failed: {exc}", color="negative")

    except Exception as exc:
        with graph_container:
            ui.label(f"Failed to load ECS graph: {exc}").classes("text-negative q-pa-md")
            ui.label("Ensure the AIP backend is running and ECS store is configured.").classes("text-caption")

    ui.button("Back to Chat", on_click=lambda: ui.navigate.to("/"), color="grey").classes("q-mt-md")


@ui.page("/wiki")
async def wiki_browser_page():
    """Wiki browser — browse and search compiled knowledge."""
    ui.page_title("Wiki — AIP_Brain")
    state = get_state()

    ui.label("Knowledge Wiki").classes("text-h4 q-my-md")
    ui.label("Browse and search compiled knowledge items with provenance tracking.").classes(
        "text-subtitle2 q-mb-md"
    )

    # Search bar
    with ui.row().classes("w-full items-center gap-2 q-mb-md"):
        wiki_search = ui.input(placeholder="Search knowledge...").props("outlined dense").classes("flex-grow")
        wiki_search.on("keydown.enter", lambda: asyncio.create_task(do_wiki_search()))
        ui.button("Search", on_click=lambda: asyncio.create_task(do_wiki_search()), icon="search", color="primary")

    # Filters
    with ui.row().classes("gap-2 q-mb-md"):
        domain_filter = ui.input(placeholder="Domain filter").props("outlined dense")
        state_filter = ui.select(
            ["", "SPECIFIED", "COMPILED", "REVIEWED", "APPROVED", "FAILED"],
            value="",
            with_input=True,
        ).props("outlined dense")

    # Content area
    wiki_container = ui.column().classes("w-full")

    async def do_wiki_search():
        query = wiki_search.value.strip()
        domain = domain_filter.value.strip() or None
        if query:
            # Search mode
            wiki_container.clear()
            with wiki_container:
                ui.label("Searching...").classes("text-grey")
            try:
                result = await state.api_client.search_knowledge(q=query, domain=domain, limit=20)
                results = result.get("results", [])
                wiki_container.clear()
                with wiki_container:
                    ui.label(f"Search: {len(results)} result(s)").classes("text-caption q-mb-sm")
                    for i, item in enumerate(results, 1):
                        with ui.card().classes("w-full q-mb-sm"):
                            with ui.card_section():
                                kid = item.get("knowledge_id", "?")
                                score = item.get("score", 0)
                                source_type = item.get("source", "unknown")
                                content = item.get("content", "")
                                ui.label(f"#{i} — {kid}").classes("text-weight-medium")
                                with ui.row().classes("gap-2"):
                                    ui.badge(source_type, color="blue")
                                    ui.badge(f"Score: {score:.3f}", color="green")
                                ui.label(content[:300] + ("..." if len(content) > 300 else "")).classes(
                                    "text-caption q-mt-sm"
                                )
            except Exception as exc:
                wiki_container.clear()
                with wiki_container:
                    ui.label(f"Search failed: {exc}").classes("text-negative")
        else:
            # Browse mode
            await load_wiki_items()

    async def load_wiki_items():
        domain = domain_filter.value.strip() or None
        st = state_filter.value or None
        wiki_container.clear()
        with wiki_container:
            ui.label("Loading...").classes("text-grey")
        try:
            result = await state.api_client.list_knowledge(domain=domain, state=st)
            items = result.get("items", [])
            wiki_container.clear()
            with wiki_container:
                ui.label(f"{len(items)} knowledge item(s)").classes("text-caption q-mb-sm")
                for item in items[:50]:
                    with ui.card().classes("w-full q-mb-sm"):
                        with ui.card_section():
                            kid = item.get("knowledge_id", "?")
                            kstate = item.get("state", "UNKNOWN")
                            kdomain = item.get("domain", "")
                            content = item.get("content", "")
                            ui.label(kid).classes("text-weight-medium")
                            with ui.row().classes("gap-2"):
                                state_colors = {
                                    "APPROVED": "positive",
                                    "REVIEWED": "warning",
                                    "COMPILED": "info",
                                    "SPECIFIED": "grey",
                                    "FAILED": "negative",
                                }
                                ui.badge(kstate, color=state_colors.get(kstate, "grey"))
                                if kdomain:
                                    ui.badge(kdomain, color="orange")
                            ui.label(content[:200] + ("..." if len(content) > 200 else "")).classes(
                                "text-caption q-mt-sm"
                            )
                            # Provenance link
                            source_ids = item.get("source_canonical_ids", [])
                            if source_ids:
                                ui.label(f"Provenance: {len(source_ids)} source(s)").classes("text-caption text-grey")
        except Exception as exc:
            wiki_container.clear()
            with wiki_container:
                ui.label(f"Failed to load: {exc}").classes("text-negative")

    # Load initial items
    await load_wiki_items()

    ui.button("Back to Chat", on_click=lambda: ui.navigate.to("/"), color="grey").classes("q-mt-md")


@ui.page("/sources")
async def sources_browser_page():
    """Sources browser — overview of all indexed content."""
    ui.page_title("Sources — AIP_Brain")
    state = get_state()

    ui.label("Sources Browser").classes("text-h4 q-my-md")
    ui.label("Overview of all indexed content: conversations, artifacts, and compiled knowledge.").classes(
        "text-subtitle2 q-mb-md"
    )

    sources_container = ui.column().classes("w-full")

    # Load stats first
    try:
        stats = await state.api_client.get_sources_stats()

        with sources_container:
            # Stats cards
            ui.label("Index Statistics").classes("text-h6 q-mb-sm")
            with ui.row().classes("w-full gap-4 q-mb-md"):
                # Vector store stats
                vs = stats.get("vector_store", {})
                with ui.card().classes("min-w-[200px]"):
                    with ui.card_section():
                        ui.label("Vector Store").classes("text-weight-medium")
                        if vs.get("available"):
                            ui.label(f"{vs.get('total_vectors', 0)} vectors").classes("text-h5 text-positive")
                        else:
                            ui.label("Not available").classes("text-caption text-warning")

                # Entity store stats
                es = stats.get("entity_store", {})
                with ui.card().classes("min-w-[200px]"):
                    with ui.card_section():
                        ui.label("Entity Store").classes("text-weight-medium")
                        if es.get("available"):
                            ui.label(f"{es.get('total_entities', 0)} entities").classes("text-h5 text-positive")
                        else:
                            ui.label("Not available").classes("text-caption text-warning")

                # Knowledge store stats
                ks = stats.get("knowledge_store", {})
                with ui.card().classes("min-w-[200px]"):
                    with ui.card_section():
                        ui.label("Knowledge Store").classes("text-weight-medium")
                        if ks.get("available"):
                            ui.label(f"{ks.get('total_items', 0)} items").classes("text-h5 text-positive")
                        else:
                            ui.label("Not available").classes("text-caption text-warning")

                # Lexical store stats
                ls = stats.get("lexical_store", {})
                with ui.card().classes("min-w-[200px]"):
                    with ui.card_section():
                        ui.label("Lexical Store (FTS5)").classes("text-weight-medium")
                        if ls.get("available"):
                            ui.label("Available").classes("text-h5 text-positive")
                        else:
                            ui.label("Not available").classes("text-caption text-warning")

    except Exception as exc:
        with sources_container:
            ui.label(f"Failed to load stats: {exc}").classes("text-negative q-pa-md")

    # Source list with filters
    with ui.row().classes("w-full items-center gap-2 q-mb-md"):
        domain_input = ui.input(placeholder="Domain filter").props("outlined dense")
        type_input = ui.select(
            ["", "conversation_chunk", "artifact", "compiled_knowledge"],
            value="",
            with_input=True,
        ).props("outlined dense")
        ui.button("Refresh", on_click=lambda: asyncio.create_task(load_sources()), icon="refresh", color="primary")

    source_list_container = ui.column().classes("w-full")

    async def load_sources():
        domain = domain_input.value.strip() or None
        source_type = type_input.value or None
        source_list_container.clear()
        with source_list_container:
            ui.label("Loading sources...").classes("text-grey")
        try:
            result = await state.api_client.list_sources(domain=domain, source_type=source_type)
            sources = result.get("sources", [])
            source_list_container.clear()
            with source_list_container:
                ui.label(f"{len(sources)} source(s)").classes("text-caption q-mb-sm")
                for s in sources[:50]:
                    with ui.card().classes("w-full q-mb-sm"):
                        with ui.card_section():
                            sid = s.get("source_id", "?")
                            stype = s.get("source_type", "unknown")
                            sdomain = s.get("domain", "")
                            stitle = s.get("title", "")
                            ui.label(f"{stitle or sid}").classes("text-weight-medium")
                            with ui.row().classes("gap-2"):
                                ui.badge(stype, color="blue")
                                if sdomain:
                                    ui.badge(sdomain, color="orange")
                                meta = s.get("metadata", {})
                                if meta.get("state"):
                                    ui.badge(meta["state"], color="green")
        except Exception as exc:
            source_list_container.clear()
            with source_list_container:
                ui.label(f"Failed to load: {exc}").classes("text-negative")

    # Load initial sources
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
    ui.label("Artifacts pending DEFINER review. Approve to promote to canonical, reject to mark as failed.").classes(
        "text-subtitle2 q-mb-md"
    )

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
                                    ui.label(summary[:500] + ("..." if len(summary) > 500 else "")).classes(
                                        "text-caption q-mb-sm"
                                    )

                                with ui.row().classes("gap-2"):
                                    ui.button(
                                        "Approve",
                                        color="positive",
                                        size="sm",
                                        on_click=lambda aid=artifact_id: asyncio.create_task(
                                            handle_review_decision(aid, True)
                                        ),
                                    )
                                    ui.button(
                                        "Reject",
                                        color="negative",
                                        size="sm",
                                        on_click=lambda aid=artifact_id: asyncio.create_task(
                                            handle_review_decision(aid, False)
                                        ),
                                    )
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
            # Reload the review queue
            await load_reviews()
        except Exception as exc:
            ui.notify(f"Review decision failed: {exc}", color="negative")

    # Load initial items
    await load_reviews()

    ui.button("Back to Chat", on_click=lambda: ui.navigate.to("/"), color="grey").classes("q-mt-md")


# ---------------------------------------------------------------------------
# Application startup
# ---------------------------------------------------------------------------

ui.run(title="AIP_Brain", port=8080, reload=True)
