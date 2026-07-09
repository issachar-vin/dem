from collections.abc import Awaitable, Callable

import httpx
from pydantic import BaseModel

from conductor.github import github_headers

# Cheapest current model; the connection test only needs a 200, not a useful answer.
CLAUDE_TEST_MODEL = "claude-haiku-4-5"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
GITHUB_REPOS_URL = "https://api.github.com/user/repos"


def _claude_headers(oauth_token: str | None, api_key: str | None) -> dict[str, str]:
    """Anthropic auth headers. OAuth subscription token → Authorization: Bearer + beta header;
    API key → x-api-key."""
    headers = {"anthropic-version": "2023-06-01"}
    if oauth_token:
        headers["authorization"] = f"Bearer {oauth_token}"
        headers["anthropic-beta"] = "oauth-2025-04-20"
    else:
        headers["x-api-key"] = api_key or ""
    return headers


class VerifyResult(BaseModel):
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
        return VerifyResult(ok=False, detail="Set exactly one of subscription token or API key.")

    headers = {**_claude_headers(oauth_token, api_key), "content-type": "application/json"}
    mode = "subscription" if oauth_token else "api key"

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


async def list_claude_models(
    *,
    oauth_token: str | None,
    api_key: str | None,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Live model IDs for the configured Claude credential. Empty list on any failure — the UI
    falls back to whatever value is already stored."""
    if bool(oauth_token) == bool(api_key):
        return []
    headers = _claude_headers(oauth_token, api_key)

    async def fetch(c: httpx.AsyncClient) -> list[str]:
        try:
            response = await c.get(ANTHROPIC_MODELS_URL, headers=headers, params={"limit": 100})
        except httpx.HTTPError:
            return []
        if response.status_code != 200:
            return []
        data = response.json().get("data", [])
        return [str(m["id"]) for m in data if isinstance(m, dict) and "id" in m]

    if client is not None:
        return await fetch(client)
    async with httpx.AsyncClient(timeout=20) as owned:
        return await fetch(owned)


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
    headers = github_headers(token)
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


async def list_github_repos(
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, str]:
    """Map of `owner/name` → default branch for every repo the token can access, for the wizard's
    repo picker (the default branch seeds each repo's base-branch field). Empty dict on any failure
    — the UI falls back to a free-typed repo field. A fine-grained PAT only lists repos explicitly
    granted at token creation, so a missing repo means the token's own GitHub-side grant needs
    editing."""
    headers = github_headers(token)

    async def fetch(c: httpx.AsyncClient) -> dict[str, str]:
        repos: dict[str, str] = {}
        url: str | None = GITHUB_REPOS_URL
        params: dict[str, str] | None = {"per_page": "100", "sort": "full_name"}
        while url:
            try:
                response = await c.get(url, headers=headers, params=params)
            except httpx.HTTPError:
                return {}
            if response.status_code != 200:
                return {}
            page = response.json()
            if not isinstance(page, list):
                return {}
            for r in page:
                if isinstance(r, dict) and "full_name" in r:
                    repos[str(r["full_name"])] = str(r.get("default_branch") or "main")
            url = response.links.get("next", {}).get("url")
            params = None  # the next-page URL already carries the query string
        return repos

    if client is not None:
        return await fetch(client)
    async with httpx.AsyncClient(timeout=20) as owned:
        return await fetch(owned)


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
            return VerifyResult(ok=False, detail=f"Connection failed: {exc}")
        if response.status_code == 200:
            return VerifyResult(ok=True, detail=ok_detail)
        if response.status_code == 404 and not_found_detail is not None:
            return VerifyResult(ok=False, detail=not_found_detail)
        return VerifyResult(ok=False, detail=_http_detail(response))

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
