"""AIP GUI Per-Session State — replaces module-level _state singleton.

Each NiceGUI client gets its own GuiState instance via get_session_state().
This ensures multi-tab / multi-user sessions don't share mutable state.

Import boundary: this module imports ONLY from gui.api_client.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from gui.api_client import AipApiClient, get_api_client

log = logging.getLogger("gui.state")

# ── PERSISTENCE PATHS ─────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SLOT_MODELS_FILE = _PROJECT_ROOT / "config" / "slot_models.json"
_SELECTED_MODELS_FILE = _PROJECT_ROOT / "config" / "selected_models.json"


# ── PERSISTENCE HELPERS (log errors instead of `except: pass`) ────────

def _load_slot_models() -> dict[str, str]:
    """Load persisted slot->model mapping from config/slot_models.json."""
    try:
        if _SLOT_MODELS_FILE.exists():
            d = json.loads(_SLOT_MODELS_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str)}
    except (json.JSONDecodeError, OSError) as exc:
        log.error("failed to load slot models from %s: %s", _SLOT_MODELS_FILE, exc)
    return {}


def _save_slot_models(data: dict[str, str]) -> None:
    """Persist slot->model mapping to config/slot_models.json."""
    try:
        _SLOT_MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SLOT_MODELS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.debug("slot models persisted: %s", data)
    except OSError as exc:
        log.error("failed to persist slot models: %s", exc)


def _load_selected_models() -> list[str]:
    """Load persisted selected models from config/selected_models.json."""
    try:
        if _SELECTED_MODELS_FILE.exists():
            d = json.loads(_SELECTED_MODELS_FILE.read_text(encoding="utf-8"))
            if isinstance(d, list):
                return [m for m in d if isinstance(m, str)]
    except (json.JSONDecodeError, OSError) as exc:
        log.error("failed to load selected models from %s: %s", _SELECTED_MODELS_FILE, exc)
    return []


def _save_selected_models(models: list[str]) -> None:
    """Persist selected models to config/selected_models.json."""
    try:
        _SELECTED_MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SELECTED_MODELS_FILE.write_text(json.dumps(models, indent=2), encoding="utf-8")
    except OSError as exc:
        log.error("failed to persist selected models: %s", exc)


# ── MODULE-LEVEL SHARED DATA (not per-session — config file cache) ────
_role_models: dict[str, str] = _load_slot_models()
for _slot in ("synthesis", "evaluation", "sexton", "embedding", "beast", "vigil"):
    _role_models.setdefault(_slot, "")

_selected_models: list[str] = _load_selected_models()


def get_selected_models() -> list[str]:
    """Get the list of models selected from the OpenRouter catalog."""
    return _selected_models


def set_selected_models(models: list[str]) -> None:
    """Set the list of selected models and persist to disk."""
    global _selected_models
    _selected_models = models
    _save_selected_models(models)


def get_role_model(slot: str) -> str:
    """Get the model ID assigned to a role/slot."""
    return _role_models.get(slot, "")


def set_role_model(slot: str, model: str) -> None:
    """Set a model for a slot and persist immediately."""
    _role_models[slot] = model
    _save_slot_models(_role_models)


def build_model_options(slots: list[dict[str, Any]]) -> list[str]:
    """Build the universal model dropdown options list.

    Priority:
    1. Models selected from OpenRouter catalog (persisted in selected_models.json)
    2. Persisted slot model assignments
    3. Models currently configured in backend slots
    4. Config file default models as last resort
    """
    sel = get_selected_models()
    persisted = [m for m in _role_models.values() if m and not m.startswith("<")]
    backend = [s.get("model", "") for s in slots if s.get("model") and not s.get("model", "").startswith("<")]
    opts = list(dict.fromkeys(sel + persisted + backend + ["google/gemma-3-4b-it"]))
    return [m for m in opts if m] or ["(no models -- open Settings)"]


# ── PER-SESSION STATE ─────────────────────────────────────────────────

_session_states: dict[str, "GuiState"] = {}


class GuiState:
    """Per-session GUI state — one instance per NiceGUI client.

    Replaces the module-level _state singleton from shell.py.
    """

    def __init__(self) -> None:
        self.api_client: AipApiClient = get_api_client()
        self.session_id: str | None = None
        self.current_role: str | None = None
        self.current_model_slot: str = "synthesis"
        self.current_mode: str = "normal"  # "normal" or "augmented"
        self.available_slots: list[dict[str, Any]] = []
        self.backend_reachable: bool = False
        self.pending_gate: dict[str, Any] | None = None
        self.auto_save: bool = True
        self.ingestion_status: str = "idle"  # "idle" | "ingesting" | "error"
        self.chunks_indexed: int = 0
        self.current_project: str | None = None
        self.client = None  # NiceGUI client reference for background task UI updates

        # ── New fields for UI Cycle 2 ──
        self.dogfood_mode: str = "BARE"  # "FULL" | "DEGRADED" | "BARE" | "DIRECT MODEL ONLY"
        self.actor_status: dict[str, Any] = {}
        self.retrieval_health: dict[str, Any] = {}
        self.warnings: list[str] = []
        self.pending_gates_count: int = 0

        # ── UI Cycle 3: Consolidated status summary from /api/v1/status/summary ──
        self.status_summary: dict[str, Any] = {}

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
        """Reset session state (e.g., when changing models/modes)."""
        self.session_id = None
        self.pending_gate = None
        self.ingestion_status = "idle"
        self.chunks_indexed = 0

    def refresh_dogfood_mode(self) -> None:
        """Compute dogfood_mode from status_summary or backend_reachable + actor/retrieval health.

        If status_summary is populated (from /api/v1/status/summary), uses its
        dogfood_mode directly. Otherwise falls back to heuristic from actor/retrieval health.

        FULL:      backend reachable, all actors active, retrieval healthy
        DEGRADED:  backend reachable, some subsystems down
        BARE:      backend reachable, no actors or retrieval
        DIRECT MODEL ONLY: backend unreachable
        """
        # Prefer the authoritative dogfood_mode from the consolidated endpoint
        if self.status_summary and "dogfood_mode" in self.status_summary:
            self.dogfood_mode = self.status_summary["dogfood_mode"]
            return

        if not self.backend_reachable:
            self.dogfood_mode = "DIRECT MODEL ONLY"
            return

        actors_ok = all(
            self.actor_status.get(a, {}).get("initialized", False)
            for a in ("beast", "vigil", "sexton")
        )
        retrieval_ok = bool(self.retrieval_health)

        if actors_ok and retrieval_ok:
            self.dogfood_mode = "FULL"
        elif actors_ok or retrieval_ok:
            self.dogfood_mode = "DEGRADED"
        else:
            self.dogfood_mode = "BARE"

    async def refresh_status_summary(self) -> None:
        """Fetch consolidated status summary from /api/v1/status/summary.

        Populates self.status_summary and updates derived fields:
          - dogfood_mode
          - actor_status (from actor_status_summary)
          - retrieval_health (from retrieval_health_summary)
          - warnings (from warnings list)
          - pending_gates_count (from review_queue_summary.count)

        This is the single-call refresh path for UI Cycle 3.
        If the fetch fails, fields are left unchanged and backend_reachable is set False.
        """
        try:
            summary = await self.api_client.get_status_summary()
            if not summary:
                self.backend_reachable = False
                return

            self.status_summary = summary
            self.backend_reachable = True

            # Derive actor status from the summary
            actor_summary = summary.get("actor_status_summary", {})
            if actor_summary:
                self.actor_status = actor_summary

            # Derive retrieval health from the summary
            retrieval_summary = summary.get("retrieval_health_summary", {})
            if retrieval_summary:
                self.retrieval_health = retrieval_summary

            # Derive warnings
            warnings_list = summary.get("warnings", [])
            if isinstance(warnings_list, list):
                self.warnings = warnings_list

            # Derive pending gates count
            review_summary = summary.get("review_queue_summary", {})
            if isinstance(review_summary, dict) and "count" in review_summary:
                self.pending_gates_count = review_summary["count"]

            # Refresh dogfood mode from authoritative source
            self.refresh_dogfood_mode()

        except Exception as exc:
            log.error("refresh_status_summary failed: %s", exc)
            self.backend_reachable = False


def get_session_state() -> GuiState:
    """Get or create a per-session GuiState.

    Uses NiceGUI's app.storage.user when available for per-client state.
    Falls back to a simple ID-based map when storage is not yet initialized.
    """
    try:
        from nicegui import app, context

        # Try to use NiceGUI's per-user storage
        client_id = getattr(context.client, "id", None)
        if client_id is None:
            # No client context (e.g., during import or testing)
            return GuiState()

        if client_id not in _session_states:
            _session_states[client_id] = GuiState()
        return _session_states[client_id]
    except Exception:
        # Fallback for import-time or test contexts
        return GuiState()
