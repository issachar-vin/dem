from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

# Cheapest current model; the connection test only needs a 200, not a useful answer.
CLAUDE_TEST_MODEL = "claude-haiku-4-5"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    detail: str


def _http_detail(response: httpx.Response) -> str:
    message = None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
        message = message or body.get("message")
    return f"HTTP {response.status_code}: {message or response.text[:200]}"


async def verify_claude(
    *,
    oauth_token: str | None,
    api_key: str | None,
    client: httpx.AsyncClient | None = None,
) -> VerifyResult:
    """A minimal Messages API call (Haiku, max_tokens=4). OAuth and API-key auth differ."""
    if bool(oauth_token) == bool(api_key):
        return VerifyResult(False, "Set exactly one of subscription token or API key.")

    headers = {"anthropic-version": "2023-06-01", "content-type": "application/json"}
    if oauth_token:
        headers["authorization"] = f"Bearer {oauth_token}"
        headers["anthropic-beta"] = "oauth-2025-04-20"
        mode = "subscription"
    else:
        headers["x-api-key"] = api_key or ""
        mode = "api key"

    body = {
        "model": CLAUDE_TEST_MODEL,
        "max_tokens": 4,
        "messages": [{"role": "user", "content": "Reply with: ok"}],
    }
    return await _post_ok(
        client,
        ANTHROPIC_MESSAGES_URL,
        headers=headers,
        json=body,
        ok_detail=f"Authenticated via {mode}.",
    )


async def verify_plane(
    *,
    base_url: str,
    api_key: str,
    workspace_slug: str,
    client: httpx.AsyncClient | None = None,
) -> VerifyResult:
    url = f"{base_url.rstrip('/')}/api/v1/workspaces/{workspace_slug}/members/"
    return await _get_ok(
        client,
        url,
        headers={"X-API-Key": api_key},
        ok_detail=f"Reached workspace '{workspace_slug}'.",
        not_found_detail=f"Workspace slug '{workspace_slug}' not found.",
    )


async def verify_github(
    *,
    token: str,
    repo: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> VerifyResult:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    auth = await _get_ok(
        client,
        "https://api.github.com/user",
        headers=headers,
        ok_detail="Token authenticated.",
        not_found_detail="Token endpoint not found.",
    )
    if not auth.ok or not repo:
        return auth
    return await _get_ok(
        client,
        f"https://api.github.com/repos/{repo}",
        headers=headers,
        ok_detail=f"Token can access {repo}.",
        not_found_detail=f"Token authenticated but cannot access repo '{repo}'.",
    )


async def _request(
    client: httpx.AsyncClient | None,
    send: Callable[[httpx.AsyncClient], Awaitable[httpx.Response]],
    *,
    ok_detail: str,
    not_found_detail: str | None = None,
) -> VerifyResult:
    async def evaluate(c: httpx.AsyncClient) -> VerifyResult:
        try:
            response = await send(c)
        except httpx.HTTPError as exc:
            return VerifyResult(False, f"Connection failed: {exc}")
        if response.status_code == 200:
            return VerifyResult(True, ok_detail)
        if response.status_code == 404 and not_found_detail is not None:
            return VerifyResult(False, not_found_detail)
        return VerifyResult(False, _http_detail(response))

    if client is not None:
        return await evaluate(client)
    async with httpx.AsyncClient(timeout=20) as owned:
        return await evaluate(owned)


async def _get_ok(
    client: httpx.AsyncClient | None,
    url: str,
    *,
    headers: dict[str, str],
    ok_detail: str,
    not_found_detail: str,
) -> VerifyResult:
    return await _request(
        client,
        lambda c: c.get(url, headers=headers),
        ok_detail=ok_detail,
        not_found_detail=not_found_detail,
    )


async def _post_ok(
    client: httpx.AsyncClient | None,
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, object],
    ok_detail: str,
) -> VerifyResult:
    return await _request(
        client,
        lambda c: c.post(url, headers=headers, json=json),
        ok_detail=ok_detail,
    )
