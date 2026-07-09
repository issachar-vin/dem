from __future__ import annotations

import binascii
import json
from collections import defaultdict
from typing import Any

import yaml
from cryptography.fernet import InvalidToken
from nicegui import ui

from conductor import plane
from conductor.models import WorkflowState
from conductor.plane import PlaneError
from conductor.store import ConfigFieldView
from conductor.ui.context import get_context
from conductor.ui.shell import _layout, _page
from conductor.ui.widgets import _Section


@ui.page("/config")
async def config_page() -> None:
    _layout("/config")
    by_step: dict[str, list[ConfigFieldView]] = defaultdict(list)
    for field in await get_context().store.list_config():
        by_step[str(field.step)].append(field)

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

    ui.label("targets.yml — full project→repos mapping incl. secrets; plaintext, handle carefully.")

    async def download_targets() -> None:
        ctx = get_context()
        workspace = (await ctx.store.resolved()).get("plane_workspace_slug", "")
        ui.download.content(await ctx.mappings.export_targets(workspace), "targets.yml")

    ui.button("Download targets.yml", on_click=download_targets)

    ui.label("Import targets.yml")

    async def on_targets_upload(event: Any) -> None:
        text = event.content.read().decode()
        try:
            imported = await get_context().mappings.import_targets_text(text)
        except (yaml.YAMLError, ValueError):
            ui.notify("Invalid targets.yml.", color="negative")
            return
        ui.notify(f"Imported {imported} project mapping(s).")

    ui.upload(label="targets.yml file", auto_upload=True, on_upload=on_targets_upload)


def _is_owner_name(repo: str) -> bool:
    return repo.count("/") == 1 and not repo.startswith("/") and not repo.endswith("/")


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
