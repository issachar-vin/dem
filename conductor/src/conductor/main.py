import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI

from conductor import __version__, telemetry
from conductor.api import auth as auth_api
from conductor.api import config as config_api
from conductor.api import mappings as mappings_api
from conductor.api import webhooks as webhooks_api
from conductor.auth import AuthStore
from conductor.config import BootstrapSettings, get_settings
from conductor.crypto import SecretBox
from conductor.db import create_engine, create_sessionmaker
from conductor.mappings import MappingStore
from conductor.store import ConfigStore

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

    store = ConfigStore(sessionmaker, SecretBox(settings.dem_secret_key))
    app.state.store = store
    seeded = await store.seed_from_env(
        _seed_env(settings.config_seed_file), reseed=settings.reseed_from_env
    )
    logger.info("Seeded %d config value(s) from env/seed file", seeded)

    app.state.auth = AuthStore(sessionmaker, settings.dem_secret_key)

    mappings = MappingStore(sessionmaker)
    app.state.mappings = mappings
    if settings.targets_file:
        imported = await mappings.import_targets(
            settings.targets_file, reseed=settings.reseed_from_env
        )
        logger.info("Seeded %d project mapping(s) from %s", imported, settings.targets_file)

    issues = (await store.status())["issues"]
    if issues:
        logger.warning("App config incomplete: %s", "; ".join(issues))

    telemetry.build_info.labels(version=__version__).set(1)
    try:
        yield
    finally:
        await engine.dispose()


def create_app(settings: BootstrapSettings | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    settings = settings or get_settings()
    app = FastAPI(title="conductor", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.include_router(telemetry.router)
    app.include_router(auth_api.router)
    app.include_router(config_api.router)
    app.include_router(mappings_api.router)
    app.include_router(webhooks_api.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.conductor_host, port=settings.conductor_port)


if __name__ == "__main__":
    main()
