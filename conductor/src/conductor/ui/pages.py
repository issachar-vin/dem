"""Console pages: /config (all fields + migration), /projects (the design exemplar — project
cards with stat tiles, repo resource rows, kebab menus, a collapsed add-repository form), /states
(canonical → Plane state mapping), and /jobs (the intake queue — data-dense, so the one
legitimate table, restyled)."""

from __future__ import annotations

import binascii
import json
import logging
from collections import defaultdict
from typing import Any

import yaml
from cryptography.fernet import InvalidToken
from nicegui import ui

from conductor import agent_runs, job_events, plane, verify
from conductor import jobs as jobs_mod
from conductor.agents import dockerctl
from conductor.localtime import format_display
from conductor.mappings import MappingStore, RepoMappingView
from conductor.models import WorkflowState
from conductor.plane import PlaneError
from conductor.store import ConfigFieldView, ConfigStore
from conductor.tickets import TicketStore
from conductor.ui import kit, widgets
from conductor.ui.context import get_context
from conductor.ui.shell import layout, page

logger = logging.getLogger("conductor")


async def _apply_targets_upload(mappings: MappingStore, file: Any) -> int:
    """Read an uploaded targets.yml and import it, returning the count. `file` is NiceGUI 3.x's
    `FileUpload` (async `.text()`/`.read()`); this seam keeps the adapter in one testable place."""
    return await mappings.import_targets_text(await file.text())


async def _apply_bundle_upload(store: ConfigStore, file: Any, passphrase: str) -> int:
    return await store.import_bundle(await file.read(), passphrase)


# ── configuration ────────────────────────────────────────────────────────────────
@ui.page("/config")
async def config_page() -> None:
    layout("/config")
    by_step: dict[str, list[ConfigFieldView]] = defaultdict(list)
    for field in await get_context().store.list_config():
        by_step[str(field.step)].append(field)

    resolved = await get_context().store.resolved()
    models = await verify.list_claude_models(
        oauth_token=resolved.get("claude_code_oauth_token") or None,
        api_key=resolved.get("anthropic_api_key") or None,
    )

    with page():
        kit.page_header(
            "Configuration",
            "Every field in one place. The wizard is the guided version of this page.",
        )
        steps = list(by_step)
        with ui.tabs().classes("w-full v2-tabs") as tabs:
            for step in steps:
                ui.tab(step, label=step.title())
            ui.tab("migration", label="Migration")
        with ui.tab_panels(tabs, value=steps[0]).classes("w-full").style("background:transparent"):
            for step, fields in by_step.items():
                with ui.tab_panel(step).classes("px-0"), kit.panel():
                    _config_section(step, fields, models)
            with ui.tab_panel("migration").classes("px-0"):
                _migration_panel()


# notify mode → the URL field it needs; used to show only the relevant one (parity with the wizard).
_NOTIFY_URL_FIELD = {
    "ntfy": "notify_ntfy_url",
    "slack": "notify_slack_webhook_url",
    "webhook": "notify_webhook_url",
}


def _config_section(step: str, fields: list[ConfigFieldView], models: list[str]) -> None:
    """Render one config tab with the same behaviours as the wizard, reusing the same widgets:
    live model dropdowns on Claude, mode-driven visibility on GitHub (poll interval) and
    notifications (the selected URL), and a Test-connection button on the verifiable steps."""
    section = widgets.Section()
    boxes: dict[str, Any] = {}
    for field in fields:
        if field.name.startswith("claude_model_"):
            section.model(field, models)  # live-fetched dropdown, shared with the wizard
        else:
            boxes[field.name] = section.field(field)
    if step == "github" and {"github_event_mode", "github_poll_interval_seconds"} <= boxes.keys():
        boxes["github_poll_interval_seconds"].bind_visibility_from(
            boxes["github_event_mode"], "value", value="poll"
        )
    if step == "notifications" and "notify_mode" in boxes:
        for mode, url_name in _NOTIFY_URL_FIELD.items():
            if url_name in boxes:
                boxes[url_name].bind_visibility_from(boxes["notify_mode"], "value", value=mode)
    section.save_button(ui.navigate.reload)
    if step in ("claude", "plane", "github"):
        widgets.test_row(step)


