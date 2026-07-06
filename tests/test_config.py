from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from conductor.config import AuthMode, Settings

EnvFactory = Callable[..., dict[str, Any]]


def _settings(env: dict[str, Any]) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


def test_subscription_auth_mode(make_env: EnvFactory) -> None:
    assert _settings(make_env()).auth_mode is AuthMode.SUBSCRIPTION


def test_api_key_auth_mode(make_env: EnvFactory) -> None:
    env = make_env(claude_code_oauth_token=None, anthropic_api_key="sk-ant-api03-test")
    assert _settings(env).auth_mode is AuthMode.API_KEY


def test_both_credentials_rejected(make_env: EnvFactory) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        _settings(make_env(anthropic_api_key="sk-ant-api03-test"))


def test_no_credentials_rejected(make_env: EnvFactory) -> None:
    with pytest.raises(ValidationError, match="No Claude credentials"):
        _settings(make_env(claude_code_oauth_token=None))


def test_missing_required_var_names_it(make_env: EnvFactory) -> None:
    env = make_env()
    del env["plane_api_key"]
    with pytest.raises(ValidationError, match="plane_api_key"):
        _settings(env)


def test_webhook_mode_requires_secret(make_env: EnvFactory) -> None:
    with pytest.raises(ValidationError, match="GITHUB_WEBHOOK_SECRET"):
        _settings(make_env(github_event_mode="webhook"))


def test_webhook_mode_with_secret_ok(make_env: EnvFactory) -> None:
    env = make_env(github_event_mode="webhook", github_webhook_secret="ghwh_test")
    assert _settings(env).github_webhook_secret == "ghwh_test"


def test_ntfy_notify_requires_url(make_env: EnvFactory) -> None:
    with pytest.raises(ValidationError, match="NOTIFY_NTFY_URL"):
        _settings(make_env(notify_mode="ntfy"))
