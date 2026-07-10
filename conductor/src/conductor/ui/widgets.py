"""Form building blocks: labeled 48px fields (label above, helper below, per the design doc),
the save-a-group-together Section, collapsible step bubbles, and the connection-test row.
Secrets are only written when a new value is typed; settings are upserted."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from nicegui import ui
from nicegui.elements.mixins.value_element import ValueElement

from conductor import verify
from conductor.store import ConfigFieldView
from conductor.ui import kit
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


def field_label(name: str) -> str:
    """Human label for a config key: `plane_base_url` → `Plane Base URL`."""
    return " ".join(_ACRONYMS.get(w, w.capitalize()) for w in name.split("_"))


def is_set(field: ConfigFieldView) -> bool:
    return bool(field.is_set) if field.secret else bool(field.value)


def is_owner_name(repo: str) -> bool:
    return repo.count("/") == 1 and not repo.startswith("/") and not repo.endswith("/")


@contextmanager
def labeled(label: str, *, helper: str | None = None) -> Iterator[None]:
    """The design-doc field pattern: 500-weight label above the control, muted helper below."""
    with ui.column().classes("w-full gap-1.5"):
        ui.label(label).classes("text-sm font-medium").style(f"color:{kit.TEXT}")
        yield
        if helper:
            ui.label(helper).classes("text-xs").style(f"color:{kit.MUTED}")


def text_input(
    label: str,
    *,
    value: str = "",
    placeholder: str | None = None,
    helper: str | None = None,
    password: bool = False,
) -> ui.input:
    with labeled(label, helper=helper):
        box = (
            ui.input(
                value=value,
                placeholder=placeholder,
                password=password,
                password_toggle_button=password,
            )
            .props("outlined dense")
            .classes("w-full v2-field")
        )
    return box


def select_input(
    label: str,
    *,
    options: list[str] | dict[str, str],
    value: str | None = None,
    helper: str | None = None,
    with_input: bool = False,
    new_value_mode: str | None = None,
) -> ui.select:
    with labeled(label, helper=helper):
        box = ui.select(
            options=options,
            value=value,
            with_input=with_input,
            new_value_mode=new_value_mode,  # type: ignore[arg-type]
        ).classes("w-full v2-field")
        box.props("outlined dense options-dark")
    return box


def config_input(field: ConfigFieldView) -> ValueElement[Any]:
    """One config field's editor, v2-styled with the label outside the control. Secrets show a
    masked placeholder and stay empty until a new value is typed (identical semantics to v1)."""
    label = field_label(field.name)
    stored_note = (
        f"A value is stored (source: {field.source}); type to replace it."
        if field.secret and field.is_set
        else None
    )
    helper = " ".join(x for x in (field.help, stored_note) if x) or None
    box: ValueElement[Any]
    with labeled(label, helper=helper):
        if field.secret:
            placeholder = ("•" * 6 + (field.last_four or "")) if field.is_set else "not set"
            box = (
                ui.input(password=True, password_toggle_button=True, placeholder=placeholder)
                .props("outlined dense")
                .classes("w-full v2-field")
            )
        elif field.choices:
            choices = list(field.choices)
            current = field.value or ""
            box = (
                ui.select(options=choices, value=current if current in choices else choices[0])
                .props("outlined dense options-dark")
                .classes("w-full v2-field")
            )
        else:
            box = (
                ui.input(value=field.value or "").props("outlined dense").classes("w-full v2-field")
            )
    return box


def model_input(field: ConfigFieldView, models: list[str]) -> ValueElement[Any]:
    current: str = field.value or ""
    options = list(dict.fromkeys([*models, *([current] if current else [])]))
    with labeled(field_label(field.name)):
        box = (
            ui.select(
                options=options,
                value=current if current in options else (options[0] if options else None),
                with_input=True,
            )
            .props("outlined dense options-dark")
            .classes("w-full v2-field")
        )
    return box


def github_repo_field(options: list[str], value: str = "") -> ValueElement[Any]:
    """Repo picker: a live-fetched, typeable select when the token could list repos, else a
    free-typed owner/name input (the documented fallback)."""
    with labeled("GitHub Repository", helper="The owner and repository name on GitHub."):
        if options:
            opts = list(dict.fromkeys([*options, *([value] if value else [])]))
            box: ValueElement[Any] = (
                ui.select(options=opts, value=value or None, with_input=True)
                .props("outlined dense options-dark")
                .classes("w-full v2-field")
            )
        else:
            box = (
                ui.input(value=value, placeholder="owner/repository")
                .props("outlined dense")
                .classes("w-full v2-field")
            )
    return box


def payload_url_field(url: str) -> None:
    """Read-only payload URL with a copy button in its append slot."""

    def copy() -> None:
        ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(url)})")
        ui.notify("Copied to clipboard")

    with (
        labeled("Payload URL", helper="Detected from this page's address."),
        (
            ui.input(value=url)
            .props("readonly outlined dense")
            .classes("w-full v2-field font-mono")
        ) as box,
        box.add_slot("append"),
    ):
        btn = ui.button(on_click=copy).props("flat dense round").tooltip("Copy")
        with btn:
            kit.licon("copy", color=kit.MUTED, size=16)


class Section:
    """A group of fields saved together by one button. Secrets are only written when a new value
    is typed; settings are always written (upsert)."""

    def __init__(self) -> None:
        self._items: list[tuple[ConfigFieldView, ValueElement[Any]]] = []

    def field(self, field: ConfigFieldView) -> ValueElement[Any]:
        box = config_input(field)
        self._items.append((field, box))
        return box

    def model(self, field: ConfigFieldView, models: list[str]) -> None:
        self._items.append((field, model_input(field, models)))

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

        with ui.row().classes("w-full justify-end"):
            kit.primary_button(label, icon="save", on_click=save)


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


def test_row(service: str) -> None:
    """Connection test: secondary button + a status dot and result line."""
    with ui.row().classes("items-center gap-3 no-wrap"):

        async def run() -> None:
            dot_holder.clear()
            result.text = f"Testing {service}…"
            result.style(f"color:{kit.MUTED}")
            res = await _run_test(service)
            result.text = res.detail
            color = kit.GREEN if res.ok else kit.RED
            result.style(f"color:{color}")
            with dot_holder:
                kit.status_dot(color)

        kit.secondary_button("Test connection", icon="zap", on_click=run)
        dot_holder = ui.row().classes("items-center")
        result = ui.label().classes("text-sm").style(f"color:{kit.MUTED}")


@contextmanager
def bubble(title: str, *, complete: bool) -> Iterator[ui.expansion]:
    """A collapsible wizard section card: a status glyph in the header, collapsed once complete
    (still expandable to revisit), expanded while something is missing."""
    exp = ui.expansion(value=not complete).classes("w-full v2-bubble").props("dense-toggle")
    with exp.add_slot("header"), ui.row().classes("items-center gap-3 no-wrap"):
        if complete:
            kit.licon("circle-check", color=kit.GREEN, size=20)
        else:
            kit.licon("circle", color=kit.FAINT, size=20)
        ui.label(title).classes("font-semibold").style(f"color:{kit.TEXT}")
    with exp, ui.column().classes("w-full gap-4"):
        yield exp


@contextmanager
def help_disclosure(title: str) -> Iterator[None]:
    """Progressive disclosure for inline how-to text: a dashed, muted expander."""
    with ui.expansion(value=False).classes("w-full v2-help") as exp:
        with exp.add_slot("header"), ui.row().classes("items-center gap-2 no-wrap"):
            kit.licon("info", color=kit.FAINT, size=14)
            ui.label(title).classes("text-sm").style(f"color:{kit.MUTED}")
        yield


def md(text: str) -> ui.markdown:
    return ui.markdown(text).classes("v2-md")
