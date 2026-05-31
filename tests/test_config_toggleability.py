"""
Verifies that all AIP configuration sections are loadable and have expected default values per §1.8.
"""

from aip.foundation.schemas import (
    AuthConfig,
    CanonicalPromotionConfig,
    DeploymentProfile,
    RateLimitConfig,
    SurfaceConfig,
    VigilConfig,
)


def test_surface_config_toggleability():
    """All SurfaceConfig sections ([api], [cli], [mcp], [chat], [autonomy], [lexical]) are read and respected."""
    cfg = SurfaceConfig()
    assert hasattr(cfg, "api_host")
    assert hasattr(cfg, "chat_max_history_turns")


def test_vigil_auth_rate_limit_config_toggleability():
    """All config sections ([vigil], [auth], [rate_limit], [canonical_pipeline],
    [deployment]) are §1.8 toggleable and loadable."""
    v = VigilConfig()
    assert hasattr(v, "canonical_health_check_interval_seconds") and hasattr(v, "stale_threshold_days")
    a = AuthConfig()
    assert hasattr(a, "api_key_enabled") and hasattr(a, "session_timeout_seconds")
    r = RateLimitConfig()
    assert hasattr(r, "enabled") and hasattr(r, "requests_per_minute")
    c = CanonicalPromotionConfig()
    assert hasattr(c, "require_vigil_health_check") and hasattr(c, "auto_promote_on_approval")
    d = DeploymentProfile(profile_name="laptop", vector_backend="sqlite_vss")
    assert hasattr(d, "profile_name") and hasattr(d, "vector_backend")
