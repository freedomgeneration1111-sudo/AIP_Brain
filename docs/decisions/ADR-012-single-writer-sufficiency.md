# ADR-012: Single-Writer Sufficiency for SQLite Stores

## Status

Accepted

## Context

Sprint 5.20 evaluated whether introducing a `WritePoolMixin` (a small pool of
write-capable connections with serialized access via lock or queue) would
benefit the Sexton and Beast write paths.  The evaluation was prompted by the
successful integration of `ReadPoolMixin` (Sprint 5.19), which demonstrated
measurable throughput gains for concurrent reads.

Both Sexton and Beast perform writes through async store methods:

- **Sexton**: tagging (`update_beast_tags`), embedding (`vector.upsert`,
  `mark_embedded`), wiki generation (`artifacts.write`, `ecs.transition`),
  graph extraction (`upsert_nodes_batch`, `upsert_edges_batch`,
  `log_turn_extracted`), failure classification.
- **Beast**: domain summaries (`artifacts.write`, `ecs.transition`), corpus
  maintenance (`vector.upsert`), heartbeat events.

All writes go through each store's single persistent connection (`_conn`),
accessed via `_get_conn()`.  No actor writes raw SQL directly.

SQLite is configured in WAL mode, which allows concurrent readers alongside a
single writer.  However, SQLite only supports **one writer at a time** —
concurrent write transactions will block or fail with `SQLITE_BUSY`.

## Decision

**We will NOT implement `WritePoolMixin`.** The current single-writer model is
sufficient and a write pool would add complexity without meaningful benefit.

## Rationale

### 1. SQLite Single-Writer Constraint

SQLite WAL mode permits exactly one write transaction at a time.  Multiple
write connections would still serialize at the SQLite level — the second
connection would either block or receive `SQLITE_BUSY`.  A write pool would
not increase write throughput; it would only add context-switching overhead
and retry logic for `SQLITE_BUSY` errors that the current single-connection
model avoids entirely.

### 2. Async Event Loop Serializes Writes

All store methods are `async`.  In a single-process async application (which
AIP is), the event loop naturally serializes write operations.  When Sexton
calls `graph_store.upsert_nodes_batch()`, that coroutine runs to completion
before the next write coroutine starts (unless it yields explicitly).  The
async runtime already provides the serialization guarantee that a write-pool
lock or queue would add.

### 3. Write Workload is Bursty, Not Sustained

Sexton runs a vigil cycle every 300 seconds.  Beast fires on-demand during
chat.  Neither actor produces sustained concurrent write pressure.  The write
bursts (tagging 200 turns, extracting 50 turns) are sequential by design —
each batch waits for the previous one to complete.

### 4. Batch Operations Already Optimize Write Throughput

`GraphStore.upsert_nodes_batch()` and `upsert_edges_batch()` use a single
transaction for bulk writes, which is the optimal SQLite write pattern.
Adding more connections would not make individual transactions faster.

### 5. Risk of SQLITE_BUSY and Connection Contention

Multiple write connections would increase the frequency of `SQLITE_BUSY`
errors, requiring retry logic (which already exists for edge cases).  The
retry overhead plus the serialization at the SQLite level would likely make
writes *slower* than the current single-connection approach.

### 6. Complexity Budget

A `WritePoolMixin` would add:
- Connection lifecycle management (creation, stale detection, cleanup)
- Lock/queue-based serialization (redundant with async serialization)
- `SQLITE_BUSY` retry handling (already in `GraphStore` for edge cases)
- Health telemetry for write connections
- Integration with `StoreHealthMixin`

The estimated value (near-zero throughput gain) does not justify this
complexity.

## Consequences

- **Positive**: Simpler architecture, no `SQLITE_BUSY` contention from
  multiple writers, no additional health telemetry surface area.
- **Positive**: `ReadPoolMixin` is the right abstraction for the read-heavy
  ask pipeline; writes remain simple.
- **Negative**: If AIP ever moves to a multi-process architecture (e.g.,
  separate Sexton worker processes), the single-writer model would need
  re-evaluation.  In that scenario, a proper queue-based write coordinator
  (not a connection pool) would be the correct approach.
- **Mitigation**: The existing `SQLITE_BUSY` retry logic in `GraphStore`
  handles transient write contention from background maintenance and health
  check writes.

## References

- `src/aip/adapter/read_pool.py` — ReadPoolMixin (the successful pool model)
- `src/aip/adapter/graph_store.py` — SQLITE_BUSY retry, batch operations
- `src/aip/orchestration/actors/sexton.py` — Write paths through stores
- `src/aip/orchestration/actors/beast.py` — Write paths through stores
