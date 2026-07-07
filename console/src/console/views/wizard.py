from __future__ import annotations

from collections import defaultdict
from typing import Any

import streamlit as st

from console.api_client import ConductorClient, ConductorError
from console.views.fields import render_field


def render(client: ConductorClient) -> None:
    st.header("Setup wizard")
    st.caption(
        "Complete each step. Steps unlock nothing — configure them in any order; the badges "
        "reflect what the conductor still needs before it can run."
    )

    try:
        status = client.status()
        config = client.list_config()
    except ConductorError as exc:
        st.error(f"Cannot reach the conductor API: {exc.detail}")
        return

    by_step: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in config:
        by_step[field["step"]].append(field)

    for step in status.steps:
        badge = "Complete" if step.complete else "Incomplete"
        with st.expander(f"{step.step.title()} — {badge}", expanded=not step.complete):
            if step.step == "claude":
                st.info("Set exactly one Claude credential: the subscription token OR the API key.")
            if step.missing:
                st.warning("Missing: " + ", ".join(step.missing))
            for field in by_step.get(step.step, []):
                render_field(client, field, key_prefix="wiz-")
            if step.verifiable:
                _test_button(client, step.step)

    if status.complete:
        st.success("Configuration is complete.")
    else:
        st.warning("Outstanding: " + "; ".join(status.issues))


def _test_button(client: ConductorClient, service: str) -> None:
    if not st.button("Test connection", key=f"test-{service}"):
        return
    with st.spinner(f"Testing {service}..."):
        result = client.test_connection(service)
    if result.ok:
        st.success(result.detail or "Connection OK.")
    else:
        st.error(result.detail or "Connection failed.")
