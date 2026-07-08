from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_TIMEOUT = 15.0


class ConductorError(Exception):
    """Raised when the conductor API returns a non-2xx response."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class TestResult:
    ok: bool
    detail: str


@dataclass(frozen=True)
class StepStatus:
    step: str
    complete: bool
    missing: list[str]
    verifiable: bool


@dataclass(frozen=True)
class ConfigStatus:
    steps: list[StepStatus]
    issues: list[str]
    complete: bool


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or response.reason_phrase
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)


class ConductorClient:
    """Typed, synchronous wrapper over the conductor management API. Streamlit is sync, so this is
    too; pass an injectable `client=` (e.g. an httpx.MockTransport client) in tests."""

    def __init__(
        self, base_url: str, *, client: httpx.Client | None = None, token: str | None = None
    ) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=DEFAULT_TIMEOUT)
        self.token = token

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self.token:
            headers = dict(kwargs.pop("headers", {}))
            headers["Authorization"] = f"Bearer {self.token}"
            kwargs["headers"] = headers
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise ConductorError(0, str(exc)) from exc
        if response.status_code >= 400:
            raise ConductorError(response.status_code, _detail(response))
        return response

    # ── auth ─────────────────────────────────────────────────────────────────
    def auth_status(self) -> bool:
        data = self._request("GET", "/api/auth/status").json()
        return bool(data["initialized"])

    def register(self, username: str, password: str) -> str:
        data = self._request(
            "POST", "/api/auth/register", json={"username": username, "password": password}
        ).json()
        return str(data["token"])

    def login(self, username: str, password: str) -> str:
        data = self._request(
            "POST", "/api/auth/login", json={"username": username, "password": password}
        ).json()
        return str(data["token"])

    # ── config ───────────────────────────────────────────────────────────────
    def list_config(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self._request("GET", "/api/config").json()
        return data

    def status(self) -> ConfigStatus:
        data = self._request("GET", "/api/config/status").json()
        steps = [
            StepStatus(
                step=s["step"],
                complete=s["complete"],
                missing=list(s["missing"]),
                verifiable=s["verifiable"],
            )
            for s in data["steps"]
        ]
        return ConfigStatus(steps=steps, issues=list(data["issues"]), complete=data["complete"])

    def set_secret(self, name: str, value: str) -> None:
        self._request("PUT", f"/api/config/secret/{name}", json={"value": value})

    def set_setting(self, name: str, value: str) -> None:
        self._request("PUT", f"/api/config/setting/{name}", json={"value": value})

    def test_connection(self, service: str) -> TestResult:
        data = self._request("POST", f"/api/config/test/{service}").json()
        return TestResult(ok=bool(data["ok"]), detail=str(data["detail"]))

    def export_env(self) -> str:
        return self._request("GET", "/api/config/export.env").text

    def export_bundle(self, passphrase: str) -> str:
        data = self._request(
            "POST", "/api/config/export-bundle", json={"passphrase": passphrase}
        ).json()
        return str(data["blob_b64"])

    def import_bundle(self, blob_b64: str, passphrase: str) -> int:
        data = self._request(
            "POST",
            "/api/config/import-bundle",
            json={"blob_b64": blob_b64, "passphrase": passphrase},
        ).json()
        return int(data["imported"])

    # ── mappings ─────────────────────────────────────────────────────────────
    def workflow_states(self) -> list[str]:
        data: list[str] = self._request("GET", "/api/mappings/workflow-states").json()
        return data

    def list_projects(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self._request("GET", "/api/mappings/projects").json()
        return data

    def set_project(self, project_id: str, repo: str, base_branch: str = "main") -> None:
        self._request(
            "PUT",
            f"/api/mappings/projects/{project_id}",
            json={"repo": repo, "base_branch": base_branch},
        )

    def delete_project(self, project_id: str) -> None:
        self._request("DELETE", f"/api/mappings/projects/{project_id}")

    def list_state_mappings(self, project_id: str) -> list[dict[str, str]]:
        data: list[dict[str, str]] = self._request(
            "GET", f"/api/mappings/projects/{project_id}/states"
        ).json()
        return data

    def set_state_mapping(self, project_id: str, workflow_state: str, plane_state_id: str) -> None:
        self._request(
            "PUT",
            f"/api/mappings/projects/{project_id}/states",
            json={"workflow_state": workflow_state, "plane_state_id": plane_state_id},
        )

    def scan_states(self, project_id: str) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self._request(
            "GET", f"/api/mappings/projects/{project_id}/state-scan"
        ).json()
        return data
