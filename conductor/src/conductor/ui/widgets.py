from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from nicegui import ui
from nicegui.elements.mixins.value_element import ValueElement

from conductor import verify
from conductor.store import ConfigFieldView
from conductor.ui.context import get_context

OnSaved = Callable[[], Any]

_ACRONYMS = {
    "url": "URL",
    "api": "API",
    "qa": "QA",
    "cpu": "CPU",
    "id": "ID",
    "oauth": "OAuth",
    "otel": "OTel",
    "otlp": "OTLP",
    "github": "GitHub",
}


def _label(name: str) -> str:
    """Human label for a config key: `plane_base_url` → `Plane Base URL`."""
    return " ".join(_ACRONYMS.get(w, w.capitalize()) for w in name.split("_"))


def _is_set(field: ConfigFieldView) -> bool:
    return bool(field.is_set) if field.secret else bool(field.value)


def _is_owner_name(repo: str) -> bool:
    return repo.count("/") == 1 and not repo.startswith("/") and not repo.endswith("/")


def _input(field: ConfigFieldView) -> ValueElement[Any]:
    """Render one field's editor (no save button). Secrets show a masked placeholder and stay
    empty until a new value is typed."""
    label = _label(field.name)
    box: ValueElement[Any]
    if field.secret:
        placeholder = ("•" * 6 + (field.last_four or "")) if field.is_set else "not set"
        box = (
            ui.input(
                label=label, password=True, password_toggle_button=True, placeholder=placeholder
            )
            .props("stack-label")
            .classes("w-full")
        )
    else:
        choices: list[str] = list(field.choices)
        current: str = field.value or ""
        if choices:
            box = ui.select(
                options=choices, value=current if current in choices else choices[0], label=label
            ).classes("w-full")
        else:
            box = ui.input(label=label, value=current).classes("w-full")
    if field.help:
        ui.label(field.help).classes("text-xs text-gray-500")
    if field.secret and field.is_set:
        ui.label(f"A value is stored (source: {field.source}); type to replace it.").classes(
            "text-xs text-gray-500"
        )
    return box


def _payload_url_field(url: str) -> None:
    """Read-only payload URL input with a copy-to-clipboard button in its append slot."""

    def copy() -> None:
        ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(url)})")
        ui.notify("Copied to clipboard")

    ui.label("Payload URL (detected from this page's address):")
    with (
        ui.input(value=url).props("readonly").classes("w-full font-mono") as box,
        box.add_slot("append"),
    ):
        ui.button(icon="content_copy", on_click=copy).props("flat dense round").tooltip("Copy")


def _model_input(field: ConfigFieldView, models: list[str]) -> ValueElement[Any]:
    current: str = field.value or ""
    options = list(dict.fromkeys([*models, *([current] if current else [])]))
    return ui.select(
        options=options,
        value=current if current in options else (options[0] if options else None),
        label=_label(field.name),
        with_input=True,
    ).classes("w-full")


class _Section:
    """A group of fields saved together by one button. Secrets are only written when a new value
    is typed; settings are always written (upsert)."""

    def __init__(self) -> None:
        self._items: list[tuple[ConfigFieldView, ValueElement[Any]]] = []

    def field(self, field: ConfigFieldView) -> ValueElement[Any]:
        box = _input(field)
        self._items.append((field, box))
        return box

    def model(self, field: ConfigFieldView, models: list[str]) -> None:
        self._items.append((field, _model_input(field, models)))

    def save_button(self, on_saved: OnSaved, *, label: str = "Save") -> None:
        async def save() -> None:
            store = get_context().store
            for field, box in self._items:
                if field.secret:
                    if box.value:
                        await store.set_secret(field.name, box.value)
                else:
                    await store.set_setting(field.name, str(box.value or ""))
            ui.notify("Saved")
            on_saved()

        ui.button(label, icon="save", on_click=save).props("color=primary")


async def _run_test(service: str) -> verify.VerifyResult:
    resolved = await get_context().store.resolved()
    if service == "claude":
        return await verify.verify_claude(
            oauth_token=resolved.get("claude_code_oauth_token") or None,
            api_key=resolved.get("anthropic_api_key") or None,
        )
    if service == "plane":
        return await verify.verify_plane(
            base_url=resolved.get("plane_base_url", ""),
            api_key=resolved.get("plane_api_key", ""),
            workspace_slug=resolved.get("plane_workspace_slug", ""),
        )
    return await verify.verify_github(token=resolved.get("github_token", ""))


def _test_row(service: str) -> None:
    result = ui.label().classes("text-sm")

    async def run() -> None:
        result.text = f"Testing {service}…"
        res = await _run_test(service)
        result.text = res.detail
        result.classes(replace="text-sm " + ("text-green-600" if res.ok else "text-red-600"))

    ui.button("Test connection", icon="bolt", on_click=run).props("outline")
