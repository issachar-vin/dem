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
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
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


class _SeqContainers:
    """A container collection that returns a different container per run — lets a test drive the
    re-prompt path where the first agent reply is malformed and the retry is valid."""

    def __init__(self, containers: list[FakeContainer]) -> None:
        self._it = iter(containers)
        self.calls: list[tuple[str, list[str], dict[str, object]]] = []

    def run(self, image: str, command: list[str], **kwargs: object) -> FakeContainer:
        self.calls.append((image, command, kwargs))
        return next(self._it)


class _SeqDocker:
    def __init__(self, containers: list[FakeContainer]) -> None:
        self.containers = _SeqContainers(containers)
        self.volumes = FakeDocker().volumes


def _envelope(session_id: str, result: str) -> bytes:
    return json.dumps({"session_id": session_id, "result": result}).encode()


async def test_run_parsed_returns_first_valid(store: ConfigStore) -> None:
    from conductor.agents.contracts import parse_verdict

    await store.set_secret("claude_code_oauth_token", "sk-oat")
    docker = _SeqDocker([FakeContainer(stdout=_envelope("s1", '{"pass": true}'))])
    _, verdict = await Dispatcher(
        store=store, docker_factory=lambda: docker
    ).run_parsed(_run(role=AgentRole.REVIEWER), parse_verdict)
    assert verdict.passed is True
    assert len(docker.containers.calls) == 1  # no re-prompt needed


async def test_run_parsed_reprompts_once_then_succeeds(store: ConfigStore) -> None:
    from conductor.agents.contracts import parse_verdict

    await store.set_secret("claude_code_oauth_token", "sk-oat")
    docker = _SeqDocker(
        [
            FakeContainer(stdout=_envelope("s1", "not json at all")),
            FakeContainer(stdout=_envelope("s1", '{"pass": false, "findings": []}')),
        ]
    )
    _, verdict = await Dispatcher(
        store=store, docker_factory=lambda: docker
    ).run_parsed(_run(role=AgentRole.QA), parse_verdict)
    assert verdict.passed is False
    assert len(docker.containers.calls) == 2  # re-prompted once
    # The retry resumes the same session and asks for valid JSON.
    _, retry_cmd, _ = docker.containers.calls[1]
    assert retry_cmd[:4] == ["claude", "-p", "--resume", "s1"]


async def test_run_parsed_raises_after_second_malformed(store: ConfigStore) -> None:
    import pytest

    from conductor.agents.contracts import MalformedAgentOutput, parse_verdict

    await store.set_secret("claude_code_oauth_token", "sk-oat")
    docker = _SeqDocker(
        [
            FakeContainer(stdout=_envelope("s1", "garbage")),
            FakeContainer(stdout=_envelope("s1", "still garbage")),
        ]
    )
    with pytest.raises(MalformedAgentOutput):
        await Dispatcher(store=store, docker_factory=lambda: docker).run_parsed(
            _run(role=AgentRole.REVIEWER), parse_verdict
        )


class _Recorder:
    """Captures the streaming lifecycle: one started run, its appended chunks, and the finish."""

    def __init__(self) -> None:
        self.started: dict[str, object] | None = None
        self.appended: list[str] = []
        self.finished: dict[str, object] | None = None

    async def start(
        self, *, job_id: int, ticket_id: str, role: str, loop_round: int
    ) -> int:
        self.started = {
            "job_id": job_id,
            "ticket_id": ticket_id,
            "role": role,
            "loop_round": loop_round,
        }
        return 42

    async def append(self, run_id: int, chunk: str) -> None:
        assert run_id == 42
        self.appended.append(chunk)

    async def finish(self, run_id: int, *, ok: bool, output: str | None = None) -> None:
        assert run_id == 42
        self.finished = {"ok": ok, "output": output}

    @property
    def output(self) -> str:
        extra = str((self.finished or {}).get("output") or "")
        return "".join(self.appended) + extra


async def test_run_records_success_output(store: ConfigStore) -> None:
    await store.set_secret("claude_code_oauth_token", "sk-oat")
    docker = FakeDocker(FakeContainer(stdout=b'{"session_id":"s","result":"ok"}'))
    recorder = _Recorder()
    await Dispatcher(store=store, docker_factory=lambda: docker, recorder=recorder).run(
        _run(job_id=7, loop_round=2)
    )
    assert recorder.started == {
        "job_id": 7,
        "ticket_id": "T-1",
        "role": "engineer",
        "loop_round": 2,
    }
    assert recorder.finished is not None and recorder.finished["ok"] is True
    assert '"result":"ok"' in recorder.output  # streamed live via append


async def test_run_records_failure_logs_and_reraises(store: ConfigStore) -> None:
    import pytest

    from conductor.agents.dockerctl import ContainerFailed

    await store.set_secret("claude_code_oauth_token", "sk-oat")
    docker = FakeDocker(FakeContainer(exit_code=1, stderr=b"boom happened"))
    recorder = _Recorder()
    with pytest.raises(ContainerFailed):
        await Dispatcher(
            store=store, docker_factory=lambda: docker, recorder=recorder
        ).run(_run())
    assert recorder.finished is not None and recorder.finished["ok"] is False
    assert "boom happened" in recorder.output
