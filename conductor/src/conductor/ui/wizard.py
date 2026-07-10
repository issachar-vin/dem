"""Setup wizard: tabbed steps with completion ticks, collapsible section bubbles that fold once
satisfied, live connection tests, model lists fetched from the account, a Plane project checklist
driving the GitHub tab, per-project repo mapping with branch seeding from each repo's GitHub
default, and per-project webhook secrets."""

from __future__ import annotations

import secrets as _secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nicegui import ui
from nicegui.elements.mixins.value_element import ValueElement
from starlette.requests import Request

from conductor import catalog, plane, verify
from conductor.mappings import ProjectMappingView, RepoMappingView
from conductor.models import WorkflowState
from conductor.plane import PlaneError
from conductor.store import ConfigFieldView
from conductor.ui import kit, widgets
from conductor.ui.context import get_context
from conductor.ui.pages import render_state_form
from conductor.ui.shell import layout, origin, page

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
            kit.ghost_button(f"Previous: {prev.title()}", on_click=lambda: tabs.set_value(prev))
        else:
            ui.element()
        if nxt is not None and allow_next:
            kit.primary_button(f"Next: {nxt.title()}", on_click=lambda: tabs.set_value(nxt))
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
    step = str(step_status.step)
    _set_tab_icon(tabs, step, step_status.complete)
    with ui.row().classes("items-center gap-2 no-wrap mt-2"):
        if step_status.complete:
            kit.status_dot(kit.GREEN)
            ui.label("This step is set up.").classes("text-sm").style(f"color:{kit.GREEN}")
        else:
            missing = ", ".join(step_status.missing) or "the fields above"
            kit.status_dot(kit.YELLOW)
            ui.label(f"Still needed: {missing}").classes("text-sm").style(f"color:{kit.YELLOW}")
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
    creds_ok = widgets.is_set(config["claude_code_oauth_token"]) or widgets.is_set(
        config["anthropic_api_key"]
    )

    with widgets.bubble("Step 1 — Claude credential (set exactly one)", complete=creds_ok):
        widgets.md("A Pro/Max **subscription token** or an **API key** — one, not both.")
        with widgets.help_disclosure("How do I get these?"):
            widgets.md(
                "- **Subscription token** (Pro/Max): run `claude setup-token` in a terminal with "
                "the Claude Code CLI installed — it opens a browser, then prints a token starting "
                "with `sk-ant-oat01-…`.\n"
                "- **API key** (metered): create one at console.anthropic.com → API keys.\n"
                "- Set one **or** the other — not both."
            )
        creds = widgets.Section()
        creds.field(config["claude_code_oauth_token"])
        creds.field(config["anthropic_api_key"])
        creds.save_button(_claude_panel.refresh)
        widgets.test_row("claude")

    if not creds_ok:
        ui.label("Set a credential above to choose models.").classes("text-sm").style(
            f"color:{kit.MUTED}"
        )
        return

    with widgets.bubble("Step 2 — Models", complete=True):
        widgets.md("Choose the model for each agent role.")
        resolved = await get_context().store.resolved()
        models = await verify.list_claude_models(
            oauth_token=resolved.get("claude_code_oauth_token") or None,
            api_key=resolved.get("anthropic_api_key") or None,
        )
        if models:
            ui.label("Models loaded live from your account.").classes("text-xs").style(
                f"color:{kit.MUTED}"
            )
        else:
            ui.label(
                "Couldn't load models from the API (check the credential above); showing stored "
                "values."
            ).classes("text-xs").style(f"color:{kit.YELLOW}")
        model_section = widgets.Section()
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
async def _plane_panel(tabs: ui.tabs, page_origin: str) -> None:
    config, steps = await _load()
    trio = ("plane_base_url", "plane_api_key", "plane_workspace_slug")
    trio_ok = all(widgets.is_set(config[n]) for n in trio)

    with widgets.bubble("Step 1 — Connect to Plane", complete=trio_ok):
        widgets.md("Enter all three, then **Test**.")
        with widgets.help_disclosure("How do I get these?"):
            widgets.md(
                "- **Base URL** — your Plane root, e.g. `https://plane.eroizzy.com` "
                "(Plane Cloud: `https://api.plane.so`).\n"
                "- **API key** — in Plane, open your profile → **Personal Access Tokens** → *Add "
                "personal access token* → copy it. Sent as the `X-API-Key` header.\n"
                "- **Workspace slug** — the segment in your Plane URL right after the domain: "
                "`plane.eroizzy.com/<slug>/…`. It's the lowercased workspace name (workspace *DEM* "
                "→ slug `dem`). Plane's API can't list it, so enter it here; Test verifies it."
            )
        connection = widgets.Section()
        connection.field(config["plane_base_url"])
        connection.field(config["plane_api_key"])
        connection.field(config["plane_workspace_slug"])
        connection.save_button(_plane_panel.refresh)
        widgets.test_row("plane")

    if not trio_ok:
        ui.label("Fill in and test the connection above to continue.").classes("text-sm").style(
            f"color:{kit.MUTED}"
        )
        return

    resolved = await get_context().store.resolved()
    with widgets.bubble(
        "Step 2 — Webhook secret", complete=widgets.is_set(config["plane_webhook_secret"])
    ):
        payload_url = f"{page_origin.rstrip('/')}/webhooks/plane"
        widgets.md(
            "1. In Plane → **Workspace Settings → Webhooks → Add webhook**.\n"
            f"2. **Payload URL:** `{payload_url}`\n"
            "3. Enable it, select **Issue** events, Save.\n"
            "4. Plane shows a **Secret key once** — copy it and paste below. The two must match, "
            "or every delivery returns 401."
        )
        widgets.payload_url_field(payload_url)
        stored_public = (resolved.get("conductor_public_url") or "").rstrip("/")
        if page_origin.rstrip("/") != stored_public:

            async def save_public() -> None:
                await get_context().store.set_setting(
                    "conductor_public_url", page_origin.rstrip("/")
                )
                ui.notify("Saved public URL")
                _plane_panel.refresh()

            kit.secondary_button(
                f"Save {page_origin.rstrip('/')} as the conductor's public URL",
                icon="save",
                on_click=save_public,
            )
        webhook = widgets.Section()
        webhook.field(config["plane_webhook_secret"])
        webhook.save_button(_plane_panel.refresh)

    with widgets.bubble(
        "Step 3 — Epic detection", complete=widgets.is_set(config["plane_epic_signal"])
    ):
        widgets.md("How the conductor recognizes an epic (the trigger).")
        widgets.md(
            "- **label** (recommended, works on Community): an issue is an epic if it carries a "
            "label named `epic`. Create that label in the project and tag the issues you want "
            "built.\n"
            "- **type**: for a Plane *Epic* work-item type — Community has none, so this falls "
            "back to the label behavior.\n"
            "- **parentless**: any issue with no parent becomes an epic (broad; usually too much)."
        )
        epic = widgets.Section()
        epic.field(config["plane_epic_signal"])
        epic.save_button(_plane_panel.refresh)

    enabled_projects = [p for p in await get_context().mappings.list_projects() if p.enabled]
    with widgets.bubble("Step 4 — Projects to manage", complete=bool(enabled_projects)):
        widgets.md(
            "Tick each Plane project the conductor should build. Enabling one reveals its repo "
            "mapping on the GitHub tab and its state mapping below."
        )
        await _project_checklist()

    states_ok = bool(enabled_projects) and await _states_mapped(enabled_projects)
    with widgets.bubble("Step 5 — Map pipeline states", complete=states_ok):
        await _state_mapping_section(enabled_projects)

    with widgets.help_disclosure("Plane Agents & bot token — under construction"):
        ui.label(
            "These will configure native Plane bot comments in a later phase. Leave blank for "
            "now — they have no effect yet."
        ).classes("text-sm").style(f"color:{kit.MUTED}")
    _step_footer(tabs, steps["plane"])


