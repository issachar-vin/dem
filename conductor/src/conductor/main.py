import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI

from conductor import __version__, agent_runs, poller, scheduler, telemetry, ui
from conductor.agents.dispatcher import Dispatcher
from conductor.agents.dockerctl import default_factory
from conductor.agents.volumes import VolumeManager
from conductor.api import webhooks as webhooks_api
from conductor.auth import AuthStore
from conductor.config import BootstrapSettings, get_settings
from conductor.crypto import SecretBox
from conductor.db import create_engine, create_sessionmaker
from conductor.mappings import MappingStore
from conductor.store import ConfigStore
from conductor.tickets import TicketStore

logger = logging.getLogger("conductor")


def _seed_env(seed_file: Path | None) -> Mapping[str, str]:
    """Env vars overlaid on an optional YAML seed file. Real env wins over the file."""
    seeded: dict[str, str] = {}
    if seed_file and seed_file.exists():
        data = yaml.safe_load(seed_file.read_text()) or {}
        seeded = {str(k).upper(): str(v) for k, v in data.items() if v is not None}
    return {**seeded, **os.environ}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: BootstrapSettings = app.state.settings

    engine = create_engine(settings.database_url)
    app.state.engine = engine
    sessionmaker = create_sessionmaker(engine)
    app.state.sessionmaker = sessionmaker

    box = SecretBox(settings.dem_secret_key)
    store = ConfigStore(sessionmaker, box)
    app.state.store = store
    seeded = await store.seed_from_env(
        _seed_env(settings.config_seed_file), reseed=settings.reseed_from_env
    )
    logger.info("Seeded %d config value(s) from env/seed file", seeded)

    auth = AuthStore(sessionmaker)
    app.state.auth = auth

    mappings = MappingStore(sessionmaker, box)
    app.state.mappings = mappings

    docker_factory = default_factory((await store.resolved()).get("docker_host"))
    ui.configure(
        store=store,
        mappings=mappings,
        auth=auth,
        settings=settings,
        sessionmaker=sessionmaker,
        docker_factory=docker_factory,
    )
    if settings.targets_file:
        imported = await mappings.import_targets(
            settings.targets_file, reseed=settings.reseed_from_env
        )
        logger.info("Seeded %d project mapping(s) from %s", imported, settings.targets_file)

    issues = (await store.status()).issues
    if issues:
        logger.warning("App config incomplete: %s", "; ".join(issues))

    poll_task = await poller.start_if_enabled(
        store=store, mappings=mappings, sessionmaker=sessionmaker
    )

    resolved = await store.resolved()

    dispatcher = Dispatcher(
        store=store,
        docker_factory=docker_factory,
        max_concurrent=_int(resolved.get("max_concurrent_agents"), 1),
        recorder=agent_runs.DbRunRecorder(sessionmaker),
    )
    sched = scheduler.Scheduler(
        sessionmaker=sessionmaker,
        store=store,
        mappings=mappings,
        tickets=TicketStore(sessionmaker),
        dispatcher=dispatcher,
        volumes=VolumeManager(store=store, docker_factory=docker_factory),
    )
    sched_task = await scheduler.start(sched)

    telemetry.build_info.labels(version=__version__).set(1)
    try:
        yield
    finally:
        await scheduler.stop(sched_task)
        await poller.stop(poll_task)
        await engine.dispose()


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value else default
    except ValueError:
        return default


def create_app(settings: BootstrapSettings | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    # LOG_LEVEL raises only the conductor logger (e.g. DEBUG surfaces raw webhook payloads via
    # api/webhooks._log_delivery) — root stays INFO so aiosqlite/sqlalchemy don't flood the logs.
    logging.getLogger("conductor").setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    settings = settings or get_settings()
    app = FastAPI(title="conductor", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.include_router(telemetry.router)
    app.include_router(webhooks_api.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Mount last so the routes above take precedence over NiceGUI's root mount.
    ui.setup(app, storage_secret=settings.dem_secret_key)
    return app


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.conductor_host, port=settings.conductor_port)


if __name__ == "__main__":
    main()
