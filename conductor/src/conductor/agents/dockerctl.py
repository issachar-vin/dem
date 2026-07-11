"""The conductor's whole Docker surface, behind Protocols so the dispatcher and volume manager
depend on an abstraction (and tests inject a fake) rather than the concrete docker SDK. The SDK is
synchronous; every blocking call is pushed to a thread so it never stalls the event loop."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from typing import Any, Protocol, cast

logger = logging.getLogger("conductor")

# When streaming a run's logs live, batch the appends: flush accumulated lines at most this often so
# a chatty agent (the stream-json spam) can't turn into a write per event.
_FLUSH_INTERVAL_SECONDS = 1.0


class Container(Protocol):
    name: str

    def wait(self) -> dict[str, Any]: ...
    # stream=True + follow=True returns a blocking byte-chunk iterator (live logs); otherwise bytes.
    def logs(
        self, *, stdout: bool = ..., stderr: bool = ..., stream: bool = ..., follow: bool = ...
    ) -> Any: ...
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
    on_output: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Run a container to completion and return its stdout. Non-zero exit → `ContainerFailed`;
    exceeding `timeout` → kill + `ContainerTimeout`. The container is always removed afterward
    (we manage removal by hand rather than `--rm` so logs survive a timeout kill).

    If `on_output` is given, the container's stdout is *followed* live and flushed to it in batches
    as it arrives (so the console can watch a run in progress) instead of read once at the end."""
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
        if on_output is None:
            try:
                result = await asyncio.wait_for(asyncio.to_thread(container.wait), timeout)
            except TimeoutError:
                await asyncio.to_thread(container.kill)
                raise ContainerTimeout(name, timeout or 0.0) from None
            raw = await asyncio.to_thread(container.logs, stdout=True, stderr=False)
            stdout = cast(bytes, raw).decode()
        else:
            stdout, timed_out = await _follow_stdout(container, on_output, timeout)
            if timed_out:
                raise ContainerTimeout(name, timeout or 0.0) from None
            result = await asyncio.to_thread(container.wait)  # already exited; returns at once
        exit_code = int(result.get("StatusCode", 0))
        if exit_code != 0:
            stderr = (await asyncio.to_thread(container.logs, stdout=False, stderr=True)).decode()
            raise ContainerFailed(name, exit_code, stderr or stdout)
        return stdout
    finally:
        try:
            await asyncio.to_thread(container.remove, force=True)
        except Exception:  # cleanup must not mask the real result/error
            logger.warning("Failed to remove container %s", name, exc_info=True)


async def _follow_stdout(
    container: Container,
    on_output: Callable[[str], Awaitable[None]],
    timeout: float | None,
) -> tuple[str, bool]:
    """Stream the container's stdout, flushing to `on_output` on `_FLUSH_INTERVAL_SECONDS`, and
    return (full stdout, timed_out). On timeout the container is killed so the log iterator ends and
    the pump thread can finish; the caller turns `timed_out` into a `ContainerTimeout`."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def _pump() -> None:
        stream = cast(
            Iterator[bytes],
            container.logs(stdout=True, stderr=False, stream=True, follow=True),
        )
        try:
            for chunk in stream:
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel: stream ended

    pump = asyncio.create_task(asyncio.to_thread(_pump))
    collected: list[str] = []
    pending: list[str] = []
    last_flush = loop.time()
    deadline = None if timeout is None else loop.time() + timeout
    timed_out = False

    async def flush() -> None:
        nonlocal last_flush
        if pending:
            await on_output("".join(pending))
            pending.clear()
            last_flush = loop.time()

    try:
        while True:
            wait_for = None if deadline is None else max(0.0, deadline - loop.time())
            try:
                chunk = await asyncio.wait_for(queue.get(), wait_for)
            except TimeoutError:
                timed_out = True
                break
            if chunk is None:
                break
            text = chunk.decode(errors="replace")
            collected.append(text)
            pending.append(text)
            if loop.time() - last_flush >= _FLUSH_INTERVAL_SECONDS:
                await flush()
    finally:
        await flush()
    if timed_out:
        await asyncio.to_thread(container.kill)  # unblock the iterator so the pump can exit
    await pump
    return "".join(collected), timed_out