def _export_row(icon: str, title: str, caption: str, label: str, on_click: Any) -> None:
    with (
        ui.element("div").classes("v2-row w-full p-4"),
        ui.row().classes("w-full items-center justify-between no-wrap gap-4"),
    ):
        with ui.row().classes("items-center gap-3 no-wrap"):
            kit.icon_tile(icon, color=kit.ORANGE, size=40)
            with ui.column().classes("gap-0"):
                ui.label(title).classes("font-medium").style(f"color:{kit.TEXT}")
                ui.label(caption).classes("text-xs").style(f"color:{kit.MUTED}")
        kit.secondary_button(label, icon="download", on_click=on_click)


def _migration_panel() -> None:
    """Export and import the whole configuration. Two cards: everything you can download on top,
    everything you can upload below."""
    with ui.column().classes("w-full gap-4"):
        with kit.panel():
            kit.section_header("Export", "Take the configuration with you.")

            async def download_env() -> None:
                ui.download.content(await get_context().store.export_env(), "dem.env")

            _export_row(
                "file-down",
                ".env",
                "Plaintext secrets; handle carefully.",
                "Download .env",
                download_env,
            )

            export_pass = widgets.text_input(
                "Passphrase (export)",
                placeholder="Protects the bundle below",
                password=True,
            )

            async def download_bundle() -> None:
                if not export_pass.value:
                    ui.notify("Enter an export passphrase first.", color="negative")
                    return
                blob = await get_context().store.export_bundle(export_pass.value)
                ui.download.content(blob, "dem.bundle")

            _export_row(
                "lock",
                "Encrypted bundle",
                "Passphrase-protected, safe to store.",
                "Download bundle",
                download_bundle,
            )

            async def download_targets() -> None:
                ctx = get_context()
                workspace = (await ctx.store.resolved()).get("plane_workspace_slug", "")
                ui.download.content(await ctx.mappings.export_targets(workspace), "targets.yml")

            _export_row(
                "package",
                "targets.yml",
                "Full project→repos mapping incl. secrets; plaintext, handle carefully.",
                "Download targets.yml",
                download_targets,
            )

        with kit.panel():
            kit.section_header("Import", "Restore configuration from a previous export.")
            import_pass = widgets.text_input(
                "Passphrase (import)", placeholder="The bundle's passphrase", password=True
            )

            async def on_upload(event: Any) -> None:
                if not import_pass.value:
                    ui.notify("Enter the import passphrase first.", color="negative")
                    return
                try:
                    imported = await _apply_bundle_upload(
                        get_context().store, event.file, import_pass.value
                    )
                except (InvalidToken, binascii.Error, ValueError, json.JSONDecodeError):
                    ui.notify("Wrong passphrase or corrupt bundle.", color="negative")
                    return
                except Exception:  # never let an upload fail silently with no toast
                    logger.exception("Bundle import failed")
                    ui.notify("Bundle import failed — check server logs.", color="negative")
                    return
                ui.notify(f"Imported {imported} value(s).")
                ui.navigate.reload()

            with widgets.labeled("Encrypted bundle"):
                ui.upload(label="Bundle file", auto_upload=True, on_upload=on_upload).classes(
                    "v2-upload"
                ).props("flat")

            async def on_targets_upload(event: Any) -> None:
                try:
                    imported = await _apply_targets_upload(get_context().mappings, event.file)
                except (yaml.YAMLError, ValueError):
                    ui.notify("Invalid targets.yml.", color="negative")
                    return
                except Exception:  # never let an upload fail silently with no toast
                    logger.exception("targets.yml import failed")
                    ui.notify("targets.yml import failed — check server logs.", color="negative")
                    return
                ui.notify(f"Imported {imported} project mapping(s).")
                ui.navigate.to("/projects")

            with widgets.labeled("targets.yml"):
                ui.upload(
                    label="targets.yml file", auto_upload=True, on_upload=on_targets_upload
                ).classes("v2-upload").props("flat")


# ── projects ─────────────────────────────────────────────────────────────────────
async def _plane_project_meta() -> dict[str, dict[str, str]]:
    """id → {name, emoji} from Plane, best-effort (empty on failure), so a project card can show
    its real name and icon instead of a bare UUID."""
    try:
        client = plane.client_from_resolved(await get_context().store.resolved())
        projects = await client.list_projects()
    except PlaneError:
        return {}
    meta: dict[str, dict[str, str]] = {}
    for p in projects:
        pid = str(p.get("id") or "")
        if pid:
            meta[pid] = {
                "name": str(p.get("name") or pid),
                "emoji": _plane_emoji(p.get("logo_props")),
            }
    return meta


