from __future__ import annotations

import binascii
import json
import logging
from collections import defaultdict
from typing import Any

import yaml
from cryptography.fernet import InvalidToken
from nicegui import ui

from conductor import jobs as jobs_mod
from conductor import plane
from conductor.mappings import MappingStore
from conductor.models import WorkflowState
from conductor.plane import PlaneError
from conductor.store import ConfigFieldView, ConfigStore
from conductor.ui.context import get_context
from conductor.ui.shell import _layout, _page
from conductor.ui.widgets import _is_owner_name, _Section

logger = logging.getLogger("conductor")


async def _apply_targets_upload(mappings: MappingStore, file: Any) -> int:
    """Read an uploaded targets.yml and import it, returning the count. `file` is NiceGUI 3.x's
    `FileUpload` (async `.text()`/`.read()`); the old `event.content.read()` API is gone, so this
    seam keeps the adapter in one testable place."""
    return await mappings.import_targets_text(await file.text())


async def _apply_bundle_upload(store: ConfigStore, file: Any, passphrase: str) -> int:
    return await store.import_bundle(await file.read(), passphrase)


@ui.page("/config")
async def config_page() -> None:
    _layout("/config")
    by_step: dict[str, list[ConfigFieldView]] = defaultdict(list)
    for field in await get_context().store.list_config():
        by_step[str(field.step)].append(field)

    with _page():
        ui.label("Configuration").classes("text-2xl font-bold")
        ui.label("Every field in one place. The wizard is the guided version of this page.")
        steps = list(by_step)
        with ui.tabs().classes("w-full") as tabs:
            for step in steps:
                ui.tab(step, label=step.title())
            ui.tab("migration", label="Migration")
        with ui.tab_panels(tabs, value=steps[0]).classes("w-full"):
            for step, fields in by_step.items():
                with ui.tab_panel(step):
                    section = _Section()
                    for field in fields:
                        section.field(field)
                    section.save_button(ui.navigate.reload)
            with ui.tab_panel("migration"):
                _migration_panel()


def _migration_panel() -> None:
    """Export and import the whole configuration. Split into two clear sections: everything you can
    download on top, everything you can upload below."""
    ui.label("Export").classes("text-lg font-semibold")
    ui.label(".env — plaintext secrets; handle carefully.").classes("text-sm text-gray-500")

    async def download_env() -> None:
        ui.download.content(await get_context().store.export_env(), "dem.env")

    ui.button("Download .env", on_click=download_env)

    ui.label("Encrypted bundle — passphrase-protected, safe to store.").classes(
        "text-sm text-gray-500"
    )
    export_pass = ui.input("Passphrase (export)", password=True)

    async def download_bundle() -> None:
        if not export_pass.value:
            ui.notify("Enter an export passphrase first.", color="negative")
            return
        blob = await get_context().store.export_bundle(export_pass.value)
        ui.download.content(blob, "dem.bundle")

    ui.button("Download bundle", on_click=download_bundle)

    ui.label(
        "targets.yml — full project→repos mapping incl. secrets; plaintext, handle carefully."
    ).classes("text-sm text-gray-500")

    async def download_targets() -> None:
        ctx = get_context()
        workspace = (await ctx.store.resolved()).get("plane_workspace_slug", "")
        ui.download.content(await ctx.mappings.export_targets(workspace), "targets.yml")

    ui.button("Download targets.yml", on_click=download_targets)

    ui.separator()
    ui.label("Import").classes("text-lg font-semibold mt-2")
    ui.label("Encrypted bundle").classes("text-sm text-gray-500")
    import_pass = ui.input("Passphrase (import)", password=True)

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

    ui.upload(label="Bundle file", auto_upload=True, on_upload=on_upload)

    ui.label("targets.yml").classes("text-sm text-gray-500")

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

    ui.upload(label="targets.yml file", auto_upload=True, on_upload=on_targets_upload)


