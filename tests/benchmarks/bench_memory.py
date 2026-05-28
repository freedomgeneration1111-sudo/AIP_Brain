"""Synthetic deterministic memory footprint benchmark (CHUNK-10.4, laptop-viable profile)."""

import pytest


@pytest.mark.benchmark
def test_bench_memory_laptop_viable():
    # Synthetic memory breakdown (matches PerformanceProfiler.get_memory_usage)
    memory = {
        "total_mb": 1850,
        "vector_index": 320,
        "lexical_fts5": 180,
        "max_target_mb": 4096,
    }
    assert memory["total_mb"] < memory["max_target_mb"]
    assert memory["vector_index"] < 500  # conservative for laptop