def _plane_emoji(logo_props: Any) -> str:
    """Render a Plane project's emoji logo. Plane stores it as a **decimal** Unicode codepoint
    (e.g. 9822 → ♞); parsing it as hex yields the wrong glyph. Empty for an icon-type logo or
    anything unparseable — the card falls back to a generic tile."""
    if isinstance(logo_props, dict) and logo_props.get("in_use") == "emoji":
        value = (logo_props.get("emoji") or {}).get("value")
        try:
            return "".join(chr(int(c)) for c in str(value).split("-")) if value else ""
        except (ValueError, TypeError):
            return ""
    return ""


@ui.page("/projects")
async def projects_page() -> None:
    layout("/projects")
    ctx = get_context()
    projects = await ctx.mappings.list_projects()
    meta = await _plane_project_meta()
    repo_defaults = await verify.list_github_repos(
        token=(await ctx.store.resolved()).get("github_token", "")
    )

    with page(wide=True):
        kit.page_header(
            "Projects", "Plane projects the conductor manages and the GitHub repositories they own."
        )
        if not projects:
            with kit.panel():
                ui.label(
                    "No projects yet. Enable them in the wizard's Plane step, or add one below."
                ).style(f"color:{kit.MUTED}")
        for project in projects:
            _project_card(project, meta.get(project.plane_project_id, {}), repo_defaults)
        _add_project_card()


def _project_card(project: Any, meta: dict[str, str], repo_defaults: dict[str, str]) -> None:
    pid = project.plane_project_id
    name = meta.get("name") or pid
    emoji = meta.get("emoji") or ""

    async def change_project_icon(spec: str) -> None:
        await get_context().mappings.set_project(pid, enabled=project.enabled, icon=spec)
        ui.navigate.reload()

    def pick_project_icon() -> None:
        kit.icon_picker(change_project_icon)

    with kit.panel():
        with ui.row().classes("w-full items-start justify-between no-wrap"):
            with ui.row().classes("items-center gap-4 no-wrap"):
                if project.icon:
                    kit.icon_tile_spec(
                        project.icon, color=kit.ORANGE, size=64, on_click=pick_project_icon
                    )
                elif emoji:
                    kit.emoji_tile(emoji, on_click=pick_project_icon)
                else:
                    kit.icon_tile(
                        "layout-grid", color=kit.ORANGE, size=64, on_click=pick_project_icon
                    )
                with ui.column().classes("gap-1"):
                    ui.label(name).classes("text-2xl font-bold").style(f"color:{kit.TEXT}")
                    ui.label("Plane Project").classes("text-sm").style(f"color:{kit.MUTED}")
                    kit.copy_chip(pid)
            with ui.row().classes("items-stretch gap-6 no-wrap"):
                ui.separator().props("vertical")
                secret = project.has_webhook_secret
                kit.stat_tile(
                    "shield",
                    "Secret Connected" if secret else "No Secret",
                    "Secrets are configured and available to this project."
                    if secret
                    else "Add this project's webhook secret in the wizard.",
                    dot=kit.GREEN if secret else kit.RED,
                )
                ui.separator().props("vertical")
                count = len(project.repos)
                kit.stat_tile(
                    "package",
                    f"{count} Repositor{'y' if count == 1 else 'ies'}",
                    "The GitHub repositories connected to this project.",
                )

        ui.separator()

        add_form_holder: list[ui.element] = []
        kit.section_header(
            "Repositories",
            "GitHub repositories attached to this project.",
            action_label="Add Repository",
            action_icon="plus",
            on_action=lambda: add_form_holder[0].set_visibility(True),
        )
        for repo in project.repos:
            _repo_row(pid, repo)
        form = _add_repo_form(pid, repo_defaults)
        form.set_visibility(False)
        add_form_holder.append(form)

        ui.separator()
        with ui.row().classes("w-full items-center justify-between"):
            kit.secondary_button(
                "Import from Plane",
                icon="download",
                on_click=lambda: ui.navigate.to("/config"),
            )
            kit.danger_button(
                "Delete Project", icon="trash-2", on_click=lambda: _delete_project(pid)
            )


