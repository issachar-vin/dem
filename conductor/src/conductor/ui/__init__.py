from __future__ import annotations

from fastapi import FastAPI
from nicegui import ui

# Imported for side effects: each module registers its @ui.page routes (and auth its middleware)
# on NiceGUI's global app at import time.
from conductor.ui import auth as auth  # noqa: F401
from conductor.ui import pages as pages  # noqa: F401
from conductor.ui import wizard as wizard  # noqa: F401
from conductor.ui.context import configure, get_context

__all__ = ["configure", "get_context", "setup"]


def setup(app: FastAPI, *, storage_secret: str) -> None:
    """Mount the NiceGUI console into the conductor's FastAPI app. Call once, after the parent
    app's own routes are registered so they take precedence over NiceGUI's root mount."""
    ui.run_with(
        app,
        title="D.E.M. Console",
        favicon="🤖",
        storage_secret=storage_secret,
        show_welcome_message=False,
    )