# The states the pipeline relies on today: the trigger (ready_for_dev) and the two the scheduler
# moves the card through (in_progress, in_review). Board mirroring skips any that aren't mapped.
_ESSENTIAL_STATES = (
    WorkflowState.READY_FOR_DEV,
    WorkflowState.IN_PROGRESS,
    WorkflowState.IN_REVIEW,
)


async def _states_mapped(enabled_projects: list[ProjectMappingView]) -> bool:
    mappings = get_context().mappings
    for project in enabled_projects:
        mapped = {m.workflow_state for m in await mappings.list_states(project.plane_project_id)}
        if not all(s.value in mapped for s in _ESSENTIAL_STATES):
            return False
    return True


async def _state_mapping_section(enabled_projects: list[ProjectMappingView]) -> None:
    widgets.md(
        "The conductor drives a fixed set of **canonical pipeline states** "
        "(`ready_for_dev` → `in_progress` → `in_review` → …). Your Plane project has its own state "
        "names. Map each canonical state onto the matching Plane state so the conductor can:\n"
        "- **spot the ticket you hand it** — `ready_for_dev` is the trigger, and\n"
        "- **move the card across your board** as it works — `in_progress` while building, "
        "`in_review` when the engineer is done.\n\n"
        "Leave a state unmapped and the conductor simply won't move the card there."
    )
    if not enabled_projects:
        ui.label("Enable a project in Step 4 first.").classes("text-sm").style(
            f"color:{kit.YELLOW}"
        )
        return
    for project in enabled_projects:
        pid = project.plane_project_id
        ui.label(f"{pid} ({len(project.repos)} repo(s))").classes("font-semibold mt-2").style(
            f"color:{kit.TEXT}"
        )
        await render_state_form(pid)


