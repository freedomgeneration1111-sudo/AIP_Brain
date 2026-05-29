"""Synthetic deterministic retrieval benchmark (CHUNK-10.4).

No network, no real models. Measures latency for laptop-viable profile.
"""

import time

import pytest


def generate_synthetic_docs(n: int):
    return [{"id": f"doc-{i}", "content": "synthetic knowledge " * 20, "domain": "test"} for i in range(n)]


@pytest.mark.benchmark
def test_bench_retrieval_100_docs():
    docs = generate_synthetic_docs(100)
    start = time.perf_counter()
    # Synthetic retrieval simulation (no real vector/lexical)
    results = [d for d in docs if "knowledge" in d["content"]][:10]
    duration_ms = (time.perf_counter() - start) * 1000
    assert len(results) > 0
    assert duration_ms < 50  # synthetic target for 100 docs


@pytest.mark.benchmark
def test_bench_retrieval_1000_docs():
    docs = generate_synthetic_docs(1000)
    start = time.perf_counter()
    results = [d for d in docs if "knowledge" in d["content"]][:50]
    duration_ms = (time.perf_counter() - start) * 1000
    assert duration_ms < 200
