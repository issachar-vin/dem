from collections.abc import AsyncIterator, Callable

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from conductor import verify
from conductor.api import config as config_api
from conductor.store import ConfigStore
from conductor.verify import VerifyResult

EnvFactory = Callable[..., dict[str, str]]


@pytest_asyncio.fixture
async def api(store: ConfigStore) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.store = store
    app.include_router(config_api.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_list_config_masks_secrets(
    api: httpx.AsyncClient, store: ConfigStore, make_env: EnvFactory
) -> None:
    await store.seed_from_env(make_env(), reseed=False)
    resp = await api.get("/api/config")
    assert resp.status_code == 200
    assert "plane_api_test" not in resp.text


async def test_status_endpoint(api: httpx.AsyncClient) -> None:
    resp = await api.get("/api/config/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["complete"] is False
    assert {"step", "complete", "missing", "verifiable"} <= body["steps"][0].keys()


async def test_put_secret_ok_then_persisted(
    api: httpx.AsyncClient, store: ConfigStore
) -> None:
    resp = await api.put("/api/config/secret/plane_api_key", json={"value": "abc"})
    assert resp.status_code == 200
    assert await store.get_secret("plane_api_key") == "abc"


async def test_put_secret_unknown_name_404(api: httpx.AsyncClient) -> None:
    resp = await api.put("/api/config/secret/plane_base_url", json={"value": "x"})
    assert resp.status_code == 404


async def test_put_setting_rejects_bad_choice(api: httpx.AsyncClient) -> None:
    resp = await api.put("/api/config/setting/github_event_mode", json={"value": "carrier-pigeon"})
    assert resp.status_code == 422


async def test_put_setting_secret_name_404(api: httpx.AsyncClient) -> None:
    resp = await api.put("/api/config/setting/plane_api_key", json={"value": "x"})
    assert resp.status_code == 404


async def test_test_endpoint_wires_to_verify(
    api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(**_: object) -> VerifyResult:
        return VerifyResult(True, "stubbed")

    monkeypatch.setattr(verify, "verify_github", fake)
    resp = await api.post("/api/config/test/github")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "detail": "stubbed"}


async def test_test_endpoint_unknown_service_404(api: httpx.AsyncClient) -> None:
    resp = await api.post("/api/config/test/pigeon")
    assert resp.status_code == 404


async def test_export_env_is_plaintext(
    api: httpx.AsyncClient, store: ConfigStore, make_env: EnvFactory
) -> None:
    await store.seed_from_env(make_env(), reseed=False)
    resp = await api.get("/api/config/export.env")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "PLANE_API_KEY=plane_api_test" in resp.text


async def test_export_then_import_bundle(
    api: httpx.AsyncClient, store: ConfigStore, make_env: EnvFactory
) -> None:
    await store.seed_from_env(make_env(), reseed=False)
    exported = await api.post("/api/config/export-bundle", json={"passphrase": "pw"})
    blob_b64 = exported.json()["blob_b64"]

    good = await api.post(
        "/api/config/import-bundle", json={"blob_b64": blob_b64, "passphrase": "pw"}
    )
    assert good.status_code == 200
    assert good.json()["imported"] > 0


async def test_import_bundle_wrong_passphrase_400(
    api: httpx.AsyncClient, store: ConfigStore, make_env: EnvFactory
) -> None:
    await store.seed_from_env(make_env(), reseed=False)
    exported = await api.post("/api/config/export-bundle", json={"passphrase": "pw"})
    blob_b64 = exported.json()["blob_b64"]

    bad = await api.post(
        "/api/config/import-bundle", json={"blob_b64": blob_b64, "passphrase": "nope"}
    )
    assert bad.status_code == 400
