"""CLI commands for the turn-level corpus (aip corpus ...).

Provides ``aip corpus ingest`` for importing source conversation exports
into the CorpusTurnStore (the new atomic turn-level corpus).

This command is SEPARATE from the legacy ``aip ingest`` (which continues
to feed the old artifact + chunk pipeline for backward compat).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from typing import Any

import click

from aip.adapter.corpus_turn_store import CorpusTurnStore
from aip.adapter.event_store_queryable import QueryableEventStore
from aip.cli._db_path import get_default_db_path
from aip.orchestration.ingestion.parsers.claude_parser import parse_claude_export

# For direct Beast tagging from CLI (beast_provider via resolver, dummies for vector/embed)
try:
    from aip.adapter.artifact_store_versioned import VersionedArtifactStore
except Exception:
    VersionedArtifactStore = None  # type: ignore

try:
    from aip.adapter.ecs_store_persistent import PersistentEcsStore
except Exception:
    PersistentEcsStore = None  # type: ignore


@click.group("corpus")
def corpus() -> None:
    """Manage the turn-level AIP corpus (CorpusTurn as atomic unit).

    New ingestion path for source exports (Claude, GPT, etc.).
    Turns are complete user+assistant exchanges — the foundation for
    all future retrieval, Beast work, and knowledge.

    Existing `aip ingest` is untouched.
    """
    pass


@corpus.command("ingest")
@click.argument("path", type=click.Path(exists=True))
@click.option(
    "--source-model",
    required=True,
    type=click.Choice(["claude", "gpt", "deepseek", "glm", "gemini", "grok"], case_sensitive=False),
    help="Source model that produced the export (e.g. claude).",
)
@click.option(
    "--source-account",
    required=True,
    help="Human identifier for this export batch (e.g. claude_export_june_2026).",
)
@click.option(
    "--export-date",
    default=None,
    help="ISO date of the export (defaults to today).",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_ingest_cmd(
    path: str,
    source_model: str,
    source_account: str,
    export_date: str | None,
    db_path: str | None,
) -> None:
    """Ingest a source conversation export into the turn-level corpus."""
    try:
        if not export_date:
            export_date = date.today().isoformat()

        if source_model == "claude":
            try:
                turns, warnings = parse_claude_export(
                    path, source_account, export_date
                )
            except (FileNotFoundError, ValueError) as e:
                click.echo(f"Parse error: {e}", err=True)
                sys.exit(1)
        else:
            click.echo(
                f"Parser for '{source_model}' not yet implemented. "
                f"Coming in a future prompt."
            )
            sys.exit(0)

        click.echo(f"Parsed {len(turns)} turns from {path}")
        if warnings:
            click.echo(f"Warnings ({len(warnings)}):")
            for w in warnings[:10]:
                click.echo(f"  {w}")
            if len(warnings) > 10:
                click.echo(f"  ... and {len(warnings) - 10} more")

        resolved_db_path = db_path or get_default_db_path()

        async def _ingest_turns(turns_list):
            store = CorpusTurnStore(db_path=resolved_db_path)
            await store.initialize()
            try:
                ingested = 0
                skipped = 0
                total = len(turns_list)
                for i, turn in enumerate(turns_list):
                    try:
                        existing = await store.get_turn(turn.turn_id)
                        if existing is not None:
                            skipped += 1
                            continue
                        await store.write_turn(turn)
                        ingested += 1
                    except Exception as e:
                        warnings.append(f"turn {turn.turn_id}: write failed: {e}")
                        skipped += 1

                    if (i + 1) % 100 == 0 or (i + 1) == total:
                        click.echo(f"\rWriting turns... {i+1}/{total}", nl=False)
                click.echo("")
                return ingested, skipped
            finally:
                await store.close()

        ingested, skipped = asyncio.run(_ingest_turns(turns))
        click.echo(
            f"Ingested {ingested} turns. "
            f"{skipped} skipped (duplicates or errors)."
        )

        # Write corpus_modified event (follows existing pattern)
        async def _write_event(turn_count: int):
            event_store = QueryableEventStore(db_path=resolved_db_path)
            await event_store.initialize()
            try:
                await event_store.write_event(
                    event_type="corpus_modified",
                    actor="corpus_ingest",
                    artifact_id="corpus",
                    from_state=None,
                    to_state=None,
                    metadata={
                        "domain": "corpus",
                        "source_model": source_model,
                        "source_account": source_account,
                        "turns_ingested": turn_count,
                        "timestamp": date.today().isoformat(),
                    },
                )
            finally:
                await event_store.close()

        asyncio.run(_write_event(ingested))

        # Get total
        async def _get_total():
            store = CorpusTurnStore(db_path=resolved_db_path)
            await store.initialize()
            try:
                return await store.total_turns()
            finally:
                await store.close()

        total = asyncio.run(_get_total())
        click.echo(f"Corpus now contains {total} turns.")
        click.echo("Run 'aip status' to see full breakdown.")
        click.echo(
            "Beast will tag turns on next cycle. "
            "Run 'aip review list' after Beast runs to see domain summaries."
        )

    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@corpus.command("tag")
@click.option(
    "--limit",
    default=200,
    type=int,
    help="Max turns to tag this run (default 200). No upper limit enforced for manual sessions (pass large values at your own risk/cost).",
)
@click.option(
    "--retag",
    is_flag=True,
    default=False,
    help="If set, also re-tag turns that already have tagging_version > 0 (low-importance first).",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_tag_cmd(limit: int, retag: bool, db_path: str | None) -> None:
    """Run Beast turn tagging directly (batch 8 turns per LLM call to the beast slot).

    Loads the domain registry, pulls untagged (or retag) turns, calls the beast
    provider with the exact mandated prompt, parses/validates results, updates
    via update_beast_tags (increments tagging_version), and files any proposals
    as GENERATED beast_domain_proposal artifacts.

    This is the manual trigger for initial corpus tagging or spot re-tagging.
    Background scheduler will also tag up to 200 untagged per cycle when a
    beast_provider is configured.
    """
    if limit < 1:
        limit = 1

    resolved_db_path = db_path or get_default_db_path()

    async def _run_tagging():
        # Resolve beast_provider exactly like app lifespan (ModelSlotResolver + config/env)
        import tomllib
        from pathlib import Path

        from aip.adapter.model_slot_resolver import ModelSlotResolver
        from aip.foundation.schemas import BeastCadenceConfig
        from aip.orchestration.actors.beast import Beast

        cfg: dict = {}
        config_p = Path("config/aip.config.toml")
        if config_p.exists():
            with open(config_p, "rb") as f:
                cfg = tomllib.load(f)

        resolver = None
        try:
            resolver = ModelSlotResolver(cfg)
            bslot = cfg.get("models", {}).get("beast", {}) if isinstance(cfg.get("models"), dict) else {}
            if not (isinstance(bslot, dict) and bslot.get("provider") and bslot.get("model")):
                resolver = None
        except Exception as exc:
            click.echo(f"Warning: beast slot resolver failed ({exc}); tagging will be skipped if no provider.")
            resolver = None

        if resolver is None:
            click.echo("No [models.beast] slot with model configured (or AIP_BEAST_* env). "
                       "Tagging requires a beast LLM. Configure and retry.", err=True)
            # Still proceed to exercise registry etc; _run will return skipped.

        # Stores (CorpusTurnStore required; artifact+ecs so proposals become GENERATED)
        cts = CorpusTurnStore(db_path=resolved_db_path)
        await cts.initialize()

        arts = None
        if VersionedArtifactStore is not None:
            try:
                arts = VersionedArtifactStore(db_path=resolved_db_path)
                await arts.initialize()
            except Exception:
                arts = None

        ecs = None
        if PersistentEcsStore is not None:
            try:
                ecs = PersistentEcsStore(db_path=resolved_db_path, event_store=None)
                await ecs.initialize()
            except Exception:
                ecs = None

        # Minimal dummies so Beast ctor succeeds (tagging path does not exercise maint/embed)
        class _DummyVectorStore:
            async def health_check(self) -> dict:
                return {"connected": True}
            async def list_stale_vectors(self, **kwargs: Any) -> list[dict]:
                return []
            async def upsert(self, **kwargs: Any) -> None:
                return None

        class _DummyEmbeddingProvider:
            async def embed(self, text: str) -> list[float]:
                # 8-dim fake; never used by tagging
                return [0.0] * 8

        dummy_vec = _DummyVectorStore()
        dummy_emb = _DummyEmbeddingProvider()

        bconf_dict = cfg.get("beast", {}) if isinstance(cfg.get("beast"), dict) else {}
        bconf = BeastCadenceConfig(
            **{k: v for k, v in bconf_dict.items() if k in BeastCadenceConfig.__dataclass_fields__}
        )

        beast = Beast(
            config=bconf,
            vector_store=dummy_vec,
            embedding_provider=dummy_emb,
            beast_provider=resolver,
            artifact_store=arts,
            ecs_store=ecs,
            corpus_turn_store=cts,
        )

        try:
            total = await cts.total_turns()
            # probe for "X of T total" (even if 0, shows intent)
            probe = await cts.get_untagged_turns(limit=1)
            # Load here too for the exact mandated CLI UX line (count + connectors)
            try:
                from aip.orchestration.actors.domain_registry import load_registry
                reg = load_registry("docs/beast_domain_registry_v1.md")
                click.echo(f"Loading domain registry... {len(reg.domains)} domains, {len(reg.connectors)} connectors")
            except Exception as e:
                click.echo(f"Loading domain registry... (error: {e})")
            click.echo(f"Getting untagged turns... {len(probe) or 0} of {total} total (cap {limit})")

            stats = await beast._run_turn_tagging(limit=limit, retag=retag)

            tagged_n = stats.get("turns_tagged", 0)
            failed_n = stats.get("turns_failed", 0)
            prop_n = stats.get("proposals_filed", 0)
            click.echo(f"Tagged {tagged_n} turns. {failed_n} failed. {prop_n} proposals filed.")

            dist = stats.get("domain_distribution", {}) or {}
            if dist:
                # sort desc, show all or top
                top = sorted(dist.items(), key=lambda kv: -kv[1])
                dist_str = ", ".join(f"{d}:{c}" for d, c in top)
                click.echo(f"Domain distribution: {dist_str}")
            else:
                click.echo("Domain distribution: (none this run)")

            if stats.get("note"):
                click.echo(f"Note: {stats['note']}")
            if stats.get("skipped"):
                click.echo(f"Skipped: {stats['skipped']}")
            return stats
        finally:
            await cts.close()

    try:
        asyncio.run(_run_tagging())
    except Exception as exc:
        click.echo(f"Error during tagging: {exc}", err=True)
        sys.exit(1)