async def _project_checklist() -> None:
    mappings = get_context().mappings
    projects = await _plane_projects()
    if not projects:
        ui.label(
            "Couldn't list projects from Plane (check the connection above); manage them on the "
            "Projects page instead."
        ).classes("text-xs").style(f"color:{kit.YELLOW}")
        return
    enabled = {p.plane_project_id for p in await mappings.list_projects() if p.enabled}
    for project in projects:
        pid, name = project["id"], project["name"]

        async def toggle(event: object, project_id: str = pid, label: str = name) -> None:
            checked = bool(getattr(event, "value", False))
            await get_context().mappings.set_project(project_id, enabled=checked)
            ui.notify(f"{'Enabled' if checked else 'Disabled'} {label}")
            _github_panel.refresh()

        ui.checkbox(name, value=pid in enabled, on_change=toggle).props("dense")


@ui.refreshable
async def _github_panel(tabs: ui.tabs, page_origin: str) -> None:
    config, steps = await _load()
    token_ok = widgets.is_set(config["github_token"])

    with widgets.bubble("Step 1 — GitHub token", complete=token_ok):
        with widgets.help_disclosure("How do I create this?"):
            widgets.md(
                "GitHub → **Settings → Developer settings → Fine-grained tokens → Generate new "
                "token**. Grant it access to the target repos with **Contents: Read and write** "
                "and **Pull requests: Read and write**. A dedicated machine account is recommended "
                "so PRs aren't attributed to you.\n\n"
                "Full walkthrough (machine account, exact token scopes, per-project webhooks, and "
                "branch protection): [docs/SETUP_GITHUB.md]"
                "(https://github.com/issachar-vin/dem/blob/main/docs/SETUP_GITHUB.md)."
            )
        token_section = widgets.Section()
        token_section.field(config["github_token"])
        token_section.save_button(_github_panel.refresh)
        widgets.test_row("github")

    if not token_ok:
        ui.label("Set and test the token above to continue.").classes("text-sm").style(
            f"color:{kit.MUTED}"
        )
        return

    with widgets.bubble(
        "Step 2 — Event delivery", complete=widgets.is_set(config["github_event_mode"])
    ):
        widgets.md("How GitHub events reach the conductor.")
        with widgets.help_disclosure("What do these mean?"):
            widgets.md(
                "- **Event mode** — how PR review/merge events reach the conductor:\n"
                "  - **webhook** (recommended, needs a public URL): GitHub pushes events "
                "instantly. Choose it below to reveal the payload URL and secret, then in the repo "
                "→ **Settings → Webhooks → Add webhook** use that payload URL, content type "
                "**application/json**, a **secret** matching the one you save here, keep **SSL "
                "verification enabled**, and under **Which events?** pick *Let me select "
                "individual events* → **Pull requests**, **Pull request reviews**, **Pull request "
                "review comments**, and **Pull request review threads**.\n"
                "  - **poll**: the conductor periodically asks GitHub for changes — no webhook "
                "needed, but slower. Set the interval in seconds."
            )
        delivery = widgets.Section()
        mode_box = delivery.field(config["github_event_mode"])
        with (
            ui.column()
            .classes("w-full gap-2")
            .bind_visibility_from(mode_box, "value", value="poll")
        ):
            delivery.field(config["github_poll_interval_seconds"])
        delivery.save_button(_github_panel.refresh)

    projects = [p for p in await get_context().mappings.list_projects() if p.enabled]
    repos_ok = bool(projects) and all(p.repos for p in projects)
    with widgets.bubble("Step 3 — Repositories per project", complete=repos_ok):
        widgets.md(
            "Map the GitHub repos each enabled project owns. The planner assigns every ticket "
            "exactly one of them."
        )
        if not projects:
            ui.label("Enable at least one project on the Plane tab first.").classes(
                "text-sm"
            ).style(f"color:{kit.YELLOW}")
        else:
            resolved = await get_context().store.resolved()
            repo_defaults = await verify.list_github_repos(token=resolved.get("github_token", ""))
            names = {p["id"]: p["name"] for p in await _plane_projects()}
            payload_url = f"{page_origin.rstrip('/')}/webhooks/github"
            # Fallback only; each repo's base branch is seeded from its live GitHub default below.
            default_branch = "main"
            webhook_mode = mode_box.value == "webhook"
            if webhook_mode:
                widgets.payload_url_field(payload_url)
                widgets.md(
                    "One payload URL serves every repo below; routing is resolved per delivery. "
                    "Add it to **each** repo's **Settings → Webhooks → Add webhook** with content "
                    "type `application/json`, **SSL verification enabled**, this project's secret, "
                    "and *Let me select individual events* → **Pull requests**, **Pull request "
                    "reviews**, **Pull request review comments**, **Pull request review threads**."
                ).classes("text-xs")

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

            with ui.row().classes("w-full justify-end"):
                kit.primary_button("Save", icon="save", on_click=save_all)

    _step_footer(tabs, steps["github"])


