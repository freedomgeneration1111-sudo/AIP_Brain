#!/usr/bin/env python3
"""Verify the AIP_Brain demo database integrity.

Checks:
  - DB files exist and are non-empty
  - Minimum 50+ Q&A turns present
  - Minimum 20+ wiki articles present
  - Tags present on turns
  - Entities/graph nodes present
  - Embeddings present or explicitly pending
  - No private absolute paths in the data
  - FTS5 search works (smoke test queries)

Usage:
    python scripts/demo/verify_aip_demo_db.py [--db-path PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEMO_ROOT = PROJECT_ROOT / "demo" / "aip_demo"
DEFAULT_DB_DIR = DEMO_ROOT / "db"

# Private path patterns that should NEVER appear in demo data
_PRIVATE_PATH_PATTERNS = [
    re.compile(r"/home/[^/]+/"),  # /home/username/
    re.compile(r"/Users/[^/]+/"),  # /Users/username/
    re.compile(r"C:\\Users\\"),  # Windows user paths
    re.compile(r"/var/lib/"),  # System data dirs
    re.compile(r"/etc/"),  # System config
    re.compile(r"ghp_[A-Za-z0-9]{36}"),  # GitHub PATs
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI API keys
    re.compile(r"AKIA[A-Z0-9]{16}"),  # AWS access keys
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify AIP_Brain demo database")
    parser.add_argument("--db-path", default=None, help="Override demo DB directory path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    args = parser.parse_args()

    db_dir = Path(args.db_path) if args.db_path else DEFAULT_DB_DIR
    verbose = args.verbose

    print("=" * 60)
    print("AIP_Brain Demo DB Verification")
    print("=" * 60)
    print(f"DB directory: {db_dir}")

    all_passed = True

    # 1. Check DB files exist
    print("\n[1] Checking database files...")
    required_dbs = ["state.db", "lexical.db", "vectors.db", "trace.db"]
    for name in required_dbs:
        path = db_dir / name
        if not path.exists():
            print(f"  FAIL: {name} does not exist")
            all_passed = False
        elif path.stat().st_size == 0:
            print(f"  FAIL: {name} is empty (0 bytes)")
            all_passed = False
        else:
            size_kb = path.stat().st_size / 1024
            print(f"  OK:   {name} ({size_kb:.1f} KB)")

    state_db_path = db_dir / "state.db"
    if not state_db_path.exists():
        print("\nCannot continue verification without state.db")
        sys.exit(1)

    conn = sqlite3.connect(str(state_db_path))

    # 2. Check Q&A turn count
    print("\n[2] Checking Q&A turns (minimum 50)...")
    turn_count = conn.execute("SELECT COUNT(*) FROM corpus_turns").fetchone()[0]
    if turn_count >= 50:
        print(f"  OK:   {turn_count} turns present")
    else:
        print(f"  FAIL: Only {turn_count} turns (minimum 50 required)")
        all_passed = False

    # 3. Check wiki articles
    print("\n[3] Checking wiki articles (minimum 20)...")
    try:
        wiki_count = conn.execute("SELECT COUNT(*) FROM codex_topics WHERE is_wiki_page = 1").fetchone()[0]
    except Exception:
        wiki_count = conn.execute("SELECT COUNT(*) FROM codex_topics").fetchone()[0]
    if wiki_count >= 20:
        print(f"  OK:   {wiki_count} wiki articles present")
    else:
        print(f"  FAIL: Only {wiki_count} wiki articles (minimum 20 required)")
        all_passed = False

    # 4. Check tags
    print("\n[4] Checking tags on turns...")
    tagged_count = conn.execute("SELECT COUNT(*) FROM corpus_turns WHERE tagging_version > 0").fetchone()[0]
    turns_with_tags = conn.execute(
        "SELECT COUNT(*) FROM corpus_turns WHERE tags != '[]' AND tags IS NOT NULL"
    ).fetchone()[0]
    if turns_with_tags > 0 and tagged_count > 0:
        print(f"  OK:   {tagged_count} turns tagged, {turns_with_tags} turns with tags")
    else:
        print(f"  FAIL: No tagged turns found")
        all_passed = False

    # 5. Check entities/graph nodes
    print("\n[5] Checking entities and graph nodes...")
    try:
        node_count = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        if node_count > 0:
            print(f"  OK:   {node_count} graph nodes, {edge_count} edges")
        else:
            print(f"  FAIL: No graph nodes found")
            all_passed = False
    except Exception as e:
        print(f"  FAIL: Could not query graph tables: {e}")
        all_passed = False

    # 6. Check embeddings
    print("\n[6] Checking embeddings...")
    embedded_count = conn.execute("SELECT COUNT(*) FROM corpus_turns WHERE embedded = 1").fetchone()[0]
    total_turns = conn.execute("SELECT COUNT(*) FROM corpus_turns").fetchone()[0]
    if embedded_count > 0:
        pct = round(embedded_count / total_turns * 100, 1) if total_turns > 0 else 0
        print(f"  OK:   {embedded_count}/{total_turns} turns embedded ({pct}%)")
    else:
        print(f"  INFO: No embeddings present (PENDING)")
        print(f"        Run 'aip corpus embed --db-path {state_db_path}' after configuring an embedding provider.")

    # 7. Check for private paths
    print("\n[7] Checking for private paths/secrets...")
    private_found = []
    # Check corpus turns
    for row in conn.execute("SELECT turn_id, source_path, user_text, assistant_text FROM corpus_turns"):
        text_to_check = f"{row[1]} {row[2]} {row[3]}"
        for pattern in _PRIVATE_PATH_PATTERNS:
            if pattern.search(text_to_check):
                private_found.append(f"turn {row[0]}: matches {pattern.pattern}")
    # Check codex sources
    for row in conn.execute("SELECT source_id, source_path FROM codex_sources"):
        for pattern in _PRIVATE_PATH_PATTERNS:
            if pattern.search(row[1]):
                private_found.append(f"source {row[0]}: matches {pattern.pattern}")
    # Check graph nodes
    for row in conn.execute("SELECT id, aliases_json, metadata_json FROM graph_nodes"):
        text_to_check = f"{row[1]} {row[2]}"
        for pattern in _PRIVATE_PATH_PATTERNS:
            if pattern.search(text_to_check):
                private_found.append(f"graph node {row[0]}: matches {pattern.pattern}")

    if private_found:
        print(f"  FAIL: Found {len(private_found)} private path/secret matches:")
        for match in private_found[:10]:
            print(f"        {match}")
        all_passed = False
    else:
        print(f"  OK:   No private paths or secrets found")

    conn.close()

    # 8. FTS5 smoke test
    print("\n[8] Running FTS5 smoke tests...")
    state_conn = sqlite3.connect(str(state_db_path))
    smoke_queries = [
        "AIP_Brain",
        "DEFINER",
        "Beast actor",
        "retrieval pipeline",
        "artifact lifecycle",
    ]
    fts_pass = 0
    fts_fail = 0
    for query in smoke_queries:
        try:
            results = state_conn.execute(
                """
                SELECT t.turn_id FROM corpus_turns_fts f
                JOIN corpus_turns t ON f.rowid = t.rowid
                WHERE corpus_turns_fts MATCH ?
                LIMIT 1
                """,
                (query,),
            ).fetchall()
            if results:
                fts_pass += 1
                if verbose:
                    print(f"  OK:   '{query}' -> {len(results)} result(s)")
            else:
                fts_fail += 1
                print(f"  FAIL: '{query}' -> no results")
        except Exception as e:
            fts_fail += 1
            print(f"  FAIL: '{query}' -> error: {e}")
    state_conn.close()

    if fts_fail == 0:
        print(f"  OK:   All {fts_pass} smoke queries returned results")
    else:
        print(f"  WARN: {fts_fail}/{fts_pass + fts_fail} smoke queries returned no results")

    # 9. Lexical DB check
    print("\n[9] Checking lexical DB...")
    lexical_path = db_dir / "lexical.db"
    if lexical_path.exists() and lexical_path.stat().st_size > 0:
        lex_conn = sqlite3.connect(str(lexical_path))
        try:
            doc_count = lex_conn.execute("SELECT COUNT(*) FROM fts_documents").fetchone()[0]
            print(f"  OK:   {doc_count} documents in lexical index")
        except Exception as e:
            print(f"  WARN: Could not query lexical DB: {e}")
        lex_conn.close()
    else:
        print(f"  WARN: lexical.db missing or empty")

    # Summary
    print("\n" + "=" * 60)
    if all_passed:
        print("VERIFICATION PASSED")
    else:
        print("VERIFICATION FAILED — see issues above")
    print("=" * 60)

    sys.exit(0 if all_passed else 1)


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a table has a specific column."""
    try:
        conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    main()
