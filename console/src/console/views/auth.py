from __future__ import annotations

import streamlit as st

from console.api_client import ConductorClient, ConductorError


def render_gate(client: ConductorClient) -> None:
    """Login / create-admin screen. Stores the session token in st.session_state on success."""
    st.header("DEM Console")

    try:
        initialized = client.auth_status()
    except ConductorError as exc:
        st.error(f"Cannot reach the conductor API: {exc.detail}")
        return

    if initialized:
        _login(client)
    else:
        _create_admin(client)


def _login(client: ConductorClient) -> None:
    st.caption("Sign in to manage the conductor.")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in") and username and password:
            try:
                st.session_state["auth_token"] = client.login(username, password)
                st.rerun()
            except ConductorError as exc:
                st.error(exc.detail)


def _create_admin(client: ConductorClient) -> None:
    st.caption("First run — create the admin account. This is asked once.")
    with st.form("create-admin"):
        username = st.text_input("Choose a username")
        password = st.text_input("Choose a password", type="password")
        confirm = st.text_input("Confirm password", type="password")
        if st.form_submit_button("Create account") and username and password:
            if password != confirm:
                st.error("Passwords do not match.")
                return
            try:
                st.session_state["auth_token"] = client.register(username, password)
                st.rerun()
            except ConductorError as exc:
                st.error(exc.detail)
