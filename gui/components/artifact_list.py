"""Artifact List — renders a filtered list of artifacts for the workbench.

Supports tab-based filtering by ECS state:
  - All
  - Generated (pending review)
  - Needs Revision (has NEEDS_REVISION verdict)
  - Approved
  - Exported (has export event)
  - Rejected
  - Overrides (force-exported artifacts)

Each item shows: artifact_id, title, state badge, artifact type, source count,
created date. Clicking an item triggers the on_select callback.

Empty states:
  - No artifacts yet
  - Backend unavailable
  - No artifacts matching filter

Import boundary: imports only gui.* (no aip.* imports).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from nicegui import ui

from gui.components.artifact_state_badge import render_artifact_state_badge
from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_GROUND,
    C_INK40,
    C_INK60,
    C_MUTED,
    C_OK_FG,
    C_RAISED,
    C_SURFACE,
    F_MONO,
    F_SANS,
    R_MD,
    R_SM,
)

log = logging.getLogger("gui.components.artifact_list")

# Tab definitions: (filter_value, display_label)
TABS = [
    ("ALL", "All"),
    ("GENERATED", "Generated"),
    ("NEEDS_REVISION", "Needs Revision"),
    ("APPROVED", "Approved"),
    ("EXPORTED", "Exported"),
    ("REJECTED", "Rejected"),
    ("OVERRIDE", "Overrides"),
]


def render_artifact_list(
    api_client: Any,
    *,
    on_select: Callable[[str], None] | None = None,
    on_refresh: Callable[[], None] | None = None,
) -> ui.column:
    """Render the artifact list with tab filtering.

    Parameters:
        api_client: AipApiClient instance
        on_select: Callback when an artifact is selected (receives artifact_id)
        on_refresh: Callback when list needs refresh

    Returns:
        The container column
    """
    container = (
        ui.column()
        .classes("w-full")
        .style(f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; border-radius:{R_MD}; overflow:hidden;")
    )

    state = {
        "active_tab": "ALL",
        "items": [],
        "loading": True,
        "error": None,
        "search": "",
        "total": 0,
        "page": 1,
        "page_size": 20,
    }

    # ── Header ──────────────────────────────────────────────────
    with container:
        with ui.row().classes("w-full items-center").style(f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};"):
            ui.label("ARTIFACTS").style(
                f"font-size:9px; font-weight:600; letter-spacing:1px; "
                f"color:{C_AMBER}; text-transform:uppercase; font-family:{F_MONO};"
            )
            ui.space()
            ui.button(
                "↻", on_click=lambda: asyncio.ensure_future(_load(state, api_client, container, on_select, on_refresh))
            ).props("flat dense unelevated size=xs").style(f"color:{C_INK60}; font-size:12px; font-family:{F_MONO};")

        # ── Search ──────────────────────────────────────────────
        with ui.row().classes("w-full").style(f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};"):
            search_input = (
                ui.input(placeholder="Search artifacts...")
                .props("dense flat outlined size=xs")
                .classes("w-full")
                .style(
                    f"font-size:11px; font-family:{F_MONO}; color:{C_CREAM}; "
                    f"background:{C_GROUND}; border-radius:{R_SM};"
                )
            )
            search_input.on(
                "update:model-value", lambda e: _on_search(e, state, api_client, container, on_select, on_refresh)
            )

        # ── Tabs ────────────────────────────────────────────────
        with (
            ui.row()
            .classes("w-full")
            .style(f"padding:4px 8px; border-bottom:0.5px solid {C_INK40}; overflow-x:auto; flex-wrap:nowrap;")
        ):
            for filter_val, label in TABS:
                _render_tab(filter_val, label, state, api_client, container, on_select, on_refresh)

        # ── Content area ───────────────────────────────────────
        content_area = (
            ui.column().classes("w-full").style("min-height:200px; max-height:calc(100vh - 320px); overflow-y:auto;")
        )
        state["_content_area"] = content_area

    # Initial load
    asyncio.ensure_future(_load(state, api_client, container, on_select, on_refresh))

    return container


def _render_tab(
    filter_val: str,
    label: str,
    state: dict,
    api_client: Any,
    container: ui.column,
    on_select: Callable[[str], None] | None,
    on_refresh: Callable[[], None] | None,
) -> None:
    """Render a single tab button."""
    is_active = state["active_tab"] == filter_val
    bg = C_RAISED if is_active else "transparent"
    color = C_AMBER if is_active else C_INK60
    border = C_AMBER if is_active else "transparent"

    ui.button(
        label, on_click=lambda: _on_tab_click(filter_val, state, api_client, container, on_select, on_refresh)
    ).props("flat dense unelevated size=xs").style(
        f"color:{color}; font-size:9px; font-family:{F_MONO}; "
        f"background:{bg}; border:1px solid {border}; "
        f"border-radius:{R_SM}; padding:3px 8px; margin:2px; "
        f"text-transform:uppercase; letter-spacing:0.5px;"
    )


def _on_tab_click(
    filter_val: str,
    state: dict,
    api_client: Any,
    container: ui.column,
    on_select: Callable[[str], None] | None,
    on_refresh: Callable[[], None] | None,
) -> None:
    """Handle tab click — update filter and reload."""
    state["active_tab"] = filter_val
    state["page"] = 1
    asyncio.ensure_future(_load(state, api_client, container, on_select, on_refresh))


def _on_search(
    e: Any,
    state: dict,
    api_client: Any,
    container: ui.column,
    on_select: Callable[[str], None] | None,
    on_refresh: Callable[[], None] | None,
) -> None:
    """Handle search input."""
    state["search"] = e.args if hasattr(e, "args") else ""
    state["page"] = 1
    asyncio.ensure_future(_load(state, api_client, container, on_select, on_refresh))


async def _load(
    state: dict,
    api_client: Any,
    container: ui.column,
    on_select: Callable[[str], None] | None,
    on_refresh: Callable[[], None] | None,
) -> None:
    """Load artifacts from the API."""
    content_area = state.get("_content_area")
    if content_area is None:
        return

    state["loading"] = True
    state["error"] = None

    # Build query params
    params: dict[str, Any] = {
        "page": state["page"],
        "page_size": state["page_size"],
    }

    active_tab = state["active_tab"]
    if active_tab != "ALL":
        if active_tab == "OVERRIDE":
            # Override tab: show artifacts with force-export events
            # We'll handle this client-side by checking has_export + force_export
            pass  # Fetch all and filter client-side
        else:
            params["ecs_state"] = active_tab

    if state.get("search"):
        params["search"] = state["search"]

    try:
        result = await api_client.list_artifacts(**params)
        items = result.get("items", [])
        total = result.get("total", 0)

        # Client-side filter for OVERRIDE tab
        if active_tab == "OVERRIDE":
            items = [i for i in items if i.get("has_export", False) and i.get("force_export", False)]

        state["items"] = items
        state["total"] = total

    except Exception as exc:
        log.error("artifact_list_load_failed: %s", exc)
        state["error"] = str(exc)
        state["items"] = []
        state["total"] = 0

    state["loading"] = False

    # Re-render content
    _render_content(content_area, state, on_select)


def _render_content(
    content_area: ui.column,
    state: dict,
    on_select: Callable[[str], None] | None,
) -> None:
    """Render the artifact list content."""
    content_area.clear()

    with content_area:
        # Loading state
        if state.get("loading"):
            with ui.row().classes("w-full items-center justify-center").style("padding:32px;"):
                ui.label("Loading artifacts...").style(f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO};")
            return

        # Error state
        if state.get("error"):
            with ui.column().classes("w-full").style(f"padding:16px;"):
                ui.label("Backend unavailable").style(
                    f"font-size:12px; color:{C_CREAM}; font-family:{F_SANS}; font-weight:600;"
                )
                ui.label(state["error"]).style(
                    f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO}; margin-top:4px;"
                )
            return

        items = state.get("items", [])

        # Empty state
        if not items:
            with ui.column().classes("w-full items-center").style("padding:32px;"):
                ui.label("No artifacts found").style(f"font-size:12px; color:{C_MUTED}; font-family:{F_SANS};")
                active_tab = state.get("active_tab", "ALL")
                if active_tab == "ALL":
                    ui.label("Create artifacts by asking questions and saving answers").style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; margin-top:4px;"
                    )
                else:
                    ui.label(f"No artifacts in {active_tab} state").style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; margin-top:4px;"
                    )
            return

        # Item count
        total = state.get("total", len(items))
        with ui.row().classes("w-full items-center").style(f"padding:4px 12px; border-bottom:0.5px solid {C_INK40};"):
            ui.label(f"{total} artifact{'s' if total != 1 else ''}").style(
                f"font-size:9px; color:{C_INK60}; font-family:{F_MONO};"
            )

        # Render items
        for item in items:
            _render_item(item, on_select)


def _render_item(
    item: dict[str, Any],
    on_select: Callable[[str], None] | None,
) -> None:
    """Render a single artifact list item."""
    artifact_id = item.get("artifact_id", "?")
    title = item.get("title", artifact_id)[:80]
    ecs_state = item.get("ecs_state", "UNKNOWN")
    artifact_type = item.get("artifact_type", "")
    source_count = item.get("source_count", 0)
    created_at = item.get("created_at", "")
    has_needs_revision = item.get("has_needs_revision", False)
    has_export = item.get("has_export", False)

    # Truncate timestamp for display
    display_date = created_at[:16] if len(created_at) > 16 else created_at

    with (
        ui.row()
        .classes("w-full items-center")
        .style(f"padding:8px 12px; border-bottom:0.5px solid {C_INK40}; cursor:pointer; transition:background 0.15s;")
        .on("click", lambda: on_select(artifact_id) if on_select else None)
        .on("mouseenter", lambda: ui.run_javascript(f"this.style.background='{C_RAISED}'"))
        .on("mouseleave", lambda: ui.run_javascript(f"this.style.background='transparent'"))
    ):
        # State badge
        render_artifact_state_badge(
            ecs_state,
            has_needs_revision=has_needs_revision,
            has_export=has_export,
            compact=True,
        )

        # Title and ID
        with ui.column().style("margin-left:8px; flex:1; min-width:0;"):
            ui.label(title).style(
                f"font-size:11px; color:{C_CREAM}; font-family:{F_SANS}; "
                f"font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; "
                f"max-width:280px;"
            )
            # ID line (truncated)
            display_id = artifact_id[:32] + "..." if len(artifact_id) > 32 else artifact_id
            with ui.row().classes("items-center"):
                ui.label(display_id).style(
                    f"font-size:9px; color:{C_INK60}; font-family:{F_MONO}; "
                    f"overflow:hidden; text-overflow:ellipsis; white-space:nowrap; "
                    f"max-width:200px;"
                )
                if artifact_type:
                    ui.label(f"  {artifact_type}").style(f"font-size:8px; color:{C_MUTED}; font-family:{F_MONO};")
                ui.label(f"  {source_count} sources").style(f"font-size:8px; color:{C_INK60}; font-family:{F_MONO};")

        # Date
        if display_date:
            ui.label(display_date).style(
                f"font-size:9px; color:{C_INK60}; font-family:{F_MONO}; flex-shrink:0; margin-left:8px;"
            )
