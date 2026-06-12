"""Corpus Problems — problems panel for the Corpus Workbench.

Shows failed ingest jobs, unembedded chunks, stale documents,
and duplicate hashes. Honest empty states — never fakes healthy.

Import boundary: imports ONLY from gui.* (theme, api_client).
Never imports from aip.orchestration.
"""

from __future__ import annotations

import logging
from typing import Any

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
    C_WARN_FG,
    F_MONO,
    F_SANS,
    R_MD,
)

log = logging.getLogger("gui.components.corpus_problems")


class CorpusProblems:
    """Corpus problems panel for the Corpus Workbench.

    Shows:
      - Failed ingest/embed jobs (with turn_id, source_path, error)
      - Unembedded chunk count
      - Stale documents
      - Duplicate content hashes

    All data comes from backend API. Honest empty states.
    Never fakes healthy — if the store is unavailable, says so.
    """

    def __init__(self) -> None:
        self._container: ui.column | None = None

    def render(self, problems: dict[str, Any]) -> None:
        """Render the problems panel."""
        if self._container is not None:
            self._container.clear()

        with (
            ui.column()
            .classes("w-full")
            .style(
                f"background:{C_RAISED}; border:0.5px solid {C_INK40}; border-radius:{R_MD}; padding:12px; gap:8px;"
            ) as col
        ):
            self._container = col

            available = problems.get("available", True)
            error = problems.get("error", "")

            # Header
            ui.label("Problems & Warnings").style(
                f"font-size:14px; font-weight:700; color:{C_CREAM}; font-family:{F_SANS};"
            )

            if not available:
                ui.label("Corpus status unavailable — cannot check for problems.").style(
                    f"color:{C_MUTED}; font-size:12px; font-family:{F_SANS};"
                )
                if error:
                    ui.label(f"Error: {error}").style(f"color:{C_ERR_FG}; font-size:10px; font-family:{F_MONO};")
                return

            # Collect problem sections
            has_problems = False

            # Failed ingest/embed jobs
            failed_jobs = problems.get("failed_ingest_jobs", [])
            if failed_jobs:
                has_problems = True
                ui.label(f"Failed Embed Jobs ({len(failed_jobs)})").style(
                    f"font-size:12px; font-weight:600; color:{C_ERR_FG}; font-family:{F_SANS};"
                )
                for job in failed_jobs[:10]:
                    turn_id = job.get("turn_id", "?")
                    source_path = job.get("source_path", "")
                    fail_count = job.get("fail_count", 0)
                    last_error = job.get("last_error", "")
                    with ui.row().style("gap:8px; padding:2px 0;"):
                        ui.label(f"x{fail_count}").style(
                            f"font-size:10px; color:{C_ERR_FG}; font-family:{F_MONO}; min-width:24px;"
                        )
                        ui.label(source_path[:60] if source_path else turn_id[:24]).style(
                            f"font-size:10px; color:{C_CREAM}; font-family:{F_MONO}; "
                            f"overflow:hidden; text-overflow:ellipsis; flex:1;"
                        )
                        ui.label(last_error[:60]).style(f"font-size:10px; color:{C_MUTED}; font-family:{F_MONO};")
                if len(failed_jobs) > 10:
                    ui.label(f"... and {len(failed_jobs) - 10} more").style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_SANS};"
                    )

            # Unembedded chunks
            unembedded_count = problems.get("unembedded_count", 0)
            needs_reembed_count = problems.get("needs_reembed_count", 0)
            if unembedded_count > 0 or needs_reembed_count > 0:
                has_problems = True
                with ui.row().style("gap:8px; padding:2px 0;"):
                    ui.label("Unembedded Chunks:").style(f"font-size:11px; color:{C_WARN_FG}; font-family:{F_SANS};")
                    ui.label(str(unembedded_count)).style(f"font-size:11px; color:{C_CREAM}; font-family:{F_MONO};")
                    if needs_reembed_count > 0:
                        ui.label(f"(needs re-embed: {needs_reembed_count})").style(
                            f"font-size:10px; color:{C_AMBER}; font-family:{F_SANS};"
                        )

            # Duplicate hashes
            duplicates = problems.get("duplicate_hashes", [])
            if duplicates:
                has_problems = True
                ui.label(f"Duplicate Content Hashes ({len(duplicates)})").style(
                    f"font-size:12px; font-weight:600; color:{C_AMBER}; font-family:{F_SANS};"
                )
                for dup in duplicates[:5]:
                    content_hash = dup.get("content_hash", "?")[:12]
                    count = dup.get("count", 0)
                    ui.label(f"  {content_hash}... appears {count} times").style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
                    )
                if len(duplicates) > 5:
                    ui.label(f"  ... and {len(duplicates) - 5} more").style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_SANS};"
                    )

            # Stale documents
            stale_docs = problems.get("stale_docs", [])
            if stale_docs:
                has_problems = True
                ui.label(f"Stale Documents ({len(stale_docs)})").style(
                    f"font-size:12px; font-weight:600; color:{C_AMBER}; font-family:{F_SANS};"
                )
                for doc in stale_docs[:5]:
                    source_path = doc.get("source_path", "?")
                    last_updated = doc.get("last_updated", "?")[:10]
                    turn_count = doc.get("turn_count", 0)
                    ui.label(f"  {source_path[:50]} — last updated {last_updated} ({turn_count} turns)").style(
                        f"font-size:10px; color:{C_INK60}; font-family:{F_MONO};"
                    )

            # No problems found
            if not has_problems:
                ui.label("No problems found. Corpus looks healthy.").style(
                    f"color:{C_OK_FG}; font-size:12px; font-family:{F_SANS};"
                )
