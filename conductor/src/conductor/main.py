import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from conductor import __version__, telemetry
from conductor.config import Settings, get_settings
from conductor.db import create_engine, create_sessionmaker
from conductor.targets import load_targets

logger = logging.getLogger("conductor")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logger.info("Claude auth mode: %s", settings.auth_mode.value.upper())

    targets = load_targets(settings.targets_file)
    app.state.targets = targets
    logger.info("Loaded %d target repo(s) from %s", len(targets), settings.targets_file)

    engine = create_engine(settings.database_url)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    telemetry.build_info.labels(version=__version__).set(1)
    try:
        yield
    finally:
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    settings = settings or get_settings()
    app = FastAPI(title="conductor", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.include_router(telemetry.router)

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