def _repo_row(pid: str, repo: RepoMappingView) -> None:
    color, fallback_icon = kit.role_style(repo.key)

    async def change_icon(new_spec: str) -> None:
        await get_context().mappings.set_repo(
            pid,
            repo.key,
            github_repo=repo.github_repo,
            base_branch=repo.base_branch,
            icon=new_spec,
        )
        ui.navigate.reload()

    with (
        ui.element("div").classes("v2-row w-full p-4"),
        ui.row().classes("w-full items-center justify-between no-wrap"),
    ):
        with ui.row().classes("items-center gap-4 no-wrap"):
            if repo.icon:
                kit.icon_tile_spec(
                    repo.icon, color=color, on_click=lambda: kit.icon_picker(change_icon)
                )
            else:
                kit.icon_tile(
                    fallback_icon, color=color, on_click=lambda: kit.icon_picker(change_icon)
                )
            with ui.column().classes("gap-2"):
                kit.pill(repo.key, color=color)
                kit.gh_repo(repo.github_repo)
        with ui.row().classes("items-center gap-6 no-wrap"):
            with ui.column().classes("gap-1 items-start"):
                ui.label("Base Branch").classes("text-xs").style(f"color:{kit.MUTED}")
                kit.branch_chip(repo.base_branch)
            kit.kebab(
                [
                    kit.MenuAction("Edit Repository", "pencil", lambda: _repo_dialog(pid, repo)),
                    kit.MenuAction(
                        "Change Branch",
                        "git-branch",
                        lambda: _repo_dialog(pid, repo, branch_only=True),
                    ),
                    kit.MenuAction(
                        "Remove Repository",
                        "trash-2",
                        lambda: _remove_repo(pid, repo.key),
                        danger=True,
                    ),
                ]
            )


def _add_repo_form(pid: str, repo_defaults: dict[str, str]) -> ui.element:
    form = (
        ui.element("div")
        .classes("w-full p-5 rounded-xl gap-4 flex flex-col")
        .style(f"border:1px dashed {kit.ORANGE}")
    )
    with form:
        with ui.row().classes("items-center gap-2 no-wrap"):
            kit.licon("plus", color=kit.ORANGE, size=16)
            ui.label("Add Repository").classes("font-semibold").style(f"color:{kit.ORANGE}")
        with ui.row().classes("w-full gap-4 items-start"):
            with (
                ui.column().classes("grow"),
                widgets.labeled(
                    "Repository Identifier",
                    helper="Unique name used by the system. Examples: frontend, backend, docs",
                ),
            ):
                # A combobox: `frontend`/`backend` are suggestions but any value can be typed.
                key_in = (
                    ui.select(
                        options=list(kit.ROLE_SUGGESTIONS),
                        with_input=True,
                        new_value_mode="add-unique",
                    )
                    .props("outlined dense options-dark")
                    .classes("w-full v2-field")
                )
            with ui.column().classes("grow"):
                # Live-fetched GitHub repo picker (shared with the wizard), falls back to text.
                repo_in = widgets.github_repo_field(list(repo_defaults))
            with (
                ui.column().classes("grow"),
                widgets.labeled(
                    "Base Branch", helper="The default branch to use for this repository."
                ),
            ):
                branch_in = (
                    ui.input(placeholder="e.g. main")
                    .props("outlined dense")
                    .classes("w-full v2-field")
                )

        if repo_defaults:  # seed the base branch from the picked repo's default branch

            def _seed_branch() -> None:
                branch_in.value = repo_defaults.get(str(repo_in.value or ""), branch_in.value)

            repo_in.on_value_change(_seed_branch)
        ui.label("Pick an icon by clicking the repository's tile after adding it.").classes(
            "text-xs"
        ).style(f"color:{kit.MUTED}")

        async def save() -> None:
            key = str(key_in.value or "").strip().lower()
            repo = (repo_in.value or "").strip()
            if not key:
                ui.notify("A repository identifier is required.", color="negative")
                return
            if not repo:
                ui.notify("A GitHub repository is required.", color="negative")
                return
            if not widgets.is_owner_name(repo):
                ui.notify("Repository must be in owner/name form.", color="negative")
                return
            await get_context().mappings.set_repo(
                pid, key, github_repo=repo, base_branch=(branch_in.value or "main")
            )
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            kit.secondary_button("Cancel", on_click=lambda: form.set_visibility(False))
            kit.primary_button("Add Repository", icon="plus", on_click=save)
    return form


