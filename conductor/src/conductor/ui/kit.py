"""Console design kit: the shared visual language every page composes from. Tokens live here (one
place to retune the look), plus small component helpers — panels, section headers, stat tiles, role
pills, icon tiles, chips, and a kebab menu — so the console reads as one system.

Styling uses inline `.style()` for exact token colors (reliable regardless of the Tailwind build)
and utility `.classes()` for layout. Icons are Material Symbols (NiceGUI default) plus Font Awesome
(loaded in `load_head`) for brand marks like GitHub."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from nicegui import ui

from conductor.ui import icons_catalog

# ── tokens ──────────────────────────────────────────────────────────────────────
PAGE_BG = "#0C0C0E"
SURFACE = "#161619"  # cards / panels
SURFACE_2 = "#1D1D21"  # nested rows inside a panel
BORDER = "#2A2A30"
TEXT = "#E7E7EA"
MUTED = "#9A9AA3"

ORANGE = "#F97316"
BLUE = "#3B82F6"
GREEN = "#22C55E"
RED = "#EF4444"
PURPLE = "#A855F7"

# Known repo roles → (accent colour, icon). Any other (custom) key falls back to CUSTOM_STYLE.
ROLE_STYLE: dict[str, tuple[str, str]] = {
    "frontend": (ORANGE, "desktop_windows"),
    "backend": (BLUE, "dns"),
}
CUSTOM_STYLE: tuple[str, str] = (PURPLE, "folder")

# Suggested repo identifiers offered in the pickers; any custom value is allowed (typed in).
ROLE_SUGGESTIONS: list[str] = ["frontend", "backend"]


def role_style(role: str) -> tuple[str, str]:
    return ROLE_STYLE.get(role, CUSTOM_STYLE)


def load_head() -> None:
    """Load Font Awesome (brand icons like GitHub) and the Inter font. Called once per page from
    the shell layout."""
    ui.add_head_html(
        '<link rel="stylesheet" '
        'href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css">'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="stylesheet" '
        'href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">'
        # Material Symbols — our icon catalog uses Symbol names, which the default Material Icons
        # font doesn't cover (missing glyphs render as overflowing raw text). Load the real font.
        '<link rel="stylesheet" '
        'href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined">'
        f"<style>body {{ font-family: 'Inter', sans-serif; background: {PAGE_BG}; }}"
        ".material-symbols-outlined {"
        " font-family: 'Material Symbols Outlined'; font-weight: normal; font-style: normal;"
        " line-height: 1; letter-spacing: normal; text-transform: none; display: inline-block;"
        " white-space: nowrap; direction: ltr; }"
        f".dem-panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}"
        f".dem-row {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 12px; }}"
        "</style>"
    )


# ── layout primitives ───────────────────────────────────────────────────────────
@contextmanager
def panel() -> Iterator[ui.card]:
    """A standard content panel (dark surface, hairline border, rounded)."""
    card = ui.card().classes("dem-panel w-full gap-4 p-6").props("flat")
    with card:
        yield card


def section_header(
    title: str,
    subtitle: str | None = None,
    *,
    action_label: str | None = None,
    action_icon: str | None = None,
    on_action: Callable[[], object] | None = None,
) -> None:
    with ui.row().classes("w-full items-center justify-between"):
        with ui.column().classes("gap-0"):
            ui.label(title).classes("text-lg font-semibold").style(f"color:{TEXT}")
            if subtitle:
                ui.label(subtitle).classes("text-sm").style(f"color:{MUTED}")
        if action_label and on_action:
            primary_button(action_label, icon=action_icon or "add", on_click=on_action)


def stat_tile(icon: str, title: str, caption: str, *, dot: str | None = None) -> None:
    with ui.row().classes("items-start gap-3 no-wrap"):
        ui.icon(icon).classes("text-2xl mt-1").style(f"color:{MUTED}")
        with ui.column().classes("gap-0"):
            with ui.row().classes("items-center gap-2 no-wrap"):
                if dot:
                    ui.element("div").classes("rounded-full").style(
                        f"width:9px;height:9px;background:{dot}"
                    )
                ui.label(title).classes("font-semibold").style(f"color:{TEXT}")
            ui.label(caption).classes("text-xs leading-snug").style(f"color:{MUTED}")


# ── small components ─────────────────────────────────────────────────────────────
def pill(text: str, *, color: str) -> None:
    ui.label(text.upper()).classes(
        "px-2 py-0.5 rounded-full text-xs font-bold tracking-wide"
    ).style(f"background:{color}22;color:{color}")


def _tile(size: int, bg: str, *, on_click: Callable[[], object] | None = None) -> ui.element:
    tile = (
        ui.element("div")
        .classes("flex items-center justify-center rounded-xl overflow-hidden")
        .style(f"width:{size}px;height:{size}px;background:{bg}")
    )
    if on_click is not None:
        tile.classes("cursor-pointer").on("click", on_click)
    return tile


def emoji_tile(emoji: str, *, size: int = 64, on_click: Callable[[], object] | None = None) -> None:
    with _tile(size, SURFACE_2, on_click=on_click).style(f"border:1px solid {BORDER}"):
        ui.label(emoji).classes("text-3xl")


def icon_tile(
    icon: str, *, color: str, size: int = 52, on_click: Callable[[], object] | None = None
) -> None:
    with _tile(size, f"{color}22", on_click=on_click):
        ui.icon(icon).classes("text-2xl").style(f"color:{color}")


def render_icon(spec: str, *, color: str, size_class: str = "text-2xl") -> None:
    """Render an icon spec: `fa:<class>` via a Font Awesome `<i>`, otherwise a Material Symbol
    (`ms:<name>` or a bare name) via a `material-symbols-outlined` span (the font our catalog
    targets — `ui.icon`'s default Material Icons font is missing many Symbol glyphs)."""
    if spec.startswith("fa:"):
        ui.html(f'<i class="{spec[3:]}"></i>').classes(size_class).style(f"color:{color}")
    else:
        name = spec[3:] if spec.startswith("ms:") else spec
        ui.html(f'<span class="material-symbols-outlined">{name}</span>').classes(size_class).style(
            f"color:{color}"
        )


def icon_search_name(spec: str) -> str:
    body = spec.split(":", 1)[1] if ":" in spec else spec
    if body.startswith("fa-"):
        return body.split()[-1].removeprefix("fa-")  # "fa-solid fa-chess-rook" → "chess-rook"
    return body


def icon_tile_spec(
    spec: str, *, color: str, size: int = 52, on_click: Callable[[], object] | None = None
) -> None:
    with _tile(size, f"{color}22", on_click=on_click):
        render_icon(spec, color=color)


def icon_picker(on_select: Callable[[str], object]) -> None:
    """A searchable icon picker over the bundled Material Symbols + Font Awesome catalog. Renders
    only the filtered matches (capped) so it stays responsive. `on_select` may be sync or async."""
    with (
        ui.dialog() as dialog,
        ui.card().classes("dem-panel p-4 gap-3").style("width:640px;max-width:92vw"),
    ):
        ui.label("Choose an icon").classes("text-lg font-semibold").style(f"color:{TEXT}")
        search = ui.input(placeholder="Search icons…").props("clearable autofocus debounce=200")
        search.classes("w-full")
        grid = ui.row().classes("w-full gap-2 flex-wrap").style("max-height:52vh;overflow-y:auto")

        async def pick(spec: str) -> None:
            result = on_select(spec)
            if inspect.isawaitable(result):
                await result
            dialog.close()

        def render() -> None:
            grid.clear()
            query = (search.value or "").strip().lower()
            source = (
                [s for s in icons_catalog.ALL if query in icon_search_name(s)]
                if query
                else icons_catalog.ALL[:120]
            )
            with grid:
                if not source:
                    ui.label("No matching icons.").style(f"color:{MUTED}")
                for spec in source[:150]:
                    cell = (
                        ui.element("div")
                        .classes(
                            "flex items-center justify-center rounded-lg cursor-pointer "
                            "overflow-hidden"
                        )
                        .style(
                            f"width:44px;height:44px;background:{SURFACE_2};"
                            f"border:1px solid {BORDER}"
                        )
                        .tooltip(icon_search_name(spec))
                    )
                    cell.on("click", lambda s=spec: pick(s))
                    with cell:
                        render_icon(spec, color=TEXT, size_class="text-xl")

        search.on_value_change(render)
        render()
    dialog.open()


def branch_chip(branch: str) -> None:
    with (
        ui.row()
        .classes("items-center gap-1 px-2 py-1 rounded-lg")
        .style(f"background:{SURFACE_2};border:1px solid {BORDER}")
    ):
        ui.icon("account_tree").classes("text-sm").style(f"color:{GREEN}")
        ui.label(branch).classes("text-sm font-mono").style(f"color:{TEXT}")


def gh_repo(repo: str) -> None:
    with ui.row().classes("items-center gap-2 no-wrap"):
        ui.html('<i class="fa-brands fa-github"></i>').classes("text-lg").style(f"color:{TEXT}")
        ui.label(repo).classes("text-base font-medium").style(f"color:{TEXT}")


def copy_chip(text: str) -> None:
    def copy() -> None:
        ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(text)})")
        ui.notify("Copied")

    with (
        ui.row()
        .classes("items-center gap-2 px-2 py-1 rounded-lg w-fit")
        .style(f"background:{SURFACE_2};border:1px solid {BORDER}")
    ):
        ui.icon("content_copy").classes("text-xs cursor-pointer").style(f"color:{MUTED}").on(
            "click", copy
        )
        ui.label(text).classes("text-xs font-mono").style(f"color:{MUTED}")


