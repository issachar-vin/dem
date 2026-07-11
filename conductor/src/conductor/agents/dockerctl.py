"""The conductor's whole Docker surface, behind Protocols so the dispatcher and volume manager
depend on an abstraction (and tests inject a fake) rather than the concrete docker SDK. The SDK is
synchronous; every blocking call is pushed to a thread so it never stalls the event loop."""

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, cast

logger = logging.getLogger("conductor")


class Container(Protocol):
    name: str

    def wait(self) -> dict[str, Any]: ...
    def logs(self, *, stdout: bool = ..., stderr: bool = ...) -> bytes: ...
    def kill(self) -> None: ...
    def remove(self, *, force: bool = ...) -> None: ...


class ContainerCollection(Protocol):
    def run(self, image: str, command: Sequence[str], **kwargs: Any) -> Container: ...
    def list(self, **kwargs: Any) -> list[Container]: ...


class Volume(Protocol):
    def remove(self, *, force: bool = ...) -> None: ...


class VolumeCollection(Protocol):
    def create(self, name: str, **kwargs: Any) -> Volume: ...
    def get(self, name: str) -> Volume: ...


class DockerClient(Protocol):
    containers: ContainerCollection
    volumes: VolumeCollection


DockerFactory = Callable[[], DockerClient]


class ContainerFailed(Exception):
    def __init__(self, name: str, exit_code: int, logs: str) -> None:
        super().__init__(f"container {name} exited {exit_code}: {logs[-500:]}")
        self.exit_code = exit_code
        self.logs = logs


class ContainerTimeout(Exception):
    def __init__(self, name: str, timeout: float) -> None:
        super().__init__(f"container {name} exceeded its {timeout:g}s timeout and was killed")


def default_factory(docker_host: str | None) -> DockerFactory:
    def make() -> DockerClient:
        import docker

        if docker_host:
            return cast(DockerClient, docker.DockerClient(base_url=docker_host))
        return cast(DockerClient, docker.from_env())

    return make


async def kill_containers(client: DockerClient, ticket_id: str) -> list[str]:
    """Kill any live `psa-*-<ticket_id>` agent/helper containers (engineer, clone, push, diff,
    count, planner, reviewer, qa). Used to cancel a job from the console without a shell on the
    host. Best-effort: a container that vanished mid-kill is ignored. Returns the names killed."""

    def _list_and_kill() -> list[str]:
        killed: list[str] = []
        for container in client.containers.list(filters={"name": ticket_id}):
            if not (container.name.startswith("psa-") and container.name.endswith(ticket_id)):
                continue  # substring filter can over-match; require the exact psa-*-<id> shape
            try:
                container.kill()
                killed.append(container.name)
            except Exception:  # already exited / removed between list and kill
                logger.debug("Container %s not killed (likely gone)", container.name)
        return killed

    return await asyncio.to_thread(_list_and_kill)


async def run_container(
    client: DockerClient,
    *,
    image: str,
    command: Sequence[str],
    name: str,
    environment: Mapping[str, str] | None = None,
    volumes: Mapping[str, str] | None = None,
    user: str | None = None,
    entrypoint: Sequence[str] | None = None,
    mem_limit: str | None = None,
    nano_cpus: int | None = None,
    timeout: float | None = None,
) -> str:
    """Run a container to completion and return its stdout. Non-zero exit → `ContainerFailed`;
    exceeding `timeout` → kill + `ContainerTimeout`. The container is always removed afterward
    (we manage removal by hand rather than `--rm` so logs survive a timeout kill)."""
    binds = {vol: {"bind": mount, "mode": "rw"} for vol, mount in (volumes or {}).items()}

    def _start() -> Container:
        return client.containers.run(
            image,
            list(command),
            detach=True,
            remove=False,
            name=name,
            environment=dict(environment or {}),
            volumes=binds,
            user=user,
            entrypoint=list(entrypoint) if entrypoint is not None else None,
            mem_limit=mem_limit,
            nano_cpus=nano_cpus,
        )

    container = await asyncio.to_thread(_start)
    try:
        try:
            result = await asyncio.wait_for(asyncio.to_thread(container.wait), timeout)
        except TimeoutError:
            await asyncio.to_thread(container.kill)
            raise ContainerTimeout(name, timeout or 0.0) from None
        exit_code = int(result.get("StatusCode", 0))
        stdout = (await asyncio.to_thread(container.logs, stdout=True, stderr=False)).decode()
        if exit_code != 0:
            stderr = (await asyncio.to_thread(container.logs, stdout=False, stderr=True)).decode()
            raise ContainerFailed(name, exit_code, stderr or stdout)
        return stdout
    finally:
        try:
            await asyncio.to_thread(container.remove, force=True)
        except Exception:  # cleanup must not mask the real result/error
            logger.warning("Failed to remove container %s", name, exc_info=True)
