from starlette.requests import Request
from starlette.routing import Mount

from conductor.config import BootstrapSettings
from conductor.crypto import generate_key
from conductor.main import create_app
from conductor.ui.views import _origin


def _request(headers: dict[str, str], *, scheme: str = "http") -> Request:
    return Request(
        {
            "type": "http",
            "scheme": scheme,
            "server": ("internal", 8420),
            "path": "/",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
    )


def test_origin_prefers_forwarded_headers() -> None:
    request = _request(
        {
            "host": "internal:8420",
            "x-forwarded-host": "dem.eroizzy.com",
            "x-forwarded-proto": "https",
        }
    )
    assert _origin(request) == "https://dem.eroizzy.com"


def test_origin_falls_back_to_host_and_scheme() -> None:
    assert _origin(_request({"host": "localhost:8420"})) == "http://localhost:8420"


def _settings() -> BootstrapSettings:
    return BootstrapSettings(
        dem_secret_key=generate_key(), database_url="sqlite+aiosqlite:///:memory:"
    )


def test_console_pages_registered() -> None:
    # Importing the app wires the NiceGUI console; its pages must all be present.
    from nicegui import app as core

    paths = {getattr(route, "path", None) for route in core.routes}
    assert {"/", "/login", "/config", "/projects", "/states"} <= paths


def test_create_app_mounts_console_alongside_ops_routes() -> None:
    app = create_app(_settings())
    # The conductor's own ops route survives next to the console mount.
    assert "/health" in {getattr(route, "path", None) for route in app.routes}
    # NiceGUI is mounted (as the last, catch-all route) into the same FastAPI app.
    assert any(isinstance(route, Mount) for route in app.routes)
