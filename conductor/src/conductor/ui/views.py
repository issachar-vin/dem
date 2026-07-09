from __future__ import annotations

import binascii
import json
import secrets
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from cryptography.fernet import InvalidToken
from nicegui import app, ui
from nicegui.elements.mixins.value_element import ValueElement
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from conductor import __version__, plane, verify
from conductor.models import WorkflowState
from conductor.plane import PlaneError
from conductor.ui.context import get_context

# Reachable without a session; everything else routes through the login gate.
UNRESTRICTED = {"/login", "/favicon.ico"}

# label, path, Material icon
NAV = (
    ("Wizard", "/", "auto_fix_high"),
    ("Configuration", "/config", "tune"),
    ("Projects", "/projects", "hub"),
    ("States", "/states", "account_tree"),
)

STEP_ORDER = ("claude", "plane", "github", "notifications", "advanced")

OnSaved = Callable[[], Any]


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


# ── theme (dark grey surfaces, orange accent) ───────────────────────────────────
ORANGE = "#F97316"
HEADER_BG = "#161619"  # darker grey than the page/surfaces


def _theme() -> None:
    ui.dark_mode(True)
    ui.colors(
        primary=ORANGE,
        secondary=ORANGE,
        accent=ORANGE,
        dark="#2A2A2E",  # cards, drawer, panels
        dark_page="#1E1E22",  # page background
    )


# ── shell (header + collapsible drawer) ─────────────────────────────────────────
def _logout() -> None:
    app.storage.user.clear()
    ui.navigate.to("/login")


async def _toggle_nav(drawer: ui.left_drawer) -> None:
    # Below Quasar's drawer breakpoint the drawer is a hidden overlay where `mini` is
    # ignored, so on mobile the button must toggle visibility, not the mini state.
    if await ui.run_javascript("window.innerWidth <= 1023"):
        drawer.toggle()
        return
    collapsed = not bool(app.storage.user.get("nav_collapsed", False))
    app.storage.user["nav_collapsed"] = collapsed
    drawer.props(remove="mini")
    if collapsed:
        drawer.props("mini")


def _layout(active: str) -> None:
    _theme()
    collapsed = bool(app.storage.user.get("nav_collapsed", False))
    drawer = ui.left_drawer(value=False, bordered=True).props("show-if-above mini-width=72")
    if collapsed:
        drawer.props("mini")
    with drawer, ui.column().classes("h-full w-full gap-0"):
        with ui.list().props("padding").classes("w-full"):
            for label, path, icon in NAV:
                item = ui.item(on_click=lambda p=path: ui.navigate.to(p)).props("clickable")
                if path == active:
                    item.classes("bg-primary text-white")
                with item:
                    with ui.item_section().props("avatar"):
                        ui.icon(icon)
                    with ui.item_section():
                        ui.item_label(label)
        ui.space()
        ui.label(f"v{__version__}").classes("text-xs opacity-60 w-full text-center pb-3")

    with ui.header().classes("items-center justify-between").style(f"background-color:{HEADER_BG}"):
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="menu", on_click=lambda: _toggle_nav(drawer)).props(
                "flat round color=white"
            )
            ui.label("D.E.M. Console").classes("text-lg font-bold")
        username = str(app.storage.user.get("username", "user"))
        with ui.button(icon="account_circle").props("flat color=white no-caps"):
            ui.label(username).classes("ml-2")
            with ui.menu():
                ui.menu_item("Sign out", on_click=_logout)


def _page() -> ui.column:
    return ui.column().classes("p-4 w-full max-w-3xl mx-auto")


