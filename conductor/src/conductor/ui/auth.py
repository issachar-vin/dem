"""Login gate: middleware redirecting unauthenticated visitors, and the login page (sign-in, or
first-run admin creation against the AuthStore) presented as a centered brand card."""

from __future__ import annotations

from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from conductor.ui import kit, widgets
from conductor.ui.context import get_context
from conductor.ui.shell import theme

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
    theme()
    ctx = get_context()
    if app.storage.user.get("authenticated"):
        return RedirectResponse(redirect_to)
    initialized = await ctx.auth.is_initialized()
    with ui.column().classes("absolute-center items-center gap-6").style("width:400px"):
        with (
            ui.element("div")
            .classes("flex items-center justify-center rounded-2xl")
            .style(f"width:56px;height:56px;background:{kit.ORANGE}22")
        ):
            kit.licon("bot", color=kit.ORANGE, size=30)
        with ui.card().classes("v2-panel w-full gap-4 p-8").props("flat"):
            if initialized:
                _login_form(redirect_to)
            else:
                _create_admin_form(redirect_to)
    return None


def _heading(title: str, subtitle: str) -> None:
    with ui.column().classes("gap-1 w-full"):
        ui.label(title).classes("text-xl font-bold").style(f"color:{kit.TEXT}")
        ui.label(subtitle).classes("text-sm").style(f"color:{kit.MUTED}")


def _login_form(redirect_to: str) -> None:
    _heading("Welcome back", "Sign in to manage the conductor.")
    username = widgets.text_input("Username", placeholder="Your username").props("autofocus")
    password = widgets.text_input("Password", placeholder="Your password", password=True)

    async def submit() -> None:
        if not username.value or not password.value:
            return
        if await get_context().auth.verify_credentials(username.value, password.value):
            app.storage.user.update(username=username.value, authenticated=True)
            ui.navigate.to(redirect_to)
        else:
            ui.notify("Invalid username or password.", color="negative")

    password.on("keydown.enter", submit)
    kit.primary_button("Sign in", on_click=submit).classes("w-full")


def _create_admin_form(redirect_to: str) -> None:
    _heading("Create your admin account", "First run — this account manages the conductor.")
    username = widgets.text_input("Username", placeholder="Choose a username").props("autofocus")
    password = widgets.text_input("Password", placeholder="Choose a password", password=True)
    confirm = widgets.text_input(
        "Confirm password", placeholder="Repeat the password", password=True
    )

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

    confirm.on("keydown.enter", submit)
    kit.primary_button("Create account", on_click=submit).classes("w-full")
