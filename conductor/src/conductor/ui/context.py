from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.agents.dockerctl import DockerFactory
from conductor.auth import AuthStore
from conductor.config import BootstrapSettings
from conductor.mappings import MappingStore
from conductor.store import ConfigStore


@dataclass
class AppContext:
    """In-process handles the NiceGUI pages call directly. Populated from the FastAPI lifespan.

    NiceGUI is mounted as a sub-application and does not share the parent app's `app.state`, so the
    UI reaches the stores through this module-level singleton instead of `request.app.state`."""

    store: ConfigStore
    mappings: MappingStore
    auth: AuthStore
    settings: BootstrapSettings
    sessionmaker: async_sessionmaker[AsyncSession]
    docker_factory: DockerFactory


_context: AppContext | None = None


def configure(
    *,
    store: ConfigStore,
    mappings: MappingStore,
    auth: AuthStore,
    settings: BootstrapSettings,
    sessionmaker: async_sessionmaker[AsyncSession],
    docker_factory: DockerFactory,
) -> None:
    global _context
    _context = AppContext(
        store=store,
        mappings=mappings,
        auth=auth,
        settings=settings,
        sessionmaker=sessionmaker,
        docker_factory=docker_factory,
    )


def get_context() -> AppContext:
    if _context is None:
        raise RuntimeError("UI context not configured; call configure() in the app lifespan.")
    return _context