@dataclass
class _RepoRow:
    exp: ui.expansion
    role: ui.select
    repo: ValueElement[Any]
    branch: ui.input


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

    with ui.element("div").classes("v2-row w-full p-4 flex flex-col gap-3"):
        ui.label(name).classes("font-semibold").style(f"color:{kit.TEXT}")
        repo_col = ui.column().classes("w-full gap-2")

        def add_row(
            role: str = "frontend", repo: str = "", branch: str = "", *, opened: bool
        ) -> None:
            with repo_col:
                exp = ui.expansion(value=opened).classes("w-full v2-help")
                color, icon = kit.role_style(role)
                with exp.add_slot("header"), ui.row().classes("items-center gap-2 no-wrap"):
                    kit.licon(icon, color=color, size=16)
                    title = f"{role.title()} — {repo}" if repo else "New repository"
                    ui.label(title).classes("text-sm").style(f"color:{kit.TEXT}")
                with exp, ui.column().classes("w-full gap-4 pt-2"):
                    with widgets.labeled(
                        "Repository Identifier",
                        helper="Unique name used by the system. Examples: frontend, backend, docs",
                    ):
                        role_in = (
                            ui.select(
                                options=list(kit.ROLE_SUGGESTIONS),
                                value=role or None,
                                with_input=True,
                                new_value_mode="add-unique",
                            )
                            .props("outlined dense options-dark")
                            .classes("w-full v2-field")
                        )
                    repo_in = widgets.github_repo_field(list(repo_defaults), repo)
                    with widgets.labeled(
                        "Base Branch", helper="The default branch to use for this repository."
                    ):
                        branch_in = (
                            ui.input(value=branch or default_branch, placeholder="e.g. main")
                            .props("outlined dense")
                            .classes("w-full v2-field")
                        )

                    if repo_defaults:  # select mode — seed the base branch from the picked repo

                        def seed_branch(
                            _: object,
                            target: ui.input = branch_in,
                            picker: ValueElement[Any] = repo_in,
                        ) -> None:
                            target.value = repo_defaults.get(
                                str(picker.value or ""), default_branch
                            )

                        repo_in.on_value_change(seed_branch)

                    def remove() -> None:
                        repo_col.remove(exp)
                        rows[:] = [r for r in rows if r.exp is not exp]

                    with ui.row().classes("w-full justify-end"):
                        kit.danger_button("Remove", icon="trash-2", on_click=remove)
            rows.append(_RepoRow(exp=exp, role=role_in, repo=repo_in, branch=branch_in))

        for r in repos:
            add_row(r.key, r.github_repo, r.base_branch, opened=False)
        with ui.row().classes("w-full"):
            kit.secondary_button(
                "Add repository", icon="plus", on_click=lambda: add_row(opened=True)
            )

        secret_box = _project_secret_input(has_secret) if webhook_mode else None

    original_keys = {r.key for r in repos}

    async def save() -> bool:
        desired: list[tuple[str, str, str]] = []
        for row in rows:
            repo = (row.repo.value or "").strip()
            role = str(row.role.value or "").strip().lower()
            if not repo:
                continue
            if not role:
                ui.notify(f"{name}: every repository needs an identifier.", color="negative")
                return False
            if not widgets.is_owner_name(repo):
                ui.notify(f"'{repo}' must be in owner/name form.", color="negative")
                return False
            desired.append((role, repo, str(row.branch.value or "main")))

        roles = [d[0] for d in desired]
        dupes = {r for r in roles if roles.count(r) > 1}
        if dupes:
            ui.notify(
                f"{name}: duplicate identifier(s) {', '.join(sorted(dupes))}.", color="negative"
            )
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
    placeholder = "•" * 12 if has_secret else "not set"
    with ui.row().classes("items-end w-full gap-2 no-wrap"):
        with (
            ui.column().classes("grow"),
            widgets.labeled("Webhook secret", helper="Shared by this project's repos."),
        ):
            box = (
                ui.input(password=True, password_toggle_button=True, placeholder=placeholder)
                .props("outlined dense")
                .classes("w-full v2-field")
            )

        def generate() -> None:
            box.value = _secrets.token_hex(32)
            ui.notify("Secret generated — reveal it to copy onto each repo's webhook, then Save.")

        kit.secondary_button("Generate", icon="dice-5", on_click=generate)
    return box


