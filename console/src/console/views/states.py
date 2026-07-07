from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from console.api_client import ConductorClient, ConductorError


def render(client: ConductorClient) -> None:
    st.header("State mappings")
    st.caption("Map each canonical pipeline state onto one of the project's live Plane states.")

    try:
        status = client.status()
        projects = client.list_projects()
    except ConductorError as exc:
        st.error(f"Cannot reach the conductor API: {exc.detail}")
        return

    plane_step = next((s for s in status.steps if s.step == "plane"), None)
    if plane_step is None or not plane_step.complete:
        st.warning("Complete and verify the Plane step in the Setup wizard first.")
        return
    if not projects:
        st.info("Add a project mapping first.")
        return

    project_ids: list[str] = [str(p["plane_project_id"]) for p in projects]
    pid = st.selectbox("Project", project_ids, format_func=_project_label(projects))
    if pid is None:
        return

    try:
        scanned = client.scan_states(pid)
    except ConductorError as exc:
        st.error(f"Could not scan Plane states: {exc.detail}")
        return

    workflow_states = client.workflow_states()
    existing = {m["workflow_state"]: m["plane_state_id"] for m in client.list_state_mappings(pid)}

    unmapped = "— unmapped —"
    options: list[str] = [unmapped, *(str(s["id"]) for s in scanned)]
    labels: dict[str, str] = {str(s["id"]): f"{s['name']} ({s['group']})" for s in scanned}

    with st.form(f"states-{pid}"):
        selections: dict[str, str] = {}
        for ws in workflow_states:
            current = existing.get(ws)
            index = options.index(current) if current in options else 0
            choice = st.selectbox(
                ws,
                options,
                index=index,
                format_func=lambda o: labels.get(o, o),
                key=f"ws-{pid}-{ws}",
            )
            selections[ws] = choice
        if st.form_submit_button("Save mappings"):
            _save(client, pid, selections)


def _project_label(projects: list[dict[str, object]]) -> Callable[[str], str]:
    by_id = {str(p["plane_project_id"]): str(p["repo"]) for p in projects}
    return lambda pid: f"{pid} → {by_id.get(pid, '')}"


def _save(client: ConductorClient, pid: str, selections: dict[str, str]) -> None:
    saved = 0
    for ws, plane_state_id in selections.items():
        if plane_state_id == "— unmapped —":
            continue
        client.set_state_mapping(pid, ws, plane_state_id)
        saved += 1
    st.success(f"Saved {saved} mapping(s).")
