from starlette.routing import Mount

from conductor.config import BootstrapSettings
from conductor.crypto import generate_key
from conductor.main import create_app


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
