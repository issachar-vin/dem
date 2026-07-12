import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

GITHUB_API_BASE = "https://api.github.com"


class GitHubUser(BaseModel):
    """The account behind `github_token`, used to author the engineer's commits without any manual
    name/email config (see docs/HANDOFF.md → Phase 4 authorship decision)."""

    model_config = ConfigDict(extra="ignore")

    login: str
    id: int
    name: str | None = None
    email: str | None = None

    @property
    def git_name(self) -> str:
        return self.name or self.login

    @property
    def git_email(self) -> str:
        # Private accounts return email: null; the noreply address is what GitHub attributes.
        return self.email or f"{self.id}+{self.login}@users.noreply.github.com"


class PullRequest(BaseModel):
    """The subset of GitHub's created-PR response the conductor records on the ticket."""

    model_config = ConfigDict(extra="ignore")

    number: int
    html_url: str


def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


class GitHubError(Exception):
    """A non-2xx response (or transport failure) from the GitHub API."""

    def __init__(self, detail: str, *, status_code: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass
class GitHubClient:
    """Typed httpx client for the GitHub REST API. Read-only today (poll mode reads PR state);
    Phase 4's dispatcher extends it with PR create and branch push. `client` is injectable for
    tests via `httpx.MockTransport`; when omitted each call owns a short-lived client. Mirrors
    plane.py's request/error-handling shape."""

    token: str
    client: httpx.AsyncClient | None = None

    async def list_pull_requests(self, repo: str, *, state: str = "open") -> list[dict[str, Any]]:
        """Open PRs for `owner/name`. Poll mode compares this against the last-seen state."""
        result = await self._json(
            lambda c: c.get(
                f"{GITHUB_API_BASE}/repos/{repo}/pulls",
                headers=github_headers(self.token),
                params={"state": state, "per_page": "100"},
            )
        )
        return result if isinstance(result, list) else []

    async def get_user(self) -> GitHubUser:
        """The token's own account. Powers derive-from-token git authorship in the dispatcher."""
        result = await self._json(
            lambda c: c.get(f"{GITHUB_API_BASE}/user", headers=github_headers(self.token))
        )
        return GitHubUser.model_validate(result)

    async def get_readme(self, repo: str) -> str:
        """The repo's README as text (best-effort — empty if none). Fetched conductor-side so the
        router can be told what each repo is *for* without the agent ever touching GitHub."""
        try:
            result = await self._json(
                lambda c: c.get(
                    f"{GITHUB_API_BASE}/repos/{repo}/readme", headers=github_headers(self.token)
                )
            )
        except GitHubError:
            return ""
        content = result.get("content") if isinstance(result, dict) else None
        if not isinstance(content, str):
            return ""
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except (ValueError, UnicodeDecodeError):
            return ""

    async def create_pull_request(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> PullRequest:
        """Open a PR for the engineer's branch. `head`/`base` are branch names on `owner/name`."""
        result = await self._json(
            lambda c: c.post(
                f"{GITHUB_API_BASE}/repos/{repo}/pulls",
                headers=github_headers(self.token),
                json={"head": head, "base": base, "title": title, "body": body},
            )
        )
        return PullRequest.model_validate(result)

    async def _json(self, send: Callable[[httpx.AsyncClient], Awaitable[httpx.Response]]) -> Any:
        async def run(c: httpx.AsyncClient) -> Any:
            try:
                response = await send(c)
            except httpx.HTTPError as exc:
                raise GitHubError(f"Connection to GitHub failed: {exc}") from exc
            if response.status_code >= 400:
                raise GitHubError(_error_detail(response), status_code=response.status_code)
            if not response.content:
                return {}
            return response.json()

        if self.client is not None:
            return await run(self.client)
        async with httpx.AsyncClient(timeout=20) as owned:
            return await run(owned)


def _error_detail(response: httpx.Response) -> str:
    message = None
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        message = body.get("message")
        # GitHub's real reason (e.g. "No commits between main and ticket/…" on a 422) lives in the
        # errors[] array, not the top-level message — surface it so failures are self-explanatory.
        errors = body.get("errors")
        if isinstance(errors, list):
            details = "; ".join(
                str(e.get("message") or e.get("code"))
                for e in errors
                if isinstance(e, dict) and (e.get("message") or e.get("code"))
            )
            if details:
                message = f"{message}: {details}" if message else details
    return f"HTTP {response.status_code}: {message or response.text[:200]}"


def client_from_resolved(
    resolved: dict[str, str], *, client: httpx.AsyncClient | None = None
) -> GitHubClient:
    return GitHubClient(token=resolved.get("github_token", ""), client=client)