def _repo_dialog(pid: str, repo: RepoMappingView, *, branch_only: bool = False) -> None:
    with kit.dialog_card("Change Branch" if branch_only else "Edit Repository") as dialog:
        repo_in = (
            None if branch_only else widgets.text_input("GitHub Repository", value=repo.github_repo)
        )
        branch_in = widgets.text_input("Base Branch", value=repo.base_branch)

        async def save() -> None:
            github_repo = (repo_in.value if repo_in else repo.github_repo).strip()
            if not widgets.is_owner_name(github_repo):
                ui.notify("Repository must be in owner/name form.", color="negative")
                return
            await get_context().mappings.set_repo(
                pid, repo.key, github_repo=github_repo, base_branch=(branch_in.value or "main")
            )
            dialog.close()
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end gap-2"):
            kit.secondary_button("Cancel", on_click=dialog.close)
            kit.primary_button("Save", icon="save", on_click=save)
    dialog.open()


async def _remove_repo(pid: str, key: str) -> None:
    await get_context().mappings.delete_repo(pid, key)
    ui.navigate.reload()


async def _delete_project(pid: str) -> None:
    await get_context().mappings.delete_project(pid)
    ui.navigate.reload()


def _add_project_card() -> None:
    with kit.panel():
        kit.section_header(
            "Add a project", "Register a Plane project by its ID (or use the wizard)."
        )
        with ui.row().classes("w-full gap-4 items-end"):
            with ui.column().classes("grow"):
                pid_in = widgets.text_input(
                    "Plane project ID", placeholder="e.g. 4c0c03e2-9314-42d7-…"
                )
            enabled_in = ui.checkbox("Enabled").props("dense")

            async def add_project() -> None:
                if not pid_in.value:
                    return
                await get_context().mappings.set_project(pid_in.value, enabled=enabled_in.value)
                ui.navigate.reload()

            kit.primary_button("Add Project", icon="plus", on_click=add_project)


# ── states ───────────────────────────────────────────────────────────────────────
@ui.page("/states")
async def states_page() -> None:
    layout("/states")
    ctx = get_context()
    status = await ctx.store.status()
    plane_step = next((s for s in status.steps if s.step == "plane"), None)

    with page():
        kit.page_header(
            "State mappings",
            "Map each canonical pipeline state onto one of the project's live Plane states.",
        )
        if plane_step is None or not plane_step.complete:
            with kit.panel(), ui.row().classes("items-center gap-2 no-wrap"):
                kit.status_dot(kit.YELLOW)
                ui.label("Complete and verify the Plane step in the wizard first.").style(
                    f"color:{kit.YELLOW}"
                )
            return

        projects = await ctx.mappings.list_projects()
        if not projects:
            with kit.panel():
                ui.label("Add a project mapping first.").style(f"color:{kit.MUTED}")
            return

        with kit.panel():
            labels = {
                p.plane_project_id: f"{p.plane_project_id} ({len(p.repos)} repo(s))"
                for p in projects
            }
            with widgets.labeled("Project"):
                picker = (
                    ui.select(options=labels, value=next(iter(labels)))
                    .props("outlined dense options-dark")
                    .classes("w-full v2-field")
                )
            form = ui.column().classes("w-full gap-4")

            async def load() -> None:
                form.clear()
                with form:
                    await render_state_form(str(picker.value))

            picker.on_value_change(load)
            await load()


async def render_state_form(project_id: str) -> None:
    """One project's canonical→Plane state selects + save. Shared with the wizard's Step 5."""
    ctx = get_context()
    try:
        scanned = await _scan_states(project_id)
    except PlaneError as exc:
        ui.label(f"Could not scan Plane states: {exc.detail}").style(f"color:{kit.RED}")
        return

    unmapped = "— unmapped —"
    options: dict[str, str] = {unmapped: unmapped}
    for state in scanned:
        options[str(state["id"])] = f"{state['name']} ({state['group']})"
    existing = {
        m.workflow_state: m.plane_state_id for m in await ctx.mappings.list_states(project_id)
    }

    selects: dict[str, ui.select] = {}
    for ws in WorkflowState:
        current = existing.get(ws.value)
        with widgets.labeled(ws.value):
            selects[ws.value] = (
                ui.select(options=options, value=current if current in options else unmapped)
                .props("outlined dense options-dark")
                .classes("w-full v2-field")
            )

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

    with ui.row().classes("w-full justify-end"):
        kit.primary_button("Save mappings", icon="save", on_click=save)


async def _scan_states(project_id: str) -> list[dict[str, Any]]:
    client = plane.client_from_resolved(await get_context().store.resolved())
    states = await client.list_states(project_id)
    return [{"id": s.get("id"), "name": s.get("name"), "group": s.get("group")} for s in states]


