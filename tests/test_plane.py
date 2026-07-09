import json
from collections.abc import Callable

import httpx
import pytest

from conductor.plane import PlaneClient, PlaneError


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _plane(client: httpx.AsyncClient) -> PlaneClient:
    return PlaneClient(
        base_url="https://plane.example.com/",
        api_key="k",
        workspace_slug="dem",
        client=client,
    )


async def test_get_issue_sends_api_key_and_returns_json() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("x-api-key", "")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"id": "i1", "name": "Epic"})

    async with _client(handler) as c:
        issue = await _plane(c).get_issue("p1", "i1")
    assert issue["name"] == "Epic"
    assert seen["auth"] == "k"
    assert seen["path"] == "/api/v1/workspaces/dem/projects/p1/issues/i1/"


async def test_list_states_unwraps_results() -> None:
    body = {"results": [{"id": "s1", "name": "Backlog", "group": "backlog"}]}
    async with _client(lambda r: httpx.Response(200, json=body)) as c:
        states = await _plane(c).list_states("p1")
    assert states == body["results"]


async def test_list_labels_unwraps_results() -> None:
    body = {"results": [{"id": "L1", "name": "epic"}]}
    async with _client(lambda r: httpx.Response(200, json=body)) as c:
        labels = await _plane(c).list_labels("p1")
    assert labels[0]["name"] == "epic"


async def test_list_projects_unwraps_results() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"results": [{"id": "p1", "name": "chessbro"}]})

    async with _client(handler) as c:
        projects = await _plane(c).list_projects()
    assert projects[0]["name"] == "chessbro"
    assert seen["path"] == "/api/v1/workspaces/dem/projects/"


async def test_post_comment_sends_comment_html() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = request.content
        return httpx.Response(201, json={"id": "c1"})

    async with _client(handler) as c:
        await _plane(c).post_comment("p1", "i1", "<p>hi</p>")
    assert seen["method"] == "POST"
    assert b"comment_html" in seen["body"]  # type: ignore[operator]


async def test_set_state_patches_state() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "i1"})

    async with _client(handler) as c:
        await _plane(c).set_state("p1", "i1", "s2")
    assert seen["method"] == "PATCH"
    assert json.loads(seen["body"]) == {"state": "s2"}


async def test_error_response_raises_plane_error() -> None:
    async with _client(
        lambda r: httpx.Response(404, json={"detail": "not found"})
    ) as c:
        with pytest.raises(PlaneError) as exc:
            await _plane(c).get_issue("p1", "missing")
    assert exc.value.status_code == 404
    assert "not found" in exc.value.detail


async def test_connection_failure_raises_plane_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with _client(handler) as c:
        with pytest.raises(PlaneError, match="Connection to Plane failed"):
            await _plane(c).get_issue("p1", "i1")
