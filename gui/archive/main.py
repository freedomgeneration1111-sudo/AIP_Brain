"""ARCHIVED — This module has been superseded by gui/app.py (UI Cycle 2).

Original description:
AIP_Brain NiceGUI Frontend — OpenRouter Integration Pass.

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
import logging
from pathlib import Path
from typing import Any

from nicegui import context, ui

from gui.api_client import AipApiClient, get_api_client

# Module-level logger for the GUI chat flow
log = logging.getLogger("gui.chat")

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
        self.current_role: str | None = (
            None  # default: no actor role for plain chat (prevents "Beast" system prompt leakage into normal chat)
        )
        self.current_model_slot: str = "synthesis"
        self.current_mode: str = "normal"  # "normal" or "augmented"
        self.available_slots: list[dict[str, Any]] = []
        self.backend_reachable: bool = False
        self.pending_gate: dict[str, Any] | None = None
        self.auto_save: bool = True
        self.ingestion_status: str = "idle"  # "idle" | "ingesting" | "error"
        self.chunks_indexed: int = 0
        # NiceGUI client reference — needed to update UI from background tasks.
        # Set once in main_page() via context.client.
        self.client = None

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
    """Check if the AIP backend is reachable. Returns status string.

    Hard cap of 4 seconds total — page renders regardless of backend state.
    """
    state = get_state()
    try:
        health = await asyncio.wait_for(state.api_client.check_health(), timeout=4.0)
        state.backend_reachable = True
        slots = health.get("model_slots", [])
        return f"Backend: OK (slots: {', '.join(slots)})"
    except asyncio.TimeoutError:
        state.backend_reachable = False
        return "Backend: TIMEOUT (>4s) — using direct OpenRouter"
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
    """Handle the send button click — sends message via WebSocket or direct OpenRouter.

    CRITICAL: This function is called via asyncio.create_task(), which means
    any unhandled exception is silently swallowed. We wrap the entire function
    in a top-level try/except to ensure errors are ALWAYS visible to the user.

    CRITICAL 2: asyncio.create_task() runs outside NiceGUI's UI slot context.
    We MUST enter the client context (via state.client) before creating or
    modifying any UI elements. Without this, ui.label / add_message / etc.
    crash with "The current slot cannot be determined".
    """
    state = get_state()
    try:
        # Enter the NiceGUI client context so UI operations work.
        # This is required because asyncio.create_task() runs outside
        # the normal request-handling slot stack.
        if state.client is not None:
            with state.client:
                await _send_prompt_inner()
        else:
            # Fallback: no client captured (shouldn't happen in normal flow).
            # Try anyway — some UI operations might still work.
            await _send_prompt_inner()
    except Exception as exc:
        # Top-level catch — asyncio.create_task silently swallows exceptions,
        # so we MUST catch everything here and show it to the user.
        import traceback

        traceback.print_exc()
        try:
            ui.notify(f"Send failed: {exc}", color="negative", timeout=8000)
        except Exception:
            pass  # If even the notification fails, nothing more we can do


async def _send_prompt_inner() -> None:
    """Inner implementation of send_prompt with full error propagation."""
    state = get_state()
    prompt = input_field.value.strip()
    if not prompt:
        return

    # ---- Resolve chat model to a REAL model ID ----
    # Priority: role assignment → first selected model → first dropdown option
    # NEVER fall back to a slot name like "synthesis" — that's not a valid model ID.
    chat_model = get_role_model("synthesis")
    if not chat_model or chat_model.startswith("("):
        # No model assigned to synthesis role — use first selected model
        selected = get_selected_models()
        if selected:
            chat_model = selected[0]
        else:
            # Try the first option from the model dropdown
            try:
                all_options = build_model_options(state.available_slots)
                if all_options and not all_options[0].startswith("("):
                    chat_model = all_options[0]
            except Exception:
                pass

    if not chat_model or chat_model.startswith("("):
        ui.notify("No model selected. Go to Models page to select one.", color="warning")
        return

    log.info(
        "send_prompt: model=%s backend_reachable=%s prompt_len=%d", chat_model, state.backend_reachable, len(prompt)
    )

    add_message("user", prompt)
    input_field.value = ""

    # Create "Thinking…" label inside chat_container so it appears in the
    # correct place and inherits the container's context.
    with chat_container:
        thinking_label = ui.label("Thinking...").classes("text-grey")

    # ---- Lazy backend retry ----
    # If backend was unreachable at page load (timeout/down), probe once per send
    # so recovery is automatic once the backend comes up — no page reload needed.
    if not state.backend_reachable:
        try:
            await asyncio.wait_for(state.api_client.check_health(), timeout=3.0)
            state.backend_reachable = True
            log.info("send_prompt: backend recovered (lazy retry)")
        except Exception:
            pass  # Still down — fall through to direct OpenRouter below

    # ---- Route 1: Backend reachable → use WebSocket chat ----
    if state.backend_reachable:
        try:
            session_id = await state.ensure_session()
            log.info("send_prompt: session_id=%s", session_id)
        except Exception as exc:
            # Backend failed mid-flight — fall through to direct route
            log.warning("send_prompt: ensure_session failed: %s", exc)
            state.backend_reachable = False

    if state.backend_reachable:

        def on_response(resp: dict[str, Any]) -> None:
            log.info("on_response: model=%s content_len=%d", resp.get("model", "?"), len(resp.get("content", "")))
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
            log.error("on_error: %s", err.get("content", "Unknown"))
            thinking_label.delete()
            content = err.get("content", "Unknown error")
            add_system_message(f"Error: {content}")
            ui.notify(content, color="negative")

        def on_gate(gate: dict[str, Any]) -> None:
            log.info("on_gate: gate_type=%s", gate.get("gate_type", "?"))
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
            log.info("send_prompt: calling chat_via_websocket session=%s slot=%s", session_id, state.current_model_slot)
            await state.api_client.chat_via_websocket(
                session_id=session_id,
                message=prompt,
                on_response=on_response,
                on_error=on_error,
                on_gate=on_gate,
                model_slot=state.current_model_slot,
            )
            return
        except Exception as exc:
            log.error("send_prompt: websocket failed: %s", exc)
            thinking_label.delete()
            # Backend failed — fall through to direct OpenRouter.
            # Reset session so next send creates a fresh one (backend may restart).
            state.backend_reachable = False
            state.reset_session()
            add_system_message(f"Backend chat failed, trying direct OpenRouter: {exc}")

    # ---- Route 2: Backend unreachable → direct OpenRouter API call ----
    log.info("send_prompt: using direct OpenRouter with model=%s", chat_model)
    try:
        result = await state.api_client.chat_direct_openrouter(
            model=chat_model,
            messages=[{"role": "user", "content": prompt}],
            api_key=state.api_client.get_openrouter_api_key(),
        )
        thinking_label.delete()

        if result.get("error"):
            add_system_message(f"Error: {result.get('content', 'Unknown error')}")
            ui.notify(result.get("content", "Chat failed"), color="negative")
        else:
            add_message(
                "assistant",
                result.get("content", ""),
                model=result.get("model", chat_model),
                latency_ms=result.get("latency_ms"),
            )
            tokens = result.get("tokens_used", 0)
            if tokens > 0:
                add_system_message(f"Tokens: {tokens}")
            add_system_message("(direct OpenRouter — backend not connected)")
    except Exception as exc:
        log.error("send_prompt: direct OpenRouter failed: %s", exc)
        thinking_label.delete()
        add_system_message(f"Direct OpenRouter call failed: {exc}")
        ui.notify(f"Chat failed: {exc}", color="negative")


async def handle_gate_response(approved: bool) -> None:
    """Handle a DEFINER gate approval/rejection.

    Called via asyncio.create_task(), so needs client context for UI ops.
    """
    state = get_state()
    if state.session_id is None:
        return

    # Enter client context for UI operations (same pattern as send_prompt)
    ctx = state.client

    def _do_ui():
        decision_text = "approved" if approved else "rejected"
        add_system_message(f"Gate {decision_text}")

    try:
        result = await state.api_client.send_gate_response(
            session_id=state.session_id,
            approved=approved,
        )
        if ctx is not None:
            with ctx:
                _do_ui()
                if result.get("type") == "error":
                    add_system_message(f"Gate response error: {result.get('content', 'Unknown error')}")
                    ui.notify(f"Gate response failed: {result.get('content', 'Unknown error')}", color="negative")
                elif result.get("type") == "response":
                    content = result.get("content", "")
                    add_message("assistant", content)
        else:
            _do_ui()
            if result.get("type") == "error":
                add_system_message(f"Gate response error: {result.get('content', 'Unknown error')}")
                ui.notify(f"Gate response failed: {result.get('content', 'Unknown error')}", color="negative")
            elif result.get("type") == "response":
                content = result.get("content", "")
                add_message("assistant", content)
    except Exception as exc:
        if ctx is not None:
            with ctx:
                add_system_message(f"Gate response failed: {exc}")
                ui.notify(f"Gate response failed: {exc}", color="negative")
        else:
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
    """Manually trigger an actor cycle and show the result.

    Called via asyncio.create_task(), so needs client context for UI ops.
    """
    state = get_state()
    try:
        result = await state.api_client.trigger_actor_cycle(actor_name)
        triggered = result.get("triggered", False)

        def _notify():
            if triggered:
                ui.notify(f"{actor_name.capitalize()} cycle triggered successfully", color="positive")
            else:
                error = result.get("error", "Unknown error")
                ui.notify(f"{actor_name.capitalize()} cycle failed: {error}", color="negative")

        if state.client is not None:
            with state.client:
                _notify()
        else:
            _notify()
    except Exception as exc:
        try:
            if state.client is not None:
                with state.client:
                    ui.notify(f"Failed to trigger {actor_name}: {exc}", color="negative")
            else:
                ui.notify(f"Failed to trigger {actor_name}: {exc}", color="negative")
        except Exception:
            pass


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
    """Refresh ingestion status from the backend.

    Called via asyncio.create_task(), so needs client context for UI updates.
    """
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
        # Update label in client context
        if state.client is not None:
            with state.client:
                if ingestion_status == "ingesting":
                    ingestion_label_ref.text = f"Indexing... ({chunks_indexed} chunks)"
                elif ingestion_status == "error":
                    ingestion_label_ref.text = "Auto-save: error (check logs)"
                else:
                    ingestion_label_ref.text = f"Indexed: {chunks_indexed} chunks" if chunks_indexed > 0 else ""
        else:
            if ingestion_status == "ingesting":
                ingestion_label_ref.text = f"Indexing... ({chunks_indexed} chunks)"
            elif ingestion_status == "error":
                ingestion_label_ref.text = "Auto-save: error (check logs)"
            else:
                ingestion_label_ref.text = f"Indexed: {chunks_indexed} chunks" if chunks_indexed > 0 else ""
    except Exception:
        pass


async def refresh_budget_status(label_ref, state: GuiState) -> None:
    """Fetch budget status and update the footer label.

    Runs as a long-lived background task (polling every 30s).
    Needs client context for UI updates.
    """
    while True:
        try:
            budget = await state.api_client.get_budget_status(scope="session", scope_id="default")
            consumed = budget.get("consumed_tokens", 0)
            limit = budget.get("limit", 0)
            fraction = budget.get("fraction_used", 0)

            # Update label in client context
            def _update_ui():
                if limit > 0:
                    pct = f"{fraction:.0%}"
                    label_ref.text = f"Budget: {consumed}/{limit} ({pct})"
                    if fraction >= 0.8:
                        label_ref.classes("text-[10px] text-negative", remove="text-grey-6 text-black")
                    else:
                        label_ref.classes("text-[10px] text-grey-6", remove="text-negative text-black")
                elif budget.get("budget_manager") is False:
                    label_ref.text = "Budget: n/a"

            if state.client is not None:
                with state.client:
                    _update_ui()
            else:
                _update_ui()
        except Exception:
            try:
                if state.client is not None:
                    with state.client:
                        label_ref.text = ""
                else:
                    label_ref.text = ""
            except Exception:
                pass
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
        key_input = (
            ui.input(
                placeholder="sk-or-v1-...",
                password=True,
            )
            .props("outlined dense")
            .classes("w-full q-mt-md")
        )
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
    3. Config file default models as last resort
    4. Fallback message
    """
    selected = get_selected_models()
    backend_models = [s.get("model", "") for s in slots if s.get("model") and not s.get("model", "").startswith("<")]
    # Also include the hardcoded default from config as fallback
    config_defaults = ["google/gemma-3-4b-it"]
    all_options = list(dict.fromkeys(selected + backend_models + config_defaults))
    # Remove empty strings
    all_options = [m for m in all_options if m]
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

    # Capture the NiceGUI client context for this page.
    # This is ESSENTIAL: background tasks (asyncio.create_task) run outside
    # NiceGUI's slot stack.  By storing the client reference, we can re-enter
    # the context with `with state.client:` whenever we need to update UI
    # from a background coroutine (send_prompt, handle_gate_response, etc.).
    state.client = context.client

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
            ui.notify(
                "No API key set. Model catalog and chat will not work. You can set it later via Models page.",
                color="warning",
                position="top",
            )

    # ---- STEP 2: Load backend data ----
    backend_status = await check_backend_health()
    slots = await load_model_slots()

    # Populate local _role_model_assignments from backend's current slot config.
    # This makes the assignments (used by chat model resolution and fallbacks)
    # reflect the authoritative backend state (config + any AIP_*_MODEL env from PATCHes).
    for s in slots:
        sn = s.get("slot_name")
        m = s.get("model")
        if sn and m and not str(m).startswith("<"):
            set_role_model(sn, m)

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
        ui.badge("USING INGESTED DATA", color="amber").classes("text-[9px] q-ml-xs").bind_visibility_from(
            state, "current_mode", backward=lambda m: m == "augmented"
        )
        ui.space()

        # --- Chat Model selector (amber bordered) ---
        with (
            ui.row()
            .classes("items-center q-pa-xs rounded-borders")
            .style("background: rgba(255,255,255,0.15); border: 2px solid #FFC107; border-radius: 6px;")
        ):
            ui.icon("chat", size="xs").classes("text-amber q-mr-xs")
            ui.label("Chat Model").classes("text-caption text-amber text-weight-bold q-mr-xs")
            (
                ui.select(
                    all_model_options,
                    value=current_chat_model,
                    on_change=lambda e: on_chat_model_changed(e.value),
                )
                .classes("min-w-[180px] text-black")
                .props("dense")
            )

        ui.checkbox(
            "Auto-save",
            value=True,
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
            ui.button(icon="storage", on_click=lambda: ui.navigate.to("/vector")).props(
                "flat text-color=white dense round"
            )
            ui.button(icon="account_tree", on_click=lambda: ui.navigate.to("/graph")).props(
                "flat text-color=white dense round"
            )
            ui.button(icon="menu_book", on_click=lambda: ui.navigate.to("/wiki")).props(
                "flat text-color=white dense round"
            )
            ui.button(icon="source", on_click=lambda: ui.navigate.to("/sources")).props(
                "flat text-color=white dense round"
            )
            ui.button(icon="rate_review", on_click=lambda: ui.navigate.to("/review")).props(
                "flat text-color=white dense round"
            )

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

        # AI actor roles get model dropdowns; Sexton is admin-only (no model).
        # Note: synthesis/Beast does not get a sidebar model picker here — the top
        # "Chat Model" header is the authoritative controller for the synthesis slot
        # (used by normal chat). This decouples chat model selection from the Beast actor UI.
        actor_defs = [
            ("synthesis", "Beast", "brown", "rgba(121,85,72,0.08)", "beast", False),
            ("evaluation", "Vigil", "indigo", "rgba(63,81,181,0.08)", "vigil", True),
            ("embedding", "Embed", "teal", "rgba(0,150,136,0.08)", "embedding", True),
            ("sexton", "Sexton", "grey", "rgba(0,0,0,0.04)", "sexton", False),
        ]

        for slot_name, label, border_color, bg_color, actor_key, needs_model in actor_defs:
            with (
                ui.row()
                .classes("w-full items-center no-wrap q-px-xs q-py-none q-mt-xs rounded-borders")
                .style(f"background: {bg_color}; border-left: 3px solid {border_color};")
            ):
                actor = actors.get(actor_key, {})
                actor_init = actor.get("initialized", False)
                ui.icon(
                    "check_circle" if actor_init else "cancel",
                    color="positive" if actor_init else "negative",
                    size="xs",
                )
                ui.label(label).classes("text-[11px] text-weight-bold q-mr-xs")
                if needs_model:
                    # Model dropdown for AI roles only
                    current_role_model = get_role_model(slot_name)
                    role_default = slot_models.get(slot_name, "")
                    role_value = (
                        current_role_model or role_default or (all_model_options[0] if all_model_options else "")
                    )
                    if role_value not in all_model_options:
                        role_value = all_model_options[0] if all_model_options else ""
                    ui.select(
                        all_model_options,
                        value=role_value,
                        on_change=lambda e, sn=slot_name: on_role_model_changed(sn, e.value),
                    ).props("dense").classes("flex-grow text-[11px]")
                else:
                    # Non-AI role (e.g. Sexton) — show status label instead of dropdown.
                    # For Beast/synthesis the model is controlled exclusively by the top Chat Model header.
                    if slot_name == "synthesis":
                        ui.label("(Chat Model header)").classes("text-[10px] text-grey-6")
                    else:
                        ui.label("(admin)").classes("text-[10px] text-grey-6")

        # Trigger buttons (compact)
        ui.separator().classes("q-my-xs")
        with ui.row().classes("w-full gap-1"):
            ui.button("Run B", color="brown", on_click=lambda: asyncio.create_task(trigger_actor("beast"))).props(
                "size=xs dense"
            )
            ui.button("Run V", color="indigo", on_click=lambda: asyncio.create_task(trigger_actor("vigil"))).props(
                "size=xs dense"
            )
            ui.button("Run S", color="teal", on_click=lambda: asyncio.create_task(trigger_actor("sexton"))).props(
                "size=xs dense"
            )

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
            ui.label(
                "AIP Backend not reachable — chat will use direct OpenRouter API (no auto-save, no actors)."
            ).classes("text-warning text-weight-medium q-pa-md")
            ui.label(
                (
                    "For full features (auto-save, actors, augmented mode), "
                    "start the backend: uvicorn aip.adapter.api.app:create_app --factory --port 8000"
                )
            ).classes("text-caption q-px-md")
    else:
        with chat_container:
            api_key_status = "API key: Set" if state.api_client.has_openrouter_api_key() else "API key: MISSING"
            selected_count = len(get_selected_models())
            ui.label(
                f"Connected to AIP Backend. {len(slots)} slot(s). {api_key_status}. "
                f"{selected_count} model(s) selected from catalog."
            ).classes(
                "text-positive text-caption q-pa-sm"
                if state.api_client.has_openrouter_api_key()
                else "text-warning text-caption q-pa-sm"
            )

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
    """Show the API key dialog from the header key icon.

    Called via asyncio.create_task(), so needs client context for UI ops.
    """
    state = get_state()
    # Must enter client context to create UI elements
    if state.client is not None:
        with state.client:
            with ui.dialog() as dialog, ui.card().classes("p-6 min-w-[480px]"):
                ui.label("OpenRouter API Key").classes("text-h6")
                current = state.api_client.get_openrouter_api_key() or ""
                masked = current[:8] + "..." + current[-4:] if len(current) > 12 else "(not set)"
                ui.label(f"Current: {masked}").classes("text-caption q-mt-xs")
                key_input = (
                    ui.input(placeholder="sk-or-v1-...", password=True)
                    .props("outlined dense")
                    .classes("w-full q-mt-md")
                )
                with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
                    ui.button("Cancel", color="grey", on_click=lambda: dialog.submit(None))
                    ui.button("Save", color="primary", on_click=lambda: dialog.submit(key_input.value.strip()))

            result = await dialog
            if result:
                state.api_client.set_openrouter_api_key(result)
                ui.notify(
                    "API key updated! It will be used for all OpenRouter calls.", color="positive", position="top"
                )
    else:
        # Fallback without client context
        with ui.dialog() as dialog, ui.card().classes("p-6 min-w-[480px]"):
            ui.label("OpenRouter API Key").classes("text-h6")
            current = state.api_client.get_openrouter_api_key() or ""
            masked = current[:8] + "..." + current[-4:] if len(current) > 12 else "(not set)"
            ui.label(f"Current: {masked}").classes("text-caption q-mt-xs")
            key_input = (
                ui.input(placeholder="sk-or-v1-...", password=True).props("outlined dense").classes("w-full q-mt-md")
            )
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
    The top Chat Model is the authoritative control for normal chat (synthesis slot);
    we explicitly ensure no actor role is attached to avoid Beast prompt leakage.
    """
    state = get_state()
    state.current_role = None  # plain chat — never attach "beast" (or other) role here
    # The chat always uses the "synthesis" slot
    set_role_model("synthesis", model_id)
    state.reset_session()
    # Push to backend — set AIP_SYNTHESIS_MODEL env var via API
    api_key = state.api_client.get_openrouter_api_key()
    asyncio.create_task(state.api_client.update_slot_model("synthesis", model_id, api_key=api_key))
    ui.notify(f"Chat model → {model_id}", color="info")


def on_role_model_changed(slot_name: str, model_id: str) -> None:
    """Handle model change for an actor role in the sidebar.

    Updates the GUI state AND pushes the model change to the backend
    so that the next actor call uses the selected model.
    """
    set_role_model(slot_name, model_id)
    state = get_state()
    api_key = state.api_client.get_openrouter_api_key()
    asyncio.create_task(state.api_client.update_slot_model(slot_name, model_id, api_key=api_key))
    ui.notify(f"{slot_name.capitalize()} → {model_id}", color="info")


# The subordinate @ui.page routes have been removed as they are now handled by shell.py
# Only imports and shared utilities are kept for compatibility

# Removed subordinate @ui.page routes - they are now handled by shell.py
# Only imports and shared utilities are kept for compatibility

if __name__ == "__main__":
    ui.run(title="AIP_Brain", port=8080, reload=True)
