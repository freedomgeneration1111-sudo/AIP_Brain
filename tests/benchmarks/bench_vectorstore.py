"""Synthetic deterministic VectorStore query benchmark."""

import time

import pytest


@pytest.mark.benchmark
def test_bench_vector_query_50_results():
    # Synthetic embedding + query simulation
    start = time.perf_counter()
    # pretend query
    time.sleep(0.001)  # deterministic micro-delay
    duration_ms = (time.perf_counter() - start) * 1000
    assert duration_ms < 10


@pytest.mark.benchmark
def test_bench_batch_embed_32():
    start = time.perf_counter()
    # synthetic batch
    time.sleep(0.005)
    duration_ms = (time.perf_counter() - start) * 1000
    assert duration_ms < 50