@ui.refreshable
async def _notifications_panel(tabs: ui.tabs) -> None:
    config, steps = await _load()
    with widgets.bubble("Notifications (optional)", complete=steps["notifications"].complete):
        widgets.md("Where the conductor sends alerts.")
        section = widgets.Section()
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
            ).classes("text-xs").style(f"color:{kit.MUTED}")
            section.field(config[url_field])
        else:
            ui.label("No notifications configured.").classes("text-sm").style(f"color:{kit.MUTED}")
        section.save_button(_notifications_panel.refresh)

    _step_footer(tabs, steps["notifications"])


@ui.refreshable
async def _advanced_panel(tabs: ui.tabs) -> None:
    config, steps = await _load()
    widgets.md("**Advanced** — sensible defaults; change only if you know why.")

    with widgets.bubble("Agent image (required)", complete=widgets.is_set(config["agent_image"])):
        widgets.md(
            "The Docker image the conductor runs for **every** agent container (clone, engineer, "
            "reviewer, QA). CI publishes it alongside the conductor, so the default below is the "
            "one built for this project — accept it unless you maintain your own. **The host "
            "running the containers must be able to pull this tag**, or every dispatch fails with "
            "*No such image*."
        )
        # Pre-fill the suggested image when unset (the field has no catalog default on purpose, so
        # this step reads incomplete until you actively save it).
        current = config["agent_image"].value or catalog.DEFAULT_AGENT_IMAGE
        image_box = widgets.text_input("Agent image", value=current)

        async def save_image() -> None:
            await get_context().store.set_setting("agent_image", str(image_box.value or ""))
            ui.notify("Saved")
            _advanced_panel.refresh()

        with ui.row().classes("w-full justify-end"):
            kit.primary_button("Save", icon="save", on_click=save_image)

    with widgets.bubble("Other advanced settings", complete=True):
        section = widgets.Section()
        for field in config.values():
            if field.step == "advanced" and field.name != "agent_image":
                section.field(field)
        section.save_button(_advanced_panel.refresh)

    _set_tab_icon(tabs, "advanced", steps["advanced"].complete)
    with ui.row().classes("items-center gap-2 no-wrap mt-2"):
        if all(s.complete for s in steps.values()):
            kit.status_dot(kit.GREEN)
            ui.label("Configuration is complete across every step.").classes("text-sm").style(
                f"color:{kit.GREEN}"
            )
        else:
            kit.status_dot(kit.YELLOW)
            ui.label("Some earlier steps are still incomplete — see their tabs.").classes(
                "text-sm"
            ).style(f"color:{kit.YELLOW}")
    _nav_row(tabs, "advanced", allow_next=False)