@dataclass
class MenuAction:
    label: str
    icon: str
    on_click: Callable[[], object]
    danger: bool = False


def kebab(actions: Sequence[MenuAction]) -> None:
    with (
        ui.button(icon="more_vert").props("flat round dense").style(f"color:{MUTED}"),
        ui.menu().classes("dem-panel"),
    ):
        for action in actions:
            color = RED if action.danger else TEXT
            with (
                ui.menu_item(on_click=action.on_click),
                ui.row().classes("items-center gap-2 no-wrap"),
            ):
                ui.icon(action.icon).style(f"color:{color}")
                ui.label(action.label).style(f"color:{color}")


# ── buttons ──────────────────────────────────────────────────────────────────────
def primary_button(
    label: str, *, icon: str | None = None, on_click: Callable[[], object]
) -> ui.button:
    return ui.button(label, icon=icon, on_click=on_click).props("unelevated no-caps color=primary")


def ghost_button(
    label: str, *, icon: str | None = None, on_click: Callable[[], object]
) -> ui.button:
    return (
        ui.button(label, icon=icon, on_click=on_click)
        .props("flat no-caps")
        .style(f"color:{TEXT};border:1px solid {BORDER}")
    )


def danger_button(
    label: str, *, icon: str | None = None, on_click: Callable[[], object]
) -> ui.button:
    return (
        ui.button(label, icon=icon, on_click=on_click)
        .props("flat no-caps")
        .style(f"color:{RED};border:1px solid {RED}55")
    )
