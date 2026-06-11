"""Retrieval Channel Results — per-channel result cards for the Retrieval Lab.

Displays each channel's result in a card with:
- Channel name and state badge
- Result count and latency
- Result items (id, title, snippet, score)
- Warning/error messages
- Vector-specific fields (backend type, VSS, embedding)
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from gui.theme import (
    C_AMBER,
    C_CREAM,
    C_ERR_FG,
    C_GROUND,
    C_INK40,
    C_MUTED,
    C_OK_FG,
    C_SURFACE,
    F_MONO,
    F_SANS,
    R_SM,
)

# Channel display labels
CHANNEL_LABELS: dict[str, str] = {
    "fts": "Lexical (FTS5)",
    "vector": "Vector",
    "graph": "Graph",
    "wiki": "Wiki/CODEX",
    "procedural": "Procedural",
    "corpus": "Corpus",
}

# State badge colors
STATE_COLORS: dict[str, str] = {
    "active": C_OK_FG,
    "degraded": C_AMBER,
    "failed": C_ERR_FG,
    "disabled": C_MUTED,
    "unavailable": C_MUTED,
    "not_configured": C_MUTED,
    "empty": C_AMBER,
}


class RetrievalChannelResults:
    """Renders per-channel result cards for a retrieval test."""

    def __init__(self) -> None:
        self._container: ui.column | None = None

    def render(self, test_result: dict[str, Any]) -> None:
        """Render channel result cards from a retrieval test response.

        Args:
            test_result: Response from POST /api/v1/retrieval/test.
        """
        if self._container is None:
            return
        self._container.clear()

        channel_results = test_result.get("channel_results", {})
        if not channel_results:
            with self._container:
                ui.label("No channel results").style(f"font-size:12px; color:{C_MUTED}; font-family:{F_MONO};")
            return

        # Sort channels: active first, then degraded, then empty, then failed, then unavailable/not_configured
        order = {
            "active": 0,
            "degraded": 1,
            "empty": 2,
            "failed": 3,
            "disabled": 4,
            "unavailable": 5,
            "not_configured": 6,
        }
        sorted_channels = sorted(
            channel_results.keys(),
            key=lambda ch: order.get(channel_results[ch].get("state", "unavailable"), 7),
        )

        with self._container:
            with ui.row().classes("w-full items-center gap-2 mb-2"):
                ui.label("Channel Results").style(
                    f"font-family:{F_SANS}; font-size:14px; font-weight:700; color:{C_CREAM};"
                )

                # Quick summary badges
                ch_health = test_result.get("channel_health", {})
                active = sum(1 for s in ch_health.values() if s == "active")
                degraded = sum(1 for s in ch_health.values() if s == "degraded")
                failed = sum(1 for s in ch_health.values() if s in ("failed", "unavailable"))
                if active > 0:
                    ui.label(f"{active} active").style(
                        f"font-size:9px; color:{C_OK_FG}; font-family:{F_MONO}; "
                        f"border:0.5px solid {C_OK_FG}; border-radius:2px; padding:1px 4px;"
                    )
                if degraded > 0:
                    ui.label(f"{degraded} degraded").style(
                        f"font-size:9px; color:{C_AMBER}; font-family:{F_MONO}; "
                        f"border:0.5px solid {C_AMBER}; border-radius:2px; padding:1px 4px;"
                    )
                if failed > 0:
                    ui.label(f"{failed} failed").style(
                        f"font-size:9px; color:{C_ERR_FG}; font-family:{F_MONO}; "
                        f"border:0.5px solid {C_ERR_FG}; border-radius:2px; padding:1px 4px;"
                    )

            for ch_key in sorted_channels:
                ch_data = channel_results[ch_key]
                self._render_channel_card(ch_key, ch_data)

    def _render_channel_card(self, ch_key: str, ch_data: dict[str, Any]) -> None:
        """Render a single channel result card."""
        state = ch_data.get("state", "unavailable")
        label = CHANNEL_LABELS.get(ch_key, ch_data.get("channel", ch_key))
        color = STATE_COLORS.get(state, C_MUTED)
        result_count = ch_data.get("result_count", 0)
        latency_ms = ch_data.get("latency_ms", 0)
        warning = ch_data.get("warning", "")
        error = ch_data.get("error", "")
        items = ch_data.get("items", [])
        backend_type = ch_data.get("backend_type", "")

        # Card background: slightly different for non-active channels
        bg = C_SURFACE if state in ("active", "empty") else C_GROUND

        with (
            ui.card()
            .classes("w-full p-3 mb-2")
            .style(
                f"background:{bg}; border:0.5px solid {C_INK40}; border-radius:{R_SM}; border-left:3px solid {color};"
            )
        ):
            # Header row
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(label).style(f"font-family:{F_SANS}; font-size:13px; font-weight:700; color:{C_CREAM};")

                # State badge
                ui.label(state.upper().replace("_", " ")).style(
                    f"font-size:9px; font-weight:600; color:{color}; "
                    f"font-family:{F_MONO}; text-transform:uppercase; "
                    f"border:0.5px solid {color}; border-radius:2px; padding:1px 4px;"
                )

                # Result count and latency
                ui.label(f"{result_count} results").style(f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO};")
                if latency_ms > 0:
                    latency_color = C_OK_FG if latency_ms < 500 else C_AMBER if latency_ms < 2000 else C_ERR_FG
                    ui.label(f"{latency_ms:.0f}ms").style(
                        f"font-size:10px; color:{latency_color}; font-family:{F_MONO};"
                    )

                # Backend type for vector
                if backend_type and state not in ("unavailable", "not_configured"):
                    ui.label(f"[{backend_type}]").style(f"font-size:9px; color:{C_MUTED}; font-family:{F_MONO};")

            # Warning/error messages
            if warning:
                ui.label(f"Warning: {warning[:120]}").style(
                    f"font-size:10px; color:{C_AMBER}; font-family:{F_MONO}; margin-top:2px;"
                )
            if error:
                ui.label(f"Error: {error[:120]}").style(
                    f"font-size:10px; color:{C_ERR_FG}; font-family:{F_MONO}; margin-top:2px;"
                )

            # Vector-specific info
            vss = ch_data.get("vss_available")
            emb = ch_data.get("embedding_provider_configured")
            if vss is not None or emb is not None:
                with ui.row().classes("gap-2 mt-1"):
                    if vss is not None:
                        vss_label = "VSS: available" if vss else "VSS: not available"
                        vss_color = C_OK_FG if vss else C_AMBER
                        ui.label(vss_label).style(f"font-size:9px; color:{vss_color}; font-family:{F_MONO};")
                    if emb is not None:
                        emb_label = "Embedding: configured" if emb else "Embedding: not configured"
                        emb_color = C_OK_FG if emb else C_ERR_FG
                        ui.label(emb_label).style(f"font-size:9px; color:{emb_color}; font-family:{F_MONO};")

            # Result items (collapsible if many)
            if items:
                with (
                    ui.expansion(
                        f"Show {len(items)} result(s)",
                        value=len(items) <= 5,
                    )
                    .classes("w-full mt-1")
                    .style(f"font-size:11px; color:{C_CREAM}; font-family:{F_SANS};")
                ):
                    for i, item in enumerate(items):
                        self._render_item(i, item)
            elif state in ("active", "empty") and result_count == 0:
                ui.label("No results returned").style(
                    f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO}; margin-top:4px;"
                )

    def _render_item(self, index: int, item: dict[str, Any]) -> None:
        """Render a single result item."""
        item_id = item.get("id", "")
        title = item.get("title", "")[:80]
        snippet = item.get("snippet", "")[:200]
        score = item.get("score", 0)
        source_type = item.get("source_type", "")
        domain = item.get("domain", "")

        with ui.row().classes("w-full items-start gap-1 py-1").style(f"border-bottom:0.5px solid {C_INK40};"):
            # Index
            ui.label(f"#{index + 1}").style(f"font-size:9px; color:{C_MUTED}; font-family:{F_MONO}; min-width:20px;")
            # Content
            with ui.column().classes("flex-1").style("gap:1px;"):
                if title:
                    ui.label(title).style(f"font-size:11px; font-weight:600; color:{C_CREAM}; font-family:{F_SANS};")
                if snippet:
                    ui.label(snippet).style(
                        f"font-size:9px; color:{C_MUTED}; font-family:{F_MONO}; "
                        f"white-space:pre-wrap; max-height:60px; overflow:hidden;"
                    )
            # Score
            score_color = C_OK_FG if score >= 0.8 else C_AMBER if score >= 0.5 else C_MUTED
            ui.label(f"{score:.3f}").style(
                f"font-size:10px; color:{score_color}; font-family:{F_MONO}; min-width:40px; text-align:right;"
            )