# ── jobs ─────────────────────────────────────────────────────────────────────────
_JOB_COLUMNS: list[dict[str, Any]] = [
    {"name": "id", "label": "ID", "field": "id", "align": "left", "sortable": True},
    {"name": "source", "label": "Source", "field": "source", "align": "left", "sortable": True},
    {
        "name": "event_type",
        "label": "Event",
        "field": "event_type",
        "align": "left",
        "sortable": True,
    },
    {"name": "status", "label": "Status", "field": "status", "align": "left", "sortable": True},
    {"name": "dedupe_key", "label": "Dedupe key", "field": "dedupe_key", "align": "left"},
    {
        "name": "deliveries",
        "label": "Deliveries",
        "field": "deliveries",
        "align": "right",
        "sortable": True,
    },
    {
        "name": "created_at",
        "label": "Created",
        "field": "created_at",
        "align": "left",
        "sortable": True,
    },
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]

_STATUS_COLOR = {
    "queued": kit.MUTED,
    "running": kit.ORANGE,
    "done": kit.GREEN,
    "failed": kit.RED,
    "stopped": kit.RED,
}

# Jobs still in flight can be stopped (kills their containers); terminal jobs can only be deleted.
_ACTIVE_JOB_STATUSES = {"queued", "running"}


@ui.page("/jobs")
async def jobs_page() -> None:
    layout("/jobs")
    jobs = await jobs_mod.list_jobs(get_context().sessionmaker)
    with page(wide=True):
        kit.page_header(
            "Jobs",
            "Intake queue — webhook/poll deliveries turned into work, consumed by the scheduler.",
        )
        if not jobs:
            with kit.panel():
                ui.label("No jobs yet.").classes("text-sm").style(f"color:{kit.MUTED}")
            return

        # Keep the (potentially large) payloads server-side, keyed by id, rather than shipping
        # them into every table row; the info modal looks them up on click.
        payloads = {job.id: job.raw_payloads for job in jobs}
        rows = [
            {
                "id": job.id,
                "source": job.source,
                "event_type": job.event_type,
                "status": job.status,
                "status_color": _STATUS_COLOR.get(job.status, kit.MUTED),
                "error": job.error or "",
                "ticket_id": str(job.payload.get("issue_id") or ""),
                "dedupe_key": job.dedupe_key or "—",
                "deliveries": len(job.raw_payloads),
                "created_at": format_display(job.created_at),
            }
            for job in jobs
        ]
        table = (
            ui.table(columns=_JOB_COLUMNS, rows=rows, row_key="id", pagination=25)
            .classes("w-full v2-table")
            .props("flat")
        )
        # Status as a pill-shaped badge with a leading dot, per the design system.
        table.add_slot(
            "body-cell-status",
            r"""
            <q-td :props="props">
                <span :style="'display:inline-flex;align-items:center;gap:6px;padding:2px 10px;'
                    + 'border-radius:999px;font-size:11px;font-weight:600;letter-spacing:.06em;'
                    + 'text-transform:uppercase;background:' + props.row.status_color + '22;'
                    + 'color:' + props.row.status_color">
                    <span :style="'width:7px;height:7px;border-radius:999px;background:'
                        + props.row.status_color"></span>
                    {{ props.value }}
                    <q-tooltip v-if="props.row.error" max-width="480px"
                        style="white-space:pre-wrap">{{ props.row.error }}</q-tooltip>
                </span>
            </q-td>
            """,
        )
        # Long UUID dedupe keys: truncate per-cell with an ellipsis + hover tooltip, so the column
        # can't overflow into its neighbours.
        table.add_slot(
            "body-cell-dedupe_key",
            r"""
            <q-td :props="props" class="ellipsis" style="max-width: 260px">
                {{ props.value }}
                <q-tooltip v-if="props.value !== '—'">{{ props.value }}</q-tooltip>
            </q-td>
            """,
        )
        table.add_slot(
            "body-cell-actions",
            r"""
            <q-td :props="props" class="text-right">
                <q-btn flat round dense icon="info"
                       @click="() => $parent.$emit('info', props.row)">
                    <q-tooltip>Raw payloads</q-tooltip>
                </q-btn>
                <q-btn v-if="props.row.ticket_id" flat round dense icon="terminal"
                       @click="() => $parent.$emit('logs', props.row)">
                    <q-tooltip>Agent run logs</q-tooltip>
                </q-btn>
                <q-btn v-if="props.row.status === 'queued' || props.row.status === 'running'"
                       flat round dense color="warning" icon="stop"
                       @click="() => $parent.$emit('stop', props.row)">
                    <q-tooltip>Stop job (kills its containers)</q-tooltip>
                </q-btn>
                <q-btn flat round dense color="negative" icon="delete"
                       @click="() => $parent.$emit('delete', props.row)">
                    <q-tooltip>Delete job</q-tooltip>
                </q-btn>
            </q-td>
            """,
        )
        table.on("info", lambda e: _show_payloads(int(e.args["id"]), payloads[int(e.args["id"])]))
        table.on("logs", lambda e: _show_logs(int(e.args["id"])))
        table.on("stop", lambda e: _stop_job(int(e.args["id"])))
        table.on("delete", lambda e: _delete_job(int(e.args["id"])))


