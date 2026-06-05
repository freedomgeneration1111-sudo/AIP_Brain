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


@corpus.command("wiki")
@click.option("--domain", default=None, help="Generate wiki for one specific domain.")
@click.option("--force", is_flag=True, default=False, help="Regenerate even if wiki exists and is recent.")
@click.option("--all", "all_domains", is_flag=True, default=False, help="Generate for all domains (default if no --domain).")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_wiki_cmd(domain: str | None, force: bool, all_domains: bool, db_path: str | None) -> None:
    """Generate domain wiki articles from tagged corpus turns.

    Beast generates one wiki article per active domain using top-importance
    tagged turns as evidence. Articles go into GENERATED state for DEFINER
    review before becoming canonical.

    Examples:
      uv run aip corpus wiki --domain aip
      uv run aip corpus wiki --domain theology_research --force
      uv run aip corpus wiki --all
    """
    resolved_db_path = db_path or get_default_db_path()

    async def _run_wiki():
        import tomllib
        from pathlib import Path
        from typing import Any

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
            click.echo(f"Warning: beast slot resolver failed ({exc})", err=True)
            resolver = None

        if resolver is None:
            click.echo(
                "No [models.beast] slot with model configured. "
                "Wiki generation requires a beast LLM. Configure and retry.",
                err=True,
            )
            sys.exit(1)

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

        class _DummyVectorStore:
            async def health_check(self) -> dict:
                return {"connected": True}
            async def list_stale_vectors(self, **kwargs: Any) -> list[dict]:
                return []
            async def upsert(self, **kwargs: Any) -> None:
                return None

        class _DummyEmbeddingProvider:
            async def embed(self, text: str) -> list[float]:
                return [0.0] * 8

        bconf_dict = cfg.get("beast", {}) if isinstance(cfg.get("beast"), dict) else {}
        bconf = BeastCadenceConfig(
            **{k: v for k, v in bconf_dict.items() if k in BeastCadenceConfig.__dataclass_fields__}
        )

        beast = Beast(
            config=bconf,
            vector_store=_DummyVectorStore(),
            embedding_provider=_DummyEmbeddingProvider(),
            beast_provider=resolver,
            artifact_store=arts,
            ecs_store=ecs,
            corpus_turn_store=cts,
        )

        try:
            force_domains = [domain] if domain else None
            # CLI --all or no --domain: no cap
            max_per = 9999 if (all_domains or domain is None) else 1

            if force:
                # Pass force_domains even for --all to trigger force logic
                if force_domains is None:
                    # Load registry to get all domain ids
                    try:
                        from aip.orchestration.actors.domain_registry import load_registry
                        reg = load_registry("docs/beast_domain_registry_v1.md")
                        excluded = {"quarantine", "unclassified"}
                        force_domains = [d for d in reg.get_domain_ids() if d not in excluded]
                    except Exception:
                        pass  # will still work; force handled inside _run_wiki_generation

            stats = await beast._run_wiki_generation(
                force_domains=force_domains if force else (force_domains),
                max_per_cycle=max_per,
            )

            gen_n = stats.get("domains_generated", 0)
            skip_n = stats.get("domains_skipped", 0)
            err_n = stats.get("errors", 0)
            click.echo(f"Generated {gen_n} wiki articles. {skip_n} skipped (recent).")
            if err_n:
                click.echo(f"Errors: {err_n}", err=True)
            if stats.get("skipped"):
                click.echo(f"Skipped reason: {stats['skipped']}")
        finally:
            await cts.close()

    try:
        asyncio.run(_run_wiki())
    except SystemExit:
        raise
    except Exception as exc:
        click.echo(f"Error during wiki generation: {exc}", err=True)
        sys.exit(1)


