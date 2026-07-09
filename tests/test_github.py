import httpx
import pytest

from conductor.github import GitHubClient, GitHubError


def _client(handler: httpx.MockTransport) -> GitHubClient:
    return GitHubClient(token="tok", client=httpx.AsyncClient(transport=handler))


async def test_list_pull_requests_returns_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octo/backend/pulls"
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json=[{"number": 1, "state": "open"}])

    prs = await _client(httpx.MockTransport(handler)).list_pull_requests("octo/backend")
    assert prs == [{"number": 1, "state": "open"}]


async def test_list_pull_requests_passes_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["state"] == "all"
        return httpx.Response(200, json=[])

    await _client(httpx.MockTransport(handler)).list_pull_requests("o/r", state="all")


async def test_error_status_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(GitHubError) as exc:
        await _client(httpx.MockTransport(handler)).list_pull_requests("o/r")
    assert exc.value.status_code == 404


async def test_get_user_public_email() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user"
        return httpx.Response(
            200, json={"login": "bot", "id": 7, "name": "Bot", "email": "b@x.io"}
        )

    user = await _client(httpx.MockTransport(handler)).get_user()
    assert user.git_name == "Bot"
    assert user.git_email == "b@x.io"


async def test_get_user_falls_back_to_noreply() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"login": "bot", "id": 7, "name": None, "email": None}
        )

    user = await _client(httpx.MockTransport(handler)).get_user()
    assert user.git_name == "bot"
    assert user.git_email == "7+bot@users.noreply.github.com"
