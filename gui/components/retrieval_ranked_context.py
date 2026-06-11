"""Retrieval Ranked Context — fused/ranked result list for the Retrieval Lab.

Displays the RRF-fused, quality-gated, ranked context list that would be
selected for answer synthesis. Shows each result with score, source channel,
domain, and snippet. Also shows the fusion scores summary.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from gui.theme import (
    C_CREAM,
    C_GROUND,
    C_SURFACE,
    C_RAISED,
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


class RetrievalRankedContext:
    """Renders the fused, ranked context list from a retrieval test."""

    def __init__(self) -> None:
        self._container: ui.column | None = None

    def render(self, test_result: dict[str, Any]) -> None:
        """Render the ranked context from a retrieval test response.

        Args:
            test_result: Response from POST /api/v1/retrieval/test.
        """
        if self._container is None:
            return
        self._container.clear()

        fusion_results = test_result.get("fusion_results", [])
        selected_context = test_result.get("selected_context", [])
        scores = test_result.get("scores", {})
        warnings = test_result.get("warnings", [])
        lexical_only = test_result.get("lexical_only", False)
        vector_contributed = test_result.get("vector_contributed", False)
        total_latency = test_result.get("latency_ms", 0)

        with self._container:
            # Header with honesty flags
            with ui.row().classes("w-full items-center gap-2 mb-2"):
                ui.label("Ranked Context").style(
                    f"font-family:{F_SANS}; font-size:14px; font-weight:700; color:{C_CREAM};"
                )
                ui.label(f"({len(fusion_results)} results)").style(
                    f"font-size:11px; color:{C_MUTED}; font-family:{F_MONO};"
                )

                # Honesty flags
                if lexical_only:
                    ui.label("LEXICAL ONLY").style(
                        f"font-size:9px; font-weight:600; color:{C_AMBER}; "
                        f"font-family:{F_MONO}; border:0.5px solid {C_AMBER}; "
                        f"border-radius:2px; padding:1px 4px;"
                    )
                if vector_contributed:
                    ui.label("VECTOR OK").style(
                        f"font-size:9px; font-weight:600; color:{C_OK_FG}; "
                        f"font-family:{F_MONO}; border:0.5px solid {C_OK_FG}; "
                        f"border-radius:2px; padding:1px 4px;"
                    )
                else:
                    ui.label("NO VECTOR").style(
                        f"font-size:9px; font-weight:600; color:{C_ERR_FG}; "
                        f"font-family:{F_MONO}; border:0.5px solid {C_ERR_FG}; "
                        f"border-radius:2px; padding:1px 4px;"
                    )

            # Latency
            if total_latency > 0:
                latency_color = C_OK_FG if total_latency < 500 else C_AMBER if total_latency < 2000 else C_ERR_FG
                ui.label(f"Total latency: {total_latency:.0f}ms").style(
                    f"font-size:10px; color:{latency_color}; font-family:{F_MONO};"
                )

            # Scores summary
            if scores:
                verdict = scores.get("verdict", "")
                hits_before = scores.get("hits_before_fusion", 0)
                hits_after = scores.get("hits_after_fusion", 0)
                hits_gate = scores.get("hits_after_quality_gate", 0)

                verdict_color = C_OK_FG if verdict == "OK" else C_AMBER if verdict == "NEEDS_MORE_CONTEXT" else C_ERR_FG
                with ui.row().classes("gap-3 items-center mb-1"):
                    ui.label(f"Verdict: {verdict}").style(
                        f"font-size:10px; font-weight:600; color:{verdict_color}; "
                        f"font-family:{F_MONO};"
                    )
                    ui.label(f"Before fusion: {hits_before}").style(
                        f"font-size:9px; color:{C_MUTED}; font-family:{F_MONO};"
                    )
                    ui.label(f"After fusion: {hits_after}").style(
                        f"font-size:9px; color:{C_MUTED}; font-family:{F_MONO};"
                    )
                    ui.label(f"After gate: {hits_gate}").style(
                        f"font-size:9px; color:{C_MUTED}; font-family:{F_MONO};"
                    )

            # Warnings
            if warnings:
                with ui.column().classes("w-full gap-1 mb-2"):
                    for w in warnings:
                        ui.label(f"Warning: {w[:150]}").style(
                            f"font-size:9px; color:{C_AMBER}; font-family:{F_MONO};"
                        )

            # Result list
            if not fusion_results:
                with ui.card().classes("w-full p-4").style(
                    f"background:{C_GROUND}; border:0.5px solid {C_INK40}; "
                    f"border-radius:{R_SM};"
                ):
                    ui.label("No context selected — retrieval returned no results").style(
                        f"font-size:12px; color:{C_MUTED}; font-family:{F_MONO};"
                    )
                return

            for i, result in enumerate(fusion_results):
                self._render_result_item(i, result)

    def _render_result_item(self, index: int, result: dict[str, Any]) -> None:
        """Render a single ranked result item."""
        result_id = result.get("id", "")
        title = result.get("title", "")[:100]
        snippet = result.get("snippet", "")[:300]
        score = result.get("score", 0)
        source_type = result.get("source_type", "")
        domain = result.get("domain", "")

        with ui.row().classes("w-full items-start gap-2 py-2").style(
            f"border-bottom:0.5px solid {C_INK40};"
        ):
            # Rank number
            ui.label(f"#{index + 1}").style(
                f"font-size:11px; font-weight:700; color:{C_CREAM}; "
                f"font-family:{F_MONO}; min-width:28px;"
            )

            # Content
            with ui.column().classes("flex-1").style("gap:1px;"):
                if title:
                    ui.label(title).style(
                        f"font-size:12px; font-weight:600; color:{C_CREAM}; "
                        f"font-family:{F_SANS};"
                    )
                if snippet:
                    ui.label(snippet[:200]).style(
                        f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO}; "
                        f"white-space:pre-wrap; max-height:80px; overflow:hidden;"
                    )
                # Source info
                with ui.row().classes("gap-2 items-center"):
                    if source_type:
                        ui.label(source_type).style(
                            f"font-size:8px; color:{C_MUTED}; font-family:{F_MONO}; "
                            f"border:0.5px solid {C_INK40}; border-radius:2px; padding:0 3px;"
                        )
                    if domain:
                        ui.label(domain).style(
                            f"font-size:8px; color:{C_AMBER}; font-family:{F_MONO};"
                        )

            # Score
            score_color = C_OK_FG if score >= 0.8 else C_AMBER if score >= 0.5 else C_MUTED
            ui.label(f"{score:.4f}").style(
                f"font-size:11px; font-weight:600; color:{score_color}; "
                f"font-family:{F_MONO}; min-width:50px; text-align:right;"
            )
