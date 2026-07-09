from __future__ import annotations

from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from conductor.ui.context import get_context
from conductor.ui.shell import _theme

# Reachable without a session; everything else routes through the login gate.
UNRESTRICTED = {"/login", "/favicon.ico"}


@app.add_middleware
class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated visitors to the login page. Only guards NiceGUI pages — the
    conductor's own /webhooks, /health and /metrics routes live on the parent app and never reach
    this mounted sub-app."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if (
            app.storage.user.get("authenticated")
            or path in UNRESTRICTED
            or path.startswith("/_nicegui")
        ):
            return await call_next(request)
        return RedirectResponse(f"/login?redirect_to={path}")


@ui.page("/login")
async def login_page(redirect_to: str = "/") -> RedirectResponse | None:
    _theme()
    ctx = get_context()
    if app.storage.user.get("authenticated"):
        return RedirectResponse(redirect_to)
    initialized = await ctx.auth.is_initialized()
    with ui.card().classes("absolute-center items-stretch"):
        if initialized:
            _login_form(redirect_to)
        else:
            _create_admin_form(redirect_to)
    return None


def _login_form(redirect_to: str) -> None:
    ui.label("Sign in to manage the conductor.").classes("text-lg")
    username = ui.input("Username").props("autofocus")
    password = ui.input("Password", password=True, password_toggle_button=True)

    async def submit() -> None:
        if not username.value or not password.value:
            return
        if await get_context().auth.verify_credentials(username.value, password.value):
            app.storage.user.update(username=username.value, authenticated=True)
            ui.navigate.to(redirect_to)
        else:
            ui.notify("Invalid username or password.", color="negative")

    password.on("keydown.enter", submit)
    ui.button("Sign in", on_click=submit)


def _create_admin_form(redirect_to: str) -> None:
    ui.label("First run — create the admin account.").classes("text-lg")
    username = ui.input("Choose a username").props("autofocus")
    password = ui.input("Choose a password", password=True, password_toggle_button=True)
    confirm = ui.input("Confirm password", password=True)

    async def submit() -> None:
        if not username.value or not password.value:
            return
        if password.value != confirm.value:
            ui.notify("Passwords do not match.", color="negative")
            return
        auth = get_context().auth
        if await auth.is_initialized():
            ui.notify("An admin account already exists.", color="negative")
            return
        await auth.create_admin(username.value, password.value)
        app.storage.user.update(username=username.value, authenticated=True)
        ui.navigate.to(redirect_to)

    ui.button("Create account", on_click=submit)
