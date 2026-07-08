from __future__ import annotations

from fastapi import FastAPI
from nicegui import ui

from conductor.ui import views as views  # noqa: F401 — registers pages + auth middleware on import
from conductor.ui.context import configure, get_context

__all__ = ["configure", "get_context", "setup"]


def setup(app: FastAPI, *, storage_secret: str) -> None:
    """Mount the NiceGUI console into the conductor's FastAPI app. Call once, after the parent
    app's own routes are registered so they take precedence over NiceGUI's root mount."""
    ui.run_with(
        app,
        title="DEM Console",
        storage_secret=storage_secret,
        show_welcome_message=False,
    )
