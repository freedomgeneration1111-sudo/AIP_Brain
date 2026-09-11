"""Regression coverage for Ask-page credential error handling."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_api_key_save_failure_does_not_expose_secret(monkeypatch, caplog):
    """Credential-setting errors keep the key out of logs and UI messages."""
    from gui.pages import ask

    secret = "SUPER_SECRET_SENTINEL_12345"
    notifications: list[str] = []

    class FailingApiClient:
        def set_openrouter_api_key(self, key: str) -> None:
            assert key == secret
            raise RuntimeError(f"OpenRouter rejected API key {key}")

    async def show_secret_prompt() -> str:
        return secret

    monkeypatch.setattr(ask, "show_api_key_prompt", show_secret_prompt)
    monkeypatch.setattr(ask.ui, "notify", lambda message, **_kwargs: notifications.append(str(message)))

    with caplog.at_level(logging.WARNING, logger=ask.log.name):
        await ask._prompt_openrouter_api_key(SimpleNamespace(api_client=FailingApiClient()))

    assert secret not in caplog.text
    assert all(secret not in message for message in notifications)
    assert notifications == ["API key could not be saved."]
    assert "OpenRouter API key prompt or save failed" in caplog.text
