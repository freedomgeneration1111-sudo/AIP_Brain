# UI Cycle 14 — Integration Notes

**Date:** 2026-06-13
**Branch:** `ui-cycle-14-integration`
**Base:** `main` (merge commit `53e61ba`)

## Integration Decisions

### 1. Dashboard Card Click-Through
Dashboard cards now navigate to the relevant sub-page on click. This is implemented via an optional `navigate_to` parameter on the `_card()` helper function. Cards without a logical target page (Dogfood Mode, Backend Health, Actor Health, Recent Activity) remain non-navigable.

**Decision:** Use `ui.navigate.to()` on card click rather than wrapping cards in `ui.link`. This avoids visual changes (no underlines, no link coloring) and keeps the card looking like a card, not a link. The `cursor:pointer` style is added only when `navigate_to` is set.

### 2. Link Wiki Wiring
The Ask page now wires `on_link_wiki` to a new `_handle_link_wiki()` handler that calls `api_client.create_knowledge_link()`. The direct-model fallback path still uses `on_link_wiki=None` because there is no backend to call.

**Decision:** `target_id="auto"` is passed to the knowledge link API, letting the backend determine the appropriate wiki target. If the backend returns an error, the user sees a notification. No auto-creation of wiki articles.

### 3. Model Slot Change Notification
`_on_chat_model_changed` was changed from a sync fire-and-forget to an `async def` that awaits the backend call. On failure, the user sees a warning notification.

**Decision:** This is a minor UX improvement, not a behavior change. The model slot still changes locally immediately (via `set_role_model`), but the user now knows if the backend didn't confirm.

### 4. Status Language Unification
Right rail and dashboard now use "UNAVAILABLE" consistently for the `unavailable` retrieval channel state. Previously, the right rail used "DOWN" while the dashboard used "UNAVAILABLE".

**Decision:** "UNAVAILABLE" is clearer and less alarming than "DOWN". A channel can be unavailable for many reasons (not configured, no data, backend issue) that don't necessarily mean the system is "down".

### 5. API Client Error Honesty
`list_text_generation_slots()` now returns `ci_mode: False` on failure instead of `ci_mode: True`. The old code fabricated CI mode as active when the backend was unreachable.

**Decision:** The GUI must never pretend a subsystem is healthy when it can't verify. Returning `ci_mode: False` with an `error` key lets callers render the correct degraded state.

### 6. Corpus Page Error Handling
`_load_all()` now wraps all 4 parallel API calls in a try/except, setting `backend_reachable = False` and populating fallback empty data on failure. Previously, any single API call failure would crash the entire page load.

**Decision:** Follow the same pattern as the Maintenance page, which already has comprehensive error handling with fallback data.

### 7. Settings Page
The Settings page was a placeholder stub. It now shows real data from the API: backend status, dogfood mode, model slots, and API key status. No new features — just making the existing page functional.

**Decision:** The Settings page is a read-only status display at this point. Model slot changes are still made via the Ask page dropdown. This keeps the integration pass focused on consistency, not new features.

## Known Remaining Limitations

1. **No deep-link navigation from within pages** — e.g., clicking a corpus document doesn't navigate to `/corpus?doc=...`. All cross-surface navigation is limited to the left-nav and the new dashboard click-through.

2. **Wiki backlinks are text-only** — not clickable links to the referenced article. The wiki article view renders backlinks as labels without navigation callbacks.

3. **`approve_all_reviews()` exists in API client but is not wired** — the FROZEN shell.py has a bulk approve button, but the active GUI does not expose it. If wired in the future, it must require explicit confirmation.

4. **Vigil quality routes (30+) have no GUI consumer** — these API endpoints are available but no GUI page or component uses them.

5. **Graph visualization not implemented** — the graph/ECS API routes exist but have no dedicated GUI page. Graph data is accessible indirectly through the Ask/Retrieval Lab.

6. **Actor runs route split across modules** — `GET /actors/{name}/runs` is in `maintenance.py` rather than `actors.py`. This is an organizational gap, not a functional issue.

7. **Legacy files still exist** — `gui/main.py`, `gui/shell.py`, `gui/archive/main.py` are FROZEN/PRESERVED. They contain patterns (like `approve_all_reviews`) that don't meet current standards but are not active code.

## Test Coverage

`tests/test_ui_integration_cycle14.py` covers:
1. Navigation/page reachability (3 tests)
2. Route registration/no shadowing (1 test)
3. GUI import boundary (1 test)
4. API client fallback/error handling (2 tests)
5. Mutating actions remain explicit/sovereign (3 tests)
6. Status language consistency (1 test)
7. Cross-surface link wiring (4 tests)
8. Error/empty state handling (3 tests)
9. Existing UI cycle test compatibility (2 tests)
10. Model slot change notification (1 test)

Total: 22 integration tests, all passing.
