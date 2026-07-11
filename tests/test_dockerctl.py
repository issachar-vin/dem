import pytest

from conductor.agents.dockerctl import ContainerFailed, ContainerTimeout, run_container

from agents_fakes import FakeContainer, FakeDocker


async def test_success_returns_stdout_and_removes() -> None:
    container = FakeContainer(stdout=b"hello")
    docker = FakeDocker(container)
    out = await run_container(docker, image="img", command=["echo"], name="c1")
    assert out == "hello"
    assert container.removed is True


async def test_run_passes_limits_and_binds() -> None:
    docker = FakeDocker()
    await run_container(
        docker,
        image="img",
        command=["true"],
        name="c1",
        volumes={"vol": "/work"},
        user="root",
        mem_limit="4g",
        nano_cpus=2_000_000_000,
    )
    _, command, kwargs = docker.containers.calls[0]
    assert command == ["true"]
    assert kwargs["volumes"] == {"vol": {"bind": "/work", "mode": "rw"}}
    assert kwargs["user"] == "root"
    assert kwargs["mem_limit"] == "4g"
    assert kwargs["nano_cpus"] == 2_000_000_000


async def test_nonzero_exit_raises_container_failed() -> None:
    container = FakeContainer(exit_code=1, stderr=b"boom")
    with pytest.raises(ContainerFailed) as exc:
        await run_container(
            FakeDocker(container), image="img", command=["x"], name="c1"
        )
    assert exc.value.exit_code == 1
    assert "boom" in exc.value.logs
    assert container.removed is True


async def test_timeout_kills_and_raises() -> None:
    container = FakeContainer(wait_delay=0.3)
    with pytest.raises(ContainerTimeout):
        await run_container(
            FakeDocker(container), image="img", command=["x"], name="c1", timeout=0.01
        )
    assert container.killed is True
    assert container.removed is True


class _KContainer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.killed = False

    def kill(self) -> None:
        self.killed = True


class _KContainers:
    def __init__(self, containers: list[_KContainer]) -> None:
        self._containers = containers

    def run(self, image: str, command: list[str], **kwargs: object) -> _KContainer:
        raise NotImplementedError

    def list(self, **kwargs: object) -> list[_KContainer]:
        return self._containers  # kill_containers applies its own psa-*-<id> guard


class _KDocker:
    def __init__(self, containers: list[_KContainer]) -> None:
        self.containers = _KContainers(containers)


async def test_kill_containers_kills_only_matching_shape() -> None:
    from conductor.agents.dockerctl import kill_containers

    containers = [
        _KContainer("psa-engineer-T1"),
        _KContainer("psa-clone-T1"),
        _KContainer("psa-reviewer-T2"),  # different ticket
        _KContainer("other-T1"),  # not a psa- container
        _KContainer("psa-engineer-T1x"),  # doesn't end with the id
    ]
    killed = await kill_containers(_KDocker(containers), "T1")  # type: ignore[arg-type]
    assert set(killed) == {"psa-engineer-T1", "psa-clone-T1"}
    assert [c.name for c in containers if c.killed] == [
        "psa-engineer-T1",
        "psa-clone-T1",
    ]
