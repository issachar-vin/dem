from __future__ import annotations

import secrets

from nicegui import ui
from starlette.requests import Request

from conductor import catalog, verify
from conductor.store import ConfigFieldView
from conductor.ui.context import get_context
from conductor.ui.shell import _layout, _origin, _page
from conductor.ui.widgets import _is_set, _payload_url_field, _Section, _test_row

STEP_ORDER = ("claude", "plane", "github", "notifications", "advanced")


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


def _step_footer(tabs: ui.tabs, step_status: catalog.StepStatus) -> None:
    ui.separator()
    step = str(step_status.step)
    _set_tab_icon(tabs, step, step_status.complete)
    if step_status.complete:
        ui.label("This step is set up.").classes("text-green-600")
        nxt = _next_step(step)
        if nxt is not None:
            ui.button(f"Next: {nxt.title()} →", on_click=lambda: tabs.set_value(nxt)).props(
                "color=primary"
            )
    else:
        missing = ", ".join(step_status.missing) or "the fields above"
        ui.label(f"Still needed: {missing}").classes("text-orange-600")


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
