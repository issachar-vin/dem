from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from console.api_client import ConductorClient, ConductorError

MakeClient = Callable[[Callable[[httpx.Request], httpx.Response]], ConductorClient]


def _record(response: httpx.Response) -> tuple[dict[str, object], Callable[..., httpx.Response]]:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = request.content.decode() if request.content else ""
        seen["authorization"] = request.headers.get("Authorization")
        return response

    return seen, handler


# ── auth ───────────────────────────────────────────────────────────────────────
def test_auth_status(make_client: MakeClient) -> None:
    _, handler = _record(httpx.Response(200, json={"initialized": True}))
    assert make_client(handler).auth_status() is True


def test_register_returns_token(make_client: MakeClient) -> None:
    seen, handler = _record(httpx.Response(200, json={"token": "t0k", "username": "admin"}))
    assert make_client(handler).register("admin", "pw") == "t0k"
    assert seen["path"] == "/api/auth/register"
    assert json.loads(str(seen["body"])) == {"username": "admin", "password": "pw"}


def test_login_returns_token(make_client: MakeClient) -> None:
    seen, handler = _record(httpx.Response(200, json={"token": "t0k", "username": "admin"}))
    assert make_client(handler).login("admin", "pw") == "t0k"
    assert seen["path"] == "/api/auth/login"


def test_token_is_sent_as_bearer_header(make_client: MakeClient) -> None:
    seen, handler = _record(httpx.Response(200, json=[]))
    client = make_client(handler)
    client.token = "t0k"
    client.list_config()
    assert seen["authorization"] == "Bearer t0k"


def test_no_token_sends_no_auth_header(make_client: MakeClient) -> None:
    seen, handler = _record(httpx.Response(200, json={"initialized": False}))
    make_client(handler).auth_status()
    assert seen["authorization"] is None


def test_unauthenticated_write_raises_401(make_client: MakeClient) -> None:
    _, handler = _record(httpx.Response(401, json={"detail": "Authentication required."}))
    with pytest.raises(ConductorError) as excinfo:
        make_client(handler).set_secret("plane_api_key", "x")
    assert excinfo.value.status_code == 401


def test_list_config(make_client: MakeClient) -> None:
    seen, handler = _record(httpx.Response(200, json=[{"name": "plane_api_key"}]))
    result = make_client(handler).list_config()
    assert result == [{"name": "plane_api_key"}]
    assert seen["method"] == "GET"
    assert seen["path"] == "/api/config"


def test_status_parses_steps(make_client: MakeClient) -> None:
    payload = {
        "steps": [
            {"step": "claude", "complete": False, "missing": ["x"], "verifiable": True},
            {"step": "advanced", "complete": True, "missing": [], "verifiable": False},
        ],
        "issues": ["No Claude credential set."],
        "complete": False,
    }
    _, handler = _record(httpx.Response(200, json=payload))
    status = make_client(handler).status()
    assert not status.complete
    assert status.issues == ["No Claude credential set."]
    assert status.steps[0].step == "claude"
    assert status.steps[0].missing == ["x"]
    assert status.steps[0].verifiable is True
    assert status.steps[1].complete is True


def test_set_secret_sends_value(make_client: MakeClient) -> None:
    seen, handler = _record(httpx.Response(200, json={"status": "set"}))
    make_client(handler).set_secret("plane_api_key", "s3cr3t")
    assert seen["method"] == "PUT"
    assert seen["path"] == "/api/config/secret/plane_api_key"
    assert json.loads(str(seen["body"])) == {"value": "s3cr3t"}


def test_set_setting_sends_value(make_client: MakeClient) -> None:
    seen, handler = _record(httpx.Response(200, json={"status": "set"}))
    make_client(handler).set_setting("plane_epic_signal", "label")
    assert seen["method"] == "PUT"
    assert seen["path"] == "/api/config/setting/plane_epic_signal"
    assert json.loads(str(seen["body"])) == {"value": "label"}


def test_test_connection_ok(make_client: MakeClient) -> None:
    seen, handler = _record(httpx.Response(200, json={"ok": True, "detail": "authenticated"}))
    result = make_client(handler).test_connection("plane")
    assert result.ok is True
    assert result.detail == "authenticated"
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/config/test/plane"


def test_test_connection_failure_is_not_an_error(make_client: MakeClient) -> None:
    # A failed connection is still HTTP 200 with ok=false — it must not raise.
    _, handler = _record(httpx.Response(200, json={"ok": False, "detail": "401 Unauthorized"}))
    result = make_client(handler).test_connection("github")
    assert result.ok is False
    assert "401" in result.detail


