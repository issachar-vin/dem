from __future__ import annotations

import binascii
import json
from collections import defaultdict
from typing import Any

from cryptography.fernet import InvalidToken
from nicegui import app, ui
from nicegui.elements.mixins.value_element import ValueElement
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from conductor import plane, verify
from conductor.models import WorkflowState
from conductor.plane import PlaneError
from conductor.ui.context import get_context

# Reachable without a session; everything else routes through the login gate.
UNRESTRICTED = {"/login", "/favicon.ico"}

NAV = (
    ("Wizard", "/"),
    ("Configuration", "/config"),
    ("Projects", "/projects"),
    ("States", "/states"),
)


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


# ── shared layout ──────────────────────────────────────────────────────────────
def _header() -> None:
    with ui.header().classes("items-center justify-between"):
        ui.label("DEM Console").classes("text-lg font-bold")
        with ui.row():
            for label, path in NAV:
                ui.button(label, on_click=lambda p=path: ui.navigate.to(p)).props(
                    "flat color=white"
                )

        def logout() -> None:
            app.storage.user.clear()
            ui.navigate.to("/login")

        ui.button("Sign out", on_click=logout).props("flat color=white")


def _page() -> ui.column:
    return ui.column().classes("p-4 w-full max-w-3xl mx-auto")


# ── field rendering (secrets + settings) ───────────────────────────────────────
def _field_row(field: dict[str, Any]) -> None:
    name: str = field["name"]
    box: ValueElement[Any]
    with ui.row().classes("items-center w-full gap-2"):
        if field["secret"]:
            placeholder = (
                f"set — ...{field['last_four']} (source: {field['source']})"
                if field["set"]
                else "not set"
            )
            box = ui.input(
                label=name, password=True, password_toggle_button=True, placeholder=placeholder
            ).classes("grow")

            async def save_secret() -> None:
                if box.value:
                    await get_context().store.set_secret(name, box.value)
                    ui.navigate.reload()

            ui.button("Save", on_click=save_secret)
        else:
            choices: list[str] = list(field["choices"])
            current: str = field["value"] or ""
            if choices:
                box = ui.select(
                    options=choices,
                    value=current if current in choices else choices[0],
                    label=name,
                ).classes("grow")
            else:
                box = ui.input(label=name, value=current).classes("grow")

            async def save_setting() -> None:
                await get_context().store.set_setting(name, str(box.value or ""))
                ui.navigate.reload()

            ui.button("Save", on_click=save_setting)
    if field["help"]:
        ui.label(field["help"]).classes("text-xs text-gray-500")


async def _run_test(service: str) -> verify.VerifyResult:
    resolved = await get_context().store.resolved()
    if service == "claude":
        return await verify.verify_claude(
            oauth_token=resolved.get("claude_code_oauth_token") or None,
            api_key=resolved.get("anthropic_api_key") or None,
        )
    if service == "plane":
        return await verify.verify_plane(
            base_url=resolved.get("plane_base_url", ""),
            api_key=resolved.get("plane_api_key", ""),
            workspace_slug=resolved.get("plane_workspace_slug", ""),
        )
    return await verify.verify_github(token=resolved.get("github_token", ""))


def _test_button(service: str) -> None:
    result = ui.label().classes("text-sm")

    async def run() -> None:
        result.text = f"Testing {service}..."
        res = await _run_test(service)
        result.text = res.detail
        result.classes(replace="text-sm " + ("text-green-600" if res.ok else "text-red-600"))

    ui.button("Test connection", on_click=run)


# ── pages ──────────────────────────────────────────────────────────────────────
@ui.page("/")
async def wizard_page() -> None:
    _header()
    ctx = get_context()
    status = await ctx.store.status()
    by_step: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in await ctx.store.list_config():
        by_step[field["step"]].append(field)

    with _page():
        ui.label("Setup wizard").classes("text-2xl font-bold")
        ui.label("Configure each step in any order; badges reflect what the conductor still needs.")
        for step in status["steps"]:
            badge = "Complete" if step["complete"] else "Incomplete"
            with ui.expansion(
                f"{step['step'].title()} — {badge}", value=not step["complete"]
            ).classes("w-full"):
                if step["step"] == "claude":
                    ui.label("Set exactly one Claude credential: subscription token OR API key.")
                if step["missing"]:
                    ui.label("Missing: " + ", ".join(step["missing"])).classes("text-orange-600")
                for field in by_step.get(step["step"], []):
                    _field_row(field)
                if step["verifiable"]:
                    _test_button(step["step"])
        if status["complete"]:
            ui.label("Configuration is complete.").classes("text-green-600")
        else:
            ui.label("Outstanding: " + "; ".join(status["issues"])).classes("text-orange-600")


@ui.page("/config")
async def config_page() -> None:
    _header()
    ctx = get_context()
    by_step: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in await ctx.store.list_config():
        by_step[field["step"]].append(field)

    with _page():
        ui.label("Configuration").classes("text-2xl font-bold")
        for step, fields in by_step.items():
            ui.label(step.title()).classes("text-lg font-semibold mt-2")
            for field in fields:
                _field_row(field)
        ui.separator()
        _export_import()


