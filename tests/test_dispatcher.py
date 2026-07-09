import json

from conductor.agents.dispatcher import AgentRun, Dispatcher
from conductor.agents.roles import AgentRole
from conductor.store import ConfigStore

from agents_fakes import FakeContainer, FakeDocker


def _run(**overrides: object) -> AgentRun:
    kwargs: dict[str, object] = {
        "role": AgentRole.ENGINEER,
        "ticket_id": "T-1",
        "prompt": "build it",
        "model": "claude-x",
    }
    kwargs.update(overrides)
    return AgentRun(**kwargs)  # type: ignore[arg-type]


async def _dispatch(store: ConfigStore, docker: FakeDocker, run: AgentRun) -> object:
    return await Dispatcher(store=store, docker_factory=lambda: docker).run(run)


async def test_run_parses_envelope(store: ConfigStore) -> None:
    await store.set_secret("claude_code_oauth_token", "sk-oat")
    container = FakeContainer(
        stdout=json.dumps({"session_id": "s9", "result": "ok"}).encode()
    )
    docker = FakeDocker(container)
    env = await _dispatch(store, docker, _run())
    assert env.session_id == "s9"  # type: ignore[attr-defined]


async def test_run_builds_command_and_volumes(store: ConfigStore) -> None:
    await store.set_secret("claude_code_oauth_token", "sk-oat")
    docker = FakeDocker(FakeContainer(stdout=b'{"session_id":"s"}'))
    await _dispatch(store, docker, _run())
    _, command, kwargs = docker.containers.calls[0]
    assert command == [
        "claude",
        "-p",
        "build it",
        "--output-format",
        "json",
        "--model",
        "claude-x",
    ]
    assert kwargs["name"] == "psa-engineer-T-1"
    assert kwargs["volumes"] == {
        "psa-repo-T-1": {"bind": "/work", "mode": "rw"},
        "psa-claude-T-1": {"bind": "/home/agent/.claude", "mode": "rw"},
    }
    assert kwargs["environment"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-oat"


async def test_resume_inserts_flag(store: ConfigStore) -> None:
    await store.set_secret("claude_code_oauth_token", "sk-oat")
    docker = FakeDocker(FakeContainer(stdout=b'{"session_id":"s"}'))
    await _dispatch(store, docker, _run(resume_session_id="prev"))
    _, command, _ = docker.containers.calls[0]
    assert command[:4] == ["claude", "-p", "--resume", "prev"]


async def test_otel_env_when_configured(store: ConfigStore) -> None:
    await store.set_secret("claude_code_oauth_token", "sk-oat")
    await store.set_setting("otel_exporter_otlp_endpoint", "http://collector:4317")
    docker = FakeDocker(FakeContainer(stdout=b'{"session_id":"s"}'))
    await _dispatch(store, docker, _run(loop_round=2))
    _, _, kwargs = docker.containers.calls[0]
    attrs = kwargs["environment"]["OTEL_RESOURCE_ATTRIBUTES"]
    assert "agent.role=engineer" in attrs
    assert "ticket.id=T-1" in attrs
    assert "loop.round=2" in attrs


async def test_no_otel_env_without_endpoint(store: ConfigStore) -> None:
    await store.set_secret("claude_code_oauth_token", "sk-oat")
    docker = FakeDocker(FakeContainer(stdout=b'{"session_id":"s"}'))
    await _dispatch(store, docker, _run())
    _, _, kwargs = docker.containers.calls[0]
    assert "OTEL_RESOURCE_ATTRIBUTES" not in kwargs["environment"]
