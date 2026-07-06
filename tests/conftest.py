from collections.abc import Callable
from typing import Any

import pytest

# Env vars that could leak in from the host and make Settings tests non-deterministic.
_MANAGED_ENV_PREFIXES = ("CLAUDE_", "ANTHROPIC_", "PLANE_", "GITHUB_", "OTEL_", "NOTIFY_")
_MANAGED_ENV_EXACT = ("DATABASE_URL", "TARGETS_FILE", "DOCKER_HOST", "CONDUCTOR_PUBLIC_URL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ):
        if key.startswith(_MANAGED_ENV_PREFIXES) or key in _MANAGED_ENV_EXACT:
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def make_env() -> Callable[..., dict[str, Any]]:
    def _make(**overrides: Any) -> dict[str, Any]:
        env: dict[str, Any] = {
            "claude_code_oauth_token": "sk-ant-oat01-test",
            "plane_base_url": "https://plane.example.com",
            "plane_api_key": "plane_api_test",
            "plane_workspace_slug": "dem",
            "plane_webhook_secret": "whsec_test",
            "github_token": "github_pat_test",
            "github_event_mode": "poll",
        }
        env.update(overrides)
        return env

    return _make
