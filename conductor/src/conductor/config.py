from enum import StrEnum
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthMode(StrEnum):
    SUBSCRIPTION = "subscription"
    API_KEY = "api_key"


class EpicSignal(StrEnum):
    LABEL = "label"
    TYPE = "type"
    PARENTLESS = "parentless"


class GitHubEventMode(StrEnum):
    POLL = "poll"
    WEBHOOK = "webhook"


class NotifyMode(StrEnum):
    NTFY = "ntfy"
    SLACK = "slack"
    WEBHOOK = "webhook"
    NONE = "none"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # ── Claude authentication (exactly one) ──────────────────────────────────
    claude_code_oauth_token: str | None = None
    anthropic_api_key: str | None = None

    claude_model_engineer: str = "claude-sonnet-4-6"
    claude_model_planner: str = "claude-sonnet-4-6"
    claude_model_reviewer: str = "claude-haiku-4-5"
    claude_model_qa: str = "claude-haiku-4-5"

    # ── Plane ────────────────────────────────────────────────────────────────
    plane_base_url: str
    plane_api_key: str
    plane_workspace_slug: str
    plane_webhook_secret: str
    plane_epic_signal: EpicSignal = EpicSignal.LABEL
    plane_agents_enabled: bool = False
    plane_bot_token: str | None = None

    # ── GitHub ─────────────────────────────────────────────────────────────—
    # Per-repo routing lives in targets.yml (keyed on Plane project_id). One
    # token here whose machine account can reach every repo listed there.
    github_token: str
    github_base_branch: str = "main"
    github_event_mode: GitHubEventMode = GitHubEventMode.POLL
    github_poll_interval_seconds: int = 60
    github_webhook_secret: str | None = None

    # ── Conductor service ─────────────────────────────────────────────────────
    conductor_host: str = "0.0.0.0"
    conductor_port: int = 8420
    conductor_public_url: str = "http://localhost:8420"
    database_url: str = "sqlite+aiosqlite:////data/conductor.db"
    max_concurrent_agents: int = 1
    agent_timeout_minutes: int = 30

    # ── Agent containers ──────────────────────────────────────────────────────
    agent_image: str = "dem/agent-runner:latest"
    agent_memory_limit: str = "4g"
    agent_cpu_limit: float = 2
    docker_host: str = "unix:///var/run/docker.sock"

    # ── Observability ─────────────────────────────────────────────────────────
    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "dem"

    # ── Notifications ─────────────────────────────────────────────────────────
    notify_mode: NotifyMode = NotifyMode.NONE
    notify_ntfy_url: str | None = None
    notify_slack_webhook_url: str | None = None
    notify_webhook_url: str | None = None

    # ── Targets (project_id -> repo) ─────────────────────────────────────────
    targets_file: Path = Path("targets.yml")

    @property
    def auth_mode(self) -> AuthMode:
        return AuthMode.SUBSCRIPTION if self.claude_code_oauth_token else AuthMode.API_KEY

    @model_validator(mode="after")
    def _validate_claude_auth(self) -> "Settings":
        has_oauth = bool(self.claude_code_oauth_token)
        has_api_key = bool(self.anthropic_api_key)
        if has_oauth and has_api_key:
            raise ValueError(
                "Both CLAUDE_CODE_OAUTH_TOKEN and ANTHROPIC_API_KEY are set. Set exactly one "
                "(subscription OR API key) to avoid silent double-billing. Unset one and restart."
            )
        if not has_oauth and not has_api_key:
            raise ValueError(
                "No Claude credentials set. Set exactly one of CLAUDE_CODE_OAUTH_TOKEN "
                "(Pro/Max, via `claude setup-token`) or ANTHROPIC_API_KEY (console.anthropic.com)."
            )
        return self

    @model_validator(mode="after")
    def _validate_webhook_secret(self) -> "Settings":
        if self.github_event_mode is GitHubEventMode.WEBHOOK and not self.github_webhook_secret:
            raise ValueError(
                "GITHUB_EVENT_MODE=webhook requires GITHUB_WEBHOOK_SECRET. Set it, or switch to "
                "GITHUB_EVENT_MODE=poll."
            )
        return self

    @model_validator(mode="after")
    def _validate_notify(self) -> "Settings":
        required = {
            NotifyMode.NTFY: ("NOTIFY_NTFY_URL", self.notify_ntfy_url),
            NotifyMode.SLACK: ("NOTIFY_SLACK_WEBHOOK_URL", self.notify_slack_webhook_url),
            NotifyMode.WEBHOOK: ("NOTIFY_WEBHOOK_URL", self.notify_webhook_url),
        }
        if self.notify_mode in required:
            var, value = required[self.notify_mode]
            if not value:
                raise ValueError(f"NOTIFY_MODE={self.notify_mode} requires {var}.")
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
