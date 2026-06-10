#!/usr/bin/env python3
"""Update golden queries with real corpus turn IDs and graph entity names.

Reads ``tests/retrieval_goldens/golden_queries.json``, runs FTS5 searches
against the ``corpus_turns`` table in the AIP state database, and replaces
placeholder IDs (e.g. ``"doc:aip_overview"``) with real ``turn_id`` values.
Also populates ``expected_entities`` from ``graph_nodes.canonical_name`` matches.

Usage::

    # Default: db/state.db
    python scripts/update_golden_ids.py

    # Custom DB path
    python scripts/update_golden_ids.py --db-path /path/to/state.db

    # Custom output path
    python scripts/update_golden_ids.py --output tests/retrieval_goldens/golden_queries.json

    # Dry run (print changes, don't write)
    python scripts/update_golden_ids.py --dry-run

The script is idempotent: running it multiple times produces the same output
given the same DB state.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "db" / "state.db"
DEFAULT_GOLDEN_PATH = PROJECT_ROOT / "tests" / "retrieval_goldens" / "golden_queries.json"

# Number of top FTS5 matches to keep as relevant_ids per query
FTS_TOP_K = 5

# Minimum FTS5 rank score to consider a match relevant
FTS_MIN_RANK = -50.0


# ---------------------------------------------------------------------------
# FTS5 search
# ---------------------------------------------------------------------------

def fts5_search(conn: sqlite3.Connection, query: str, top_k: int = FTS_TOP_K) -> list[str]:
    """Run an FTS5 search against corpus_turns_fts and return top-k turn_ids.

    Uses the FTS5 ``rank`` column (bm25) for ordering.  Lower rank values
    indicate better matches.  The query string is cleaned to be FTS5-safe
    (strips special characters, joins words with AND).

    Args:
        conn: SQLite connection to state.db.
        query: The query string to search for.
        top_k: Maximum number of turn_ids to return.

    Returns:
        List of turn_id strings, ordered by FTS5 relevance.
    """
    # Clean query for FTS5: remove special chars, split into words, join with AND
    import re
    words = re.findall(r"[a-zA-Z0-9_]+", query)
    if not words:
        return []
    fts_query = " AND ".join(words)

    try:
        cursor = conn.execute(
            """
            SELECT f.turn_id, f.rank
            FROM corpus_turns_fts f
            WHERE corpus_turns_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, top_k),
        )
        results = cursor.fetchall()
        return [row[0] for row in results if row[1] >= FTS_MIN_RANK]
    except sqlite3.OperationalError:
        # FTS5 table may not exist yet
        return []


# ---------------------------------------------------------------------------
# Graph entity lookup
# ---------------------------------------------------------------------------

def lookup_entities(conn: sqlite3.Connection, query: str) -> list[str]:
    """Look up entity names from graph_nodes that match the query.

    Searches graph_nodes.canonical_name using a LIKE query with each
    significant word from the query.  Returns unique entity names
    ordered by confidence.

    Args:
        conn: SQLite connection to state.db.
        query: The query string to search entities for.

    Returns:
        List of canonical_name strings from graph_nodes.
    """
    import re
    words = re.findall(r"[a-zA-Z0-9_]+", query)
    if not words:
        return []

    entities: list[str] = []
    seen: set[str] = set()

    for word in words:
        if len(word) < 3:  # skip very short words
            continue
        try:
            cursor = conn.execute(
                """
                SELECT canonical_name FROM graph_nodes
                WHERE canonical_name LIKE ? OR aliases_json LIKE ?
                ORDER BY confidence DESC
                LIMIT 3
                """,
                (f"%{word}%", f"%{word}%"),
            )
            for row in cursor.fetchall():
                name = row[0]
                if name not in seen:
                    seen.add(name)
                    entities.append(name)
        except sqlite3.OperationalError:
            # graph_nodes table may not exist yet
            continue

    return entities[:5]  # cap at 5 entities per query


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def update_golden_queries(
    db_path: str | Path,
    golden_path: str | Path,
    output_path: str | Path | None = None,
    dry_run: bool = False,
) -> None:
    """Update golden queries with real turn IDs and entity names.

    Args:
        db_path: Path to the AIP state.db SQLite database.
        golden_path: Path to the input golden queries JSON file.
        output_path: Path to write the updated golden queries. Defaults to
            overwriting the input file.
        dry_run: If True, print changes without writing.
    """
    db_path = Path(db_path)
    golden_path = Path(golden_path)
    output_path = Path(output_path) if output_path else golden_path

    if not golden_path.exists():
        print(f"Error: Golden queries file not found: {golden_path}", file=sys.stderr)
        sys.exit(1)

    # Load existing golden queries
    with open(golden_path) as f:
        queries = json.load(f)

    # Connect to DB
    if not db_path.exists():
        print(f"Warning: Database not found at {db_path}", file=sys.stderr)
        print("Cannot resolve real turn IDs. Writing golden queries with placeholder IDs.", file=sys.stderr)
        # Just write the file as-is (no changes possible)
        if not dry_run:
            with open(output_path, "w") as f:
                json.dump(queries, f, indent=2)
            print(f"Wrote {len(queries)} queries to {output_path} (no DB changes)")
        return

    conn = sqlite3.connect(str(db_path))

    try:
        updated_count = 0
        for i, item in enumerate(queries):
            query = item.get("query", "")
            if not query:
                continue

            # --- Update relevant_ids via FTS5 ---
            old_ids = item.get("relevant_ids", [])
            new_ids = fts5_search(conn, query, top_k=FTS_TOP_K)

            if new_ids:
                item["relevant_ids"] = new_ids
                if old_ids != new_ids:
                    updated_count += 1
                    if dry_run:
                        print(f"  Q{i}: '{query[:50]}...'")
                        print(f"    OLD ids: {old_ids[:3]}...")
                        print(f"    NEW ids: {new_ids[:3]}...")

            # --- Update expected_entities from graph_nodes ---
            old_entities = item.get("expected_entities", [])
            # Only update if the current list contains placeholder-looking entries
            # or if the list is empty and we find real entities
            new_entities = lookup_entities(conn, query)

            if new_entities and (not old_entities or any(" " not in e for e in old_entities)):
                # Replace with real entities from the graph if:
                # - no entities were specified, or
                # - existing entities look like placeholders (no spaces)
                item["expected_entities"] = new_entities
                if old_entities != new_entities:
                    updated_count += 1
                    if dry_run:
                        print(f"    OLD entities: {old_entities}")
                        print(f"    NEW entities: {new_entities}")

            # Preserve existing domain and tags
            # (these are already well-specified in the golden queries)

        # Write output
        if dry_run:
            print(f"\nDry run: {updated_count} fields would be updated across {len(queries)} queries")
        else:
            os.makedirs(output_path.parent, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(queries, f, indent=2)
            print(f"Updated {updated_count} fields across {len(queries)} queries")
            print(f"Written to: {output_path}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update golden queries with real corpus turn IDs and graph entity names",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to AIP state.db (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--golden-queries",
        default=str(DEFAULT_GOLDEN_PATH),
        help=f"Path to input golden queries JSON (default: {DEFAULT_GOLDEN_PATH})",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output path for updated golden queries (default: overwrite input)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print changes without writing to file",
    )
    args = parser.parse_args()
    update_golden_queries(
        db_path=args.db_path,
        golden_path=args.golden_queries,
        output_path=args.output,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
