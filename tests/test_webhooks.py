import hashlib
import hmac
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor import plane
from conductor.api import webhooks as webhooks_api
from conductor.mappings import MappingStore
from conductor.models import Job
from conductor.store import ConfigStore

SECRET = "whsec_test"


@pytest_asyncio.fixture
async def api(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    await store.set_secret("plane_webhook_secret", SECRET)
    await mappings.set_project("proj-1", enabled=True)
    app = FastAPI()
    app.state.store = store
    app.state.mappings = mappings
    app.state.sessionmaker = sessionmaker
    app.include_router(webhooks_api.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _issue_body(
    *, labels: list[str], project: str = "proj-1", issue: str = "i1"
) -> bytes:
    payload = {
        "event": "issue",
        "action": "created",
        "data": {"id": issue, "project": project, "labels": labels},
    }
    return json.dumps(payload).encode()


def _headers(
    body: bytes, *, delivery: str = "d1", signed: bool = True
) -> dict[str, str]:
    h = {
        "Content-Type": "application/json",
        "X-Plane-Event": "issue",
        "X-Plane-Delivery": delivery,
    }
    if signed:
        h["X-Plane-Signature"] = _sign(body)
    return h


def _stub_epic_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"id": "L-epic", "name": "epic"}]})

    real = plane.client_from_resolved

    def fake(resolved: dict[str, str], **_: object) -> plane.PlaneClient:
        return real(
            resolved, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

    monkeypatch.setattr(webhooks_api.plane, "client_from_resolved", fake)


async def _job_count(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    async with sessionmaker() as session:
        return (
            await session.execute(select(func.count()).select_from(Job))
        ).scalar_one()


async def test_valid_epic_webhook_creates_job(
    api: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_epic_labels(monkeypatch)
    body = _issue_body(labels=["L-epic"])
    resp = await api.post("/webhooks/plane", content=body, headers=_headers(body))
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert await _job_count(sessionmaker) == 1


async def test_missing_signature_401(api: httpx.AsyncClient) -> None:
    body = _issue_body(labels=["L-epic"])
    resp = await api.post(
        "/webhooks/plane", content=body, headers=_headers(body, signed=False)
    )
    assert resp.status_code == 401


async def test_bad_signature_401(api: httpx.AsyncClient) -> None:
    body = _issue_body(labels=["L-epic"])
    headers = _headers(body)
    headers["X-Plane-Signature"] = "deadbeef"
    resp = await api.post("/webhooks/plane", content=body, headers=headers)
    assert resp.status_code == 401


async def test_replayed_delivery_id_is_deduped(
    api: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_epic_labels(monkeypatch)
    body = _issue_body(labels=["L-epic"])
    first = await api.post("/webhooks/plane", content=body, headers=_headers(body))
    second = await api.post("/webhooks/plane", content=body, headers=_headers(body))
    assert first.json()["status"] == "queued"
    assert second.json()["status"] == "duplicate"
    assert await _job_count(sessionmaker) == 1


async def test_non_epic_issue_ignored(
    api: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_epic_labels(monkeypatch)
    body = _issue_body(labels=["L-other"])
    resp = await api.post("/webhooks/plane", content=body, headers=_headers(body))
    assert resp.json()["status"] == "ignored"
    assert await _job_count(sessionmaker) == 0


async def test_unmapped_project_ignored(
    api: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    body = _issue_body(labels=["L-epic"], project="unknown")
    resp = await api.post("/webhooks/plane", content=body, headers=_headers(body))
    assert resp.json()["status"] == "ignored"
    assert await _job_count(sessionmaker) == 0


async def test_disabled_project_ignored(
    api: httpx.AsyncClient,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await mappings.set_project("proj-1", enabled=False)
    body = _issue_body(labels=["L-epic"])
    resp = await api.post("/webhooks/plane", content=body, headers=_headers(body))
    assert resp.json()["status"] == "ignored"
    assert await _job_count(sessionmaker) == 0


async def test_non_issue_event_ignored(api: httpx.AsyncClient) -> None:
    payload = {"event": "cycle", "action": "created", "data": {}}
    body = json.dumps(payload).encode()
    headers = _headers(body)
    headers["X-Plane-Event"] = "cycle"
    resp = await api.post("/webhooks/plane", content=body, headers=headers)
    assert resp.json()["status"] == "ignored"