@ui.page("/projects")
async def projects_page() -> None:
    _layout("/projects")
    ctx = get_context()
    projects = await ctx.mappings.list_projects()

    with _page():
        ui.label("Project mappings").classes("text-2xl font-bold")
        ui.label("Enable each Plane project and map the GitHub repos it owns (owner/name form).")
        if projects:
            for project in projects:
                pid = project.plane_project_id
                with ui.card().classes("w-full gap-2"):
                    with ui.row().classes("items-center w-full gap-4"):
                        ui.label(pid).classes("grow font-semibold")
                        secret_note = "secret set" if project.has_webhook_secret else "no secret"
                        ui.label(f"{'enabled' if project.enabled else 'disabled'} · {secret_note}")
                        ui.label(project.source)

                        async def delete_proj(project_id: str = pid) -> None:
                            await get_context().mappings.delete_project(project_id)
                            ui.navigate.reload()

                        ui.button("Delete project", on_click=delete_proj, color="negative")

                    for repo in project.repos:
                        with ui.row().classes("items-center w-full gap-4 pl-4"):
                            ui.label(repo.key).classes("w-24")
                            ui.label(repo.github_repo).classes("grow")
                            ui.label(repo.base_branch)

                            async def delete_repo(
                                project_id: str = pid, key: str = repo.key
                            ) -> None:
                                await get_context().mappings.delete_repo(project_id, key)
                                ui.navigate.reload()

                            ui.button("Remove", on_click=delete_repo, color="negative")

                    key_in = ui.input("Repo key (e.g. ui, backend)")
                    repo_in = ui.input("Repo (owner/name)")
                    branch_in = ui.input("Base branch", value="main")

                    async def add_repo(
                        project_id: str = pid,
                        key_field: Any = key_in,
                        repo_field: Any = repo_in,
                        branch_field: Any = branch_in,
                    ) -> None:
                        key, repo = key_field.value, repo_field.value
                        if not key or not repo:
                            return
                        if not _is_owner_name(repo):
                            ui.notify("Repo must be in owner/name form.", color="negative")
                            return
                        await get_context().mappings.set_repo(
                            project_id,
                            key,
                            github_repo=repo,
                            base_branch=branch_field.value or "main",
                        )
                        ui.navigate.reload()

                    ui.button("Add repo", on_click=add_repo)
        else:
            ui.label("No project mappings yet.")

        ui.separator()
        ui.label("Add project").classes("text-lg font-semibold")
        pid_in = ui.input("Plane project ID")
        enabled_in = ui.checkbox("Enabled")

        async def add_project() -> None:
            if not pid_in.value:
                return
            await get_context().mappings.set_project(pid_in.value, enabled=enabled_in.value)
            ui.navigate.reload()

        ui.button("Save", on_click=add_project)


@ui.page("/states")
async def states_page() -> None:
    _layout("/states")
    ctx = get_context()
    status = await ctx.store.status()
    plane_step = next((s for s in status.steps if s.step == "plane"), None)

    with _page():
        ui.label("State mappings").classes("text-2xl font-bold")
        ui.label("Map each canonical pipeline state onto one of the project's live Plane states.")
        if plane_step is None or not plane_step.complete:
            ui.label("Complete and verify the Plane step in the wizard first.").classes(
                "text-orange-600"
            )
            return

        projects = await ctx.mappings.list_projects()
        if not projects:
            ui.label("Add a project mapping first.")
            return

        labels = {
            p.plane_project_id: f"{p.plane_project_id} ({len(p.repos)} repo(s))" for p in projects
        }
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
        m.workflow_state: m.plane_state_id for m in await ctx.mappings.list_states(project_id)
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


@ui.page("/jobs")
async def jobs_page() -> None:
    _layout("/jobs")
    jobs = await jobs_mod.list_jobs(get_context().sessionmaker)
    with _page():
        ui.label("Jobs").classes("text-2xl font-bold")
        ui.label(
            "Intake queue — webhook/poll deliveries turned into work. Nothing consumes these yet "
            "(the dispatcher lands in Phase 4)."
        )
        if not jobs:
            ui.label("No jobs yet.").classes("text-sm text-gray-500")
            return

        # Keep the (potentially large) payloads server-side, keyed by id, rather than shipping them
        # into every table row; the info modal looks them up on click.
        payloads = {job.id: job.raw_payloads for job in jobs}
        rows = [
            {
                "id": job.id,
                "source": job.source,
                "event_type": job.event_type,
                "status": job.status,
                "dedupe_key": job.dedupe_key or "—",
                "deliveries": len(job.raw_payloads),
                "created_at": job.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for job in jobs
        ]
        table = (
            ui.table(columns=_JOB_COLUMNS, rows=rows, row_key="id", pagination=25)
            .classes("w-full")
            .props("flat bordered")
        )
        # Long UUID dedupe keys: truncate per-cell with an ellipsis + hover tooltip, so the column
        # can't overflow into its neighbours (the bug in the hand-rolled row layout).
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
                <q-btn flat round dense color="negative" icon="delete"
                       @click="() => $parent.$emit('delete', props.row)">
                    <q-tooltip>Delete job</q-tooltip>
                </q-btn>
            </q-td>
            """,
        )
        table.on("info", lambda e: _show_payloads(int(e.args["id"]), payloads[int(e.args["id"])]))
        table.on("delete", lambda e: _delete_job(int(e.args["id"])))


def _show_payloads(job_id: int, raw_payloads: list[dict[str, Any]]) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-4xl"):
        ui.label(f"Job {job_id} — raw payloads ({len(raw_payloads)})").classes("text-lg font-bold")
        ui.json_editor(
            {"content": {"json": raw_payloads}, "readOnly": True, "mode": "tree"}
        ).classes("w-full")
        ui.button("Close", on_click=dialog.close)
    dialog.open()


async def _delete_job(job_id: int) -> None:
    await jobs_mod.delete_job(get_context().sessionmaker, job_id)
    ui.notify(f"Deleted job {job_id}.")
    ui.navigate.reload()