def _show_payloads(job_id: int, raw_payloads: list[dict[str, Any]]) -> None:
    with kit.dialog_card(
        f"Job {job_id} — raw payloads ({len(raw_payloads)})", min_width=720
    ) as dialog:
        ui.json_editor(
            {"content": {"json": raw_payloads}, "readOnly": True, "mode": "tree"}
        ).classes("w-full")
        with ui.row().classes("w-full justify-end"):
            kit.secondary_button("Close", on_click=dialog.close)
    dialog.open()


_EVENT_COLOR = {
    "info": kit.MUTED,
    "success": kit.GREEN,
    "warning": kit.YELLOW,
    "error": kit.RED,
}


async def _show_logs(job_id: int) -> None:
    """A job's timeline: the conductor's pipeline events (prep repo, opened PR, review passed, …)
    interleaved with the agent runs by timestamp. A run still in progress streams into its row, so
    the modal polls every 2s — re-rendering only when something changed (no idle flicker) and
    keeping the view pinned to the newest output while you're at the bottom (sticky tail), restoring
    your position if you'd scrolled up. The timer stops when the dialog closes."""
    sessionmaker = get_context().sessionmaker
    state: dict[str, Any] = {"sig": None, "pct": 1.0, "at_bottom": True}

    def _on_scroll(event: Any) -> None:
        state["pct"] = event.vertical_percentage
        state["at_bottom"] = event.vertical_percentage > 0.95

    with kit.dialog_card(f"Job {job_id} — activity timeline", min_width=820) as dialog:
        scroll = ui.scroll_area().classes("w-full gap-3").style("height:60vh").on_scroll(_on_scroll)

        async def refresh() -> None:
            runs = await agent_runs.runs_for_job(sessionmaker, job_id)
            events = await job_events.events_for_job(sessionmaker, job_id)
            sig = [("r", r.id, r.status, len(r.output)) for r in runs] + [
                ("e", e.id) for e in events
            ]
            if sig == state["sig"]:
                return  # nothing changed → skip the rebuild that caused the flicker
            state["sig"] = sig
            # Merge into one chronological timeline (events are markers between the run cards).
            timeline: list[Any] = sorted([*runs, *events], key=lambda item: item.created_at)
            scroll.clear()
            with scroll:
                if not timeline:
                    ui.label("No activity recorded yet.").classes("text-sm").style(
                        f"color:{kit.MUTED}"
                    )
                for item in timeline:
                    if isinstance(item, agent_runs.AgentRunView):
                        _run_entry(item)
                    else:
                        _event_row(item)
            scroll.scroll_to(percent=1.0 if state["at_bottom"] else state["pct"])

        await refresh()
        timer = ui.timer(2.0, refresh)
        with ui.row().classes("w-full justify-end"):
            kit.secondary_button("Close", on_click=dialog.close)
    dialog.on("hide", timer.cancel)  # stop polling once the modal is closed
    dialog.open()


def _event_row(event: job_events.JobEventView) -> None:
    """A conductor pipeline step as a compact timeline marker between the agent-run cards."""
    color = _EVENT_COLOR.get(event.level, kit.MUTED)
    with ui.row().classes("w-full items-center gap-3 no-wrap px-1"):
        kit.status_dot(color)
        ui.label(event.message).classes("text-sm grow").style(
            f"color:{kit.TEXT};white-space:normal;overflow-wrap:anywhere"
        )
        ui.label(format_display(event.created_at, "%H:%M:%S")).classes("text-xs shrink-0").style(
            f"color:{kit.MUTED}"
        )


