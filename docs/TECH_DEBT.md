## Test Isolation Debt
test_model_slot_resolver.py: 4 tests fail in full suite due to env var
pollution from another test file. All pass in isolation. Fix: find
which test sets AIP_* env vars without cleanup and add proper
teardown or use monkeypatch fixture consistently.
Suspected files: test_config_validation.py, test_cli.py, test_dogfood_loop.py


## DEBT-003: test_sqlite_vss_graceful_skip ordering pollution
test_sqlite_vss_graceful_skip fails in full suite but passes in isolation.
Same pattern as model_slot_resolver tests — another test is setting global
state (likely sqlite_vss availability flag) without cleanup.
Suspected files: same env-var-setting tests as DEBT-002.
Fix: find which test imports sqlite_vss or sets _vss_available global
and add proper teardown.
