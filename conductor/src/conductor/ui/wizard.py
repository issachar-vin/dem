from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nicegui import ui
from starlette.requests import Request

from conductor import catalog, plane, verify
from conductor.mappings import RepoMappingView
from conductor.plane import PlaneError
from conductor.store import ConfigFieldView
from conductor.ui.context import get_context
from conductor.ui.shell import _layout, _origin, _page
from conductor.ui.widgets import _is_owner_name, _is_set, _payload_url_field, _Section, _test_row

STEP_ORDER = ("claude", "plane", "github", "notifications", "advanced")


async def _plane_projects() -> list[dict[str, str]]:
    """Workspace projects (id + name) for the wizard's project pickers. Empty on any failure —
    the caller degrades to the manually-managed /projects page."""
    client = plane.client_from_resolved(await get_context().store.resolved())
    try:
        projects = await client.list_projects()
    except PlaneError:
        return []
    return [
        {"id": str(p["id"]), "name": str(p.get("name") or p["id"])} for p in projects if p.get("id")
    ]


def _next_step(step: str) -> str | None:
    index = STEP_ORDER.index(step)
    return STEP_ORDER[index + 1] if index + 1 < len(STEP_ORDER) else None


def _prev_step(step: str) -> str | None:
    index = STEP_ORDER.index(step)
    return STEP_ORDER[index - 1] if index > 0 else None


def _nav_row(tabs: ui.tabs, step: str, *, allow_next: bool) -> None:
    """Previous button pinned bottom-left, Next bottom-right. Empty flex children hold the ends so
    a missing button doesn't drag the other off its side."""
    prev, nxt = _prev_step(step), _next_step(step)
    with ui.row().classes("w-full justify-between mt-2"):
        if prev is not None:
            ui.button(f"← Previous: {prev.title()}", on_click=lambda: tabs.set_value(prev)).props(
                "flat"
            )
        else:
            ui.element()
        if nxt is not None and allow_next:
            ui.button(f"Next: {nxt.title()} →", on_click=lambda: tabs.set_value(nxt)).props(
                "color=primary"
            )
        else:
            ui.element()


def _step_icon(complete: bool) -> str:
    return "check_circle" if complete else "radio_button_unchecked"


def _set_tab_icon(tabs: ui.tabs, step: str, complete: bool) -> None:
    # The tab bar is built once at page load; after an in-session save we update the
    # completed step's icon in place so the check appears without a full refresh.
    tab = tabs.default_slot.children[STEP_ORDER.index(step)]
    if isinstance(tab, ui.tab):
        tab.set_icon(_step_icon(complete))


def _step_footer(tabs: ui.tabs, step_status: catalog.StepStatus) -> None:
    ui.separator()
    step = str(step_status.step)
    _set_tab_icon(tabs, step, step_status.complete)
    if step_status.complete:
        ui.label("This step is set up.").classes("text-green-600")
    else:
        missing = ", ".join(step_status.missing) or "the fields above"
        ui.label(f"Still needed: {missing}").classes("text-orange-600")
    _nav_row(tabs, step, allow_next=step_status.complete)


async def _load() -> tuple[dict[str, ConfigFieldView], dict[str, catalog.StepStatus]]:
    store = get_context().store
    config = {f.name: f for f in await store.list_config()}
    steps = {str(s.step): s for s in (await store.status()).steps}
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
    ui.markdown(
        "**Step 4 — Projects to manage.** Tick each Plane project the conductor should build. "
        "Enabling one reveals its repo mapping on the GitHub tab."
    )
    await _project_checklist()

    ui.separator()
    with ui.expansion("Plane Agents & bot token — under construction", icon="construction").classes(
        "w-full"
    ):
        ui.label(
            "These will configure native Plane bot comments in a later phase. Leave blank for "
            "now — they have no effect yet."
        )
    _step_footer(tabs, steps["plane"])


async def _project_checklist() -> None:
    mappings = get_context().mappings
    projects = await _plane_projects()
    if not projects:
        ui.label(
            "Couldn't list projects from Plane (check the connection above); manage them on the "
            "Projects page instead."
        ).classes("text-xs text-orange-600")
        return
    enabled = {p.plane_project_id for p in await mappings.list_projects() if p.enabled}
    for project in projects:
        pid, name = project["id"], project["name"]

        async def toggle(event: object, project_id: str = pid, label: str = name) -> None:
            checked = bool(getattr(event, "value", False))
            await get_context().mappings.set_project(project_id, enabled=checked)
            ui.notify(f"{'Enabled' if checked else 'Disabled'} {label}")
            _github_panel.refresh()

        ui.checkbox(name, value=pid in enabled, on_change=toggle)


