from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from conductor import plane
from conductor.api import mappings as mappings_api
from conductor.auth import AuthStore
from conductor.mappings import MappingStore
from conductor.models import WorkflowState
from conductor.store import ConfigStore


# ── store ────────────────────────────────────────────────────────────────────
async def test_set_get_and_delete_project(mappings: MappingStore) -> None:
    await mappings.set_project("p1", repo="izzy/chess", base_branch="dev")
    row = await mappings.get_project("p1")
    assert row is not None
    assert row.repo == "izzy/chess"
    assert row.base_branch == "dev"
    assert await mappings.delete_project("p1") is True
    assert await mappings.get_project("p1") is None
    assert await mappings.delete_project("p1") is False


async def test_delete_project_cascades_state_mappings(mappings: MappingStore) -> None:
    await mappings.set_project("p1", repo="izzy/chess")
    await mappings.set_state("p1", WorkflowState.IN_REVIEW, "s1")
    assert await mappings.delete_project("p1") is True
    assert await mappings.list_states("p1") == []


async def test_set_project_updates_existing(mappings: MappingStore) -> None:
    await mappings.set_project("p1", repo="izzy/a")
    await mappings.set_project("p1", repo="izzy/b")
    rows = await mappings.list_projects()
    assert len(rows) == 1
    assert rows[0]["repo"] == "izzy/b"


async def test_state_mapping_upserts_by_project_and_state(
    mappings: MappingStore,
) -> None:
    await mappings.set_state("p1", WorkflowState.IN_REVIEW, "s1")
    await mappings.set_state("p1", WorkflowState.IN_REVIEW, "s2")
    states = await mappings.list_states("p1")
    assert states == [{"workflow_state": "in_review", "plane_state_id": "s2"}]


async def test_import_targets_seeds_once(
    mappings: MappingStore, tmp_path: Path
) -> None:
    f = tmp_path / "targets.yml"
    f.write_text(
        "targets:\n"
        "  - workspace: dem\n"
        "    project_id: p1\n"
        "    github_repo: izzy/chess\n"
        "    base_branch: main\n"
    )
    assert await mappings.import_targets(f) == 1
    # DB wins: a second import without reseed skips the existing row.
    await mappings.set_project("p1", repo="izzy/changed")
    assert await mappings.import_targets(f) == 0
    row = await mappings.get_project("p1")
    assert row is not None and row.repo == "izzy/changed"
    assert await mappings.import_targets(f, reseed=True) == 1
    row = await mappings.get_project("p1")
    assert row is not None and row.repo == "izzy/chess"


# ── API ──────────────────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def api(
    mappings: MappingStore, store: ConfigStore, auth: AuthStore, auth_token: str
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.mappings = mappings
    app.state.store = store
    app.state.auth = auth
    app.include_router(mappings_api.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {auth_token}"},
    ) as client:
        yield client


async def test_workflow_states_endpoint(api: httpx.AsyncClient) -> None:
    resp = await api.get("/api/mappings/workflow-states")
    assert resp.status_code == 200
    assert "in_review" in resp.json()


async def test_put_project_ok_then_listed(api: httpx.AsyncClient) -> None:
    resp = await api.put("/api/mappings/projects/p1", json={"repo": "izzy/chess"})
    assert resp.status_code == 200
    listed = await api.get("/api/mappings/projects")
    assert listed.json()[0]["plane_project_id"] == "p1"


async def test_put_project_bad_repo_422(api: httpx.AsyncClient) -> None:
    resp = await api.put("/api/mappings/projects/p1", json={"repo": "not-a-repo"})
    assert resp.status_code == 422


async def test_mappings_require_auth(
    mappings: MappingStore, store: ConfigStore, auth: AuthStore
) -> None:
    app = FastAPI()
    app.state.mappings = mappings
    app.state.store = store
    app.state.auth = auth
    app.include_router(mappings_api.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as anon:
        resp = await anon.get("/api/mappings/projects")
    assert resp.status_code == 401


async def test_delete_missing_project_404(api: httpx.AsyncClient) -> None:
    resp = await api.delete("/api/mappings/projects/nope")
    assert resp.status_code == 404


async def test_set_state_mapping_rejects_unknown_state(api: httpx.AsyncClient) -> None:
    resp = await api.put(
        "/api/mappings/projects/p1/states",
        json={"workflow_state": "made_up", "plane_state_id": "s1"},
    )
    assert resp.status_code == 422


async def test_state_scan_calls_plane(
    api: httpx.AsyncClient, store: ConfigStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await store.set_setting("plane_base_url", "https://plane.example.com")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"results": [{"id": "s1", "name": "Backlog", "group": "backlog"}]}
        )

    real = plane.client_from_resolved

    def fake_from_resolved(resolved: dict[str, str], **_: object) -> plane.PlaneClient:
        return real(
            resolved, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

    monkeypatch.setattr(mappings_api.plane, "client_from_resolved", fake_from_resolved)
    resp = await api.get("/api/mappings/projects/p1/state-scan")
    assert resp.status_code == 200
    assert resp.json() == [{"id": "s1", "name": "Backlog", "group": "backlog"}]
