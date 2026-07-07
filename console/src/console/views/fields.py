from __future__ import annotations

from typing import Any

import streamlit as st

from console.api_client import ConductorClient, ConductorError


def render_field(client: ConductorClient, field: dict[str, Any], *, key_prefix: str = "") -> None:
    """Render one catalog field (secret or setting) with an inline save. `key_prefix` keeps widget
    keys unique when the same field appears on more than one page."""
    name = field["name"]
    prefix = f"{key_prefix}{name}"
    if field["secret"]:
        _render_secret(client, field, name, prefix)
    else:
        _render_setting(client, field, name, prefix)


def _render_secret(client: ConductorClient, field: dict[str, Any], name: str, prefix: str) -> None:
    if field["set"]:
        current = f"set — ...{field['last_four']} (source: {field['source']})"
    else:
        current = "not set"
    col_input, col_button = st.columns([4, 1])
    value = col_input.text_input(
        name, value="", type="password", help=field["help"], placeholder=current, key=f"in-{prefix}"
    )
    if col_button.button("Save", key=f"save-{prefix}") and value:
        client.set_secret(name, value)
        st.rerun()


def _render_setting(client: ConductorClient, field: dict[str, Any], name: str, prefix: str) -> None:
    choices: list[str] = field["choices"]
    current = field["value"] or ""
    col_input, col_button = st.columns([4, 1])
    if choices:
        index = choices.index(current) if current in choices else 0
        value = col_input.selectbox(
            name, choices, index=index, help=field["help"], key=f"in-{prefix}"
        )
    else:
        value = col_input.text_input(name, value=current, help=field["help"], key=f"in-{prefix}")
    if col_button.button("Save", key=f"save-{prefix}"):
        try:
            client.set_setting(name, value)
            st.rerun()
        except ConductorError as exc:
            st.error(exc.detail)
