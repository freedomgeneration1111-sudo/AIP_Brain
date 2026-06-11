"""AIP Modal Dialogs — blocking dialogs for critical interactions."""

from __future__ import annotations

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_INK40,
    C_MUTED,
    C_RAISED,
    C_SURFACE,
    F_SANS,
    R_MD,
)


async def show_api_key_prompt() -> str | None:
    """Show a BLOCKING dialog asking for the OpenRouter API key.

    This dialog cannot be dismissed without entering a key or explicitly
    skipping. Returns the key if provided, None if skipped.
    """
    with (
        ui.dialog().props("persistent") as dialog,
        ui.card().style(
            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_MD}; min-width:420px; padding:24px;"
        ),
    ):
        ui.icon("vpn_key", size="lg", color="amber").classes("q-mb-sm")
        ui.label("OpenRouter API Key Required").style(
            f"font-family:{F_SANS}; font-size:18px; font-weight:700; color:{C_CREAM};"
        )
        ui.label(
            "AIP_Brain uses OpenRouter for ALL model slots (chat, beast, vigil, embed). "
            "Enter your OpenRouter API key to get started. "
            "You can get one at openrouter.ai/keys"
        ).style(f"font-size:13px; color:{C_MUTED}; margin-top:8px;")
        key_input = (
            ui.input(
                placeholder="sk-or-v1-...",
                password=True,
            )
            .props("outlined dense dark")
            .classes("w-full")
            .style(f"margin-top:16px; background:{C_RAISED}; border:0.5px solid {C_INK40}; border-radius:4px;")
        )
        with ui.row().classes("w-full justify-end gap-2").style("margin-top:16px;"):
            ui.button("Skip (limited functionality)", color="grey", on_click=lambda: dialog.submit(None))
            ui.button("Save Key", color="primary", on_click=lambda: dialog.submit(key_input.value.strip()))

    result = await dialog
    return result
