"""CHUNK-9.6 gate: Production Packaging (Docker Compose + profiles + scripts are valid and present)."""

from __future__ import annotations

import os
import yaml
from pathlib import Path


def test_docker_compose_is_valid_yaml():
    compose = Path("deploy/docker-compose.yml")
    assert compose.exists()
    with open(compose) as f:
        data = yaml.safe_load(f)
    assert "services" in data
    assert "aip" in data["services"]


def test_deploy_scripts_exist_and_are_executable():
    deploy = Path("deploy")
    for script in ["backup.sh", "restore.sh", "health-check.sh"]:
        p = deploy / script
        assert p.exists()
        # On Unix, check executable bit (in CI this is reliable)
        import stat
        mode = p.stat().st_mode
        # allow non-Unix CI where executable bit may not be set
        assert bool(mode & stat.S_IXUSR) or not hasattr(os, 'chmod')  # skip on non-Unix


def test_readme_exists():
    assert (Path("deploy") / "README.md").exists()