def _origin(request: Request) -> str:
    """Public origin of this page as the browser reached it. Reads proxy headers (Caddy sets
    X-Forwarded-* and preserves Host) rather than request.url, which carries the internal bind."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    )
    return f"{proto}://{host}"


# ── field helpers ───────────────────────────────────────────────────────────────
def _is_set(field: dict[str, Any]) -> bool:
    return bool(field["set"]) if field["secret"] else bool(field["value"])


def _input(field: dict[str, Any]) -> ValueElement[Any]:
    """Render one field's editor (no save button). Secrets show a masked placeholder and stay
    empty until a new value is typed."""
    name: str = field["name"]
    box: ValueElement[Any]
    if field["secret"]:
        placeholder = ("•" * 6 + field["last_four"]) if field["set"] else "not set"
        box = (
            ui.input(
                label=name, password=True, password_toggle_button=True, placeholder=placeholder
            )
            .props("stack-label")
            .classes("w-full")
        )
    else:
        choices: list[str] = list(field["choices"])
        current: str = field["value"] or ""
        if choices:
            box = ui.select(
                options=choices, value=current if current in choices else choices[0], label=name
            ).classes("w-full")
        else:
            box = ui.input(label=name, value=current).classes("w-full")
    if field["help"]:
        ui.label(field["help"]).classes("text-xs text-gray-500")
    if field["secret"] and field["set"]:
        ui.label(f"A value is stored (source: {field['source']}); type to replace it.").classes(
            "text-xs text-gray-500"
        )
    return box


def _payload_url_field(url: str) -> None:
    """Read-only payload URL input with a copy-to-clipboard button in its append slot."""

    def copy() -> None:
        ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(url)})")
        ui.notify("Copied to clipboard")

    ui.label("Payload URL (detected from this page's address):")
    with (
        ui.input(value=url).props("readonly").classes("w-full font-mono") as box,
        box.add_slot("append"),
    ):
        ui.button(icon="content_copy", on_click=copy).props("flat dense round").tooltip("Copy")


def _model_input(field: dict[str, Any], models: list[str]) -> ValueElement[Any]:
    name: str = field["name"]
    current: str = field["value"] or ""
    options = list(dict.fromkeys([*models, *([current] if current else [])]))
    return ui.select(
        options=options,
        value=current if current in options else (options[0] if options else None),
        label=name,
        with_input=True,
    ).classes("w-full")


class _Section:
    """A group of fields saved together by one button. Secrets are only written when a new value
    is typed; settings are always written (upsert)."""

    def __init__(self) -> None:
        self._items: list[tuple[dict[str, Any], ValueElement[Any]]] = []

    def field(self, field: dict[str, Any]) -> ValueElement[Any]:
        box = _input(field)
        self._items.append((field, box))
        return box

    def model(self, field: dict[str, Any], models: list[str]) -> None:
        self._items.append((field, _model_input(field, models)))

    def save_button(self, on_saved: OnSaved, *, label: str = "Save") -> None:
        async def save() -> None:
            store = get_context().store
            for field, box in self._items:
                if field["secret"]:
                    if box.value:
                        await store.set_secret(field["name"], box.value)
                else:
                    await store.set_setting(field["name"], str(box.value or ""))
            ui.notify("Saved")
            on_saved()

        ui.button(label, icon="save", on_click=save).props("color=primary")


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


def _test_row(service: str) -> None:
    result = ui.label().classes("text-sm")

    async def run() -> None:
        result.text = f"Testing {service}…"
        res = await _run_test(service)
        result.text = res.detail
        result.classes(replace="text-sm " + ("text-green-600" if res.ok else "text-red-600"))

    ui.button("Test connection", icon="bolt", on_click=run).props("outline")


def _next_step(step: str) -> str | None:
    index = STEP_ORDER.index(step)
    return STEP_ORDER[index + 1] if index + 1 < len(STEP_ORDER) else None


def _step_icon(complete: bool) -> str:
    return "check_circle" if complete else "radio_button_unchecked"


def _set_tab_icon(tabs: ui.tabs, step: str, complete: bool) -> None:
    # The tab bar is built once at page load; after an in-session save we update the
    # completed step's icon in place so the check appears without a full refresh.
    tab = tabs.default_slot.children[STEP_ORDER.index(step)]
    if isinstance(tab, ui.tab):
        tab.set_icon(_step_icon(complete))


def _step_footer(tabs: ui.tabs, step_status: dict[str, Any]) -> None:
    ui.separator()
    step = step_status["step"]
    _set_tab_icon(tabs, step, step_status["complete"])
    if step_status["complete"]:
        ui.label("This step is set up.").classes("text-green-600")
        nxt = _next_step(step)
        if nxt is not None:
            ui.button(f"Next: {nxt.title()} →", on_click=lambda: tabs.set_value(nxt)).props(
                "color=primary"
            )
    else:
        missing = ", ".join(step_status["missing"]) or "the fields above"
        ui.label(f"Still needed: {missing}").classes("text-orange-600")


async def _load() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    store = get_context().store
    config = {f["name"]: f for f in await store.list_config()}
    steps = {s["step"]: s for s in (await store.status())["steps"]}
    return config, steps


# ── wizard panels ────────────────────────────────────────────────────────────────
@ui.refreshable
async def _claude_panel(tabs: ui.tabs) -> None:
    config, steps = await _load()
    ui.markdown(
        "**Step 1 — Claude credential.** Set exactly one: a Pro/Max **subscription token** or an "
        "**API key**."
    )
    with ui.expansion("How do I get these?", icon="help_outline").classes("w-full"):
        ui.markdown(
            "- **Subscription token** (Pro/Max): run `claude setup-token` in a terminal with the "
            "Claude Code CLI installed — it opens a browser, then prints a token starting with "
            "`sk-ant-oat01-…`.\n"
            "- **API key** (metered): create one at console.anthropic.com → API keys.\n"
            "- Set one **or** the other — not both."
        )
    creds = _Section()
    creds.field(config["claude_code_oauth_token"])
    creds.field(config["anthropic_api_key"])
    creds.save_button(_claude_panel.refresh)
    _test_row("claude")

    if not (_is_set(config["claude_code_oauth_token"]) or _is_set(config["anthropic_api_key"])):
        ui.separator()
        ui.label("Set a credential above to choose models.").classes("text-sm text-gray-500")
        return

    ui.separator()
    ui.markdown("**Step 2 — Models.** Choose the model for each agent role.")
    resolved = await get_context().store.resolved()
    models = await verify.list_claude_models(
        oauth_token=resolved.get("claude_code_oauth_token") or None,
        api_key=resolved.get("anthropic_api_key") or None,
    )
    if models:
        ui.label("Models loaded live from your account.").classes("text-xs text-gray-500")
    else:
        ui.label(
            "Couldn't load models from the API (check the credential above); showing stored values."
        ).classes("text-xs text-orange-600")
    model_section = _Section()
    for role in (
        "claude_model_engineer",
        "claude_model_planner",
        "claude_model_reviewer",
        "claude_model_qa",
    ):
        model_section.model(config[role], models)
    model_section.save_button(_claude_panel.refresh)

    _step_footer(tabs, steps["claude"])


@ui.refreshable
async def _plane_panel(tabs: ui.tabs, origin: str) -> None:
    config, steps = await _load()
    ui.markdown("**Step 1 — Connect to Plane.** Enter all three, then Test.")
    with ui.expansion("How do I get these?", icon="help_outline").classes("w-full"):
        ui.markdown(
            "- **Base URL** — your Plane root, e.g. `https://plane.eroizzy.com` "
            "(Plane Cloud: `https://api.plane.so`).\n"
            "- **API key** — in Plane, open your profile → **Personal Access Tokens** → *Add "
            "personal access token* → copy it. Sent as the `X-API-Key` header.\n"
            "- **Workspace slug** — the segment in your Plane URL right after the domain: "
            "`plane.eroizzy.com/<slug>/…`. It's the lowercased workspace name (workspace *DEM* → "
            "slug `dem`). Plane's API can't list it, so enter it here; Test verifies it."
        )
    connection = _Section()
    connection.field(config["plane_base_url"])
    connection.field(config["plane_api_key"])
    connection.field(config["plane_workspace_slug"])
    connection.save_button(_plane_panel.refresh)
    _test_row("plane")

    trio = ("plane_base_url", "plane_api_key", "plane_workspace_slug")
    if not all(_is_set(config[n]) for n in trio):
        ui.separator()
        ui.label("Fill in and test the connection above to continue.").classes(
            "text-sm text-gray-500"
        )
        return

    ui.separator()
    ui.markdown("**Step 2 — Webhook secret.**")
    resolved = await get_context().store.resolved()
    payload_url = f"{origin.rstrip('/')}/webhooks/plane"
    ui.markdown(
        "1. In Plane → **Workspace Settings → Webhooks → Add webhook**.\n"
        f"2. **Payload URL:** `{payload_url}`\n"
        "3. Enable it, select **Issue** events, Save.\n"
        "4. Plane shows a **Secret key once** — copy it and paste below. The two must match, or "
        "every delivery returns 401."
    )
    _payload_url_field(payload_url)
    stored_public = (resolved.get("conductor_public_url") or "").rstrip("/")
    if origin.rstrip("/") != stored_public:

        async def save_public() -> None:
            await get_context().store.set_setting("conductor_public_url", origin.rstrip("/"))
            ui.notify("Saved public URL")
            _plane_panel.refresh()

        ui.button(
            f"Save {origin.rstrip('/')} as the conductor's public URL",
            icon="save",
            on_click=save_public,
        ).props("flat color=primary")
    webhook = _Section()
    webhook.field(config["plane_webhook_secret"])
    webhook.save_button(_plane_panel.refresh)

    ui.separator()
    ui.markdown("**Step 3 — Epic detection.** How the conductor recognizes an epic (the trigger).")
    ui.markdown(
        "- **label** (recommended, works on Community): an issue is an epic if it carries a label "
        "named `epic`. Create that label in the project and tag the issues you want built.\n"
        "- **type**: for a Plane *Epic* work-item type — Community has none, so this falls back to "
        "the label behavior.\n"
        "- **parentless**: any issue with no parent becomes an epic (broad; usually too much)."
    )
    epic = _Section()
    epic.field(config["plane_epic_signal"])
    epic.save_button(_plane_panel.refresh)

    ui.separator()
    with ui.expansion("Plane Agents & bot token — under construction", icon="construction").classes(
        "w-full"
    ):
        ui.label(
            "These will configure native Plane bot comments in a later phase. Leave blank for "
            "now — they have no effect yet."
        )
    _step_footer(tabs, steps["plane"])


@ui.refreshable
async def _github_panel(tabs: ui.tabs, origin: str) -> None:
    config, steps = await _load()
    ui.markdown("**Step 1 — GitHub token.**")
    with ui.expansion("How do I create this?", icon="help_outline").classes("w-full"):
        ui.markdown(
            "GitHub → **Settings → Developer settings → Fine-grained tokens → Generate new "
            "token**. Grant it access to the target repos with **Contents: Read and write** and "
            "**Pull requests: Read and write**. A dedicated machine account is recommended so PRs "
            "aren't attributed to you."
        )
    token_section = _Section()
    token_section.field(config["github_token"])
    token_section.save_button(_github_panel.refresh)
    _test_row("github")

    if not _is_set(config["github_token"]):
        ui.separator()
        ui.label("Set and test the token above to continue.").classes("text-sm text-gray-500")
        return

    ui.separator()
    ui.markdown(
        "**Step 2 — Delivery & branch.** How PRs are targeted and how GitHub events reach "
        "the conductor."
    )
    with ui.expansion("What do these mean?", icon="help_outline").classes("w-full"):
        ui.markdown(
            "- **Base branch** — the branch each PR is opened against (usually `main`).\n"
            "- **Event mode** — how PR review/merge events reach the conductor:\n"
            "  - **webhook** (recommended, needs a public URL): GitHub pushes events instantly. "
            "Choose it below to reveal the payload URL and secret, then in the repo → **Settings → "
            "Webhooks → Add webhook** use that payload URL, content type **application/json**, "
            "a **secret** matching the one you save here, keep **SSL verification enabled**, and "
            "under **Which events?** pick *Let me select individual events* → **Pull requests**, "
            "**Pull request reviews**, **Pull request review comments**, and **Pull request review "
            "threads**.\n"
            "  - **poll**: the conductor periodically asks GitHub for changes — no webhook needed, "
            "but slower. Set the interval in seconds."
        )
    delivery = _Section()
    delivery.field(config["github_base_branch"])
    mode_box = delivery.field(config["github_event_mode"])

    payload_url = f"{origin.rstrip('/')}/webhooks/github"
    with (
        ui.column().classes("w-full gap-2").bind_visibility_from(mode_box, "value", value="webhook")
    ):
        _payload_url_field(payload_url)
        ui.markdown(
            "On the GitHub webhook, set **content type** to `application/json`, keep **SSL "
            "verification enabled** (the public URL has a valid certificate), and under **Which "
            "events would you like to trigger this webhook?** choose *Let me select individual "
            "events* and tick **Pull requests**, **Pull request reviews**, **Pull request review "
            "comments**, and **Pull request review threads**."
        ).classes("text-xs text-gray-500")
        secret_box = delivery.field(config["github_webhook_secret"])
        ui.label(
            "Use the same secret on both sides — save it here and paste it on GitHub."
        ).classes("text-xs text-gray-500")

        def generate_secret() -> None:
            secret_box.value = secrets.token_hex(32)
            ui.notify("Secret generated — reveal it to copy onto the GitHub webhook, then Save.")

        ui.button("Generate secret", icon="casino", on_click=generate_secret).props(
            "flat color=primary"
        )
    with ui.column().classes("w-full gap-2").bind_visibility_from(mode_box, "value", value="poll"):
        delivery.field(config["github_poll_interval_seconds"])
    delivery.save_button(_github_panel.refresh)

    _step_footer(tabs, steps["github"])


@ui.refreshable
async def _notifications_panel(tabs: ui.tabs) -> None:
    config, steps = await _load()
    ui.markdown("**Notifications** — where the conductor sends alerts (optional).")
    section = _Section()
    section.field(config["notify_mode"])
    mode = config["notify_mode"]["value"] or "none"
    url_field = {
        "ntfy": "notify_ntfy_url",
        "slack": "notify_slack_webhook_url",
        "webhook": "notify_webhook_url",
    }.get(mode)
    if url_field is not None:
        ui.label(
            {
                "ntfy": "Your ntfy topic URL, e.g. https://ntfy.sh/your-topic.",
                "slack": "A Slack Incoming Webhook URL.",
                "webhook": "Any URL to receive a JSON POST per notification.",
            }[mode]
        ).classes("text-xs text-gray-500")
        section.field(config[url_field])
    else:
        ui.label("No notifications configured.").classes("text-sm text-gray-500")
    section.save_button(_notifications_panel.refresh)

    _step_footer(tabs, steps["notifications"])


@ui.refreshable
async def _advanced_panel(tabs: ui.tabs) -> None:
    config, steps = await _load()
    ui.markdown("**Advanced** — sensible defaults; change only if you know why.")
    section = _Section()
    for field in config.values():
        if field["step"] == "advanced":
            section.field(field)
    section.save_button(_advanced_panel.refresh)
    _set_tab_icon(tabs, "advanced", steps["advanced"]["complete"])
    ui.separator()
    if all(s["complete"] for s in steps.values()):
        ui.label("Configuration is complete across every step.").classes("text-green-600")
    else:
        ui.label("Some earlier steps are still incomplete — see their tabs.").classes(
            "text-orange-600"
        )


# ── pages ──────────────────────────────────────────────────────────────────────
@ui.page("/")
async def wizard_page(request: Request) -> None:
    _layout("/")
    origin = _origin(request)
    status = await get_context().store.status()
    step_complete = {s["step"]: s["complete"] for s in status["steps"]}
    start = next((s["step"] for s in status["steps"] if not s["complete"]), "claude")

    with _page():
        ui.label("Setup wizard").classes("text-2xl font-bold")
        ui.label("Work through each tab; fields unlock as you complete the one before.")
        with ui.tabs().classes("w-full") as tabs:
            for step in STEP_ORDER:
                ui.tab(step, label=step.title(), icon=_step_icon(bool(step_complete.get(step))))
        with ui.tab_panels(tabs, value=start).classes("w-full"):
            with ui.tab_panel("claude"):
                await _claude_panel(tabs)
            with ui.tab_panel("plane"):
                await _plane_panel(tabs, origin)
            with ui.tab_panel("github"):
                await _github_panel(tabs, origin)
            with ui.tab_panel("notifications"):
                await _notifications_panel(tabs)
            with ui.tab_panel("advanced"):
                await _advanced_panel(tabs)


@ui.page("/config")
async def config_page() -> None:
    _layout("/config")
    by_step: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in await get_context().store.list_config():
        by_step[field["step"]].append(field)

    with _page():
        ui.label("Configuration").classes("text-2xl font-bold")
        ui.label("Every field in one place. The wizard is the guided version of this page.")
        for step, fields in by_step.items():
            ui.label(step.title()).classes("text-lg font-semibold mt-2")
            section = _Section()
            for field in fields:
                section.field(field)
            section.save_button(ui.navigate.reload)
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
    _layout("/projects")
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
    _layout("/states")
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
