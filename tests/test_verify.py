from collections.abc import Callable

import httpx

from conductor import verify


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_verify_claude_oauth_uses_bearer_and_beta() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        result = await verify.verify_claude(
            oauth_token="tok", api_key=None, client=client
        )
    assert result.ok
    assert seen["authorization"] == "Bearer tok"
    assert seen["anthropic-beta"] == "oauth-2025-04-20"


async def test_verify_claude_api_key_uses_x_api_key() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        result = await verify.verify_claude(oauth_token=None, api_key="sk", client=client)
    assert result.ok
    assert seen["x-api-key"] == "sk"
    assert "authorization" not in seen


async def test_verify_claude_requires_exactly_one_credential() -> None:
    result = await verify.verify_claude(oauth_token="a", api_key="b")
    assert not result.ok
    assert "exactly one" in result.detail


async def test_verify_plane_ok() -> None:
    async with _client(lambda r: httpx.Response(200, json={})) as client:
        result = await verify.verify_plane(
            base_url="https://plane.example.com/",
            api_key="k",
            workspace_slug="dem",
            client=client,
        )
    assert result.ok


async def test_verify_plane_unauthorized() -> None:
    async with _client(lambda r: httpx.Response(401, json={"error": "bad key"})) as client:
        result = await verify.verify_plane(
            base_url="https://plane.example.com", api_key="k", workspace_slug="dem", client=client
        )
    assert not result.ok
    assert "401" in result.detail


async def test_verify_plane_workspace_not_found() -> None:
    async with _client(lambda r: httpx.Response(404, json={})) as client:
        result = await verify.verify_plane(
            base_url="https://plane.example.com", api_key="k", workspace_slug="nope", client=client
        )
    assert not result.ok
    assert "nope" in result.detail


async def test_verify_github_ok() -> None:
    async with _client(lambda r: httpx.Response(200, json={})) as client:
        result = await verify.verify_github(token="g", client=client)
    assert result.ok


async def test_verify_github_bad_token() -> None:
    async with _client(lambda r: httpx.Response(401, json={"message": "Bad credentials"})) as client:
        result = await verify.verify_github(token="g", client=client)
    assert not result.ok
    assert "401" in result.detail


async def test_verify_github_repo_access_checked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})

    async with _client(handler) as client:
        result = await verify.verify_github(token="g", repo="owner/name", client=client)
    assert not result.ok
    assert "owner/name" in result.detail
