"""Wiki Article List component — searchable, filterable article tree/list.

Renders the left panel of the Wiki/CODEX Home page showing all wiki
articles grouped by domain with search, state filter, and selection.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_ERR_FG,
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

log = logging.getLogger("gui.components.wiki_article_list")


def render_wiki_article_list(
    articles: list[dict[str, Any]],
    *,
    on_select: Callable[[str], None] | None = None,
    selected_id: str | None = None,
    search_text: str = "",
    state_filter: str = "all",
) -> None:
    """Render the wiki article list panel.

    Parameters:
        articles: List of WikiArticle dicts from the API
        on_select: Callback when an article is selected (receives article ID)
        selected_id: Currently selected article ID for highlighting
        search_text: Current search filter
        state_filter: Current state filter ("all", "APPROVED", "GENERATED", etc.)
    """
    # Filter articles
    filtered = articles
    if search_text:
        search_lower = search_text.lower()
        filtered = [
            a
            for a in filtered
            if search_lower in (a.get("title", "")).lower()
            or search_lower in (a.get("domain", "")).lower()
            or search_lower in (a.get("summary", "")).lower()
        ]
    if state_filter and state_filter != "all":
        filtered = [a for a in filtered if a.get("status", a.get("state", "")) == state_filter]

    if not filtered:
        if not articles:
            ui.label("No wiki articles yet.").style(f"font-size:12px; color:{C_MUTED}; font-family:{F_SANS};")
            ui.label("Create your first article or wait for Sexton to generate one.").style(
                f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; margin-top:4px;"
            )
        else:
            ui.label("No articles match the current filter.").style(
                f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO};"
            )
        return

    # Group by domain
    domain_groups: dict[str, list[dict[str, Any]]] = {}
    for article in filtered:
        domain = article.get("domain", "(unclassified)")
        domain_groups.setdefault(domain, []).append(article)

    # Render grouped
    for domain, domain_articles in sorted(domain_groups.items()):
        with (
            ui.column()
            .classes("w-full")
            .style(
                f"margin-bottom:8px; background:{C_SURFACE}; "
                f"border:0.5px solid {C_INK40}; border-radius:{R_MD}; padding:0;"
            )
        ):
            # Domain header
            with (
                ui.row().classes("w-full items-center").style(f"padding:8px 12px; border-bottom:0.5px solid {C_INK40};")
            ):
                ui.icon("folder", size="14px").style(f"color:{C_AMBER}; margin-right:6px;")
                ui.label(domain).style(
                    f"font-size:10px; font-weight:600; letter-spacing:0.5px; "
                    f"color:{C_AMBER}; text-transform:uppercase; font-family:{F_MONO};"
                )
                ui.label(f"({len(domain_articles)})").style(
                    f"font-size:10px; color:{C_INK60}; font-family:{F_MONO}; margin-left:4px;"
                )

            # Articles in this domain
            with ui.column().classes("w-full").style("padding:4px 0;"):
                for article in domain_articles:
                    _render_article_row(
                        article,
                        on_select=on_select,
                        is_selected=article.get("id") == selected_id,
                    )


def _render_article_row(
    article: dict[str, Any],
    *,
    on_select: Callable[[str], None] | None = None,
    is_selected: bool = False,
) -> None:
    """Render a single article row in the list."""
    article_id = article.get("id", "")
    title = article.get("title", article_id)
    state = article.get("status", article.get("state", "UNKNOWN"))
    word_count = article.get("word_count", 0)

    # State color mapping
    state_colors = {
        "APPROVED": C_OK_FG,
        "GENERATED": C_WARN_FG,
        "REVIEWED": C_AMBER,
        "REJECTED": C_ERR_FG,
        "SUPERSEDED": C_INK60,
        "UNKNOWN": C_MUTED,
    }
    state_color = state_colors.get(state, C_MUTED)

    bg = C_RAISED if is_selected else "transparent"
    border_left = f"2px solid {C_AMBER}" if is_selected else "2px solid transparent"

    with (
        ui.row()
        .classes("w-full items-center cursor-pointer")
        .style(f"padding:6px 12px; background:{bg}; border-left:{border_left}; transition:background 0.15s;")
        .on("click", lambda aid=article_id: on_select(aid) if on_select else None)
    ):
        # State indicator dot
        ui.label("●").style(f"font-size:8px; color:{state_color}; margin-right:6px;")

        # Title
        ui.label(title[:48] + ("..." if len(title) > 48 else "")).style(
            f"font-size:11px; color:{C_CREAM if is_selected else C_CREAM}; "
            f"font-weight:{'600' if is_selected else '400'}; font-family:{F_SANS}; flex:1;"
        )

        # State label
        ui.label(state[:3]).style(
            f"font-size:9px; color:{state_color}; font-family:{F_MONO}; "
            f"border:0.5px solid {state_color}; border-radius:{R_SM}; "
            f"padding:1px 4px; letter-spacing:0.5px; margin-left:4px;"
        )
