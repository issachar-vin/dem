from conductor import catalog
from conductor.catalog import ConfigStep


def _resolved(**overrides: str) -> dict[str, str]:
    values = catalog.defaults()
    values.update(
        {
            "claude_code_oauth_token": "tok",
            "plane_base_url": "https://plane.example.com",
            "plane_workspace_slug": "dem",
            "plane_api_key": "k",
            "plane_webhook_secret": "s",
            "github_token": "g",
        }
    )
    values.update(overrides)
    return values


def test_complete_config_has_no_issues() -> None:
    # Webhook secrets are project-scoped (in the mapping store), not a global config field.
    assert catalog.validate_config(_resolved()) == []


def test_both_claude_credentials_flagged() -> None:
    issues = catalog.validate_config(_resolved(anthropic_api_key="k"))
    assert any("exactly one" in i for i in issues)


def test_no_claude_credential_flagged() -> None:
    issues = catalog.validate_config(_resolved(claude_code_oauth_token=""))
    assert any("No Claude credential" in i for i in issues)


def test_ntfy_notify_requires_url() -> None:
    issues = catalog.validate_config(_resolved(notify_mode="ntfy"))
    assert any("NOTIFY_NTFY_URL" in i for i in issues)


def test_step_status_flags_incomplete_plane() -> None:
    resolved = _resolved(plane_api_key="")
    statuses = {s.step: s for s in catalog.step_status(resolved)}
    assert statuses[ConfigStep.PLANE].complete is False
    assert "plane_api_key" in statuses[ConfigStep.PLANE].missing
    assert statuses[ConfigStep.CLAUDE].complete is True
    assert statuses[ConfigStep.CLAUDE].verifiable is True


def test_step_status_claude_needs_exactly_one_credential() -> None:
    resolved = _resolved(claude_code_oauth_token="", anthropic_api_key="")
    claude = next(
        s for s in catalog.step_status(resolved) if s.step is ConfigStep.CLAUDE
    )
    assert claude.complete is False
    assert claude.missing
