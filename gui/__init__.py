"""AIP_Brain Operator Console — NiceGUI Frontend.

UI Cycle 2: Three-region layout (top bar, left nav, main workspace, right rail).

This package implements the GUI as an Adapter-layer surface, communicating
with the backend exclusively through REST and WebSocket endpoints.
No direct imports from aip.orchestration or AipContainer.

Entry point: python -m gui.app

Structure:
  gui/theme.py           — Design tokens (colors, fonts, spacing)
  gui/state.py           — Per-session GuiState + persistence helpers
  gui/api_client.py      — HTTP/WebSocket client for backend communication
  gui/app.py             — Entry point (registers page routes)
  gui/components/        — Reusable UI building blocks
  gui/pages/             — Page modules (each registers @ui.page route)
  gui/panels/            — Persistent panels (right rail)
  gui/archive/           — Deprecated modules preserved for reference
"""
