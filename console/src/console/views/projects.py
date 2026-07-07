from __future__ import annotations

import streamlit as st

from console.api_client import ConductorClient, ConductorError


def render(client: ConductorClient) -> None:
    st.header("Project mappings")
    st.caption("Route each Plane project to a GitHub repo. Repo must be in `owner/name` form.")

    try:
        projects = client.list_projects()
    except ConductorError as exc:
        st.error(f"Cannot reach the conductor API: {exc.detail}")
        return

    if projects:
        header = st.columns([3, 3, 2, 2, 1])
        for col, label in zip(header, ("Project ID", "Repo", "Branch", "Source", ""), strict=True):
            col.markdown(f"**{label}**")
        for project in projects:
            pid = project["plane_project_id"]
            cols = st.columns([3, 3, 2, 2, 1])
            cols[0].text(pid)
            cols[1].text(project["repo"])
            cols[2].text(project["base_branch"])
            cols[3].text(project["source"])
            if cols[4].button("Delete", key=f"del-{pid}"):
                client.delete_project(pid)
                st.rerun()
    else:
        st.info("No project mappings yet.")

    st.divider()
    st.subheader("Add / update")
    with st.form("add-project", clear_on_submit=True):
        pid = st.text_input("Plane project ID")
        repo = st.text_input("Repo (owner/name)")
        branch = st.text_input("Base branch", value="main")
        if st.form_submit_button("Save") and pid and repo:
            try:
                client.set_project(pid, repo, branch)
                st.rerun()
            except ConductorError as exc:
                st.error(exc.detail)
