#!/usr/bin/env python3
"""Build the AIP_Brain demo database from curated source files.

Creates a self-contained demo DB under demo/aip_demo/db/ with:
  - 60 Q&A turns (from source/aip_qa_turns.jsonl)
  - 24 wiki articles (from source/wiki_articles.yaml)
  - FTS5 search index
  - Tags, entities, domains
  - Knowledge graph seed nodes
  - CODEX sources and topics
  - Vector embeddings (if embedding provider configured, otherwise marked pending)

Usage:
    python scripts/demo/build_aip_demo_db.py [--reset] [--db-path PATH]

The --reset flag deletes existing demo DB files before rebuilding.
The --db-path flag overrides the default demo DB location.

This script uses the same schema as the real AIP_Brain stores (CorpusTurnStore,
CodexStore, GraphStore, etc.) to ensure compatibility.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path so we can import aip modules
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

DEMO_ROOT = PROJECT_ROOT / "demo" / "aip_demo"
SOURCE_DIR = DEMO_ROOT / "source"
DEFAULT_DB_DIR = DEMO_ROOT / "db"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build AIP_Brain demo database")
    parser.add_argument("--reset", action="store_true", help="Delete existing demo DB files before rebuilding")
    parser.add_argument("--db-path", default=None, help="Override demo DB directory path")
    args = parser.parse_args()

    db_dir = Path(args.db_path) if args.db_path else DEFAULT_DB_DIR

    print("=" * 60)
    print("AIP_Brain Demo DB Builder")
    print("=" * 60)
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Demo root:    {DEMO_ROOT}")
    print(f"DB directory: {db_dir}")

    # 1. Reset if requested
    if args.reset and db_dir.exists():
        print(f"\nResetting: removing {db_dir}")
        shutil.rmtree(db_dir)

    db_dir.mkdir(parents=True, exist_ok=True)

    # 2. Initialize databases with correct schemas
    state_db_path = db_dir / "state.db"
    lexical_db_path = db_dir / "lexical.db"
    vectors_db_path = db_dir / "vectors.db"
    trace_db_path = db_dir / "trace.db"

    print("\nInitializing database schemas...")

    # state.db — all core tables
    _init_state_db(state_db_path)
    print(f"  state.db: schema initialized")

    # lexical.db — FTS5 index
    _init_lexical_db(lexical_db_path)
    print(f"  lexical.db: schema initialized")

    # vectors.db — vector metadata + VSS
    _init_vectors_db(vectors_db_path)
    print(f"  vectors.db: schema initialized")

    # trace.db — trace events
    _init_trace_db(trace_db_path)
    print(f"  trace.db: schema initialized")

    # Other DBs — touch them
    for name in ["vigil_quality.db", "alert_history.db", "ace_playbook.db"]:
        (db_dir / name).touch(exist_ok=True)
    print(f"  Other DBs: touched (vigil_quality, alert_history, ace_playbook)")

    # 3. Create default project
    _create_default_project(state_db_path)
    print("  Default project created")

    # 4. Ingest Q&A turns
    qa_path = SOURCE_DIR / "aip_qa_turns.jsonl"
    if not qa_path.exists():
        print(f"\nERROR: Q&A turns file not found: {qa_path}", file=sys.stderr)
        sys.exit(1)

    turns_ingested = _ingest_qa_turns(state_db_path, qa_path)
    print(f"\n  Q&A turns ingested: {turns_ingested}")

    # 5. Create wiki articles as codex topics + compiled knowledge
    wiki_path = SOURCE_DIR / "wiki_articles.yaml"
    if not wiki_path.exists():
        print(f"\nERROR: Wiki articles file not found: {wiki_path}", file=sys.stderr)
        sys.exit(1)

    wiki_count = _ingest_wiki_articles(state_db_path, wiki_path)
    print(f"  Wiki articles created: {wiki_count}")

    # 6. Create CODEX sources from Q&A turns
    source_count = _create_codex_sources(state_db_path)
    print(f"  CODEX sources registered: {source_count}")

    # 7. Create knowledge graph seed nodes
    graph_count = _create_graph_seed(state_db_path)
    print(f"  Graph nodes seeded: {graph_count}")

    # 8. Index into lexical DB for FTS5 search
    lexical_count = _index_lexical(lexical_db_path, state_db_path)
    print(f"  Lexical index entries: {lexical_count}")

    # 9. Try embeddings
    embeddings_status = _try_embeddings(state_db_path, vectors_db_path)
    print(f"  Embeddings: {embeddings_status}")

    # 10. Summary
    print("\n" + "=" * 60)
    print("Demo DB build complete!")
    print("=" * 60)
    _print_summary(state_db_path, vectors_db_path)

    print(f"\nDatabase location: {db_dir}/")
    print(f"To verify: python scripts/demo/verify_aip_demo_db.py")
    print(f"To use:    export AIP_DB_PATH={state_db_path}")


# ---------------------------------------------------------------------------
# Schema initialization (matches aip/cli/init.py exactly)
# ---------------------------------------------------------------------------


def _init_state_db(db_path: Path) -> None:
    """Initialize state.db with all required tables."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        -- VersionedArtifactStore: artifacts table
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT NOT NULL,
            version INTEGER NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_artifacts_id ON artifacts(id);

        -- SqliteProjectStore: projects table
        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            domain TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- QueryableEventStore: events table
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_artifact ON events(artifact_id);

        -- PersistentEcsStore: ECS state + transitions
        CREATE TABLE IF NOT EXISTS ecs_state (
            artifact_id TEXT PRIMARY KEY,
            current_state TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ecs_transitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_id TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            superseded_by TEXT,
            timestamp TEXT NOT NULL,
            metadata TEXT DEFAULT '{}'
        );

        -- SqliteCanonicalStore
        CREATE TABLE IF NOT EXISTS canonical_artifacts (
            artifact_id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            approved_by TEXT NOT NULL,
            domain TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            superseded_by TEXT
        );

        -- Entity table
        CREATE TABLE IF NOT EXISTS entities (
            entity_id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            name TEXT NOT NULL,
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- Model library
        CREATE TABLE IF NOT EXISTS enabled_models (
            model_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'openrouter',
            cost_input_per_million REAL,
            cost_output_per_million REAL,
            context_length INTEGER,
            supports_vision INTEGER DEFAULT 0,
            supports_tools INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 0,
            is_custom INTEGER DEFAULT 0,
            custom_base_url TEXT,
            custom_api_key TEXT,
            last_fetched TEXT
        );

        -- CorpusTurnStore: corpus_turns + FTS5
        CREATE TABLE IF NOT EXISTS corpus_turns (
            turn_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            conversation_name TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            source_model TEXT NOT NULL,
            source_account TEXT NOT NULL,
            export_date TEXT NOT NULL,
            content_hash TEXT NOT NULL DEFAULT '',
            source_path TEXT NOT NULL DEFAULT '',
            doc_version INTEGER NOT NULL DEFAULT 0,
            user_text TEXT NOT NULL,
            assistant_text TEXT NOT NULL,
            turn_timestamp TEXT NOT NULL,
            thinking_text TEXT NOT NULL DEFAULT '',
            domains TEXT NOT NULL DEFAULT '[]',
            primary_domain TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            importance REAL NOT NULL DEFAULT 0.0,
            bridges TEXT NOT NULL DEFAULT '[]',
            beast_confidence REAL NOT NULL DEFAULT 0.0,
            tagging_version INTEGER NOT NULL DEFAULT 0,
            searchable_text TEXT NOT NULL,
            word_count INTEGER NOT NULL DEFAULT 0,
            embedded INTEGER NOT NULL DEFAULT 0,
            embedding_model TEXT DEFAULT '',
            needs_reembed INTEGER NOT NULL DEFAULT 0,
            last_embed_at TEXT DEFAULT NULL,
            metadata_json TEXT DEFAULT '{}',
            embed_fail_count INTEGER NOT NULL DEFAULT 0,
            last_embed_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_turns_conversation ON corpus_turns(conversation_id);
        CREATE INDEX IF NOT EXISTS idx_turns_primary_domain ON corpus_turns(primary_domain);
        CREATE INDEX IF NOT EXISTS idx_turns_tagging_version ON corpus_turns(tagging_version);
        CREATE INDEX IF NOT EXISTS idx_turns_importance ON corpus_turns(importance DESC);
        CREATE INDEX IF NOT EXISTS idx_turns_embedded ON corpus_turns(embedded);

        -- FTS5 virtual table for corpus turns
        CREATE VIRTUAL TABLE IF NOT EXISTS corpus_turns_fts USING fts5(
            turn_id UNINDEXED,
            conversation_name,
            searchable_text,
            primary_domain UNINDEXED,
            content='corpus_turns',
            content_rowid='rowid'
        );

        -- FTS5 triggers
        CREATE TRIGGER IF NOT EXISTS corpus_turns_ai
        AFTER INSERT ON corpus_turns BEGIN
            INSERT INTO corpus_turns_fts(
                rowid, turn_id, conversation_name, searchable_text, primary_domain
            ) VALUES (new.rowid, new.turn_id, new.conversation_name,
                      new.searchable_text, new.primary_domain);
        END;

        CREATE TRIGGER IF NOT EXISTS corpus_turns_ad
        AFTER DELETE ON corpus_turns BEGIN
            INSERT INTO corpus_turns_fts(
                corpus_turns_fts, rowid, turn_id, conversation_name,
                searchable_text, primary_domain
            ) VALUES ('delete', old.rowid, old.turn_id, old.conversation_name,
                      old.searchable_text, old.primary_domain);
        END;

        -- CODEX tables
        CREATE TABLE IF NOT EXISTS codex_sources (
            source_id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT 'document',
            source_path TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            topics_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active',
            content_hash TEXT NOT NULL DEFAULT '',
            word_count INTEGER NOT NULL DEFAULT 0,
            turn_count INTEGER NOT NULL DEFAULT 0,
            first_ingested_at TEXT NOT NULL DEFAULT '',
            last_updated_at TEXT NOT NULL DEFAULT '',
            last_reviewed_at TEXT NOT NULL DEFAULT '',
            superseded_by TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS codex_topics (
            topic_id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            source_ids_json TEXT NOT NULL DEFAULT '[]',
            related_topics_json TEXT NOT NULL DEFAULT '[]',
            contradiction_count INTEGER NOT NULL DEFAULT 0,
            staleness_score REAL NOT NULL DEFAULT 0.0,
            last_activity_at TEXT NOT NULL DEFAULT '',
            is_wiki_page INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS codex_contradictions (
            contradiction_id TEXT PRIMARY KEY,
            topic_id TEXT NOT NULL DEFAULT '',
            claim_a TEXT NOT NULL DEFAULT '',
            source_a_id TEXT NOT NULL DEFAULT '',
            source_a_title TEXT NOT NULL DEFAULT '',
            claim_b TEXT NOT NULL DEFAULT '',
            source_b_id TEXT NOT NULL DEFAULT '',
            source_b_title TEXT NOT NULL DEFAULT '',
            severity TEXT NOT NULL DEFAULT 'major',
            status TEXT NOT NULL DEFAULT 'open',
            context TEXT NOT NULL DEFAULT '',
            resolution_notes TEXT NOT NULL DEFAULT '',
            resolved_by TEXT NOT NULL DEFAULT '',
            resolved_at TEXT NOT NULL DEFAULT '',
            detected_at TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        -- Graph tables
        CREATE TABLE IF NOT EXISTS graph_nodes (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            domain TEXT,
            confidence REAL DEFAULT 1.0,
            source TEXT DEFAULT 'manual',
            aliases_json TEXT DEFAULT '[]',
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS graph_edges (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            bridge_tag TEXT,
            confidence REAL DEFAULT 1.0,
            evidence_turn_ids_json TEXT DEFAULT '[]',
            weight REAL DEFAULT 1.0,
            created_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_id);
        CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_id);
        CREATE INDEX IF NOT EXISTS idx_graph_nodes_domain ON graph_nodes(domain);

        -- Compiled knowledge
        CREATE TABLE IF NOT EXISTS compiled_knowledge (
            knowledge_id TEXT PRIMARY KEY,
            content TEXT,
            source_canonical_ids TEXT,
            domain TEXT,
            state TEXT,
            metadata TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS compiled_knowledge_provenance (
            knowledge_id TEXT,
            canonical_id TEXT,
            canonical_domain TEXT,
            canonical_title TEXT,
            canonical_evaluation_scores TEXT,
            canonical_state TEXT,
            PRIMARY KEY (knowledge_id, canonical_id)
        );
    """)
    conn.commit()
    conn.close()


def _init_lexical_db(db_path: Path) -> None:
    """Initialize lexical.db with FTS5 schema."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fts_documents (
            doc_id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            domain TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_index
            USING fts5(content, domain, metadata, tokenize=unicode61);
    """)
    conn.commit()
    conn.close()


def _init_vectors_db(db_path: Path) -> None:
    """Initialize vectors.db with vector metadata table."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vector_metadata (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()


def _init_trace_db(db_path: Path) -> None:
    """Initialize trace.db."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trace_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            node_type TEXT,
            model_slot TEXT,
            model_name TEXT,
            token_count_in INTEGER DEFAULT 0,
            token_count_out INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            latency_ms REAL DEFAULT 0.0,
            failure_type TEXT,
            detail TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()


def _create_default_project(db_path: Path) -> None:
    """Create the default demo project."""
    conn = sqlite3.connect(str(db_path))
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO projects (project_id, name, status, domain, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("demo-aip", "AIP Demo", "active", "aip", now, now),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Data ingestion
# ---------------------------------------------------------------------------


def _ingest_qa_turns(db_path: Path, qa_path: Path) -> int:
    """Ingest Q&A turns from JSONL into corpus_turns table."""
    conn = sqlite3.connect(str(db_path))
    now = datetime.now(timezone.utc).isoformat() + "Z"
    count = 0

    with open(qa_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                turn = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  WARNING: skipping invalid JSON line: {e}", file=sys.stderr)
                continue

            turn_id = turn["turn_id"]
            user_text = turn.get("user_text", "")
            assistant_text = turn.get("assistant_text", "")
            searchable_text = f"{user_text} {assistant_text}"
            word_count = len(searchable_text.split())
            content_hash = hashlib.sha256(searchable_text.encode()).hexdigest()[:16]

            conn.execute(
                """
                INSERT OR REPLACE INTO corpus_turns (
                    turn_id, conversation_id, conversation_name, turn_index,
                    source_model, source_account, export_date,
                    content_hash, source_path, doc_version,
                    user_text, assistant_text, turn_timestamp, thinking_text,
                    domains, primary_domain, tags, importance, bridges,
                    beast_confidence, tagging_version,
                    searchable_text, word_count,
                    embedded, metadata_json, embedding_model, needs_reembed, last_embed_at,
                    embed_fail_count, last_embed_error,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    turn.get("conversation_id", "demo"),
                    turn.get("conversation_name", "AIP Demo Q&A"),
                    int(turn.get("turn_index", count)),
                    turn.get("source_model", "aip_demo"),
                    turn.get("source_account", "demo_corpus"),
                    turn.get("export_date", "2026-06-11"),
                    content_hash,
                    "demo/aip_demo/source/aip_qa_turns.jsonl",
                    1,
                    user_text,
                    assistant_text,
                    now,
                    turn.get("thinking_text", ""),
                    json.dumps(turn.get("domains", ["aip"])),
                    turn.get("primary_domain", "aip"),
                    json.dumps(turn.get("tags", [])),
                    float(turn.get("importance", 0.5)),
                    json.dumps(turn.get("bridges", [])),
                    float(turn.get("beast_confidence", 0.9)),
                    int(turn.get("tagging_version", 1)),
                    searchable_text,
                    word_count,
                    0,  # embedded=0 (pending)
                    json.dumps({"demo": True, "wiki_topic": turn.get("wiki_topic", "")}),
                    "",  # embedding_model
                    0,  # needs_reembed
                    None,  # last_embed_at
                    0,  # embed_fail_count
                    "",  # last_embed_error
                    now,
                    now,
                ),
            )
            count += 1

    conn.commit()
    conn.close()
    return count


def _ingest_wiki_articles(db_path: Path, wiki_path: Path) -> int:
    """Ingest wiki articles from YAML into codex_topics and compiled_knowledge."""
    try:
        import yaml
    except ImportError:
        print("  WARNING: PyYAML not installed, attempting to parse manually...", file=sys.stderr)
        return _ingest_wiki_articles_manual(db_path, wiki_path)

    conn = sqlite3.connect(str(db_path))
    now = datetime.now(timezone.utc).isoformat()

    with open(wiki_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    articles = data.get("articles", [])
    count = 0

    for article in articles:
        article_id = article["article_id"]
        title = article.get("title", article_id)
        domain = article.get("domain", "aip")
        description = article.get("description", "")
        content = article.get("content", "")
        tags = article.get("tags", [])
        entities = article.get("entities", [])
        related_topics = article.get("related_topics", [])
        importance = float(article.get("importance", 0.8))

        # Insert into codex_topics as wiki page
        conn.execute(
            """
            INSERT OR REPLACE INTO codex_topics (
                topic_id, title, domain, description, source_ids_json, related_topics_json,
                contradiction_count, staleness_score, last_activity_at, is_wiki_page,
                metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article_id,
                title,
                domain,
                description,
                json.dumps([f"demo-qa-{i:03d}" for i in range(1, 61)]),  # Link to all Q&A turns
                json.dumps(related_topics),
                0,
                0.0,  # Fresh
                now,
                1,  # is_wiki_page
                json.dumps(
                    {
                        "tags": tags,
                        "entities": entities,
                        "importance": importance,
                        "source_type": "curated",
                        "demo": True,
                    }
                ),
                now,
            ),
        )

        # Insert into compiled_knowledge as APPROVED
        conn.execute(
            """
            INSERT OR REPLACE INTO compiled_knowledge (
                knowledge_id, content, source_canonical_ids, domain, state, metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article_id,
                content,
                json.dumps([f"demo-qa-{i:03d}" for i in range(1, 61)]),
                domain,
                "APPROVED",
                json.dumps(
                    {
                        "title": title,
                        "description": description,
                        "tags": tags,
                        "entities": entities,
                        "importance": importance,
                        "source_type": "curated",
                        "demo": True,
                    }
                ),
                now,
                now,
            ),
        )

        count += 1

    conn.commit()
    conn.close()
    return count


def _ingest_wiki_articles_manual(db_path: Path, wiki_path: Path) -> int:
    """Fallback: parse wiki YAML without PyYAML (basic parser for our known structure)."""
    import re

    conn = sqlite3.connect(str(db_path))
    now = datetime.now(timezone.utc).isoformat()
    content = wiki_path.read_text(encoding="utf-8")

    # Split on article_id pattern
    article_blocks = re.split(r"\n\s*-\s+article_id:", content)
    count = 0

    for block in article_blocks[1:]:  # Skip the header before first article
        lines = block.strip().split("\n")
        article_id = lines[0].strip().strip('"').strip("'")

        # Extract simple key: value pairs
        data: dict = {"article_id": article_id}
        for line in lines[1:]:
            m = re.match(r'\s+(\w+):\s*["\']?(.+?)["\']?\s*$', line)
            if m:
                data[m.group(1)] = m.group(2)

        title = data.get("title", article_id)
        domain = data.get("domain", "aip")
        description = data.get("description", "")

        conn.execute(
            """
            INSERT OR REPLACE INTO codex_topics (
                topic_id, title, domain, description, source_ids_json, related_topics_json,
                contradiction_count, staleness_score, last_activity_at, is_wiki_page,
                metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (article_id, title, domain, description, "[]", "[]", 0, 0.0, now, 1, "{}", now),
        )
        count += 1

    conn.commit()
    conn.close()
    return count


def _create_codex_sources(db_path: Path) -> int:
    """Create CODEX source entries from the Q&A conversations."""
    conn = sqlite3.connect(str(db_path))
    now = datetime.now(timezone.utc).isoformat()

    # Create one source per conversation_id
    conn.execute(
        """
        SELECT DISTINCT conversation_id, conversation_name, primary_domain, COUNT(*) as turn_count
        FROM corpus_turns GROUP BY conversation_id
        """
    )
    rows = conn.execute(
        """
        SELECT DISTINCT conversation_id, conversation_name, primary_domain, COUNT(*) as turn_count
        FROM corpus_turns GROUP BY conversation_id
        """
    ).fetchall()

    count = 0
    for row in rows:
        conv_id = row[0]
        conv_name = row[1]
        domain = row[2]
        turn_count = row[3]

        source_id = f"src-{conv_id}"
        conn.execute(
            """
            INSERT OR REPLACE INTO codex_sources (
                source_id, title, source_type, source_path, domain, topics_json,
                status, content_hash, word_count, turn_count,
                first_ingested_at, last_updated_at, last_reviewed_at,
                superseded_by, metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                conv_name,
                "conversation",
                "demo/aip_demo/source/aip_qa_turns.jsonl",
                domain,
                json.dumps([domain]),
                "active",
                "",
                0,
                turn_count,
                now,
                now,
                "",
                "",
                json.dumps({"demo": True}),
                now,
            ),
        )
        count += 1

    # Also create a source for the wiki articles file
    conn.execute(
        """
        INSERT OR REPLACE INTO codex_sources (
            source_id, title, source_type, source_path, domain, topics_json,
            status, content_hash, word_count, turn_count,
            first_ingested_at, last_updated_at, last_reviewed_at,
            superseded_by, metadata_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "src-wiki-articles",
            "AIP Wiki Articles (Curated)",
            "document",
            "demo/aip_demo/source/wiki_articles.yaml",
            "aip",
            json.dumps(["aip"]),
            "active",
            "",
            0,
            0,
            now,
            now,
            "",
            "",
            json.dumps({"demo": True, "wiki": True}),
            now,
        ),
    )
    count += 1

    conn.commit()
    conn.close()
    return count


def _create_graph_seed(db_path: Path) -> int:
    """Create seed graph nodes for key AIP entities."""
    conn = sqlite3.connect(str(db_path))
    now = datetime.now(timezone.utc).isoformat()

    nodes = [
        (
            "aip",
            "PROJECT",
            "AIP",
            "aip",
            ["AI Poiesis", "AIP_Brain", "AIP Brain", "the system", "the knowledge engine"],
        ),
        (
            "definer",
            "CONCEPT",
            "DEFINER",
            "aip",
            ["the DEFINER", "definer gate", "human-in-the-loop authority", "the sovereign"],
        ),
        ("beast", "PROJECT", "Beast", "aip", ["Beast actor", "the Beast", "beast agent", "the maintenance actor"]),
        ("vigil", "PROJECT", "Vigil", "aip", ["Vigil actor", "the Vigil", "vigil agent", "the quality monitor"]),
        ("sexton", "PROJECT", "Sexton", "aip", ["Sexton orchestrator", "sexton agent", "the orchestrator"]),
        ("codex", "PROJECT", "CODEX", "aip", ["CODEX", "the librarian", "corpus map"]),
        ("ecs_lifecycle", "CONCEPT", "ECS lifecycle", "aip", ["ECS", "artifact lifecycle", "state machine"]),
        ("retrieval_pipeline", "CONCEPT", "Retrieval pipeline", "aip", ["hybrid retrieval", "RRF", "FTS5+vector"]),
        ("model_slots", "CONCEPT", "Model slots", "aip", ["ModelSlotResolver", "slot resolution", "model dispatch"]),
        ("storage_model", "CONCEPT", "Storage model", "aip", ["SQLite", "multi-store", "local-first"]),
        ("dogfood_mode", "CONCEPT", "Dogfood mode", "aip", ["full dogfood", "diagnostic mode", "minimal mode"]),
        (
            "no_silent_degradation",
            "CONCEPT",
            "No silent degradation",
            "aip",
            ["honest evaluation", "fail loudly", "no silent pass"],
        ),
    ]

    edges = [
        ("aip__CONTAINS__beast", "aip", "beast", "CONTAINS"),
        ("aip__CONTAINS__vigil", "aip", "vigil", "CONTAINS"),
        ("aip__CONTAINS__sexton", "aip", "sexton", "CONTAINS"),
        ("aip__CONTAINS__codex", "aip", "codex", "CONTAINS"),
        ("aip__ENFORCES__definer", "aip", "definer", "ENFORCES"),
        ("beast__MAINTAINS__ecs_lifecycle", "beast", "ecs_lifecycle", "MAINTAINS"),
        ("beast__MAINTAINS__retrieval_pipeline", "beast", "retrieval_pipeline", "MAINTAINS"),
        ("vigil__MONITORS__model_slots", "vigil", "model_slots", "MONITORS"),
        ("sexton__HANDLES__no_silent_degradation", "sexton", "no_silent_degradation", "HANDLES"),
    ]

    for node_id, entity_type, canonical_name, domain, aliases in nodes:
        conn.execute(
            """
            INSERT OR IGNORE INTO graph_nodes
            (id, entity_type, canonical_name, domain, confidence, source, aliases_json, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node_id,
                entity_type,
                canonical_name,
                domain,
                1.0,
                "demo_seed",
                json.dumps(aliases),
                json.dumps({"demo": True}),
                now,
                now,
            ),
        )

    for edge_id, source_id, target_id, rel_type in edges:
        conn.execute(
            """
            INSERT OR IGNORE INTO graph_edges
            (id, source_id, target_id, relationship_type, bridge_tag, confidence, evidence_turn_ids_json, weight, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (edge_id, source_id, target_id, rel_type, f"{source_id}->{target_id}", 1.0, json.dumps([]), 1.0, now),
        )

    conn.commit()
    node_count = len(nodes)
    conn.close()
    return node_count


def _index_lexical(lexical_db_path: Path, state_db_path: Path) -> int:
    """Index corpus turns and wiki articles into the lexical FTS5 store."""
    # Read turns from state.db
    state_conn = sqlite3.connect(str(state_db_path))
    turns = state_conn.execute("SELECT turn_id, searchable_text, primary_domain FROM corpus_turns").fetchall()
    wiki = state_conn.execute(
        "SELECT topic_id, description, domain FROM codex_topics WHERE is_wiki_page = 1"
    ).fetchall()
    state_conn.close()

    # Insert into lexical.db
    lex_conn = sqlite3.connect(str(lexical_db_path))
    now = datetime.now(timezone.utc).isoformat()
    count = 0

    for turn_id, text, domain in turns:
        lex_conn.execute(
            "INSERT OR REPLACE INTO fts_documents (doc_id, content, domain, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            (f"turn:{turn_id}", text, domain, json.dumps({"type": "corpus_turn", "turn_id": turn_id}), now),
        )
        count += 1

    for topic_id, description, domain in wiki:
        lex_conn.execute(
            "INSERT OR REPLACE INTO fts_documents (doc_id, content, domain, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
            (f"wiki:{topic_id}", description, domain, json.dumps({"type": "wiki", "topic_id": topic_id}), now),
        )
        count += 1

    lex_conn.commit()
    lex_conn.close()
    return count


def _try_embeddings(state_db_path: Path, vectors_db_path: Path) -> str:
    """Attempt to generate embeddings. Returns status string."""
    try:
        import tomllib

        config_path = PROJECT_ROOT / "config" / "aip.config.toml"
        cfg: dict = {}
        if config_path.exists():
            with open(config_path, "rb") as f:
                cfg = tomllib.load(f)

        eslot = cfg.get("models", {}).get("embedding", {}) if isinstance(cfg.get("models"), dict) else {}
        if not (isinstance(eslot, dict) and eslot.get("provider") and eslot.get("model")):
            return "PENDING — no [models.embedding] configured. Run 'aip corpus embed' locally after configuring an embedding provider."

        # Try to create and use the embedding provider
        from aip.adapter.api.app import _create_embedding_provider

        provider = _create_embedding_provider(cfg)
        if provider is None:
            return "PENDING — embedding provider creation failed. Check configuration."

        # Generate embeddings for all turns
        state_conn = sqlite3.connect(str(state_db_path))
        state_conn.row_factory = sqlite3.Row
        turns = state_conn.execute(
            "SELECT turn_id, searchable_text FROM corpus_turns WHERE embedded = 0 LIMIT 100"
        ).fetchall()
        state_conn.close()

        if not turns:
            return "All turns already embedded."

        import asyncio

        async def _embed():
            vec_conn = sqlite3.connect(str(vectors_db_path))
            now = datetime.now(timezone.utc).isoformat()
            embedded_count = 0

            for turn in turns:
                try:
                    embedding = await provider.embed(turn["searchable_text"][:2000])
                    if embedding and len(embedding) > 0:
                        # Store in vector_metadata (without actual VSS index, just metadata)
                        vec_conn.execute(
                            "INSERT OR REPLACE INTO vector_metadata (id, content, domain, metadata_json, created_at) VALUES (?, ?, ?, ?, ?)",
                            (
                                turn["turn_id"],
                                turn["searchable_text"][:500],
                                "aip",
                                json.dumps({"dim": len(embedding), "demo": True}),
                                now,
                            ),
                        )
                        embedded_count += 1
                except Exception:
                    pass

            vec_conn.commit()
            vec_conn.close()

            # Mark turns as embedded
            state_conn2 = sqlite3.connect(str(state_db_path))
            turn_ids = [t["turn_id"] for t in turns[:embedded_count]]
            for tid in turn_ids:
                state_conn2.execute(
                    "UPDATE corpus_turns SET embedded = 1, embedding_model = ?, last_embed_at = ?, updated_at = ? WHERE turn_id = ?",
                    (eslot.get("model", "unknown"), now, now, tid),
                )
            state_conn2.commit()
            state_conn2.close()

            return embedded_count

        count = asyncio.run(_embed())
        return f"OK — {count} turns embedded with {eslot.get('model', 'unknown')}"

    except Exception as e:
        return f"PENDING — embedding skipped ({e}). Run 'aip corpus embed' locally."


def _print_summary(state_db_path: Path, vectors_db_path: Path) -> None:
    """Print a summary of the demo DB contents."""
    conn = sqlite3.connect(str(state_db_path))

    turns = conn.execute("SELECT COUNT(*) FROM corpus_turns").fetchone()[0]
    tagged = conn.execute("SELECT COUNT(*) FROM corpus_turns WHERE tagging_version > 0").fetchone()[0]
    embedded = conn.execute("SELECT COUNT(*) FROM corpus_turns WHERE embedded = 1").fetchone()[0]
    wiki = conn.execute("SELECT COUNT(*) FROM codex_topics WHERE is_wiki_page = 1").fetchone()[0]
    sources = conn.execute("SELECT COUNT(*) FROM codex_sources").fetchone()[0]
    graph_nodes = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
    graph_edges = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
    compiled = conn.execute("SELECT COUNT(*) FROM compiled_knowledge").fetchone()[0]
    projects = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]

    conn.close()

    print(f"  Turns:          {turns} ({tagged} tagged, {embedded} embedded)")
    print(f"  Wiki articles:  {wiki}")
    print(f"  CODEX sources:  {sources}")
    print(f"  Graph nodes:    {graph_nodes}")
    print(f"  Graph edges:    {graph_edges}")
    print(f"  Compiled knowledge: {compiled}")
    print(f"  Projects:       {projects}")

    # File sizes
    for db_file in sorted(state_db_path.parent.glob("*.db")):
        size_kb = db_file.stat().st_size / 1024
        print(f"  {db_file.name}: {size_kb:.1f} KB")


if __name__ == "__main__":
    main()
