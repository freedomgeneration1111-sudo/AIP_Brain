"""Rate limiter tests (token bucket, middleware, overrides)."""

from __future__ import annotations

from aip.adapter.middleware.rate_limiter import TokenBucketRateLimiter
from aip.foundation.schemas import RateLimitConfig


def test_token_bucket_allows_within_limit():
    config = RateLimitConfig(enabled=True, requests_per_minute=60, burst_size=5)
    limiter = TokenBucketRateLimiter(config)
    key = "test"
    for _ in range(5):
        assert limiter.allow_request(key) is True
    assert limiter.allow_request(key) is False  # burst exhausted


def test_rate_limiter_disabled_passes_all():
    config = RateLimitConfig(enabled=False)
    limiter = TokenBucketRateLimiter(config)
    for _ in range(100):
        assert limiter.allow_request("any") is True


def test_layering():
    from pathlib import Path

    rl_file = Path(__file__).parent.parent / "src/aip/adapter/middleware/rate_limiter.py"
    if rl_file.exists():
        text = rl_file.read_text()
        assert "from aip.orchestration" not in text