@corpus.command("clear-vectors")
@click.option(
    "--confirm",
    is_flag=True,
    default=False,
    help="Required to confirm deletion of all vectors.",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_clear_vectors_cmd(confirm: bool, db_path: str | None) -> None:
    """Clear all vectors from the vector store (vectors.db).

    This removes stale or old vectors (e.g. from previous chunked ingestion).
    Vectors will be re-embedded by the embedding pipeline.
    Requires --confirm flag (no interactive prompt).
    """
    if not confirm:
        click.echo("Error: --confirm flag is required. This will delete ALL vectors.", err=True)
        click.echo("Usage: uv run aip corpus clear-vectors --confirm", err=True)
        sys.exit(1)

    async def _clear():
        import tomllib
        from pathlib import Path
        import sqlite3
        cfg: dict = {}
        config_p = Path("config/aip.config.toml")
        if config_p.exists():
            with open(config_p, "rb") as f:
                cfg = tomllib.load(f)
        vec_db = cfg.get("vector_backend", {}).get("db_path", "db/vectors.db")
        conn = sqlite3.connect(vec_db)
        try:
            cur = conn.execute("SELECT COUNT(*) FROM vector_metadata")
            before = cur.fetchone()[0] or 0
            conn.execute("DELETE FROM vector_metadata")
            try:
                conn.execute("DELETE FROM vss_vectors")
            except Exception:
                pass  # no vss table or not available
            conn.commit()
            click.echo(f"Cleared {before} vectors.")
        finally:
            conn.close()

        # Also reset embedded flags in main db so status reflects 0 after clear
        try:
            from aip.cli._db_path import get_default_db_path
            main_db = db_path or get_default_db_path()
            connm = sqlite3.connect(main_db)
            try:
                connm.execute("UPDATE corpus_turns SET embedded = 0")
                connm.commit()
            finally:
                connm.close()
        except Exception:
            pass  # best effort

    try:
        asyncio.run(_clear())
    except Exception as exc:
        click.echo(f"Error clearing vectors: {exc}", err=True)
        sys.exit(1)


@corpus.command("embed")
@click.option(
    "--limit",
    default=500,
    type=int,
    help="Max turns to embed this run (default 500).",
)
@click.option(
    "--reembed",
    is_flag=True,
    default=False,
    help="If set, re-embed turns that already have embedded=1.",
)
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_embed_cmd(limit: int, reembed: bool, db_path: str | None) -> None:
    """Run corpus embedding pass directly (batch ~32 turns per embedding call)."""
    if limit < 1:
        limit = 1

    resolved_db_path = db_path or get_default_db_path()

    async def _run_embed():
        import tomllib
        from pathlib import Path

        from aip.adapter.corpus_turn_store import CorpusTurnStore
        from aip.orchestration.actors.beast import Beast

        cfg: dict = {}
        config_p = Path("config/aip.config.toml")
        if config_p.exists():
            with open(config_p, "rb") as f:
                cfg = tomllib.load(f)

        eslot = cfg.get("models", {}).get("embedding", {}) if isinstance(cfg.get("models"), dict) else {}
        if not (isinstance(eslot, dict) and eslot.get("provider") and eslot.get("model")):
            click.echo("No [models.embedding] slot configured (or AIP_EMBEDDING_* env). "
                       "Embedding requires an embedding model. Configure and retry.", err=True)
            sys.exit(1)

        cts = CorpusTurnStore(db_path=resolved_db_path)
        await cts.initialize()

        from aip.adapter.api.app import _create_embedding_provider
        embedding_provider = _create_embedding_provider(cfg)
        if embedding_provider is None:
            click.echo("Failed to create embedding provider.", err=True)
            sys.exit(1)

        class _DummyVectorStore:
            async def health_check(self):
                return {"connected": True}
            async def list_stale_vectors(self, **k):
                return []
            async def upsert(self, **k):
                return None

        bconf_dict = cfg.get("beast", {}) if isinstance(cfg.get("beast"), dict) else {}
        from aip.foundation.schemas import BeastCadenceConfig
        bconf = BeastCadenceConfig(
            **{k: v for k, v in bconf_dict.items() if k in BeastCadenceConfig.__dataclass_fields__}
        )

        beast = Beast(
            config=bconf,
            vector_store=_DummyVectorStore(),
            embedding_provider=embedding_provider,
            corpus_turn_store=cts,
        )

        try:
            total = await cts.total_turns()
            click.echo(f"Loading embedding model... {eslot.get('model', 'unknown')}")
            if reembed:
                unemb = total
            else:
                unemb = await cts.count_unembedded()
            click.echo(f"Getting unembedded turns... {unemb} total (cap {limit})")

            stats = await beast._run_embedding_pass(limit=limit, reembed=reembed)

            emb_n = stats.get("embedded", 0)
            failed_n = stats.get("failed", 0)
            skipped_n = stats.get("skipped", 0)
            click.echo(f"Embedded {emb_n} turns. {failed_n} failed. {skipped_n} skipped.")

            return stats
        finally:
            await cts.close()

    try:
        asyncio.run(_run_embed())
    except Exception as exc:
        click.echo(f"Error during embedding: {exc}", err=True)
        sys.exit(1)


@corpus.command("graph")
@click.option("--build-from-bridges", "build_bridges", is_flag=True, default=False,
              help="Build seed graph from bridge tags in corpus_turns + entity alias registry.")
@click.option("--extract", is_flag=True, default=False,
              help="Run Beast entity extraction on high-importance turns.")
@click.option("--limit", default=50, type=int, help="Max turns to extract (for --extract).")
@click.option("--stats", is_flag=True, default=False, help="Show graph node/edge counts.")
@click.option("--neighbors", "neighbors_node", default=None, help="Show direct neighbors of a domain/node.")
@click.option("--db-path", default=None, help="SQLite database path (default: from config or db/state.db).")
def corpus_graph_cmd(
    build_bridges: bool,
    extract: bool,
    limit: int,
    stats: bool,
    neighbors_node: str | None,
    db_path: str | None,
) -> None:
    """Build and query the knowledge graph.

    Phase 1: bridge tag seed graph (no LLM). Fast.
    Phase 2: Beast entity extraction on high-importance turns (LLM).

    Examples:
      uv run aip corpus graph --build-from-bridges
      uv run aip corpus graph --extract --limit 20
      uv run aip corpus graph --stats
      uv run aip corpus graph --neighbors nbcm
    """
    if not any([build_bridges, extract, stats, neighbors_node is not None]):
        click.echo("Specify an action: --build-from-bridges, --extract, --stats, or --neighbors DOMAIN")
        sys.exit(1)

    resolved_db_path = db_path or get_default_db_path()

    from aip.adapter.graph_store import GraphStore

    if stats:
        try:
            store = GraphStore(resolved_db_path)
            h = store.health_check()
            click.echo("knowledge_graph:")
            click.echo(f"  nodes: {h.get('nodes', 0)}")
            click.echo(f"  edges: {h.get('edges', 0)}")
            # By type
            nodes = store.get_all_nodes()
            edges = store.get_all_edges()
            by_type: dict[str, int] = {}
            by_src: dict[str, int] = {}
            for n in nodes:
                by_type[n.entity_type] = by_type.get(n.entity_type, 0) + 1
                by_src[n.source] = by_src.get(n.source, 0) + 1
            for k, v in sorted(by_type.items()):
                click.echo(f"  {k}: {v}")
            click.echo(f"  by_source: {by_src}")
            bridge_edges = sum(1 for e in edges if e.bridge_tag is not None)
            click.echo(f"  bridge_edges: {bridge_edges}")
        except Exception as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        return

    if neighbors_node is not None:
        try:
            store = GraphStore(resolved_db_path)
            neighbors = store.get_neighbors(neighbors_node, min_confidence=0.0)
            if not neighbors:
                click.echo(f"No neighbors found for '{neighbors_node}' (node may not exist or have no edges).")
            else:
                click.echo(f"Neighbors of '{neighbors_node}':")
                for n in neighbors:
                    click.echo(f"  {n.entity_type:12s} {n.canonical_name} (confidence: {n.confidence:.2f})")
        except Exception as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        return

    if build_bridges:
        _run_build_from_bridges(resolved_db_path)

    if extract:
        _run_graph_extract(resolved_db_path, limit)


def _run_build_from_bridges(db_path: str) -> None:
    """Build seed graph from bridge tags and entity alias registry."""
    import sqlite3
    from aip.adapter.graph_store import GraphStore, GraphNode, GraphEdge
    from aip.adapter.entity_alias_loader import EntityAliasRegistry

    store = GraphStore(db_path)

    # Load entity alias registry
    alias_path = "docs/entity_aliases.md"
    registry = EntityAliasRegistry(alias_path)
    click.echo(f"Loading entity aliases... {len(registry)} entries")

    # Seed nodes from alias registry
    alias_nodes = 0
    for cn in registry.all_canonical_names():
        entry = registry.get_entry(cn)
        if entry is None:
            continue
        node_id = cn.lower().replace(" ", "_")
        node = GraphNode(
            id=node_id,
            entity_type=entry.entity_type,
            canonical_name=cn,
            domain=entry.domain or None,
            confidence=1.0,
            source="manual",
            aliases=entry.aliases,
        )
        store.upsert_node(node)
        alias_nodes += 1

    click.echo(f"Processing bridge tags from corpus...")

    # Read all bridge-tagged turns
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT turn_id, bridges FROM corpus_turns WHERE bridges != '[]'"
        ).fetchall()
    finally:
        conn.close()

    click.echo(f"Found {len(rows)} turns with bridge tags")

    # Collect bridge evidence: bridge_tag -> list of turn_ids
    bridge_evidence: dict[str, list[str]] = {}
    for turn_id, bridges_json in rows:
        try:
            bridges = json.loads(bridges_json or "[]")
        except Exception:
            continue
        for tag in bridges:
            tag = tag.strip()
            if "->" not in tag:
                continue
            if tag not in bridge_evidence:
                bridge_evidence[tag] = []
            bridge_evidence[tag].append(turn_id)

    # Build domain nodes and edges from bridge tags
    domain_nodes_created = 0
    bridge_edges_created = 0

    for bridge_tag, turn_ids in bridge_evidence.items():
        parts = bridge_tag.split("->", 1)
        if len(parts) != 2:
            continue
        domain_a = parts[0].strip()
        domain_b = parts[1].strip()

        for dom in (domain_a, domain_b):
            # Create domain node if it doesn't exist
            existing = store.get_node(dom)
            if existing is None:
                node = GraphNode(
                    id=dom,
                    entity_type="DOMAIN",
                    canonical_name=dom,
                    domain=dom,
                    confidence=1.0,
                    source="bridge",
                )
                store.upsert_node(node)
                domain_nodes_created += 1

        # Create or update the bridge edge (weight = number of turns)
        edge_id = f"{domain_a}__CONNECTS__{domain_b}"
        existing_edge = None
        try:
            # Check if edge exists to accumulate evidence
            all_edges = store.get_all_edges()
            for e in all_edges:
                if e.id == edge_id:
                    existing_edge = e
                    break
        except Exception:
            pass

        all_evidence = list({*turn_ids, *(existing_edge.evidence_turn_ids if existing_edge else [])})
        edge = GraphEdge(
            id=edge_id,
            source_id=domain_a,
            target_id=domain_b,
            relationship_type="CONNECTS",
            bridge_tag=bridge_tag,
            confidence=1.0,
            evidence_turn_ids=all_evidence,
            weight=float(len(all_evidence)),
        )
        store.upsert_edge(edge)
        bridge_edges_created += 1

    total_nodes = store.node_count()
    total_edges = store.edge_count()

    click.echo(f"Created {domain_nodes_created} domain nodes from bridge tags")
    click.echo(f"Created/updated {bridge_edges_created} bridge edges")
    click.echo(f"Entity alias nodes: {alias_nodes} nodes seeded from entity_aliases.md")
    click.echo(f"Built seed graph: {total_nodes} nodes, {total_edges} edges")

    if any("aip_methodology" in tag for tag in bridge_evidence.keys()):
        click.echo("")
        click.echo("Note: aip_methodology bridge nodes are pre-rename artifacts.")
        click.echo("  Run 'aip corpus graph --merge-nodes aip_methodology aip'")
        click.echo("  after full corpus retag to consolidate.")
        click.echo("  (--merge-nodes is planned future work — see TECH_DEBT.md)")


