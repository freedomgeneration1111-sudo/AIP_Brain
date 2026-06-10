# AIP Maintenance Protocol

**Effective:** 2026-06-10
**Status:** Active
**Owner:** B. Moses Jorgensen
**Release:** 0.1.0-alpha (Alpha Test Release)

This document defines the operational procedures for the AIP v0.1 alpha test and maintenance phase.
The active development phase ended with Sprint 6.4. All future changes should follow
this protocol.

> **For Alpha Testers**: If you encounter bugs or issues during testing, please document them
> with clear reproduction steps. The highest-priority known issue is DEBT-006 (Sexton not wired),
> which means automatic corpus tagging, embedding, wiki generation, and graph extraction are not
> running. Use CLI commands (`aip corpus tag`, manual embedding) as workarounds.

---

## Guiding Principles

1. **Small, incremental changes** — No feature sprints. Each change should be a single
   commit (or small PR) with a clear purpose.
2. **Test before commit** — All changes must pass `uv run pytest` and `uv run ruff check .`
   before being committed.
3. **Document decisions** — Any non-trivial change warrants an ADR or a note in the
   commit message explaining the rationale.
4. **No scope expansion** — Maintenance means keeping the system running and fixing
   known issues, not building new features.

---

## Priority Tasks

### 1. DEBT-006: Wire Sexton into app.py (HIGHEST PRIORITY)

The new Sexton actor (`src/aip/orchestration/actors/sexton.py`) is built but not wired.
Until this is fixed:

- Automatic corpus tagging does not run
- Automatic embedding does not run
- Wiki generation and graph extraction do not run
- Only failure classification (old Sexton) fires every 300s

**Fix:** See TECH_DEBT.md#DEBT-006 for detailed remediation steps. This is a single
commit that changes imports and wiring in `app.py`.

**After fix:** Let the system run for ~17 hours to complete the full embedding pass
(~2,716 turns at 50/cycle every 300s).

### 2. Re-evaluate Retrieval Quality

After the full embedding pass completes:

```bash
# Run FTS5-only baseline
uv run aip eval retrieval --mode fts-only --save-baseline

# Run hybrid evaluation
uv run aip eval retrieval --mode hybrid --save-baseline

# Run weight tuning
uv run python scripts/retrieval_weight_tuning.py --db-path db/state.db

# Compare results
uv run aip eval retrieval-ab \
  --config-a eval_results/baseline_fts_only.json \
  --config-b eval_results/baseline_hybrid.json \
  --label-a "FTS5-only" --label-b "Hybrid"
```

Update `docs/retrieval_benchmark_baseline.json` and `[retrieval.channel_weights]` in
`aip.config.toml` based on results.

### 3. Remaining Bugs

| Bug | Description | Priority |
|-----|-------------|----------|
| BUG-001 | `aip init` creates no default project | Medium |
| BUG-002 | `chat.py` uses wrong DB path for GraphStore | Medium |
| BUG-004 | GraphStore has no Protocol, uses sync sqlite3 | Low |

These can be addressed incrementally as time permits.

---

## Regular Maintenance Tasks

### Daily (if the system is running)

- Check `aip status` for actor health
- Monitor Vigil alerts (if alerting is configured)
- Check logs for errors: `grep -i error logs/`

### Weekly

- Run `aip eval retrieval --mode hybrid` to spot-check retrieval quality
- Review Vigil quality dashboard at `/vigil/quality`
- Check embedding progress: `aip status` should show increasing coverage

### Monthly

- Ingest new conversations from Claude export (or other sources)
- Run `aip corpus tag --retag --limit 200` if registry has been updated
- Review and close TECH_DEBT.md items if applicable
- Run `scripts/retrieval_weight_tuning.py` if corpus has changed significantly

### Quarterly

- Review ROADMAP.md for any items that should be promoted or deferred
- Review all ADRs for continued relevance
- Run full test suite: `uv run pytest -q --tb=short`
- Update STATUS.md with current corpus stats and system state

---

## Configuration Management

### Hot-Reloadable Settings

The following settings can be changed in `aip.config.toml` without restarting:

- `read_pool.pool_size` and per-store overrides
- `sexton.classification_batch_size`
- Retrieval channel weights under `[retrieval.channel_weights]`
- Vigil retrieval quality settings under `[vigil.retrieval_quality]`

### Settings Requiring Restart

- Model slot configurations (`[models.*]`)
- Database paths
- Auth settings
- Deployment profile

---

## Monitoring

### Health Endpoint

`GET /api/v1/health` provides:

- Actor status (Beast, Sexton, Vigil)
- Embedding coverage percentage
- Vector store size
- Graph node/edge counts
- Alerting subsystem health

### Vigil Quality Dashboard

`GET /vigil/quality` provides time-series quality metrics:

- Citation rate trends
- Grounding rate trends
- LLM faithfulness scores
- Retrieval quality samples (precision@5)

### Retrieval Evaluation CLI

```bash
# Quick quality check
uv run aip eval retrieval --mode hybrid -k 5

# FTS5-only baseline
uv run aip eval retrieval --mode fts-only -k 5

# Save baseline
uv run aip eval retrieval --mode hybrid --save-baseline
```

---

## When to Add a New ADR

Add an ADR when:

- Changing an architectural decision (e.g., switching vector store backends)
- Adding a new actor or significantly changing actor behavior
- Modifying the retrieval pipeline in a non-trivial way
- Changing the config schema
- Any decision that future contributors would need to understand

Template: `docs/decisions/ADR-000-template.md`
Next number: ADR-014

---

## Emergency Procedures

### If the system crashes repeatedly

1. Check `logs/` for error traces
2. Verify database files are not corrupted: `sqlite3 db/state.db "PRAGMA integrity_check"`
3. Check disk space
4. If database is corrupted, restore from backup (see `deploy/backup.sh`)
5. If model provider is down, set `AIP_PROFILE=laptop` and use local models

### If retrieval quality degrades

1. Check Vigil alerts at `/vigil/quality/alerts`
2. Run `aip eval retrieval --mode hybrid` to get current metrics
3. Compare against `docs/retrieval_benchmark_baseline.json`
4. If vector store is empty or corrupted, re-run embedding pass
5. If FTS5 index is stale, rebuild: `aip corpus tag --retag`

### If embedding pass is interrupted

1. Sexton tracks progress via `embedding_progress` in corpus_turns
2. Restart the system — Sexton will resume from where it left off
3. Check progress: `aip status` or `GET /api/v1/corpus/embedding-progress`

---

## Contact

For questions about the maintenance protocol, contact the project owner:
**B. Moses Jorgensen**

For technical details, refer to:
- `docs/ARCHITECTURE.md` — System architecture
- `docs/DEVELOPER_GUIDE.md` — Development setup
- `docs/API_REFERENCE.md` — API documentation
- `TECH_DEBT.md` — Known technical debt
- `ROADMAP.md` — Project roadmap (maintenance mode section)
