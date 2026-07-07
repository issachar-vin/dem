from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx


class PlaneError(Exception):
    """A non-2xx response (or transport failure) from the Plane API."""

    def __init__(self, detail: str, *, status_code: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass
class PlaneClient:
    """Typed httpx client for the Plane REST API v1.

    Auth is `X-API-Key`; all work-item routes hang off
    `{base_url}/api/v1/workspaces/{workspace_slug}/projects/{project_id}/`. `client` is injectable
    so tests can drive it with an `httpx.MockTransport`; when omitted each call owns a short-lived
    client. Mirrors verify.py's request/error-handling shape.
    """

    base_url: str
    api_key: str
    workspace_slug: str
    client: httpx.AsyncClient | None = None

    @property
    def _workspace_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/workspaces/{self.workspace_slug}"

    def _project_url(self, project_id: str) -> str:
        return f"{self._workspace_url}/projects/{project_id}"

    async def get_issue(self, project_id: str, issue_id: str) -> dict[str, Any]:
        return await self._json_obj(
            lambda c: c.get(
                f"{self._project_url(project_id)}/issues/{issue_id}/", headers=self._headers
            )
        )

    async def create_issue(self, project_id: str, *, name: str, **fields: Any) -> dict[str, Any]:
        return await self._json_obj(
            lambda c: c.post(
                f"{self._project_url(project_id)}/issues/",
                headers=self._headers,
                json={"name": name, **fields},
            )
        )

    async def post_comment(
        self, project_id: str, issue_id: str, comment_html: str
    ) -> dict[str, Any]:
        return await self._json_obj(
            lambda c: c.post(
                f"{self._project_url(project_id)}/issues/{issue_id}/comments/",
                headers=self._headers,
                json={"comment_html": comment_html},
            )
        )

    async def set_state(self, project_id: str, issue_id: str, state_id: str) -> dict[str, Any]:
        return await self._json_obj(
            lambda c: c.patch(
                f"{self._project_url(project_id)}/issues/{issue_id}/",
                headers=self._headers,
                json={"state": state_id},
            )
        )

    async def list_states(self, project_id: str) -> list[dict[str, Any]]:
        return await self._paginated(f"{self._project_url(project_id)}/states/")

    async def list_labels(self, project_id: str) -> list[dict[str, Any]]:
        return await self._paginated(f"{self._project_url(project_id)}/labels/")

    # ── internals ────────────────────────────────────────────────────────────
    @property
    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    async def _json_obj(
        self, send: Callable[[httpx.AsyncClient], Awaitable[httpx.Response]]
    ) -> dict[str, Any]:
        result = await self._json(send)
        return result if isinstance(result, dict) else {}

    async def _paginated(self, url: str) -> list[dict[str, Any]]:
        body = await self._json(lambda c: c.get(url, headers=self._headers))
        results = body.get("results") if isinstance(body, dict) else None
        return results if isinstance(results, list) else []

    async def _json(self, send: Callable[[httpx.AsyncClient], Awaitable[httpx.Response]]) -> Any:
        async def run(c: httpx.AsyncClient) -> Any:
            try:
                response = await send(c)
            except httpx.HTTPError as exc:
                raise PlaneError(f"Connection to Plane failed: {exc}") from exc
            if response.status_code >= 400:
                raise PlaneError(_error_detail(response), status_code=response.status_code)
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
        message = body.get("error") or body.get("detail") or body.get("message")
    return f"HTTP {response.status_code}: {message or response.text[:200]}"


def client_from_resolved(
    resolved: dict[str, str], *, client: httpx.AsyncClient | None = None
) -> PlaneClient:
    return PlaneClient(
        base_url=resolved.get("plane_base_url", ""),
        api_key=resolved.get("plane_api_key", ""),
        workspace_slug=resolved.get("plane_workspace_slug", ""),
        client=client,
    )