def _export_import() -> None:
    ui.label("Export / import").classes("text-lg font-semibold mt-2")

    ui.label(".env export — plaintext secrets; handle carefully.")

    async def download_env() -> None:
        ui.download.content(await get_context().store.export_env(), "dem.env")

    ui.button("Download .env", on_click=download_env)

    ui.label("Encrypted bundle — passphrase-protected, safe to store.")
    export_pass = ui.input("Passphrase (export)", password=True)

    async def download_bundle() -> None:
        if not export_pass.value:
            ui.notify("Enter an export passphrase first.", color="negative")
            return
        blob = await get_context().store.export_bundle(export_pass.value)
        ui.download.content(blob, "dem.bundle")

    ui.button("Download bundle", on_click=download_bundle)

    ui.label("Import bundle")
    import_pass = ui.input("Passphrase (import)", password=True)

    async def on_upload(event: Any) -> None:
        if not import_pass.value:
            ui.notify("Enter the import passphrase first.", color="negative")
            return
        blob = event.content.read()
        try:
            imported = await get_context().store.import_bundle(blob, import_pass.value)
        except (InvalidToken, binascii.Error, ValueError, json.JSONDecodeError):
            ui.notify("Wrong passphrase or corrupt bundle.", color="negative")
            return
        ui.notify(f"Imported {imported} value(s).")

    ui.upload(label="Bundle file", auto_upload=True, on_upload=on_upload)


@ui.page("/projects")
async def projects_page() -> None:
    _header()
    ctx = get_context()
    projects = await ctx.mappings.list_projects()

    with _page():
        ui.label("Project mappings").classes("text-2xl font-bold")
        ui.label("Route each Plane project to a GitHub repo in owner/name form.")
        if projects:
            for project in projects:
                pid: str = project["plane_project_id"]
                with ui.row().classes("items-center w-full gap-4"):
                    ui.label(pid).classes("grow")
                    ui.label(project["repo"]).classes("grow")
                    ui.label(project["base_branch"])
                    ui.label(project["source"])

                    async def delete(project_id: str = pid) -> None:
                        await get_context().mappings.delete_project(project_id)
                        ui.navigate.reload()

                    ui.button("Delete", on_click=delete, color="negative")
        else:
            ui.label("No project mappings yet.")

        ui.separator()
        ui.label("Add / update").classes("text-lg font-semibold")
        pid_in = ui.input("Plane project ID")
        repo_in = ui.input("Repo (owner/name)")
        branch_in = ui.input("Base branch", value="main")

        async def add() -> None:
            pid_value, repo = pid_in.value, repo_in.value
            if not pid_value or not repo:
                return
            if repo.count("/") != 1 or repo.startswith("/") or repo.endswith("/"):
                ui.notify("Repo must be in owner/name form.", color="negative")
                return
            await get_context().mappings.set_project(
                pid_value, repo=repo, base_branch=branch_in.value or "main"
            )
            ui.navigate.reload()

        ui.button("Save", on_click=add)


@ui.page("/states")
async def states_page() -> None:
    _header()
    ctx = get_context()
    status = await ctx.store.status()
    plane_step = next((s for s in status["steps"] if s["step"] == "plane"), None)

    with _page():
        ui.label("State mappings").classes("text-2xl font-bold")
        ui.label("Map each canonical pipeline state onto one of the project's live Plane states.")
        if plane_step is None or not plane_step["complete"]:
            ui.label("Complete and verify the Plane step in the wizard first.").classes(
                "text-orange-600"
            )
            return

        projects = await ctx.mappings.list_projects()
        if not projects:
            ui.label("Add a project mapping first.")
            return

        labels = {p["plane_project_id"]: f"{p['plane_project_id']} → {p['repo']}" for p in projects}
        picker = ui.select(options=labels, label="Project", value=next(iter(labels)))
        form = ui.column().classes("w-full")

        async def load() -> None:
            form.clear()
            with form:
                await _render_state_form(str(picker.value))

        picker.on_value_change(load)
        await load()


async def _render_state_form(project_id: str) -> None:
    ctx = get_context()
    try:
        scanned = await _scan_states(project_id)
    except PlaneError as exc:
        ui.label(f"Could not scan Plane states: {exc.detail}").classes("text-red-600")
        return

    unmapped = "— unmapped —"
    options: dict[str, str] = {unmapped: unmapped}
    for state in scanned:
        options[str(state["id"])] = f"{state['name']} ({state['group']})"
    existing = {
        m["workflow_state"]: m["plane_state_id"] for m in await ctx.mappings.list_states(project_id)
    }

    selects: dict[str, ui.select] = {}
    for ws in WorkflowState:
        current = existing.get(ws.value)
        selects[ws.value] = ui.select(
            options=options,
            value=current if current in options else unmapped,
            label=ws.value,
        ).classes("w-full")

    async def save() -> None:
        saved = 0
        for workflow_state, box in selects.items():
            if box.value == unmapped:
                continue
            await get_context().mappings.set_state(
                project_id, WorkflowState(workflow_state), str(box.value)
            )
            saved += 1
        ui.notify(f"Saved {saved} mapping(s).")

    ui.button("Save mappings", on_click=save)


async def _scan_states(project_id: str) -> list[dict[str, Any]]:
    client = plane.client_from_resolved(await get_context().store.resolved())
    states = await client.list_states(project_id)
    return [{"id": s.get("id"), "name": s.get("name"), "group": s.get("group")} for s in states]


@ui.page("/login")
async def login_page(redirect_to: str = "/") -> RedirectResponse | None:
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
