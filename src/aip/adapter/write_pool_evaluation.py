"""Write-path connection pooling evaluation for AIP_Brain.

Decision: A write connection pool is NOT beneficial for the current
architecture.  The single-writer model is sufficient.  Here's why.

---

## Current Architecture

All SQLite stores use a single persistent aiosqlite connection for writes.
WAL mode is enabled, which supports concurrent readers alongside a single
writer. The write path involves:

1. **Sexton actor** — batch tagging, embedding, wiki generation, graph
   extraction.  Runs in a scheduled background cycle.  Each step writes
   to one or more stores (corpus_turns, vector, artifacts, graph).

2. **Beast actor** — domain summaries, stale vector re-embedding.
   Runs on-demand and on a lightweight scheduled heartbeat.  Writes to
   vector store and artifact store.

3. **API routes** — chat message ingestion, corpus turn writes.  These
   are triggered by user actions and write to corpus_turns, artifacts,
   and event stores.

## Why a Write Pool Doesn't Help

1. **SQLite WAL = single concurrent writer.**  WAL mode allows multiple
   concurrent readers but still only ONE writer at a time.  Two write
   connections cannot commit simultaneously — the second will get
   SQLITE_BUSY.  A pool of write connections would just add contention.

2. **Async event loop serializes writes.**  Python's async event loop
   ensures that only one coroutine executes at a time between `await`
   points.  Since aiosqlite operations are `await`-based, writes from
   different actors are naturally serialized by the event loop.  True
   concurrent writes never occur.

3. **SQLITE_BUSY retry handles rare contention.**  When a long-running
   write transaction blocks a second writer (e.g., a batch upsert that
   takes longer than a single `await` tick), the existing retry logic
   in `GraphStore._execute_with_retry()` handles it with exponential
   backoff.  This is sufficient for the current workload.

4. **Write volume is low.**  The system processes ~200 turns per Sexton
   cycle (tagging), ~50 per embedding pass, and a handful of graph
   extraction calls.  Even at peak, write throughput is measured in
   single-digit transactions per second — well within a single
   connection's capacity.

5. **A write pool adds complexity without benefit.**  A `WritePoolMixin`
   would need: connection checkout/return, a lock or queue for
   serialized commit access, stale connection detection, and health
   telemetry.  All of this adds complexity to solve a problem that
   doesn't exist in the current architecture.

## When to Revisit

A write pool MIGHT become useful if:

- The system moves to a multi-process deployment where separate processes
  write to the same database (e.g., a dedicated embedding worker process).
- Write throughput requirements exceed what a single connection can handle
  (roughly >100 TPS for simple transactions on modern SSDs).
- The architecture migrates away from SQLite to a database that supports
  true concurrent writes (PostgreSQL, etc.), in which case a connection
  pool would be standard practice.

Until then, the single-writer model with SQLITE_BUSY retry is the right
choice — simple, reliable, and performant for the current workload.
"""
