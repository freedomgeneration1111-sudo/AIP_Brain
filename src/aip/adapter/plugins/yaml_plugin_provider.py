"""YamlPluginProvider — PluginProvider implementation driven by YAML config.

API keys come ONLY from environment variables (api_key_env name in YAML).
In CI / deterministic mode returns fixture response (no network).
Uses httpx for async calls when real execution is possible.
"""

from __future__ import annotations

import os
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

from aip.foundation.protocols import PluginProvider


class YamlPluginProvider(PluginProvider):
    """Model provider backed by a YAML configuration file.

    Security: Never stores or logs the actual API key. YAML contains only the
    *name* of the env var (e.g. CUSTOM_PROVIDER_API_KEY).
    """

    def __init__(self, config_path: str) -> None:
        self.config_path = config_path
        self._config: dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        import yaml  # local import so optional dep doesn't break base env

        with open(self.config_path, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f) or {}

    def get_slot_name(self) -> str:
        return self._config.get("slot_name", "unknown")

    def get_provider_name(self) -> str:
        return self._config.get("provider_name", "yaml-plugin")

    async def health_check(self) -> dict:
        if os.environ.get("AIP_CI_MODE") or os.environ.get("CI"):
            return {"status": "ok", "mode": "ci-fixture"}
        if httpx is None:
            return {"status": "degraded", "reason": "httpx not available"}
        try:
            # Minimal prompt
            prompt = self._config.get("health_check_prompt", "Respond with OK")
            resp = await self._call_impl(prompt, {"max_tokens": 8})
            return {"status": "ok", "response": resp[:100]}
        except Exception as e:
            return {"status": "error", "reason": str(e)}

    async def call_model(self, prompt: str, config: dict) -> str:
        """Call the remote provider or return deterministic fixture in CI mode."""
        if os.environ.get("AIP_CI_MODE") or os.environ.get("CI"):
            # Deterministic fixture response (no network)
            return f"[CI-FIXTURE] Echo: {prompt[:120]}..."

        if httpx is None:
            raise RuntimeError("httpx is required for real YamlPluginProvider calls")

        return await self._call_impl(prompt, config)

    async def _call_impl(self, prompt: str, config: dict) -> str:
        base_url = self._config["base_url"].rstrip("/")
        model = self._config["model"]
        api_key_env = self._config.get("api_key_env")
        api_key = os.environ.get(api_key_env) if api_key_env else None

        if not api_key:
            raise RuntimeError(f"Missing API key in env var: {api_key_env}")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            **self._config.get("parameters", {}),
            **config,
        }

        url = f"{base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]
