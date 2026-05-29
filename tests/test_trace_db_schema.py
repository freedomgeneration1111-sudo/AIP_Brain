"""Trace database schema — verifies trace_events and routing_outcomes tables, constraints, and indexes."""

import sqlite3
import tempfile

import pytest


def _init_trace_db(db_path: str) -> None:
    """Initialize trace.db with the required schema."""
    conn = sqlite3.connect(db_path)
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
            failure_type TEXT CHECK (failure_type IS NULL OR failure_type IN ('A', 'B', 'C', 'D', 'E', 'F')),
            detail TEXT,
            intervention_applied INTEGER DEFAULT 0,
            intervention_type TEXT,
            outcome TEXT CHECK (outcome IS NULL OR outcome IN
                ('success', 'failure', 'timeout', 'gate_blocked', 'insufficient_memory', 'detected', 'stale_detected')),
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_trace_session
        ON trace_events(session_id);

        CREATE INDEX IF NOT EXISTS idx_trace_unclassified
        ON trace_events(failure_type, outcome);

        CREATE INDEX IF NOT EXISTS idx_trace_node_type
        ON trace_events(node_type);

        CREATE TABLE IF NOT EXISTS routing_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            slot_name TEXT NOT NULL,
            domain TEXT,
            was_exploration INTEGER DEFAULT 0,
            model_name TEXT,
            token_count INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            latency_ms REAL DEFAULT 0.0,
            outcome TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_routing_session
        ON routing_outcomes(session_id);

        CREATE INDEX IF NOT EXISTS idx_routing_slot_domain
        ON routing_outcomes(slot_name, domain);
    """)
    conn.commit()
    conn.close()


@pytest.fixture
def initialized_db():
    """Create a temporary directory with an initialized trace.db."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = f"{tmp}/trace.db"
        _init_trace_db(db_path)
        yield db_path


def test_trace_events_table_exists_after_init(initialized_db):
    """trace_events table must exist after aip init."""
    conn = sqlite3.connect(initialized_db)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trace_events'")
        assert cur.fetchone() is not None, "trace_events table does not exist after init"
    finally:
        conn.close()


def test_trace_events_failure_type_constrained(initialized_db):
    """failure_type column must only accept values A-F (or NULL)."""
    conn = sqlite3.connect(initialized_db)
    try:
        # Valid values should work
        for ft in ("A", "B", "C", "D", "E", "F"):
            conn.execute(
                "INSERT INTO trace_events (session_id, node_type, failure_type, outcome) VALUES (?, ?, ?, ?)",
                ("test-session", "test", ft, "failure"),
            )

        # NULL should work
        conn.execute(
            "INSERT INTO trace_events (session_id, node_type, failure_type, outcome) VALUES (?, ?, ?, ?)",
            ("test-session", "test", None, "success"),
        )

        conn.commit()

        # Invalid value should be rejected
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO trace_events (session_id, node_type, failure_type, outcome) VALUES (?, ?, ?, ?)",
                ("test-session", "test", "Z", "failure"),
            )
    finally:
        conn.close()


def test_trace_events_outcome_constrained(initialized_db):
    """outcome column must only accept valid values (or NULL)."""
    conn = sqlite3.connect(initialized_db)
    try:
        # Valid values should work
        for outcome in ("success", "failure", "timeout", "gate_blocked", "insufficient_memory"):
            conn.execute(
                "INSERT INTO trace_events (session_id, node_type, failure_type, outcome) VALUES (?, ?, ?, ?)",
                ("test-session", "test", None, outcome),
            )

        conn.commit()

        # Invalid value should be rejected
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO trace_events (session_id, node_type, failure_type, outcome) VALUES (?, ?, ?, ?)",
                ("test-session", "test", None, "invalid_outcome"),
            )
    finally:
        conn.close()


def test_routing_outcomes_table_exists(initialized_db):
    """routing_outcomes table must exist after aip init."""
    conn = sqlite3.connect(initialized_db)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='routing_outcomes'")
        assert cur.fetchone() is not None, "routing_outcomes table does not exist after init"

        # Verify required columns
        cur.execute("PRAGMA table_info(routing_outcomes)")
        columns = {row[1] for row in cur.fetchall()}
        required = {"id", "session_id", "slot_name", "domain", "was_exploration", "outcome", "created_at"}
        missing = required - columns
        assert not missing, f"routing_outcomes table is missing required columns: {missing}"
    finally:
        conn.close()


def test_indexes_present(initialized_db):
    """Required indexes must exist on trace_events and routing_outcomes."""
    conn = sqlite3.connect(initialized_db)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='index'")
        index_names = {row[0] for row in cur.fetchall()}

        required_indexes = {
            "idx_trace_session",
            "idx_trace_unclassified",
            "idx_trace_node_type",
            "idx_routing_session",
            "idx_routing_slot_domain",
        }

        missing = required_indexes - index_names
        assert not missing, f"Required indexes missing: {missing}"
    finally:
        conn.close()