def test_export_env_returns_text(make_client: MakeClient) -> None:
    _, handler = _record(httpx.Response(200, text="PLANE_API_KEY=abc\n"))
    assert make_client(handler).export_env() == "PLANE_API_KEY=abc\n"


def test_export_bundle_returns_blob(make_client: MakeClient) -> None:
    seen, handler = _record(httpx.Response(200, json={"blob_b64": "QUJD"}))
    assert make_client(handler).export_bundle("pw") == "QUJD"
    assert json.loads(str(seen["body"])) == {"passphrase": "pw"}


def test_import_bundle_returns_count(make_client: MakeClient) -> None:
    seen, handler = _record(httpx.Response(200, json={"imported": 7}))
    assert make_client(handler).import_bundle("QUJD", "pw") == 7
    assert json.loads(str(seen["body"])) == {"blob_b64": "QUJD", "passphrase": "pw"}


def test_workflow_states(make_client: MakeClient) -> None:
    _, handler = _record(httpx.Response(200, json=["Backlog", "Done"]))
    assert make_client(handler).workflow_states() == ["Backlog", "Done"]


def test_list_projects(make_client: MakeClient) -> None:
    _, handler = _record(httpx.Response(200, json=[{"plane_project_id": "p1", "repo": "o/r"}]))
    assert make_client(handler).list_projects()[0]["repo"] == "o/r"


def test_set_project_sends_repo_and_branch(make_client: MakeClient) -> None:
    seen, handler = _record(httpx.Response(200, json={"status": "set"}))
    make_client(handler).set_project("p1", "owner/name", "develop")
    assert seen["path"] == "/api/mappings/projects/p1"
    assert json.loads(str(seen["body"])) == {"repo": "owner/name", "base_branch": "develop"}


def test_set_project_bad_repo_raises(make_client: MakeClient) -> None:
    _, handler = _record(httpx.Response(422, json={"detail": "repo must be in 'owner/name' form."}))
    with pytest.raises(ConductorError) as excinfo:
        make_client(handler).set_project("p1", "bogus")
    assert excinfo.value.status_code == 422
    assert "owner/name" in excinfo.value.detail


def test_delete_project(make_client: MakeClient) -> None:
    seen, handler = _record(httpx.Response(200, json={"status": "deleted"}))
    make_client(handler).delete_project("p1")
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/api/mappings/projects/p1"


def test_list_state_mappings(make_client: MakeClient) -> None:
    _, handler = _record(
        httpx.Response(200, json=[{"workflow_state": "Backlog", "plane_state_id": "s1"}])
    )
    assert make_client(handler).list_state_mappings("p1")[0]["plane_state_id"] == "s1"


def test_set_state_mapping(make_client: MakeClient) -> None:
    seen, handler = _record(httpx.Response(200, json={"status": "set"}))
    make_client(handler).set_state_mapping("p1", "Backlog", "s1")
    assert seen["path"] == "/api/mappings/projects/p1/states"
    assert json.loads(str(seen["body"])) == {"workflow_state": "Backlog", "plane_state_id": "s1"}


def test_scan_states(make_client: MakeClient) -> None:
    _, handler = _record(
        httpx.Response(200, json=[{"id": "s1", "name": "Todo", "group": "unstarted"}])
    )
    result = make_client(handler).scan_states("p1")
    assert result[0]["name"] == "Todo"


def test_scan_states_upstream_error(make_client: MakeClient) -> None:
    _, handler = _record(httpx.Response(502, json={"detail": "Plane unreachable"}))
    with pytest.raises(ConductorError) as excinfo:
        make_client(handler).scan_states("p1")
    assert excinfo.value.status_code == 502
    assert excinfo.value.detail == "Plane unreachable"


def test_error_detail_falls_back_to_text(make_client: MakeClient) -> None:
    _, handler = _record(httpx.Response(500, text="Internal Server Error"))
    with pytest.raises(ConductorError) as excinfo:
        make_client(handler).list_config()
    assert excinfo.value.detail == "Internal Server Error"


def test_transport_error_becomes_conductor_error(make_client: MakeClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    with pytest.raises(ConductorError) as excinfo:
        make_client(handler).status()
    assert excinfo.value.status_code == 0
    assert "Connection refused" in excinfo.value.detail
