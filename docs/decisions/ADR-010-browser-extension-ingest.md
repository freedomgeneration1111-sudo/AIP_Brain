# ADR-010: Browser Extension Ingest

**Date:** 2026-06-05
**Status:** DEFERRED — Phase 5
**DEFINER:** B. Moses Jorgensen

---

## Context

The DEFINER currently ingests from external AI interfaces by exporting
JSON from each platform and running `aip corpus ingest` against the file.
This works but has friction: export, download, run command, wait for
tagging. The process is outside the normal workflow.

A browser extension would enable one-click capture of any AI conversation
from within the browser, without leaving the interface or handling files.

## Decision (Deferred)

Build browser extensions for Chrome and Firefox that inject an "AIP Ingest"
button into supported AI chat interfaces.

### Supported Interfaces (target)

- claude.ai
- chat.openai.com
- chat.deepseek.com
- gemini.google.com
- grok.x.ai
- Any interface with accessible conversation DOM

### User Flow

1. DEFINER is in a conversation on claude.ai
2. Extension injects "AIP Ingest" button into the conversation header
3. DEFINER clicks the button
4. Extension scrapes the conversation, formats as conversations.json
5. Extension POSTs to `http://localhost:8000/api/v1/ingest/conversation`
6. Toast appears: "Ingested 23 turns → Beast tagging in background"
7. Turns appear in Thread Log within ~30 seconds

### Optional: Selection Ingest

- DEFINER selects text on any webpage
- Right-click → "Ingest to AIP"
- Extension captures selected text as a single turn with `source_type: "web_fragment"`
- Useful for ingesting reference material, articles, research notes

---

## Why Deferred

**Maintenance burden**: AI chat interfaces change their DOM frequently.
Each interface requires a custom scraper. Keeping 5 scrapers current
across interface redesigns is significant ongoing maintenance.

**Distribution overhead**: Chrome Web Store and Firefox Add-ons require
review processes, privacy policy, store listings, version management.
This is disproportionate overhead for Phase 1-4 where the DEFINER is
the primary user.

**Adequate alternatives**: The existing JSON export + `aip corpus ingest`
workflow is the established pattern and works reliably. The ADR-009 cohort
synthesis feature (Path 3) addresses the most valuable use case — running
parallel queries — without requiring a browser extension.

**Security review required**: Extensions with localhost API access require
careful permission scoping. The security review is not urgent for personal
sovereign use but is required before any multi-user deployment.

## Resumption Criteria

Resume this ADR when:
- Phase 4 (UI hardening) is complete and AIP is in daily personal use
- The DEFINER finds the JSON export workflow genuinely limiting versus
  extension-based capture
- At least one of the targeted AI interfaces has a stable enough DOM that
  a scraper would be maintainable

---

## Implementation Notes (for future reference)

**Architecture**: Manifest V3. Content scripts per domain. Background
service worker for local API communication.

**Scraping strategy**: Prefer the platform's own export JSON format
(claude.ai has an export feature; ChatGPT has an export feature) over
DOM scraping where available. Export JSON is stable; DOM is not.

**localhost communication**: Background service worker makes the POST to
localhost:8000. Content scripts cannot make cross-origin requests to
localhost directly. The background worker handles the API call.

**Format**: Output should match the existing conversations.json format
that `aip corpus ingest` already handles. No new ingest format needed.

**Rate limiting**: Add a per-source-domain cooldown (30 seconds minimum
between ingestion calls from the same interface) to prevent accidental
duplicate ingestion.

---

## Related

- ADR-008: Semantic Session Context (turns ingested via extension become
  first-class corpus turns, enter same real-time tagging loop)
- ADR-009: Cohort Synthesis (extension could capture cohort responses
  from external interfaces; the fan-out feature partially supersedes this
  use case)
- ROADMAP.md: Phase 5 (Production and Scale)