@ui.refreshable
async def _github_panel(tabs: ui.tabs, origin: str) -> None:
    config, steps = await _load()
    ui.markdown("**Step 1 — GitHub token.**")
    with ui.expansion("How do I create this?", icon="help_outline").classes("w-full"):
        ui.markdown(
            "GitHub → **Settings → Developer settings → Fine-grained tokens → Generate new "
            "token**. Grant it access to the target repos with **Contents: Read and write** and "
            "**Pull requests: Read and write**. A dedicated machine account is recommended so PRs "
            "aren't attributed to you.\n\n"
            "Full walkthrough (machine account, exact token scopes, per-project webhooks, and "
            "branch protection): [docs/SETUP_GITHUB.md]"
            "(https://github.com/issachar-vin/dem/blob/main/docs/SETUP_GITHUB.md)."
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
    ui.markdown("**Step 2 — Event delivery.** How GitHub events reach the conductor.")
    with ui.expansion("What do these mean?", icon="help_outline").classes("w-full"):
        ui.markdown(
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
    mode_box = delivery.field(config["github_event_mode"])
    with ui.column().classes("w-full gap-2").bind_visibility_from(mode_box, "value", value="poll"):
        delivery.field(config["github_poll_interval_seconds"])
    delivery.save_button(_github_panel.refresh)

    ui.separator()
    ui.markdown(
        "**Step 3 — Repositories per project.** Map the GitHub repos each enabled project owns. "
        "The planner assigns every ticket exactly one of them."
    )
    projects = [p for p in await get_context().mappings.list_projects() if p.enabled]
    if not projects:
        ui.label("Enable at least one project on the Plane tab first.").classes(
            "text-sm text-orange-600"
        )
        _step_footer(tabs, steps["github"])
        return

    resolved = await get_context().store.resolved()
    repo_defaults = await verify.list_github_repos(token=resolved.get("github_token", ""))
    names = {p["id"]: p["name"] for p in await _plane_projects()}
    payload_url = f"{origin.rstrip('/')}/webhooks/github"
    # Fallback only; each repo's base branch is seeded from its live GitHub default branch below.
    default_branch = "main"
    webhook_mode = mode_box.value == "webhook"
    if webhook_mode:
        _payload_url_field(payload_url)
        ui.markdown(
            "One payload URL serves every repo below; routing is resolved per delivery. Add it to "
            "**each** repo's **Settings → Webhooks → Add webhook** with content type "
            "`application/json`, **SSL verification enabled**, this project's secret, and *Let me "
            "select individual events* → **Pull requests**, **Pull request reviews**, **Pull "
            "request review comments**, **Pull request review threads**."
        ).classes("text-xs text-gray-500")

    savers = [
        _project_section(
            project.plane_project_id,
            names.get(project.plane_project_id, project.plane_project_id),
            project.repos,
            project.has_webhook_secret,
            repo_defaults,
            default_branch,
            webhook_mode,
        )
        for project in projects
    ]

    async def save_all() -> None:
        for saver in savers:
            if not await saver():
                return
        ui.notify("Saved.")
        _github_panel.refresh()

    ui.button("Save", icon="save", on_click=save_all).props("color=primary")

    _step_footer(tabs, steps["github"])


ROLE_CHOICES = ("frontend", "backend", "other")


@dataclass
class _RepoRow:
    exp: ui.expansion
    role: ui.select
    repo: ui.select | ui.input
    branch: ui.input


def _repo_field(options: list[str], value: str = "") -> ui.select | ui.input:
    """Repo picker: a live-fetched select (typeable) when the token could list repos, else a
    free-typed owner/name input as the documented fallback."""
    if options:
        opts = list(dict.fromkeys([*options, *([value] if value else [])]))
        return ui.select(
            options=opts, value=value or None, label="Repository (owner/name)", with_input=True
        ).classes("w-full")
    return ui.input(label="Repository (owner/name)", value=value).classes("w-full")


def _project_section(
    project_id: str,
    name: str,
    repos: list[RepoMappingView],
    has_secret: bool,
    repo_defaults: dict[str, str],
    default_branch: str,
    webhook_mode: bool,
) -> Callable[[], Awaitable[bool]]:
    """Render one project's editable repo list (add/remove rows in place, nothing persisted) plus
    its webhook secret, and return an async save() reconciling the DB with the on-screen state.
    `repo_defaults` maps each listable repo to its GitHub default branch, used to auto-fill the
    base branch when a repo is picked."""
    rows: list[_RepoRow] = []

    with ui.card().classes("w-full gap-2"):
        ui.label(name).classes("font-semibold")
        repo_col = ui.column().classes("w-full gap-2")

        def add_row(
            role: str = "frontend", repo: str = "", branch: str = "", *, opened: bool
        ) -> None:
            title = f"{role.title()} — {repo}" if repo else "New repository"
            with (
                repo_col,
                ui.expansion(title, icon="folder", value=opened).classes("w-full") as exp,
            ):
                role_in = ui.select(options=list(ROLE_CHOICES), value=role, label="Role").classes(
                    "w-full"
                )
                repo_in = _repo_field(list(repo_defaults), repo)
                branch_in = ui.input(label="Base branch", value=branch or default_branch).classes(
                    "w-full"
                )

                if repo_defaults:  # select mode — seed the base branch from the picked repo

                    def seed_branch(
                        _: object,
                        target: ui.input = branch_in,
                        picker: ui.select | ui.input = repo_in,
                    ) -> None:
                        target.value = repo_defaults.get(str(picker.value or ""), default_branch)

                    repo_in.on_value_change(seed_branch)

                def remove() -> None:
                    repo_col.remove(exp)
                    rows[:] = [r for r in rows if r.exp is not exp]

                ui.button("Remove", icon="delete", on_click=remove).props("flat color=negative")
            rows.append(_RepoRow(exp=exp, role=role_in, repo=repo_in, branch=branch_in))

        for r in repos:
            add_row(r.key, r.github_repo, r.base_branch, opened=False)
        ui.button("Add repository", icon="add", on_click=lambda: add_row(opened=True)).props(
            "flat color=primary"
        )

        secret_box = _project_secret_input(has_secret) if webhook_mode else None

    original_keys = {r.key for r in repos}

    async def save() -> bool:
        desired: list[tuple[str, str, str]] = []
        for row in rows:
            repo = (row.repo.value or "").strip()
            if not repo:
                continue
            if not _is_owner_name(repo):
                ui.notify(f"'{repo}' must be in owner/name form.", color="negative")
                return False
            desired.append((str(row.role.value), repo, str(row.branch.value or "main")))

        roles = [d[0] for d in desired]
        dupes = {r for r in roles if r != "other" and roles.count(r) > 1}
        if dupes:
            ui.notify(f"{name}: duplicate role(s) {', '.join(sorted(dupes))}.", color="negative")
            return False

        mappings = get_context().mappings
        for role, repo, branch in desired:
            await mappings.set_repo(project_id, role, github_repo=repo, base_branch=branch)
        for key in original_keys - {d[0] for d in desired}:
            await mappings.delete_repo(project_id, key)
        if secret_box is not None and secret_box.value:
            await mappings.set_project(project_id, enabled=True, webhook_secret=secret_box.value)
        return True

    return save


def _project_secret_input(has_secret: bool) -> ui.input:
    """Webhook-secret field + Generate button (no save — persisted by Step 3's catch-all Save)."""
    ui.separator()
    ui.label("Webhook secret (shared by this project's repos):").classes("text-xs text-gray-500")
    placeholder = "•" * 12 if has_secret else "not set"
    with ui.row().classes("items-end w-full gap-2"):
        box = (
            ui.input(
                label="Secret", password=True, password_toggle_button=True, placeholder=placeholder
            )
            .props("stack-label")
            .classes("grow")
        )

        def generate() -> None:
            box.value = secrets.token_hex(32)
            ui.notify("Secret generated — reveal it to copy onto each repo's webhook, then Save.")

        ui.button("Generate", icon="casino", on_click=generate).props("flat color=primary")
    return box


@ui.refreshable
async def _notifications_panel(tabs: ui.tabs) -> None:
    config, steps = await _load()
    ui.markdown("**Notifications** — where the conductor sends alerts (optional).")
    section = _Section()
    section.field(config["notify_mode"])
    mode = config["notify_mode"].value or "none"
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
        if field.step == "advanced":
            section.field(field)
    section.save_button(_advanced_panel.refresh)
    _set_tab_icon(tabs, "advanced", steps["advanced"].complete)
    ui.separator()
    if all(s.complete for s in steps.values()):
        ui.label("Configuration is complete across every step.").classes("text-green-600")
    else:
        ui.label("Some earlier steps are still incomplete — see their tabs.").classes(
            "text-orange-600"
        )
    _nav_row(tabs, "advanced", allow_next=False)


@ui.page("/")
async def wizard_page(request: Request) -> None:
    _layout("/")
    origin = _origin(request)
    status = await get_context().store.status()
    step_complete = {str(s.step): s.complete for s in status.steps}
    start = next((str(s.step) for s in status.steps if not s.complete), "claude")

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