def _run_entry(run: agent_runs.AgentRunView) -> None:
    """One run as a readable card: status pill, plain-language transcript, and a clickable result
    indicator that opens the raw stream-json in a second modal. A `running` run shows a live badge
    and its partial transcript."""
    summary = agent_runs.summarize_output(run.output)
    live = run.status == "running"
    ok = run.ok and not summary.is_error
    if live:
        color, badge = kit.ORANGE, "● live"
    else:
        color, badge = (
            (kit.GREEN if ok else kit.RED),
            (summary.outcome or ("ok" if ok else "failed")),
        )
    with ui.element("div").classes("v2-row w-full p-4 gap-3 flex flex-col"):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            with ui.row().classes("items-center gap-3 no-wrap"):
                kit.pill(badge, color=color)
                ui.label(f"{run.role} · round {run.loop_round}").classes("font-medium").style(
                    f"color:{kit.TEXT}"
                )
            ui.label(format_display(run.created_at, "%Y-%m-%d %H:%M:%S")).classes("text-xs").style(
                f"color:{kit.MUTED}"
            )

        if summary.sentences:
            with ui.column().classes("gap-1 w-full").style("max-height:320px;overflow-y:auto"):
                for sentence in summary.sentences:
                    ui.label(sentence).classes("text-sm leading-snug").style(f"color:{kit.TEXT}")
        else:
            waiting = "Waiting for output…" if live else "No transcript captured."
            ui.label(waiting).classes("text-sm").style(f"color:{kit.MUTED}")

        indicator = ui.element("div").classes(
            "v2-row w-full p-3 cursor-pointer v2-lift flex items-start"
            " justify-between no-wrap gap-3"
        )
        with indicator:
            with ui.column().classes("gap-0 min-w-0 grow"):
                fallback = "Streaming…" if live else "View raw JSON"
                # Word-wrap the result — it can be a long paragraph; never truncate it.
                ui.label(summary.result_text or fallback).classes("text-sm font-medium").style(
                    f"color:{color};white-space:normal;overflow-wrap:anywhere"
                )
                if summary.meta:
                    ui.label(summary.meta).classes("text-xs").style(f"color:{kit.MUTED}")
            kit.licon("braces", color=kit.MUTED, size=16)
        indicator.on("click", lambda: _show_raw(run))


def _show_raw(run: agent_runs.AgentRunView) -> None:
    title = f"{run.role} · round {run.loop_round} — raw JSON"
    with kit.dialog_card(title, min_width=820) as dialog:
        ui.json_editor(
            {
                "content": {"json": agent_runs.parse_events(run.output)},
                "readOnly": True,
                "mode": "tree",
            }
        ).classes("w-full")
        with ui.row().classes("w-full justify-end"):
            kit.secondary_button("Close", on_click=dialog.close)
    dialog.open()


async def _stop_job(job_id: int) -> None:
    ctx = get_context()
    job = await jobs_mod.stop_job(ctx.sessionmaker, job_id)
    if job is None:
        ui.notify(f"Job {job_id} already finished — nothing to stop.")
        ui.navigate.reload()
        return
    killed = await _kill_job_containers(job)
    suffix = f" and killed {len(killed)} container(s)" if killed else ""
    ui.notify(f"Stopped job {job_id}{suffix}.")
    ui.navigate.reload()


async def _delete_job(job_id: int) -> None:
    ctx = get_context()
    job = await jobs_mod.get_job(ctx.sessionmaker, job_id)
    if job is not None and job.status in _ACTIVE_JOB_STATUSES:
        await _kill_job_containers(job)  # don't leave an orphaned container when deleting live work
    await jobs_mod.delete_job(ctx.sessionmaker, job_id)
    ui.notify(f"Deleted job {job_id}.")
    ui.navigate.reload()


async def _kill_job_containers(job: Any) -> list[str]:
    """Kill the agent/helper containers for a job's ticket and mark that ticket `stopped`. Only
    Plane engineer/planner jobs run containers; GitHub jobs carry no `issue_id` and are a no-op."""
    ctx = get_context()
    ticket_id = str(job.payload.get("issue_id", ""))
    if not ticket_id:
        return []
    await TicketStore(ctx.sessionmaker).set_status(ticket_id, "stopped")
    return await dockerctl.kill_containers(ctx.docker_factory(), ticket_id)
