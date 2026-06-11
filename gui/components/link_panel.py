"""Crosslink System — Link Panel component.

Displays links and backlinks for a given knowledge object.
Supports:
  - List links for current object (forward + backlinks)
  - Create manual link via Link Editor dialog
  - Approve/reject suggested links
  - Edit relation type
  - Delete link

Status badges:
  - suggested: amber warning, "requires DEFINER approval"
  - approved: green
  - rejected: red strikethrough
  - deleted: grey

Suggested links visibly say "requires DEFINER approval".
Empty/unavailable states are honest — no fake links.

Import boundary: this module imports ONLY from gui.* (theme, api_client, components).
Never imports from aip.orchestration.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from nicegui import ui

from gui.components.link_editor import LinkEditorDialog
from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_ERR_FG,
    C_GROUND,
    C_INK40,
    C_INK60,
    C_MUTED,
    C_OK_FG,
    C_RAISED,
    C_SURFACE,
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_MD,
    R_SM,
)

log = logging.getLogger("gui.components.link_panel")

# Valid object types for the link panel
VALID_OBJECT_TYPES = [
    "source_document",
    "chunk",
    "conversation_turn",
    "retrieval_trace",
    "beast_commentary",
    "wiki_article",
    "artifact",
    "review_event",
    "actor_event",
    "model_comparison_report",
]

# Valid relation types
VALID_RELATION_TYPES = [
    "supports",
    "contradicts",
    "summarizes",
    "extends",
    "mentions",
    "depends_on",
    "implements",
    "supersedes",
    "related_to",
    "generated_from",
    "reviewed_by",
    "approved_by",
]

# Status display config
STATUS_CONFIG = {
    "suggested": {"color": C_AMBER, "bg": "#1A170E", "label": "SUGGESTED", "needs_approval": True},
    "approved": {"color": C_OK_FG, "bg": "#0E1F17", "label": "APPROVED", "needs_approval": False},
    "rejected": {"color": C_ERR_FG, "bg": "#1A0E0E", "label": "REJECTED", "needs_approval": False},
    "deleted": {"color": C_MUTED, "bg": C_SURFACE, "label": "DELETED", "needs_approval": False},
}


def render_link_panel(
    object_type: str,
    object_id: str,
    api_client: Any,
    *,
    show_create: bool = True,
    on_link_changed: Callable[[], None] | None = None,
) -> ui.column:
    """Render a link panel for a knowledge object.

    Parameters:
        object_type: The type of the object (e.g. "wiki_article")
        object_id: The ID of the object
        api_client: AipApiClient instance
        show_create: Whether to show the "Create Link" button
        on_link_changed: Callback when links are modified (for parent refresh)

    Returns:
        The container column (for parent layout management)
    """
    container = ui.column().classes("w-full").style(
        f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
        f"border-radius:{R_MD}; padding:0; margin-bottom:12px;"
    )

    # Shared state for async callbacks
    state = {
        "object_type": object_type,
        "object_id": object_id,
        "forward_links": [],
        "backlinks": [],
        "forward_total": 0,
        "backlink_total": 0,
        "forward_available": False,
        "backlink_available": False,
    }

    # Create the link editor dialog
    editor = LinkEditorDialog(
        source_type=object_type,
        source_id=object_id,
        api_client=api_client,
        on_submit=lambda: _on_link_created(state, api_client, container, on_link_changed),
    )

    async def _load_links():
        """Load forward links and backlinks from the API."""
        # Fetch forward links
        try:
            fwd = await api_client.get_link_forward_links(object_type, object_id)
            state["forward_links"] = fwd.get("forward_links", [])
            state["forward_total"] = fwd.get("total", 0)
            state["forward_available"] = fwd.get("available", False)
        except Exception as exc:
            log.error("link_panel_forward_failed: %s", exc)
            state["forward_links"] = []
            state["forward_total"] = 0
            state["forward_available"] = False

        # Fetch backlinks
        try:
            bl = await api_client.get_link_backlinks(object_type, object_id)
            state["backlinks"] = bl.get("backlinks", [])
            state["backlink_total"] = bl.get("total", 0)
            state["backlink_available"] = bl.get("available", False)
        except Exception as exc:
            log.error("link_panel_backlinks_failed: %s", exc)
            state["backlinks"] = []
            state["backlink_total"] = 0
            state["backlink_available"] = False

        _render_content(container, state, api_client, editor, show_create, on_link_changed)

    # Schedule the async load
    asyncio.ensure_future(_load_links())

    return container


def _render_content(
    container: ui.column,
    state: dict,
    api_client: Any,
    editor: LinkEditorDialog,
    show_create: bool,
    on_link_changed: Callable[[], None] | None,
) -> None:
    """Render the link panel content after data is loaded."""
    container.clear()

    with container:
        # Header
        with ui.row().classes("w-full items-center").style(
            f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};"
        ):
            ui.label("CROSSLINKS").style(
                f"font-size:9px; font-weight:600; letter-spacing:1px; "
                f"color:{C_AMBER}; text-transform:uppercase;"
            )
            total = state["forward_total"] + state["backlink_total"]
            ui.label(f"({total})").style(
                f"font-size:9px; color:{C_INK60}; font-family:{F_MONO}; margin-left:4px;"
            )
            ui.space()
            if show_create:
                ui.button("+ Link", on_click=editor.open_create).props(
                    "flat dense unelevated size=xs"
                ).style(
                    f"color:{C_AMBER}; font-size:9px; font-family:{F_MONO}; "
                    f"border:0.5px solid {C_AMBER}; border-radius:{R_SM}; padding:2px 8px;"
                )

        # Storage availability indicator
        if not state["forward_available"] and not state["backlink_available"]:
            with ui.row().classes("w-full").style(f"padding:8px 12px;"):
                ui.label("Link storage unavailable").style(
                    f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO};"
                )
            return

        # Forward links section
        if state["forward_links"]:
            _render_section_label("OUTGOING LINKS", state["forward_total"])
            for link in state["forward_links"][:8]:
                _render_link_item(
                    link,
                    api_client=api_client,
                    is_forward=True,
                    on_changed=lambda: _on_link_changed(state, api_client, container, editor, show_create, on_link_changed),
                )
            if state["forward_total"] > 8:
                ui.label(f"+ {state['forward_total'] - 8} more outgoing").style(
                    f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; padding:2px 12px;"
                )
        else:
            _render_section_label("OUTGOING LINKS", 0)
            ui.label("No outgoing links").style(
                f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; padding:2px 12px;"
            )

        # Backlinks section
        if state["backlinks"]:
            _render_section_label("INCOMING LINKS", state["backlink_total"])
            for link in state["backlinks"][:8]:
                _render_link_item(
                    link,
                    api_client=api_client,
                    is_forward=False,
                    on_changed=lambda: _on_link_changed(state, api_client, container, editor, show_create, on_link_changed),
                )
            if state["backlink_total"] > 8:
                ui.label(f"+ {state['backlink_total'] - 8} more incoming").style(
                    f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; padding:2px 12px;"
                )
        else:
            _render_section_label("INCOMING LINKS", 0)
            ui.label("No incoming links").style(
                f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; padding:2px 12px;"
            )


def _render_section_label(text: str, count: int) -> None:
    """Render a section label with count."""
    with ui.row().classes("w-full items-center").style(
        f"padding:6px 12px 2px 12px; margin-top:4px;"
    ):
        ui.label(text).style(
            f"font-size:8px; font-weight:700; font-family:{F_MONO}; "
            f"color:{C_INK60}; letter-spacing:0.5px;"
        )
        ui.label(f"({count})").style(
            f"font-size:8px; color:{C_INK60}; font-family:{F_MONO}; margin-left:4px;"
        )


def _render_link_item(
    link: dict[str, Any],
    *,
    api_client: Any,
    is_forward: bool,
    on_changed: Callable[[], None],
) -> None:
    """Render a single link item with status badge and actions."""
    link_id = link.get("id", "?")
    rel_type = link.get("relation_type", "?")
    status = link.get("status", "suggested")
    confidence = link.get("confidence", 1.0)
    approved = link.get("approved_by_definer", False)
    created_by = link.get("created_by", "?")

    # Determine the "other side" of the link
    if is_forward:
        other_type = link.get("target_type", "?")
        other_id = link.get("target_id", "?")
    else:
        other_type = link.get("source_type", "?")
        other_id = link.get("source_id", "?")

    cfg = STATUS_CONFIG.get(status, STATUS_CONFIG["suggested"])

    with ui.row().classes("w-full items-center").style(
        f"padding:4px 12px; "
        f"border-left:2px solid {cfg['color']}; margin:2px 8px; "
        f"background:{cfg['bg']}; border-radius:{R_SM};"
    ):
        # Status badge
        ui.label(cfg["label"]).style(
            f"font-size:8px; font-weight:700; font-family:{F_MONO}; "
            f"color:{cfg['color']}; min-width:58px;"
        )

        # Relation type
        ui.label(f"{rel_type}").style(
            f"font-size:9px; font-weight:600; color:{C_CREAM}; font-family:{F_MONO}; margin-left:4px;"
        )

        # Arrow and target/source
        arrow = "→" if is_forward else "←"
        # Truncate ID for display
        display_id = other_id[:24] + "..." if len(other_id) > 24 else other_id
        ui.label(f"{arrow} {other_type}:{display_id}").style(
            f"font-size:9px; color:{C_INK60}; font-family:{F_MONO}; margin-left:2px; "
            f"max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;"
        )

        # Confidence
        ui.label(f"({confidence:.1f})").style(
            f"font-size:8px; color:{C_INK60}; font-family:{F_MONO}; margin-left:4px;"
        )

        ui.space()

        # "requires DEFINER approval" label for suggested links
        if cfg["needs_approval"]:
            ui.label("requires DEFINER approval").style(
                f"font-size:7px; color:{C_WARN_FG}; font-family:{F_MONO}; "
                f"font-style:italic; margin-right:4px;"
            )

        # Action buttons for suggested links
        if status == "suggested":
            ui.button("Approve", on_click=lambda: _approve_link(link_id, api_client, on_changed)).props(
                "dense flat size=xs"
            ).style(
                f"color:{C_OK_FG}; font-size:8px; font-family:{F_MONO};"
            )
            ui.button("Reject", on_click=lambda: _reject_link(link_id, api_client, on_changed)).props(
                "dense flat size=xs"
            ).style(
                f"color:{C_ERR_FG}; font-size:8px; font-family:{F_MONO};"
            )

        # Delete button for non-deleted links
        if status not in ("deleted",):
            ui.button("✕", on_click=lambda: _delete_link(link_id, api_client, on_changed)).props(
                "dense flat size=xs"
            ).style(
                f"color:{C_INK60}; font-size:9px; font-family:{F_MONO};"
            )


async def _approve_link(link_id: str, api_client: Any, on_changed: Callable[[], None]) -> None:
    """Approve a suggested link."""
    try:
        result = await api_client.update_knowledge_link(
            link_id, approved_by_definer=True
        )
        if result.get("approved_by_definer"):
            ui.notify(f"Link approved: {link_id[:20]}...", color="positive")
        else:
            ui.notify(f"Approval failed: {result.get('error', 'unknown')}", color="negative")
    except Exception as exc:
        ui.notify(f"Approval failed: {exc}", color="negative")
    on_changed()


async def _reject_link(link_id: str, api_client: Any, on_changed: Callable[[], None]) -> None:
    """Reject a suggested link."""
    try:
        result = await api_client.update_knowledge_link(
            link_id, status="rejected", approved_by_definer=False
        )
        if result.get("status") == "rejected":
            ui.notify(f"Link rejected: {link_id[:20]}...", color="warning")
        else:
            ui.notify(f"Rejection failed: {result.get('error', 'unknown')}", color="negative")
    except Exception as exc:
        ui.notify(f"Rejection failed: {exc}", color="negative")
    on_changed()


async def _delete_link(link_id: str, api_client: Any, on_changed: Callable[[], None]) -> None:
    """Delete a link."""
    try:
        result = await api_client.delete_knowledge_link(link_id)
        if result.get("deleted"):
            ui.notify(f"Link deleted: {link_id[:20]}...", color="info")
        else:
            ui.notify(f"Delete failed: {result.get('error', 'unknown')}", color="negative")
    except Exception as exc:
        ui.notify(f"Delete failed: {exc}", color="negative")
    on_changed()


def _on_link_created(
    state: dict,
    api_client: Any,
    container: ui.column,
    on_link_changed: Callable[[], None] | None,
) -> None:
    """Handle link creation — reload data and re-render."""
    asyncio.ensure_future(_reload(state, api_client, container, on_link_changed))


def _on_link_changed(
    state: dict,
    api_client: Any,
    container: ui.column,
    editor: LinkEditorDialog,
    show_create: bool,
    on_link_changed: Callable[[], None] | None,
) -> None:
    """Handle link change (approve/reject/delete) — reload and re-render."""
    asyncio.ensure_future(
        _reload_with_editor(state, api_client, container, editor, show_create, on_link_changed)
    )


async def _reload(
    state: dict,
    api_client: Any,
    container: ui.column,
    on_link_changed: Callable[[], None] | None,
) -> None:
    """Reload link data and re-render the panel."""
    object_type = state["object_type"]
    object_id = state["object_id"]

    try:
        fwd = await api_client.get_link_forward_links(object_type, object_id)
        state["forward_links"] = fwd.get("forward_links", [])
        state["forward_total"] = fwd.get("total", 0)
        state["forward_available"] = fwd.get("available", False)
    except Exception as exc:
        log.warning("reload_forward_links_failed: %s", exc)
        state["forward_links"] = []
        state["forward_total"] = 0
        state["forward_available"] = False

    try:
        bl = await api_client.get_link_backlinks(object_type, object_id)
        state["backlinks"] = bl.get("backlinks", [])
        state["backlink_total"] = bl.get("total", 0)
        state["backlink_available"] = bl.get("available", False)
    except Exception as exc:
        log.warning("reload_backlinks_failed: %s", exc)
        state["backlinks"] = []
        state["backlink_total"] = 0
        state["backlink_available"] = False

    if on_link_changed:
        on_link_changed()


async def _reload_with_editor(
    state: dict,
    api_client: Any,
    container: ui.column,
    editor: LinkEditorDialog,
    show_create: bool,
    on_link_changed: Callable[[], None] | None,
) -> None:
    """Reload and re-render with editor context."""
    await _reload(state, api_client, container, on_link_changed)
    _render_content(container, state, api_client, editor, show_create, on_link_changed)