@ui.page("/")
async def wizard_page(request: Request) -> None:
    layout("/")
    page_origin = origin(request)
    status = await get_context().store.status()
    step_complete = {str(s.step): s.complete for s in status.steps}
    start = next((str(s.step) for s in status.steps if not s.complete), "claude")

    with page():
        kit.page_header(
            "Setup wizard", "Work through each tab; fields unlock as you complete the one before."
        )
        with ui.tabs().classes("w-full v2-tabs") as tabs:
            for step in STEP_ORDER:
                ui.tab(step, label=step.title(), icon=_step_icon(bool(step_complete.get(step))))
        with ui.tab_panels(tabs, value=start).classes("w-full").style("background:transparent"):
            with (
                ui.tab_panel("claude").classes("px-0"),
                ui.column().classes("w-full gap-4"),
            ):
                await _claude_panel(tabs)
            with (
                ui.tab_panel("plane").classes("px-0"),
                ui.column().classes("w-full gap-4"),
            ):
                await _plane_panel(tabs, page_origin)
            with (
                ui.tab_panel("github").classes("px-0"),
                ui.column().classes("w-full gap-4"),
            ):
                await _github_panel(tabs, page_origin)
            with (
                ui.tab_panel("notifications").classes("px-0"),
                ui.column().classes("w-full gap-4"),
            ):
                await _notifications_panel(tabs)
            with (
                ui.tab_panel("advanced").classes("px-0"),
                ui.column().classes("w-full gap-4"),
            ):
                await _advanced_panel(tabs)
