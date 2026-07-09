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
from conductor.models import Job, WorkflowState
from conductor.store import ConfigStore

SECRET = "whsec_test"
GH_SECRET = "ghsec_test"
GH_REPO = "octo/backend"


@pytest_asyncio.fixture
async def api(
    store: ConfigStore,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    await store.set_secret("plane_webhook_secret", SECRET)
    await mappings.set_project("proj-1", enabled=True, webhook_secret=GH_SECRET)
    await mappings.set_repo("proj-1", "backend", github_repo=GH_REPO)
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
    *, labels: list[str], project: str = "proj-1", issue: str = "i1", state: str = ""
) -> bytes:
    payload = {
        "event": "issue",
        "action": "created",
        "data": {"id": issue, "project": project, "labels": labels, "state": state},
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


async def test_malformed_payload_400(api: httpx.AsyncClient) -> None:
    body = json.dumps({"event": "issue", "data": "not-an-object"}).encode()
    resp = await api.post("/webhooks/plane", content=body, headers=_headers(body))
    assert resp.status_code == 400


async def test_plane_non_json_body_400_not_500(api: httpx.AsyncClient) -> None:
    # A signed but non-JSON body (e.g. a webhook left on form-encoding) must be a clean 400.
    body = b"payload=%7B%22event%22%3A%22issue%22%7D"
    resp = await api.post("/webhooks/plane", content=body, headers=_headers(body))
    assert resp.status_code == 400
    assert "application/json" in resp.json()["detail"]


async def test_ready_for_dev_issue_creates_engineer_job(
    api: httpx.AsyncClient,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await mappings.set_state("proj-1", WorkflowState.READY_FOR_DEV, "state-ready")
    body = _issue_body(labels=[], state="state-ready")
    resp = await api.post("/webhooks/plane", content=body, headers=_headers(body))
    assert resp.json()["status"] == "queued"
    async with sessionmaker() as session:
        job = (await session.execute(select(Job))).scalar_one()
    assert job.payload["trigger"] == "engineer"
    assert job.dedupe_key == "proj-1:i1"


async def test_issue_not_in_ready_for_dev_ignored(
    api: httpx.AsyncClient,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await mappings.set_state("proj-1", WorkflowState.READY_FOR_DEV, "state-ready")
    body = _issue_body(labels=[], state="state-backlog")
    resp = await api.post("/webhooks/plane", content=body, headers=_headers(body))
    assert resp.json()["status"] == "ignored"
    assert await _job_count(sessionmaker) == 0


async def test_semantic_dedupe_across_deliveries(
    api: httpx.AsyncClient,
    mappings: MappingStore,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await mappings.set_state("proj-1", WorkflowState.READY_FOR_DEV, "state-ready")
    body = _issue_body(labels=[], state="state-ready")
    first = await api.post(
        "/webhooks/plane", content=body, headers=_headers(body, delivery="d1")
    )
    # A second, distinct delivery for the same issue while the first job is still active.
    second = await api.post(
        "/webhooks/plane", content=body, headers=_headers(body, delivery="d2")
    )
    assert first.json()["status"] == "queued"
    assert second.json()["status"] == "duplicate"
    assert await _job_count(sessionmaker) == 1


# ── GitHub webhook ────────────────────────────────────────────────────────────
def _gh_sign(body: bytes) -> str:
    return "sha256=" + hmac.new(GH_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _gh_body(*, action: str = "opened", repo: str = GH_REPO, number: int = 7) -> bytes:
    payload = {
        "action": action,
        "repository": {"full_name": repo},
        "pull_request": {"number": number, "state": "open", "merged": False},
    }
    return json.dumps(payload).encode()


def _gh_headers(
    body: bytes,
    *,
    event: str = "pull_request",
    delivery: str = "gh1",
    signed: bool = True,
) -> dict[str, str]:
    h = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
    }
    if signed:
        h["X-Hub-Signature-256"] = _gh_sign(body)
    return h


async def test_github_pull_request_creates_job(
    api: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    body = _gh_body()
    resp = await api.post("/webhooks/github", content=body, headers=_gh_headers(body))
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    async with sessionmaker() as session:
        job = (await session.execute(select(Job))).scalar_one()
    assert job.source == "github"
    assert job.event_type == "pull_request.opened"
    assert job.payload["project_id"] == "proj-1"
    assert job.payload["pr_number"] == 7


@pytest.mark.parametrize(
    "event",
    [
        "pull_request_review",
        "pull_request_review_comment",
        "pull_request_review_thread",
    ],
)
async def test_github_all_subscribed_events_routed(
    api: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], event: str
) -> None:
    body = _gh_body(action="submitted")
    resp = await api.post(
        "/webhooks/github", content=body, headers=_gh_headers(body, event=event)
    )
    assert resp.json()["status"] == "queued"
    assert await _job_count(sessionmaker) == 1


async def test_github_unhandled_event_ignored(
    api: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    body = _gh_body()
    resp = await api.post(
        "/webhooks/github", content=body, headers=_gh_headers(body, event="push")
    )
    assert resp.json()["status"] == "ignored"
    assert await _job_count(sessionmaker) == 0


async def test_github_missing_signature_401(api: httpx.AsyncClient) -> None:
    body = _gh_body()
    resp = await api.post(
        "/webhooks/github", content=body, headers=_gh_headers(body, signed=False)
    )
    assert resp.status_code == 401


async def test_github_wrong_secret_401(api: httpx.AsyncClient) -> None:
    body = _gh_body()
    headers = _gh_headers(body)
    headers["X-Hub-Signature-256"] = "sha256=deadbeef"
    resp = await api.post("/webhooks/github", content=body, headers=headers)
    assert resp.status_code == 401


async def test_github_unmapped_repo_401(api: httpx.AsyncClient) -> None:
    # No project owns this repo, so there is no secret to verify against → reject.
    body = _gh_body(repo="octo/unknown")
    resp = await api.post("/webhooks/github", content=body, headers=_gh_headers(body))
    assert resp.status_code == 401


async def test_github_duplicate_delivery_deduped(
    api: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    body = _gh_body()
    first = await api.post("/webhooks/github", content=body, headers=_gh_headers(body))
    second = await api.post("/webhooks/github", content=body, headers=_gh_headers(body))
    assert first.json()["status"] == "queued"
    assert second.json()["status"] == "duplicate"
    assert await _job_count(sessionmaker) == 1


async def test_github_malformed_payload_400(api: httpx.AsyncClient) -> None:
    body = json.dumps({"repository": "not-an-object"}).encode()
    resp = await api.post("/webhooks/github", content=body, headers=_gh_headers(body))
    assert resp.status_code == 400


async def test_github_non_json_body_400_not_500(api: httpx.AsyncClient) -> None:
    # A repo webhook left on form-encoding sends a non-JSON body — must be a clean 400, not a 500.
    body = b"payload=%7B%22action%22%3A%22opened%22%7D"
    resp = await api.post("/webhooks/github", content=body, headers=_gh_headers(body))
    assert resp.status_code == 400
    assert "application/json" in resp.json()["detail"]
