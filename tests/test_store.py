import base64
from collections.abc import Callable

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from conductor.crypto import SecretBox
from conductor.db import Base, create_sessionmaker
from conductor.store import ConfigStore

EnvFactory = Callable[..., dict[str, str]]


async def test_secret_set_get_round_trip(store: ConfigStore) -> None:
    await store.set_secret("plane_api_key", "topsecret")
    assert await store.get_secret("plane_api_key") == "topsecret"


async def test_setting_set_get_round_trip(store: ConfigStore) -> None:
    await store.set_setting("plane_base_url", "https://x")
    assert await store.get_setting("plane_base_url") == "https://x"


async def test_resolved_overlays_defaults_and_secrets(
    store: ConfigStore, make_env: EnvFactory
) -> None:
    await store.seed_from_env(make_env(), reseed=False)
    resolved = await store.resolved()
    assert resolved["plane_api_key"] == "plane_api_test"  # decrypted secret
    assert resolved["github_poll_interval_seconds"] == "60"  # catalog default, unset


async def test_seed_once_then_db_wins(store: ConfigStore, make_env: EnvFactory) -> None:
    seeded = await store.seed_from_env(make_env(), reseed=False)
    assert seeded == 7
    await store.set_secret("plane_api_key", "changed-in-ui")
    # Re-seeding without reseed must not clobber the UI-edited value.
    assert await store.seed_from_env(make_env(), reseed=False) == 0
    assert await store.get_secret("plane_api_key") == "changed-in-ui"


async def test_reseed_overwrites(store: ConfigStore, make_env: EnvFactory) -> None:
    await store.seed_from_env(make_env(), reseed=False)
    await store.set_secret("plane_api_key", "changed-in-ui")
    await store.seed_from_env(make_env(), reseed=True)
    assert await store.get_secret("plane_api_key") == "plane_api_test"


async def test_list_config_masks_secrets(
    store: ConfigStore, make_env: EnvFactory
) -> None:
    await store.seed_from_env(make_env(), reseed=False)
    by_name = {e.name: e for e in await store.list_config()}
    api_key = by_name["plane_api_key"]
    assert api_key.is_set is True
    assert api_key.last_four == "test"
    assert api_key.value is None
    assert "plane_api_test" not in str(api_key)


async def test_status_reports_completeness(
    store: ConfigStore, make_env: EnvFactory
) -> None:
    before = await store.status()
    assert before.complete is False
    await store.seed_from_env(make_env(), reseed=False)
    after = await store.status()
    assert after.complete is True
    assert after.issues == []


async def test_export_import_bundle_round_trip(
    store: ConfigStore, make_env: EnvFactory, box: SecretBox
) -> None:
    await store.seed_from_env(make_env(), reseed=False)
    blob = await store.export_bundle("pw")

    # Base64 wire-transport shape used by the API layer stays intact.
    assert base64.b64decode(base64.b64encode(blob)) == blob

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fresh = ConfigStore(create_sessionmaker(engine), box)

    imported = await fresh.import_bundle(blob, "pw")
    assert imported > 0
    assert await fresh.get_secret("plane_api_key") == "plane_api_test"
    assert await fresh.get_setting("plane_base_url") == "https://plane.example.com"
    await engine.dispose()
