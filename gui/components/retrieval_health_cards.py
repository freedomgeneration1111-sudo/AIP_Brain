"""Retrieval Health Cards — per-channel health summary for the Retrieval Lab.

Displays a row of cards showing the health state of each retrieval channel:
Lexical, Vector, Graph, Wiki/CODEX, Procedural, and Corpus.
Each card shows: channel name, state badge, backend type, latency hint,
and degradation reason if applicable.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from gui.theme import (
    C_CREAM,
    C_SURFACE,
    C_INK40,
    C_OK_FG,
    C_AMBER,
    C_ERR_FG,
    C_MUTED,
    F_SANS,
    F_MONO,
    R_MD,
    R_SM,
)


# Channel display name mapping
CHANNEL_LABELS: dict[str, str] = {
    "lexical": "Lexical (FTS5)",
    "vector": "Vector",
    "graph": "Graph",
    "wiki": "Wiki/CODEX",
    "procedural": "Procedural",
    "corpus": "Corpus",
}

# Channel icon mapping (simple text icons)
CHANNEL_ICONS: dict[str, str] = {
    "lexical": "F",
    "vector": "V",
    "graph": "G",
    "wiki": "W",
    "procedural": "P",
    "corpus": "C",
}

# State color mapping
STATE_COLORS: dict[str, str] = {
    "active": C_OK_FG,
    "degraded": C_AMBER,
    "failed": C_ERR_FG,
    "disabled": C_MUTED,
    "unavailable": C_MUTED,
    "not_configured": C_MUTED,
    "empty": C_AMBER,
}


class RetrievalHealthCards:
    """Renders per-channel health summary cards for the Retrieval Lab."""

    def __init__(self) -> None:
        self._container: ui.column | None = None

    def render(self, health_data: dict[str, Any]) -> None:
        """Render health cards from the /retrieval/health response.

        Args:
            health_data: Response from GET /api/v1/retrieval/health.
        """
        if self._container is not None:
            self._container.clear()
        else:
            return

        channels = health_data.get("channels", {})
        if not channels:
            with self._container:
                ui.label("No channel health data available").style(
                    f"font-size:12px; color:{C_MUTED}; font-family:{F_MONO};"
                )
            return

        with self._container:
            with ui.row().classes("w-full gap-2 flex-wrap"):
                for ch_key, ch_data in channels.items():
                    self._render_channel_card(ch_key, ch_data)

            # Embedding coverage
            embedding = health_data.get("embedding_coverage", {})
            if embedding.get("status") == "available":
                cov_pct = embedding.get("coverage_percent", 0.0)
                cov_color = C_OK_FG if cov_pct > 10 else C_AMBER if cov_pct > 0 else C_ERR_FG
                with ui.row().classes("w-full mt-2 items-center gap-2"):
                    ui.label("Embedding Coverage:").style(f"font-size:11px; color:{C_MUTED}; font-family:{F_SANS};")
                    ui.label(f"{cov_pct:.1f}%").style(
                        f"font-size:13px; font-weight:600; color:{cov_color}; font-family:{F_MONO};"
                    )
                    total = embedding.get("total_turns", 0)
                    embedded = embedding.get("embedded_turns", 0)
                    ui.label(f"({embedded}/{total} turns)").style(
                        f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO};"
                    )

            # Summary counts
            summary = health_data.get("summary", {})
            if summary:
                with ui.row().classes("w-full mt-1 items-center gap-3"):
                    for label, key, color in [
                        ("Active", "active", C_OK_FG),
                        ("Degraded", "degraded", C_AMBER),
                        ("Unavailable", "unavailable", C_ERR_FG),
                    ]:
                        count = summary.get(key, 0)
                        if count > 0:
                            ui.label(f"{label}: {count}").style(f"font-size:10px; color:{color}; font-family:{F_MONO};")

    def _render_channel_card(self, ch_key: str, ch_data: dict[str, Any]) -> None:
        """Render a single channel health card."""
        state = ch_data.get("state", "unavailable")
        channel_name = ch_data.get("channel", ch_key)
        label = CHANNEL_LABELS.get(ch_key, channel_name)
        icon = CHANNEL_ICONS.get(ch_key, "?")
        color = STATE_COLORS.get(state, C_MUTED)
        backend_type = ch_data.get("backend_type", "")
        degradation_reason = ch_data.get("degradation_reason", "")

        with (
            ui.card()
            .classes("p-2")
            .style(
                f"background:{C_SURFACE}; border:0.5px solid {C_INK40}; "
                f"border-radius:{R_SM}; min-width:120px; max-width:180px;"
            )
        ):
            with ui.row().classes("items-center gap-1"):
                ui.label(icon).style(
                    f"font-size:11px; font-weight:700; color:{color}; "
                    f"font-family:{F_MONO}; width:16px; text-align:center;"
                )
                ui.label(label).style(f"font-size:11px; font-weight:600; color:{C_CREAM}; font-family:{F_SANS};")

            # State badge
            state_label = state.upper().replace("_", " ")
            ui.label(state_label).style(
                f"font-size:9px; font-weight:600; color:{color}; "
                f"font-family:{F_MONO}; text-transform:uppercase; "
                f"background:{C_SURFACE}; border:0.5px solid {color}; "
                f"border-radius:2px; padding:1px 4px; margin-top:2px;"
            )

            # Backend type
            if backend_type and backend_type not in ("none", ""):
                ui.label(backend_type).style(f"font-size:9px; color:{C_MUTED}; font-family:{F_MONO};")

            # Degradation reason
            if degradation_reason:
                ui.label(degradation_reason[:60]).style(
                    f"font-size:8px; color:{C_AMBER}; font-family:{F_MONO}; "
                    f"white-space:nowrap; overflow:hidden; text-overflow:ellipsis;"
                )

            # Vector-specific: VSS available
            vss = ch_data.get("vss_available")
            if vss is not None:
                vss_label = "VSS: yes" if vss else "VSS: no"
                vss_color = C_OK_FG if vss else C_AMBER
                ui.label(vss_label).style(f"font-size:8px; color:{vss_color}; font-family:{F_MONO};")

            # Vector-specific: embedding provider
            emb = ch_data.get("embedding_provider_configured")
            if emb is not None:
                emb_label = "Embed: yes" if emb else "Embed: no"
                emb_color = C_OK_FG if emb else C_ERR_FG
                ui.label(emb_label).style(f"font-size:8px; color:{emb_color}; font-family:{F_MONO};")
