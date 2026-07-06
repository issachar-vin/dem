from dataclasses import dataclass, field
from enum import StrEnum


class ConfigStep(StrEnum):
    CLAUDE = "claude"
    PLANE = "plane"
    GITHUB = "github"
    NOTIFICATIONS = "notifications"
    ADVANCED = "advanced"


# Steps that support a live connection test.
VERIFIABLE_STEPS = {ConfigStep.CLAUDE, ConfigStep.PLANE, ConfigStep.GITHUB}


@dataclass(frozen=True)
class ConfigField:
    name: str
    step: ConfigStep
    secret: bool = False
    required: bool = False
    default: str | None = None
    help: str = ""
    choices: tuple[str, ...] = field(default_factory=tuple)

    @property
    def env(self) -> str:
        return self.name.upper()


CATALOG: tuple[ConfigField, ...] = (
    # ── Claude (exactly one credential; enforced by step completion, not `required`) ──
    ConfigField(
        "claude_code_oauth_token",
        ConfigStep.CLAUDE,
        secret=True,
        help="Pro/Max subscription token from `claude setup-token`.",
    ),
    ConfigField(
        "anthropic_api_key",
        ConfigStep.CLAUDE,
        secret=True,
        help="Anthropic API key (metered). Set this OR the subscription token, not both.",
    ),
    ConfigField("claude_model_engineer", ConfigStep.CLAUDE, default="claude-sonnet-4-6"),
    ConfigField("claude_model_planner", ConfigStep.CLAUDE, default="claude-sonnet-4-6"),
    ConfigField("claude_model_reviewer", ConfigStep.CLAUDE, default="claude-haiku-4-5"),
    ConfigField("claude_model_qa", ConfigStep.CLAUDE, default="claude-haiku-4-5"),
    # ── Plane ──
    ConfigField(
        "plane_base_url",
        ConfigStep.PLANE,
        required=True,
        default="https://api.plane.so",
        help="Cloud: https://api.plane.so. Self-hosted: your instance base URL.",
    ),
    ConfigField("plane_workspace_slug", ConfigStep.PLANE, required=True),
    ConfigField(
        "plane_api_key",
        ConfigStep.PLANE,
        secret=True,
        required=True,
        help="Plane Personal Access Token.",
    ),
    ConfigField(
        "plane_webhook_secret",
        ConfigStep.PLANE,
        secret=True,
        required=True,
        help="Secret Plane generates when you create the webhook.",
    ),
    ConfigField(
        "plane_epic_signal",
        ConfigStep.PLANE,
        default="label",
        choices=("label", "type", "parentless"),
    ),
    ConfigField("plane_agents_enabled", ConfigStep.PLANE, default="false"),
    ConfigField("plane_bot_token", ConfigStep.PLANE, secret=True),
    # ── GitHub ──
    ConfigField(
        "github_token",
        ConfigStep.GITHUB,
        secret=True,
        required=True,
        help="Machine-account fine-grained PAT with access to all target repos.",
    ),
    ConfigField("github_base_branch", ConfigStep.GITHUB, default="main"),
    ConfigField(
        "github_event_mode", ConfigStep.GITHUB, default="poll", choices=("poll", "webhook")
    ),
    ConfigField(
        "github_webhook_secret",
        ConfigStep.GITHUB,
        secret=True,
        help="Required when github_event_mode is webhook.",
    ),
    ConfigField("github_poll_interval_seconds", ConfigStep.GITHUB, default="60"),
    # ── Notifications ──
    ConfigField(
        "notify_mode",
        ConfigStep.NOTIFICATIONS,
        default="none",
        choices=("ntfy", "slack", "webhook", "none"),
    ),
    ConfigField("notify_ntfy_url", ConfigStep.NOTIFICATIONS, secret=True),
    ConfigField("notify_slack_webhook_url", ConfigStep.NOTIFICATIONS, secret=True),
    ConfigField("notify_webhook_url", ConfigStep.NOTIFICATIONS, secret=True),
    # ── Advanced ──
    ConfigField("conductor_public_url", ConfigStep.ADVANCED, default="http://localhost:8420"),
    ConfigField("max_concurrent_agents", ConfigStep.ADVANCED, default="1"),
    ConfigField("agent_timeout_minutes", ConfigStep.ADVANCED, default="30"),
    ConfigField("agent_image", ConfigStep.ADVANCED, default="dem/agent-runner:latest"),
    ConfigField("agent_memory_limit", ConfigStep.ADVANCED, default="4g"),
    ConfigField("agent_cpu_limit", ConfigStep.ADVANCED, default="2"),
    ConfigField("docker_host", ConfigStep.ADVANCED, default="unix:///var/run/docker.sock"),
    ConfigField("otel_enabled", ConfigStep.ADVANCED, default="true"),
    ConfigField("otel_exporter_otlp_endpoint", ConfigStep.ADVANCED),
    ConfigField("otel_service_name", ConfigStep.ADVANCED, default="dem"),
)

BY_NAME: dict[str, ConfigField] = {f.name: f for f in CATALOG}
SECRET_NAMES: frozenset[str] = frozenset(f.name for f in CATALOG if f.secret)


def defaults() -> dict[str, str]:
    return {f.name: f.default for f in CATALOG if f.default is not None}


def _conditionally_required(resolved: dict[str, str]) -> set[str]:
    req = {f.name for f in CATALOG if f.required}
    if resolved.get("github_event_mode", "poll") == "webhook":
        req.add("github_webhook_secret")
    notify_url = {
        "ntfy": "notify_ntfy_url",
        "slack": "notify_slack_webhook_url",
        "webhook": "notify_webhook_url",
    }.get(resolved.get("notify_mode", "none"))
    if notify_url:
        req.add(notify_url)
    return req


def _claude_ok(resolved: dict[str, str]) -> bool:
    has_oauth = bool(resolved.get("claude_code_oauth_token"))
    has_api_key = bool(resolved.get("anthropic_api_key"))
    return has_oauth != has_api_key  # exactly one


def validate_config(resolved: dict[str, str]) -> list[str]:
    """Human-readable problems with a resolved config. Empty list means valid."""
    issues: list[str] = []

    has_oauth = bool(resolved.get("claude_code_oauth_token"))
    has_api_key = bool(resolved.get("anthropic_api_key"))
    if has_oauth and has_api_key:
        issues.append(
            "Both CLAUDE_CODE_OAUTH_TOKEN and ANTHROPIC_API_KEY are set. Set exactly one."
        )
    elif not has_oauth and not has_api_key:
        issues.append(
            "No Claude credential set (need CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY)."
        )

    for name in sorted(_conditionally_required(resolved)):
        if not resolved.get(name):
            issues.append(f"Missing required config: {name.upper()}.")
    return issues


@dataclass(frozen=True)
class StepStatus:
    step: ConfigStep
    complete: bool
    missing: list[str]
    verifiable: bool


def step_status(resolved: dict[str, str]) -> list[StepStatus]:
    required = _conditionally_required(resolved)
    statuses: list[StepStatus] = []
    for step in ConfigStep:
        names = [f.name for f in CATALOG if f.step is step]
        missing = [n for n in names if n in required and not resolved.get(n)]
        if step is ConfigStep.CLAUDE:
            complete = _claude_ok(resolved)
            if not complete:
                missing = ["claude_code_oauth_token_or_anthropic_api_key"]
        else:
            complete = not missing
        statuses.append(
            StepStatus(
                step=step, complete=complete, missing=missing, verifiable=step in VERIFIABLE_STEPS
            )
        )
    return statuses
