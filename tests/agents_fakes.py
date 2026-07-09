"""Fake Docker client used across the agents tests. Implements only the slice of the SDK the
conductor touches (see conductor.agents.dockerctl Protocols)."""

import time
from typing import Any


class FakeContainer:
    def __init__(
        self,
        *,
        exit_code: int = 0,
        stdout: bytes = b"{}",
        stderr: bytes = b"",
        wait_delay: float = 0.0,
    ) -> None:
        self.exit_code = exit_code
        self._stdout = stdout
        self._stderr = stderr
        self._wait_delay = wait_delay
        self.killed = False
        self.removed = False

    def wait(self) -> dict[str, Any]:
        if self._wait_delay:
            time.sleep(self._wait_delay)
        return {"StatusCode": self.exit_code}

    def logs(self, *, stdout: bool = True, stderr: bool = False) -> bytes:
        return self._stdout if stdout else self._stderr

    def kill(self) -> None:
        self.killed = True

    def remove(self, *, force: bool = False) -> None:
        self.removed = True


class FakeVolume:
    def __init__(self) -> None:
        self.removed = False

    def remove(self, *, force: bool = False) -> None:
        self.removed = True


class FakeVolumes:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.store: dict[str, FakeVolume] = {}

    def create(self, name: str, **kwargs: Any) -> FakeVolume:
        self.created.append(name)
        volume = FakeVolume()
        self.store[name] = volume
        return volume

    def get(self, name: str) -> FakeVolume:
        return self.store[name]  # KeyError mirrors docker's NotFound


class FakeContainers:
    def __init__(self, container: FakeContainer) -> None:
        self._container = container
        self.calls: list[tuple[str, list[str], dict[str, Any]]] = []

    def run(self, image: str, command: list[str], **kwargs: Any) -> FakeContainer:
        self.calls.append((image, command, kwargs))
        return self._container


class FakeDocker:
    def __init__(self, container: FakeContainer | None = None) -> None:
        self.containers = FakeContainers(container or FakeContainer())
        self.volumes = FakeVolumes()
