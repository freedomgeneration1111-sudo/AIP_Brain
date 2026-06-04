## Test Isolation Debt
test_model_slot_resolver.py: 4 tests fail in full suite due to env var
pollution from another test file. All pass in isolation. Fix: find
which test sets AIP_* env vars without cleanup and add proper
teardown or use monkeypatch fixture consistently.
Suspected files: test_config_validation.py, test_cli.py, test_dogfood_loop.py

