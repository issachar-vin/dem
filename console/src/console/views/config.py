from __future__ import annotations

from collections import defaultdict
from typing import Any

import streamlit as st

from console.api_client import ConductorClient, ConductorError
from console.views.fields import render_field


def render(client: ConductorClient) -> None:
    st.header("Configuration")

    try:
        config = client.list_config()
    except ConductorError as exc:
        st.error(f"Cannot reach the conductor API: {exc.detail}")
        return

    by_step: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in config:
        by_step[field["step"]].append(field)

    for step, fields in by_step.items():
        st.subheader(step.title())
        for field in fields:
            render_field(client, field, key_prefix="cfg-")

    st.divider()
    _export_import(client)


def _export_import(client: ConductorClient) -> None:
    st.subheader("Export / import")

    st.markdown("**.env export** — plaintext secrets; handle carefully.")
    if st.button("Generate .env"):
        st.session_state["env_export"] = client.export_env()
    if "env_export" in st.session_state:
        st.download_button(
            "Download dem.env",
            data=st.session_state["env_export"],
            file_name="dem.env",
            mime="text/plain",
        )

    st.markdown("**Encrypted bundle** — passphrase-protected, safe to store.")
    export_pass = st.text_input("Passphrase (export)", type="password", key="export-pass")
    if st.button("Generate bundle") and export_pass:
        st.session_state["bundle_export"] = client.export_bundle(export_pass)
    if "bundle_export" in st.session_state:
        st.download_button(
            "Download dem.bundle",
            data=st.session_state["bundle_export"],
            file_name="dem.bundle",
            mime="text/plain",
        )

    st.markdown("**Import bundle**")
    uploaded = st.file_uploader("Bundle file", type=None, key="bundle-upload")
    import_pass = st.text_input("Passphrase (import)", type="password", key="import-pass")
    if st.button("Import") and uploaded is not None and import_pass:
        blob_b64 = uploaded.getvalue().decode()
        try:
            imported = client.import_bundle(blob_b64, import_pass)
            st.success(f"Imported {imported} value(s).")
        except ConductorError as exc:
            st.error(exc.detail)
