"""The conductor service: webhooks, job queue, agent dispatch, state machine."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path


def _resolve_version() -> str:
    """The repo-root VERSION file is the source of truth and is read directly when running from
    source. A built image doesn't ship that file (its build context is conductor/), so there we
    fall back to the installed package metadata, which the version bump keeps in sync via
    pyproject.toml."""
    version_file = Path(__file__).resolve().parents[3] / "VERSION"
    if version_file.is_file():
        return version_file.read_text().strip()
    try:
        return importlib.metadata.version("conductor")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


__version__ = _resolve_version()
