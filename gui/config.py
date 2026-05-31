"""GUI configuration — reads from AIP backend, not local files.

Phase 1 Communication Bridge: The GUI no longer reads enabled_models.json
directly. Instead, it fetches model slot configuration from the backend
via GET /api/v1/models/slots.

This module provides GUI-specific configuration like the backend URL
and display settings. Model/slot configuration comes from the API.
"""

from __future__ import annotations

import os

# Backend URL — the AIP FastAPI server that the GUI communicates with
AIP_BACKEND_URL = os.getenv("AIP_BACKEND_URL", "http://127.0.0.1:8000")

# NiceGUI server settings
GUI_PORT = int(os.getenv("AIP_GUI_PORT", "8080"))
GUI_RELOAD = os.getenv("AIP_GUI_RELOAD", "true").lower() in ("true", "1", "yes")

# Role-to-slot mapping (used as defaults; backend may override)
DEFAULT_ROLE_SLOTS = {
    "beast": "synthesis",
    "vigil": "evaluation",
    "embedding": "embedding",
}

# Model slot names (must match backend ModelSlotResolver configuration)
KNOWN_SLOTS = ["synthesis", "evaluation", "sexton", "embedding"]
