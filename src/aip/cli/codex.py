"""CLI commands for the CODEX / Librarian system (aip codex ...).

Provides commands for inspecting and managing AIP's internal corpus map:

  aip codex map           — Show the full corpus map (sources by domain)
  aip codex topics        — List topic nodes in the knowledge map
  aip codex stale         — Show stale documents and aging topics
  aip codex contradictions — Show open contradictions between sources
  aip codex dashboard     — Full CODEX dashboard overview
  aip codex summary X     — "What do I know about X?" summary
  aip codex run           — Run the librarian maintenance cycle
"""

from __future__ import annotations

import asyncio
import sys

import click

from aip.adapter.codex.codex_store import CodexStore
from aip.cli._db_path import get_default_db_path


@click.group("codex")
def codex() -> None:
    """Manage the CODEX — AIP's internal corpus librarian.

    The CODEX (Corpus Organization, Discovery, and EXploration) system
    gives AIP an internal map of its corpus, tracking sources, topics,
    staleness, contradictions, and duplicates.

    Use these commands to inspect and manage the internal map.
    """
    pass


@codex.command("map")
@click.option("--domain", default=None, help="Filter to a specific domain.")
@click.option("--status", default=None, help="Filter by source status (active/stale/superseded).")
@click.option("--limit", default=50, type=int, help="Max sources to show (default 50).")
@click.option("--db-path", default=None, help="SQLite database path.")
def codex_map_cmd(domain: str | None, status: str | None, limit: int, db_path: str | None) -> None:
    """Show the corpus map — sources organized by domain.

    The map provides a structured view of what AIP knows, organized
    by domain. Each source shows its type, status, and freshness.

    Examples:
      aip codex map
      aip codex map --domain aip
      aip codex map --status stale
    """
    resolved_db_path = db_path or get_default_db_path()

    async def _run():
        store = CodexStore(db_path=resolved_db_path)
        await store.initialize()
        try:
            sources = await store.list_sources(domain=domain, status=status, limit=limit)
            if not sources:
                click.echo("No sources found in the CODEX map.")
                click.echo("Run 'aip codex run' to sync sources from the corpus.")
                return

            # Group by domain
            by_domain: dict[str, list] = {}
            for s in sources:
                dom = s.domain or "unclassified"
                by_domain.setdefault(dom, []).append(s)

            click.echo(f"CODEX Map ({len(sources)} sources, {len(by_domain)} domains)")
            click.echo("=" * 60)

            for dom in sorted(by_domain.keys()):
                domain_sources = by_domain[dom]
                active = sum(1 for s in domain_sources if s.status == "active")
                stale = sum(1 for s in domain_sources if s.status == "stale")
                status_str = f"{active} active"
                if stale:
                    status_str += f", {stale} stale"

                click.echo(f"\n  {dom} ({status_str})")

                for s in domain_sources:
                    status_marker = "●" if s.status == "active" else ("◯" if s.status == "stale" else "✕")
                    title = s.title[:50] if s.title else s.source_path[:50]
                    turns = f"{s.turn_count} turns" if s.turn_count else ""
                    words = f"{s.word_count} words" if s.word_count else ""
                    info = ", ".join(p for p in [turns, words] if p)
                    click.echo(f"    {status_marker} {title}  ({info})")

            click.echo(f"\n  Total: {len(sources)} sources across {len(by_domain)} domains")

        finally:
            await store.close()

    try:
        asyncio.run(_run())
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@codex.command("topics")
@click.option("--domain", default=None, help="Filter to a specific domain.")
@click.option("--stale-only", is_flag=True, default=False, help="Show only stale topics.")
@click.option("--limit", default=50, type=int, help="Max topics to show (default 50).")
@click.option("--db-path", default=None, help="SQLite database path.")
def codex_topics_cmd(domain: str | None, stale_only: bool, limit: int, db_path: str | None) -> None:
    """List topic nodes in the knowledge map.

    Topics are the canonical units of the CODEX internal map. Each topic
    represents a distinct concept, area, or subject that the system knows about.

    Examples:
      aip codex topics
      aip codex topics --domain aip
      aip codex topics --stale-only
    """
    resolved_db_path = db_path or get_default_db_path()

    async def _run():
        store = CodexStore(db_path=resolved_db_path)
        await store.initialize()
        try:
            if stale_only:
                topics = await store.get_stale_topics(staleness_threshold=0.3, limit=limit)
            else:
                topics = await store.list_topics(domain=domain, limit=limit)

            if not topics:
                click.echo("No topics found in the CODEX map.")
                click.echo("Run 'aip codex run' to build the topic map.")
                return

            click.echo(f"CODEX Topics ({len(topics)} topics)")
            click.echo("=" * 60)

            for t in topics:
                staleness_label = "fresh"
                if t.staleness_score >= 0.8:
                    staleness_label = "VERY STALE"
                elif t.staleness_score >= 0.5:
                    staleness_label = "stale"
                elif t.staleness_score >= 0.2:
                    staleness_label = "aging"

                wiki_marker = " [wiki]" if t.is_wiki_page else ""
                contra_marker = f" ⚠{t.contradiction_count}" if t.contradiction_count else ""
                title = t.title or t.topic_id

                click.echo(
                    f"  {t.topic_id:<40s}  {title:<30s}  "
                    f"sources:{len(t.source_ids):>3d}  "
                    f"{staleness_label:>10s}{wiki_marker}{contra_marker}"
                )

        finally:
            await store.close()

    try:
        asyncio.run(_run())
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@codex.command("stale")
@click.option("--threshold", default=90, type=int, help="Days since last update (default 90).")
@click.option("--limit", default=20, type=int, help="Max items to show (default 20).")
@click.option("--db-path", default=None, help="SQLite database path.")
def codex_stale_cmd(threshold: int, limit: int, db_path: str | None) -> None:
    """Show stale documents and aging topics.

    Staleness is determined by how long since the source was last updated.
    Topics inherit staleness from their most recent source.

    Examples:
      aip codex stale
      aip codex stale --threshold 30
    """
    resolved_db_path = db_path or get_default_db_path()

    async def _run():
        from aip.foundation.schemas.codex import CodexConfig

        CodexConfig(stale_threshold_days=threshold)

        store = CodexStore(db_path=resolved_db_path)
        await store.initialize()
        try:
            # Stale sources
            stale_sources = await store.get_stale_sources(threshold_days=threshold, limit=limit)

            # Stale topics
            stale_topics = await store.get_stale_topics(staleness_threshold=0.3, limit=limit)

            click.echo(f"CODEX Staleness Report (threshold: {threshold} days)")
            click.echo("=" * 60)

            if stale_sources:
                click.echo(f"\n  Stale Sources ({len(stale_sources)}):")
                for s in stale_sources:
                    title = s.title[:50] if s.title else s.source_path[:50]
                    last = s.last_updated_at[:10] if s.last_updated_at else "unknown"
                    click.echo(f"    ● {title:<50s}  last: {last}  ({s.turn_count} turns)")
            else:
                click.echo("\n  No stale sources found.")

            if stale_topics:
                click.echo(f"\n  Stale Topics ({len(stale_topics)}):")
                for t in stale_topics:
                    title = t.title or t.topic_id
                    click.echo(
                        f"    ● {t.topic_id:<40s}  staleness: {t.staleness_score:.2f}  sources: {len(t.source_ids)}"
                    )
            else:
                click.echo("\n  No stale topics found.")

            if not stale_sources and not stale_topics:
                click.echo("\n  All sources and topics are fresh!")

        finally:
            await store.close()

    try:
        asyncio.run(_run())
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@codex.command("contradictions")
@click.option("--status", default="open", help="Filter by status (default: open).")
@click.option("--severity", default=None, help="Filter by severity (critical/major/minor/apparent).")
@click.option("--limit", default=20, type=int, help="Max contradictions to show (default 20).")
@click.option("--resolve", "contradiction_id", default=None, help="Resolve a contradiction by ID.")
@click.option("--resolution", default=None, help="Resolution status for --resolve.")
@click.option("--notes", default="", help="Resolution notes for --resolve.")
@click.option("--db-path", default=None, help="SQLite database path.")
def codex_contradictions_cmd(
    status: str,
    severity: str | None,
    limit: int,
    contradiction_id: str | None,
    resolution: str | None,
    notes: str,
    db_path: str | None,
) -> None:
    """Show contradictions between sources and manage resolution.

    Contradictions are flagged when different sources make conflicting
    claims about the same topic. They require DEFINER review to resolve.

    Examples:
      aip codex contradictions
      aip codex contradictions --severity critical
      aip codex contradictions --resolve contra-abc123 --resolution resolved_correct --notes "Source A is correct"
    """
    resolved_db_path = db_path or get_default_db_path()

    async def _run():
        store = CodexStore(db_path=resolved_db_path)
        await store.initialize()
        try:
            # Handle resolution
            if contradiction_id:
                if not resolution:
                    click.echo("Error: --resolution is required with --resolve.", err=True)
                    click.echo("Valid resolutions: resolved_correct, resolved_both, resolved_outdated, dismissed")
                    sys.exit(1)
                valid_resolutions = {"resolved_correct", "resolved_both", "resolved_outdated", "dismissed"}
                if resolution not in valid_resolutions:
                    click.echo(f"Error: Invalid resolution '{resolution}'.", err=True)
                    click.echo(f"Valid: {', '.join(valid_resolutions)}")
                    sys.exit(1)
                await store.resolve_contradiction(
                    contradiction_id=contradiction_id,
                    status=resolution,
                    resolved_by="definer",
                    resolution_notes=notes,
                )
                click.echo(f"Resolved contradiction {contradiction_id} as {resolution}.")
                return

            # List contradictions
            contradictions = await store.list_contradictions(status=status, severity=severity, limit=limit)

            if not contradictions:
                click.echo(f"No {status} contradictions found.")
                return

            sev_counts = await store.count_contradictions_by_severity()
            click.echo(f"CODEX Contradictions ({status})")
            click.echo("=" * 60)
            click.echo(
                f"  Open by severity: "
                f"critical:{sev_counts.get('critical', 0)}  "
                f"major:{sev_counts.get('major', 0)}  "
                f"minor:{sev_counts.get('minor', 0)}  "
                f"apparent:{sev_counts.get('apparent', 0)}"
            )
            click.echo()

            for c in contradictions:
                sev_marker = "🔴" if c.severity == "critical" else ("🟡" if c.severity == "major" else "⚪")
                click.echo(f"  {sev_marker} [{c.severity}] {c.contradiction_id}")
                click.echo(f"     Topic: {c.topic_id}")
                click.echo(f"     Source A ({c.source_a_title}): {c.claim_a[:80]}")
                click.echo(f"     Source B ({c.source_b_title}): {c.claim_b[:80]}")
                if c.context:
                    click.echo(f"     Context: {c.context[:100]}")
                click.echo()

        finally:
            await store.close()

    try:
        asyncio.run(_run())
    except SystemExit:
        raise
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@codex.command("dashboard")
@click.option("--db-path", default=None, help="SQLite database path.")
def codex_dashboard_cmd(db_path: str | None) -> None:
    """Show the full CODEX dashboard overview.

    The dashboard provides a comprehensive snapshot of the CODEX system:
    - Source health (active/stale/superseded)
    - Topic map stats
    - Open contradictions
    - Recently changed concepts
    - Stale documents
    - Unclassified documents
    - Overall health score

    Example:
      aip codex dashboard
    """
    resolved_db_path = db_path or get_default_db_path()

    async def _run():
        store = CodexStore(db_path=resolved_db_path)
        await store.initialize()
        try:
            dash = await store.get_dashboard()

            click.echo("╔══════════════════════════════════════════════════╗")
            click.echo("║           CODEX Dashboard — Corpus Map          ║")
            click.echo("╚══════════════════════════════════════════════════╝")
            click.echo()

            # Health score
            health = dash.health_score
            health_bar = "█" * int(health * 20) + "░" * (20 - int(health * 20))
            click.echo(f"  Health: [{health_bar}] {health:.0%}")
            click.echo()

            # Sources
            click.echo("  Sources:")
            click.echo(
                f"    Total: {dash.total_sources}  "
                f"Active: {dash.active_sources}  "
                f"Stale: {dash.stale_sources}  "
                f"Superseded: {dash.superseded_sources}  "
                f"Quarantined: {dash.quarantined_sources}"
            )
            click.echo()

            # Topics
            click.echo("  Topics:")
            click.echo(
                f"    Total: {dash.total_topics}  "
                f"With contradictions: {dash.topics_with_contradictions}  "
                f"With wiki pages: {dash.topics_with_wiki}"
            )
            click.echo()

            # Topic graph
            if dash.topic_graph:
                click.echo("  Topic Graph (by domain):")
                for domain, count in sorted(dash.topic_graph.items(), key=lambda kv: -kv[1]):
                    bar = "█" * min(count, 30)
                    click.echo(f"    {domain:<25s} {bar} {count}")
                click.echo()

            # Contradictions
            click.echo("  Contradictions:")
            click.echo(
                f"    Open: {dash.open_contradictions}  "
                f"Critical: {dash.critical_contradictions}  "
                f"Major: {dash.major_contradictions}  "
                f"Minor: {dash.minor_contradictions}"
            )
            click.echo()

            # Unclassified
            if dash.unclassified_sources:
                click.echo(f"  ⚠ Unclassified sources: {dash.unclassified_sources}")
                click.echo()

            # Recently changed
            if dash.recently_changed:
                click.echo("  Recently Changed:")
                for item in dash.recently_changed[:5]:
                    contra = f" ⚠{item['contradiction_count']}" if item.get("contradiction_count") else ""
                    click.echo(f"    ● {item['title'] or item['topic_id']:<40s}  {item.get('domain', ''):<15s}{contra}")
                click.echo()

            # Stale documents
            if dash.stale_documents:
                click.echo("  Stale Documents:")
                for item in dash.stale_documents[:5]:
                    click.echo(f"    ◯ {item['title']:<50s}  last: {item.get('last_updated_at', 'unknown')[:10]}")
                click.echo()

            # Open contradictions
            if dash.open_contradiction_list:
                click.echo("  Open Contradictions:")
                for item in dash.open_contradiction_list[:5]:
                    click.echo(
                        f"    🔴 [{item['severity']}] {item.get('topic_id', '')}: "
                        f"{item['source_a_title']} vs {item['source_b_title']}"
                    )
                click.echo()

        finally:
            await store.close()

    try:
        asyncio.run(_run())
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@codex.command("summary")
@click.argument("topic")
@click.option("--db-path", default=None, help="SQLite database path.")
def codex_summary_cmd(topic: str, db_path: str | None) -> None:
    """Show "What do I know about X?" summary for a topic.

    Provides a concise overview of everything AIP knows about a
    given topic: sources, contradictions, related topics, staleness.

    Examples:
      aip codex summary vector_search
      aip codex summary aip:architecture
    """
    resolved_db_path = db_path or get_default_db_path()

    async def _run():
        store = CodexStore(db_path=resolved_db_path)
        await store.initialize()
        try:
            # Try direct topic_id first, then search
            summary = await store.get_topic_summary(topic)

            if "error" in summary:
                # Search for the topic
                results = await store.search_topics(topic, limit=5)
                if results:
                    click.echo(f"Topic '{topic}' not found directly. Did you mean:")
                    for t in results:
                        click.echo(f"  {t.topic_id}  ({t.title or t.topic_id}, domain: {t.domain})")
                    click.echo("\nUse the topic_id shown above with 'aip codex summary <topic_id>'")
                else:
                    click.echo(f"No topics matching '{topic}' found in the CODEX map.")
                    click.echo("Run 'aip codex run' to build the topic map first.")
                return

            # Display the summary
            click.echo(f"═══ What do I know about: {summary['title']} ═══")
            click.echo()
            click.echo(f"  Topic ID:    {summary['topic_id']}")
            click.echo(f"  Domain:      {summary['domain']}")
            click.echo(f"  Description: {summary['description']}")
            click.echo(f"  Staleness:   {summary['staleness_label']} (score: {summary['staleness_score']:.2f})")
            click.echo(f"  Sources:     {summary['source_count']}")
            click.echo(f"  Wiki page:   {'Yes' if summary['has_wiki_page'] else 'No'}")
            click.echo()

            if summary["sources"]:
                click.echo("  Sources:")
                for s in summary["sources"]:
                    status = s["status"]
                    marker = "●" if status == "active" else "◯"
                    click.echo(f"    {marker} {s['title']:<40s}  type: {s['source_type']:<12s}  status: {status}")
                click.echo()

            if summary["contradictions"]:
                click.echo(f"  Contradictions ({summary['open_contradictions']} open):")
                for c in summary["contradictions"]:
                    click.echo(f"    [{c['severity']}] {c['claim_a'][:60]} vs {c['claim_b'][:60]}")
                click.echo()

            if summary["related_topics"]:
                click.echo("  Related Topics:")
                for rt in summary["related_topics"]:
                    click.echo(f"    ● {rt['topic_id']}  ({rt.get('title', '')}, domain: {rt.get('domain', '')})")
                click.echo()

            if summary["last_activity_at"]:
                click.echo(f"  Last activity: {summary['last_activity_at'][:10]}")

        finally:
            await store.close()

    try:
        asyncio.run(_run())
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@codex.command("run")
@click.option("--db-path", default=None, help="SQLite database path.")
def codex_run_cmd(db_path: str | None) -> None:
    """Run the Librarian maintenance cycle.

    Executes the full maintenance cycle:
    1. Sync sources from corpus
    2. Classify unclassified sources
    3. Update topic map
    4. Detect contradictions
    5. Compute staleness scores
    6. Detect duplicate candidates

    Example:
      aip codex run
    """
    resolved_db_path = db_path or get_default_db_path()

    async def _run():
        from aip.adapter.codex.codex_store import CodexStore
        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.orchestration.codex.librarian import Librarian

        store = CodexStore(db_path=resolved_db_path)
        await store.initialize()

        # Connect corpus_turn_store for source sync
        cts = CorpusTurnStore(db_path=resolved_db_path)
        await cts.initialize()

        librarian = Librarian(
            codex_store=store,
            corpus_turn_store=cts,
        )

        try:
            click.echo("Running Librarian maintenance cycle...")
            result = await librarian.run_cycle()

            sync = result.get("sync", {})
            classify = result.get("classify", {})
            topics = result.get("topics", {})
            contradictions = result.get("contradictions", {})
            staleness = result.get("staleness", {})
            duplicates = result.get("duplicates", {})

            click.echo()
            click.echo(f"  Sync:       {sync.get('new_sources', 0)} new, {sync.get('updated_sources', 0)} updated")
            click.echo(f"  Classify:   {classify.get('classified', 0)} classified")
            click.echo(f"  Topics:     {topics.get('new_topics', 0)} new, {topics.get('topics_updated', 0)} updated")
            click.echo(f"  Contradict: {contradictions.get('new_contradictions', 0)} new")
            click.echo(
                f"  Staleness:  {staleness.get('topics_updated', 0)} topics scored, "
                f"{staleness.get('sources_marked_stale', 0)} sources marked stale"
            )
            click.echo(f"  Duplicates: {duplicates.get('new_candidates', 0)} new candidates")
            click.echo()
            click.echo(f"  Elapsed: {result.get('cycle_elapsed_seconds', 0):.2f}s")

        finally:
            await store.close()
            await cts.close()

    try:
        asyncio.run(_run())
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
