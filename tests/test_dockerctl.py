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
