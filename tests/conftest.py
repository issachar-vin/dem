import os
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from conductor.auth import AuthStore
from conductor.crypto import SecretBox, generate_key
from conductor.db import Base, create_sessionmaker
from conductor.mappings import MappingStore
from conductor.store import ConfigStore

# Env vars that could leak in from the host and make config tests non-deterministic.
_MANAGED_ENV_PREFIXES = (
    "CLAUDE_",
    "ANTHROPIC_",
    "PLANE_",
    "GITHUB_",
    "OTEL_",
    "NOTIFY_",
)
_MANAGED_ENV_EXACT = (
    "DATABASE_URL",
    "TARGETS_FILE",
    "DOCKER_HOST",
    "CONDUCTOR_PUBLIC_URL",
    "DEM_SECRET_KEY",
    "RESEED_FROM_ENV",
    "CONFIG_SEED_FILE",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(_MANAGED_ENV_PREFIXES) or key in _MANAGED_ENV_EXACT:
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def secret_key() -> str:
    return generate_key()


@pytest.fixture
def box(secret_key: str) -> SecretBox:
    return SecretBox(secret_key)


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # StaticPool keeps a single shared connection so the in-memory DB survives across sessions.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_sessionmaker(engine)
    await engine.dispose()


@pytest_asyncio.fixture
async def store(
    sessionmaker: async_sessionmaker[AsyncSession], box: SecretBox
) -> ConfigStore:
    return ConfigStore(sessionmaker, box)


@pytest_asyncio.fixture
async def mappings(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> MappingStore:
    return MappingStore(sessionmaker)


@pytest_asyncio.fixture
async def auth(
    sessionmaker: async_sessionmaker[AsyncSession], secret_key: str
) -> AuthStore:
    return AuthStore(sessionmaker, secret_key)


@pytest_asyncio.fixture
async def auth_token(auth: AuthStore) -> str:
    await auth.create_admin("admin", "pw")
    return auth.issue_token("admin")


@pytest.fixture
def make_env() -> Callable[..., dict[str, str]]:
    """A minimally-complete set of app config env vars (UPPER_CASE, as seeded from the environment)."""

    def _make(**overrides: str | None) -> dict[str, str]:
        env: dict[str, str | None] = {
            "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-test",
            "PLANE_BASE_URL": "https://plane.example.com",
            "PLANE_API_KEY": "plane_api_test",
            "PLANE_WORKSPACE_SLUG": "dem",
            "PLANE_WEBHOOK_SECRET": "whsec_test",
            "GITHUB_TOKEN": "github_pat_test",
            "GITHUB_EVENT_MODE": "poll",
        }
        env.update(overrides)
        return {k: v for k, v in env.items() if v is not None}

    return _make
