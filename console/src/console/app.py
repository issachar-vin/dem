from __future__ import annotations

import os
from collections.abc import Callable

import streamlit as st

from console.api_client import ConductorClient
from console.views import auth, config, projects, states, wizard

PAGES: dict[str, Callable[[ConductorClient], None]] = {
    "Setup wizard": wizard.render,
    "Configuration": config.render,
    "Project mappings": projects.render,
    "State mappings": states.render,
}


@st.cache_resource
def get_client() -> ConductorClient:
    return ConductorClient(os.environ.get("CONDUCTOR_API_URL", "http://localhost:8420"))


def main() -> None:
    st.set_page_config(page_title="DEM Console", layout="wide")
    client = get_client()

    token = st.session_state.get("auth_token")
    if not token:
        client.token = None
        auth.render_gate(client)
        return
    client.token = token

    st.sidebar.title("DEM Console")
    choice = st.sidebar.radio("Navigate", list(PAGES))
    if st.sidebar.button("Sign out"):
        del st.session_state["auth_token"]
        st.rerun()
    PAGES[choice](client)


main()