def _run_graph_extract(db_path: str, limit: int) -> None:
    """Run Beast entity extraction on high-importance turns via CLI."""
    import asyncio as _asyncio
    import tomllib
    from pathlib import Path

    async def _extract_async():
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
            click.echo(f"Warning: beast slot resolver failed ({exc})", err=True)
            resolver = None

        if resolver is None:
            click.echo("No [models.beast] slot configured. Entity extraction requires a beast LLM.", err=True)
            sys.exit(1)

        cts = CorpusTurnStore(db_path=db_path)
        await cts.initialize()

        arts = None
        if VersionedArtifactStore is not None:
            try:
                arts = VersionedArtifactStore(db_path=db_path)
                await arts.initialize()
            except Exception:
                arts = None

        ecs = None
        if PersistentEcsStore is not None:
            try:
                ecs = PersistentEcsStore(db_path=db_path, event_store=None)
                await ecs.initialize()
            except Exception:
                ecs = None

        class _DummyVectorStore:
            async def health_check(self) -> dict:
                return {"connected": True}
            async def list_stale_vectors(self, **kw: Any) -> list[dict]:
                return []
            async def upsert(self, **kw: Any) -> None:
                return None

        class _DummyEmbeddingProvider:
            async def embed(self, text: str) -> list[float]:
                return [0.0] * 8

        bconf_dict = cfg.get("beast", {}) if isinstance(cfg.get("beast"), dict) else {}
        bconf = BeastCadenceConfig(
            **{k: v for k, v in bconf_dict.items() if k in BeastCadenceConfig.__dataclass_fields__}
        )

        beast = Beast(
            config=bconf,
            vector_store=_DummyVectorStore(),
            embedding_provider=_DummyEmbeddingProvider(),
            beast_provider=resolver,
            artifact_store=arts,
            ecs_store=ecs,
            corpus_turn_store=cts,
        )

        try:
            stats = await beast._run_graph_extraction(limit=limit)
            turns_n = stats.get("turns_processed", 0)
            entities_n = stats.get("entities_created", 0)
            rels_n = stats.get("relationships_created", 0)
            click.echo(f"Processed {turns_n} turns. Created {entities_n} entities, {rels_n} relationships.")
            if stats.get("skipped"):
                click.echo(f"Skipped: {stats['skipped']}")
        finally:
            await cts.close()

    try:
        _asyncio.run(_extract_async())
    except SystemExit:
        raise
    except Exception as exc:
        click.echo(f"Error during graph extraction: {exc}", err=True)
        sys.exit(1)
