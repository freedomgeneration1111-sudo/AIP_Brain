"""Wiki Article View component — displays a selected wiki article.

Renders the center panel of the Wiki/CODEX Home page showing article
title, summary, body, status, tags, timestamps, and side panels for
backlinks, related objects, and contradictions.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from nicegui import ui

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
    R_LG,
    R_MD,
    R_SM,
    BORDER,
)

log = logging.getLogger("gui.components.wiki_article_view")


def render_wiki_article_view(
    article: dict[str, Any] | None,
    *,
    backlinks_data: dict[str, Any] | None = None,
    on_edit: Callable[[str], None] | None = None,
    on_create: Callable[[], None] | None = None,
) -> None:
    """Render the wiki article view panel.

    Parameters:
        article: WikiArticle dict from the API, or None for empty state
        backlinks_data: Backlinks response dict, or None
        on_edit: Callback when edit is clicked (receives article ID)
        on_create: Callback when "Create Article" is clicked in empty state
    """
    if article is None:
        _render_empty_state(on_create=on_create)
        return

    # Article header
    with ui.row().classes("w-full items-center").style(
        f"padding:16px 20px; border-bottom:0.5px solid {C_INK40}; background:{C_SURFACE};"
    ):
        # Title and state
        with ui.column().style("flex:1;"):
            ui.label(article.get("title", "Untitled")).style(
                f"font-size:20px; font-weight:700; color:{C_CREAM}; font-family:{F_SANS};"
            )
            state = article.get("status", article.get("state", "UNKNOWN"))
            _render_state_badge(state)

        # Action buttons
        with ui.row().style("gap:8px;"):
            if on_edit:
                ui.button("Edit", on_click=lambda: on_edit(article.get("id", ""))).props(
                    "flat dense unelevated"
                ).style(
                    f"color:{C_AMBER}; border:0.5px solid {C_AMBER}; border-radius:{R_SM}; "
                    f"font-size:11px; font-family:{F_MONO}; padding:4px 12px;"
                )

    # Article content area — two columns: main + sidebar
    with ui.row().classes("w-full").style("gap:16px; padding:16px 20px;"):
        # Main content column
        with ui.column().style("flex:2; min-width:0;"):
            _render_article_content(article)

        # Sidebar: backlinks, related, contradictions
        with ui.column().style("flex:1; min-width:200px;"):
            _render_sidebar(article, backlinks_data)


def _render_empty_state(*, on_create: Callable[[], None] | None = None) -> None:
    """Render the empty/none-selected state."""
    with ui.column().classes("w-full items-center justify-center").style(
        f"padding:48px; min-height:300px;"
    ):
        ui.icon("menu_book", size="48px").style(f"color:{C_INK60}; margin-bottom:16px;")
        ui.label("No article selected").style(
            f"font-size:16px; color:{C_MUTED}; font-family:{F_SANS};"
        )
        ui.label("Select an article from the list, or create a new one.").style(
            f"font-size:12px; color:{C_INK60}; font-family:{F_MONO}; margin-top:4px;"
        )
        if on_create:
            ui.button("Create First Article", on_click=on_create).props(
                "flat dense unelevated"
            ).style(
                f"color:{C_AMBER}; border:0.5px solid {C_AMBER}; border-radius:{R_SM}; "
                f"font-size:11px; font-family:{F_MONO}; padding:6px 16px; margin-top:16px;"
            )


def _render_state_badge(state: str) -> None:
    """Render a state badge for the article."""
    state_colors = {
        "APPROVED": (C_OK_FG, "#0E1F17"),
        "GENERATED": (C_WARN_FG, "#1A1A0E"),
        "REVIEWED": (C_AMBER, "#1A170E"),
        "REJECTED": (C_ERR_FG, "#1A0E0E"),
        "SUPERSEDED": (C_INK60, C_SURFACE),
        "UNKNOWN": (C_MUTED, C_SURFACE),
    }
    fg, bg = state_colors.get(state, (C_MUTED, C_SURFACE))
    ui.label(state).style(
        f"font-size:9px; font-weight:600; color:{fg}; background:{bg}; "
        f"border:0.5px solid {fg}; border-radius:{R_SM}; "
        f"padding:2px 8px; letter-spacing:0.5px; font-family:{F_MONO}; margin-top:4px;"
    )


def _render_article_content(article: dict[str, Any]) -> None:
    """Render the main content area of the article."""
    # Summary
    summary = article.get("summary", "")
    if summary:
        with ui.card().classes("w-full").style(
            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_MD}; padding:12px 16px; margin-bottom:12px;"
        ):
            ui.label("SUMMARY").style(
                f"font-size:9px; font-weight:600; letter-spacing:1px; "
                f"color:{C_AMBER}; text-transform:uppercase; margin-bottom:4px;"
            )
            ui.label(summary).style(
                f"font-size:12px; color:{C_CREAM}; font-family:{F_SANS}; line-height:1.5;"
            )

    # Body
    body = article.get("body", "")
    if body:
        with ui.card().classes("w-full").style(
            f"background:{C_GROUND}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_MD}; padding:16px; margin-bottom:12px;"
        ):
            ui.label("CONTENT").style(
                f"font-size:9px; font-weight:600; letter-spacing:1px; "
                f"color:{C_AMBER}; text-transform:uppercase; margin-bottom:8px;"
            )
            # Render body as preformatted text (wiki content is often markdown)
            ui.label(body).style(
                f"font-size:11px; color:{C_CREAM}; font-family:{F_MONO}; "
                f"white-space:pre-wrap; line-height:1.6; word-break:break-word;"
            )
    elif not summary:
        ui.label("No content yet.").style(
            f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO}; margin-bottom:12px;"
        )

    # Tags
    tags = article.get("tags", [])
    if tags:
        with ui.row().style("gap:4px; margin-bottom:12px; flex-wrap:wrap;"):
            for tag in tags[:10]:
                ui.label(f"#{tag}").style(
                    f"font-size:10px; color:{C_AMBER}; font-family:{F_MONO}; "
                    f"border:0.5px solid {C_INK40}; border-radius:{R_SM}; padding:2px 6px;"
                )

    # Metadata row
    with ui.row().style("gap:16px; flex-wrap:wrap;"):
        domain = article.get("domain", "")
        if domain:
            ui.label(f"Domain: {domain}").style(
                f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
            )
        word_count = article.get("word_count", 0)
        ui.label(f"Words: {word_count}").style(
            f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
        )
        version = article.get("version", 1)
        ui.label(f"Version: {version}").style(
            f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
        )
        updated = article.get("updated_at", "")
        if updated:
            # Show just the date portion
            date_str = updated[:10] if len(updated) >= 10 else updated
            ui.label(f"Updated: {date_str}").style(
                f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
            )


def _render_sidebar(
    article: dict[str, Any],
    backlinks_data: dict[str, Any] | None,
) -> None:
    """Render the sidebar with backlinks, related objects, and contradictions."""
    # Backlinks section
    with ui.card().classes("w-full").style(
        f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
        f"border-radius:{R_MD}; padding:0; margin-bottom:12px;"
    ):
        with ui.row().classes("w-full items-center").style(
            f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};"
        ):
            ui.label("BACKLINKS").style(
                f"font-size:9px; font-weight:600; letter-spacing:1px; "
                f"color:{C_AMBER}; text-transform:uppercase;"
            )

        with ui.column().style("padding:8px 12px;"):
            if backlinks_data and not backlinks_data.get("available", False):
                ui.label("Graph store not available").style(
                    f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO};"
                )
            elif backlinks_data:
                backlinks = backlinks_data.get("backlinks", [])
                if not backlinks:
                    ui.label("No backlinks found").style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
                    )
                else:
                    for bl in backlinks[:8]:
                        source_id = bl.get("source_id", "?")
                        rel = bl.get("relation_type", "?")
                        ui.label(f"{rel}: {source_id[:32]}").style(
                            f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};"
                        )
                    if len(backlinks) > 8:
                        ui.label(f"+ {len(backlinks) - 8} more").style(
                            f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
                        )
            else:
                ui.label("Not loaded").style(
                    f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO};"
                )

    # Related sources section
    source_docs = article.get("source_documents", [])
    related_artifacts = article.get("related_artifacts", [])
    related_turns = article.get("related_turns", [])

    with ui.card().classes("w-full").style(
        f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
        f"border-radius:{R_MD}; padding:0; margin-bottom:12px;"
    ):
        with ui.row().classes("w-full items-center").style(
            f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};"
        ):
            ui.label("RELATED").style(
                f"font-size:9px; font-weight:600; letter-spacing:1px; "
                f"color:{C_AMBER}; text-transform:uppercase;"
            )

        with ui.column().style("padding:8px 12px;"):
            if source_docs:
                ui.label(f"Sources: {len(source_docs)}").style(
                    f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};"
                )
            if related_artifacts:
                ui.label(f"Artifacts: {len(related_artifacts)}").style(
                    f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};"
                )
            if related_turns:
                ui.label(f"Turns: {len(related_turns)}").style(
                    f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};"
                )
            if not source_docs and not related_artifacts and not related_turns:
                ui.label("No related objects linked yet").style(
                    f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
                )

    # Contradictions section
    contradictions = article.get("contradictions", [])
    with ui.card().classes("w-full").style(
        f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
        f"border-radius:{R_MD}; padding:0; margin-bottom:12px;"
    ):
        with ui.row().classes("w-full items-center").style(
            f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};"
        ):
            ui.label("CONTRADICTIONS").style(
                f"font-size:9px; font-weight:600; letter-spacing:1px; "
                f"color:{C_ERR_FG if contradictions else C_AMBER}; text-transform:uppercase;"
            )

        with ui.column().style("padding:8px 12px;"):
            if contradictions:
                for c in contradictions[:5]:
                    severity = c.get("severity", "unknown")
                    claim = c.get("claim_a", "")[:40]
                    ui.label(f"[{severity}] {claim}").style(
                        f"font-size:10px; color:{C_ERR_FG}; font-family:{F_MONO};"
                    )
            else:
                ui.label("No contradictions detected").style(
                    f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
                )

    # Open questions
    open_questions = article.get("open_questions", [])
    if open_questions:
        with ui.card().classes("w-full").style(
            f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
            f"border-radius:{R_MD}; padding:0; margin-bottom:12px;"
        ):
            with ui.row().classes("w-full items-center").style(
                f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};"
            ):
                ui.label("OPEN QUESTIONS").style(
                    f"font-size:9px; font-weight:600; letter-spacing:1px; "
                    f"color:{C_WARN_FG}; text-transform:uppercase;"
                )
            with ui.column().style("padding:8px 12px;"):
                for q in open_questions[:5]:
                    ui.label(f"? {q}").style(
                        f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};"
                    )
