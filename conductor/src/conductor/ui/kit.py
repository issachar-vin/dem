"""Console design kit — Modern Dark Developer SaaS (docs/UI_DESIGN.md).

One place for the visual language: tokens, the global stylesheet, and the component helpers every
page composes from (panels, buttons, labeled fields, pills, tiles, kebab menus, dialogs). The
palette is the near-black scheme the projects page established; layout, radii, motion, and
progressive-disclosure patterns follow the design doc, with Linear/Vercel/Railway as the taste
reference for anything the doc leaves open.

Interface chrome uses the Lucide webfont (`licon`); user-chosen project/repo icons stored in the DB
keep their `fa:`/`ms:` specs, so Font Awesome and Material Symbols stay loaded and `render_icon`
still understands them."""

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
SIDEBAR_BG = "#101013"
SURFACE = "#161619"  # main cards
SURFACE_2 = "#1D1D21"  # nested rows, hover fills, menus
INPUT_BG = "#111113"  # inset field fill
BORDER = "#2A2A30"
BORDER_HOVER = "#3E3E48"
TEXT = "#E7E7EA"
MUTED = "#9A9AA3"
FAINT = "#6B6B76"

ORANGE = "#F97316"
ORANGE_HOVER = "#FB923C"
BLUE = "#3B82F6"
GREEN = "#22C55E"
YELLOW = "#EAB308"
RED = "#EF4444"
PURPLE = "#A855F7"

SHADOW = "0 4px 18px rgba(0,0,0,.22)"

# Known repo roles → (accent colour, Lucide icon). Any other (custom) key → CUSTOM_STYLE.
ROLE_STYLE: dict[str, tuple[str, str]] = {
    "frontend": (ORANGE, "monitor"),
    "backend": (BLUE, "server"),
}
CUSTOM_STYLE: tuple[str, str] = (PURPLE, "folder")

# Suggested repo identifiers offered in the pickers; any custom value is allowed (typed in).
ROLE_SUGGESTIONS: list[str] = ["frontend", "backend"]


def role_style(role: str) -> tuple[str, str]:
    return ROLE_STYLE.get(role, CUSTOM_STYLE)


# ── global stylesheet ───────────────────────────────────────────────────────────
_CSS = f"""
body {{ font-family: 'Inter', sans-serif; background: {PAGE_BG}; color: {TEXT}; }}
::-webkit-scrollbar {{ width: 10px; height: 10px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: {BORDER}; border-radius: 999px; }}
::-webkit-scrollbar-thumb:hover {{ background: {BORDER_HOVER}; }}

[class^="icon-"], [class*=" icon-"] {{ line-height: 1; }}
.material-symbols-outlined {{
  font-family: 'Material Symbols Outlined'; font-weight: normal; font-style: normal;
  line-height: 1; letter-spacing: normal; text-transform: none; display: inline-block;
  white-space: nowrap; direction: ltr; }}

/* cards */
.v2-panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 16px;
  box-shadow: {SHADOW}; }}
.v2-row {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 12px;
  transition: border-color 150ms ease-out, transform 150ms ease-out, box-shadow 150ms ease-out; }}
.v2-row:hover {{ border-color: {ORANGE}66; }}
.v2-lift {{ transition: border-color 150ms ease-out, transform 150ms ease-out,
  box-shadow 150ms ease-out; }}
.v2-lift:hover {{ transform: translateY(-2px); border-color: {ORANGE}66;
  box-shadow: 0 8px 24px rgba(0,0,0,.3); }}

/* buttons */
.v2-btn {{ border-radius: 10px !important; font-weight: 500; min-height: 40px;
  padding: 0 16px; transition: all 150ms ease-out; }}
.v2-btn-primary {{ background: {ORANGE} !important; color: #fff !important; }}
.v2-btn-primary:hover {{ background: {ORANGE_HOVER} !important;
  box-shadow: 0 6px 20px rgba(249,115,22,.25); }}
.v2-btn-secondary {{ background: transparent !important; color: {TEXT} !important;
  border: 1px solid {BORDER}; }}
.v2-btn-secondary:hover {{ border-color: {BORDER_HOVER}; background: {SURFACE_2} !important; }}
.v2-btn-ghost {{ background: transparent !important; color: {MUTED} !important; }}
.v2-btn-ghost:hover {{ color: {TEXT} !important; background: {SURFACE_2} !important; }}
.v2-btn-danger {{ background: transparent !important; color: {RED} !important;
  border: 1px solid rgba(239,68,68,.35); }}
.v2-btn-danger:hover {{ background: rgba(239,68,68,.08) !important; }}

/* fields */
.v2-field .q-field__control {{ background: {INPUT_BG}; border-radius: 10px; min-height: 48px;
  color: {TEXT}; }}
.v2-field .q-field__control:before {{ border: 1px solid {BORDER};
  transition: border-color 150ms ease-out; }}
.v2-field:hover .q-field__control:before {{ border-color: {BORDER_HOVER}; }}
.v2-field.q-field--focused .q-field__control:after {{ border: 1px solid {ORANGE};
  transform: scale(1); }}
.v2-field .q-field__native, .v2-field .q-field__input {{ color: {TEXT}; }}
.v2-field .q-field__native::placeholder, .v2-field .q-field__input::placeholder {{
  color: {FAINT}; }}
.v2-field .q-field__marginal {{ color: {MUTED}; }}

/* dropdown + context menus */
.q-menu {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER};
  border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,.4); }}
.q-menu .q-item {{ color: {TEXT}; min-height: 40px; border-radius: 8px; margin: 2px 6px; }}
.q-menu .q-item:hover, .q-menu .q-item--active {{ background: {SURFACE}; }}
.q-menu .q-item[aria-selected="true"] {{ color: {ORANGE}; }}

/* tabs */
.v2-tabs .q-tab {{ text-transform: none; border-radius: 10px; min-height: 40px;
  padding: 0 16px; margin: 4px 2px; color: {MUTED}; transition: all 150ms ease-out; }}
.v2-tabs .q-tab:hover {{ color: {TEXT}; }}
.v2-tabs .q-tab--active {{ color: {TEXT}; background: {SURFACE_2}; }}
.v2-tabs .q-tab__indicator {{ display: none; }}
.v2-tabs .q-tab__label {{ font-weight: 500; font-size: 14px; }}

/* collapsible section bubbles */
.v2-bubble {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 16px;
  box-shadow: {SHADOW}; }}
.v2-bubble > .q-expansion-item__container > .q-item {{ padding: 16px 24px; min-height: 0; }}
.v2-bubble .q-expansion-item__content {{ padding: 0 24px 24px; }}
.v2-bubble .q-item__section--side .q-icon {{ color: {MUTED}; }}

/* inline help disclosure */
.v2-help {{ border: 1px dashed {BORDER}; border-radius: 10px; }}
.v2-help > .q-expansion-item__container > .q-item {{ padding: 8px 16px; min-height: 0;
  color: {MUTED}; font-size: 13px; }}
.v2-help .q-expansion-item__content {{ padding: 0 16px 12px; }}
.v2-help .q-item__section--side .q-icon {{ color: {FAINT}; font-size: 18px; }}

/* markdown body text */
.v2-md {{ color: {MUTED}; font-size: 14px; line-height: 1.6; }}
.v2-md strong {{ color: {TEXT}; font-weight: 600; }}
.v2-md code {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px;
  padding: 1px 6px; color: {TEXT}; font-size: 13px; }}
.v2-md a {{ color: {ORANGE}; text-decoration: none; }}
.v2-md a:hover {{ color: {ORANGE_HOVER}; }}

/* uploads */
.v2-upload {{ width: 100%; background: {INPUT_BG}; border: 1px dashed {BORDER};
  border-radius: 12px; box-shadow: none; }}
.v2-upload .q-uploader__header {{ background: transparent; color: {MUTED}; }}
.v2-upload .q-uploader__list {{ color: {MUTED}; }}

/* data table */
.v2-table {{ background: transparent; border: 1px solid {BORDER}; border-radius: 14px;
  color: {TEXT}; }}
.v2-table thead tr {{ background: {SURFACE}; }}
.v2-table th {{ color: {MUTED}; font-weight: 500; font-size: 12px; text-transform: uppercase;
  letter-spacing: .05em; border-color: {BORDER}; }}
.v2-table tbody td {{ border-color: #1F1F24; font-size: 14px; }}
.v2-table tbody tr {{ transition: background 150ms ease-out; }}
.v2-table tbody tr:hover {{ background: {SURFACE_2}; }}
.v2-table .q-table__bottom {{ color: {MUTED}; border-color: {BORDER}; }}

.q-separator {{ background: {BORDER}; }}
.q-checkbox__label {{ color: {TEXT}; }}

/* sidebar nav */
.v2-nav .q-item {{ border-radius: 10px; margin: 2px 10px; min-height: 40px; color: {MUTED};
  transition: all 150ms ease-out; }}
.v2-nav .q-item:hover {{ background: {SURFACE}; color: {TEXT}; }}
.v2-nav .v2-nav-active {{ background: {SURFACE_2}; color: {TEXT}; }}
.v2-nav .q-item__section--avatar {{ min-width: 34px; }}
/* Nav icon color is set inline per item (orange when active, else a dim default) so it renders
   correctly with no CSS loaded. Hovering a non-active item should still light its icon white;
   !important is required to beat that inline style. The active item is excluded so it always
   stays orange, hovered or not. */
.v2-nav .q-item:not(.v2-nav-active):hover [class^="icon-"] {{ color: {TEXT} !important; }}

/* collapsed (mini) drawer: the avatar section is the only one shown — kill Quasar's side
   padding and min-width so each icon sits dead-center in the rail */
.q-drawer--mini .v2-nav .q-item {{ justify-content: center; padding: 8px 0; }}
.q-drawer--mini .v2-nav .q-item__section--avatar {{ min-width: 0; padding: 0; }}
.q-drawer--mini .v2-brand {{ justify-content: center; padding-left: 0; padding-right: 0; }}
.q-drawer--mini .v2-brand-text {{ display: none; }}
.q-drawer--mini .v2-nav .v2-nav-active {{ background: transparent; }}
.q-drawer--mini .v2-nav .q-item:hover {{ background: transparent; }}
/* Quasar's hover/focus tint is a separate overlay element (.q-focus-helper), not the item's own
   background-color, so the rule above alone doesn't remove it — kill the overlay directly. */
.q-drawer--mini .v2-nav .q-item .q-focus-helper {{ display: none; }}
"""


def load_head() -> None:
    """Fonts (Inter), icon webfonts (Lucide for chrome; FA + Material Symbols so stored icon
    specs keep rendering), and the global stylesheet. Called once per page from the shell."""
    ui.add_head_html(
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="stylesheet" '
        'href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">'
        '<link rel="stylesheet" '
        'href="https://unpkg.com/lucide-static@0.462.0/font/lucide.css">'
        '<link rel="stylesheet" '
        'href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css">'
        '<link rel="stylesheet" '
        'href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined">'
        f"<style>{_CSS}</style>"
    )


# ── icons ────────────────────────────────────────────────────────────────────────
def licon(name: str, *, color: str | None = None, size: int = 18) -> ui.html:
    """A Lucide glyph (webfont) for interface chrome. The wrapper is a flex box sized to the
    glyph so the icon centers exactly against neighbouring text (no baseline drift)."""
    style = f"font-size:{size}px;" + (f"color:{color};" if color else "")
    return (
        ui.html(f'<i class="icon-{name}" style="{style}"></i>')
        .classes("flex items-center justify-center shrink-0")
        .style(f"width:{size}px;height:{size}px;line-height:1")
    )


def render_icon(spec: str, *, color: str, size: int = 24) -> None:
    """Render a stored icon spec: `fa:<classes>` via Font Awesome, `lc:<name>` via Lucide,
    otherwise a Material Symbol (`ms:<name>` or bare name)."""
    if spec.startswith("fa:"):
        ui.html(f'<i class="{spec[3:]}" style="font-size:{size}px;color:{color}"></i>')
    elif spec.startswith("lc:"):
        licon(spec[3:], color=color, size=size)
    else:
        name = spec.removeprefix("ms:")
        ui.html(
            f'<span class="material-symbols-outlined" '
            f'style="font-size:{size}px;color:{color}">{name}</span>'
        )


def icon_search_name(spec: str) -> str:
    body = spec.split(":", 1)[1] if ":" in spec else spec
    if body.startswith("fa-"):
        return body.split()[-1].removeprefix("fa-")  # "fa-solid fa-chess-rook" → "chess-rook"
    return body


# ── layout primitives ───────────────────────────────────────────────────────────
@contextmanager
def panel() -> Iterator[ui.card]:
    """A main content card: dark surface, hairline border, 16px radius, soft shadow."""
    card = ui.card().classes("v2-panel w-full gap-4 p-6").props("flat")
    with card:
        yield card


def page_header(title: str, subtitle: str) -> None:
    with ui.column().classes("gap-1"):
        ui.label(title).classes("text-2xl font-bold").style(f"color:{TEXT}")
        ui.label(subtitle).classes("text-sm").style(f"color:{MUTED}")


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
            primary_button(action_label, icon=action_icon or "plus", on_click=on_action)


def stat_tile(icon: str, title: str, caption: str, *, dot: str | None = None) -> None:
    with ui.row().classes("items-start gap-3 no-wrap"):
        with ui.element("div").classes("mt-1"):
            licon(icon, color=MUTED, size=22)
        with ui.column().classes("gap-0"):
            with ui.row().classes("items-center gap-2 no-wrap"):
                if dot:
                    status_dot(dot)
                ui.label(title).classes("font-semibold").style(f"color:{TEXT}")
            ui.label(caption).classes("text-xs leading-snug").style(f"color:{MUTED}")


def status_dot(color: str) -> None:
    ui.element("div").classes("rounded-full shrink-0").style(
        f"width:9px;height:9px;background:{color};box-shadow:0 0 6px {color}66"
    )


# ── small components ─────────────────────────────────────────────────────────────
def pill(text: str, *, color: str) -> None:
    ui.label(text.upper()).classes("px-2.5 py-0.5 rounded-full text-xs font-semibold").style(
        f"background:{color}22;color:{color};letter-spacing:.06em"
    )


def _tile(size: int, bg: str, *, on_click: Callable[[], object] | None = None) -> ui.element:
    tile = (
        ui.element("div")
        .classes("flex items-center justify-center rounded-xl overflow-hidden shrink-0")
        .style(f"width:{size}px;height:{size}px;background:{bg}")
    )
    if on_click is not None:
        tile.classes("cursor-pointer v2-lift").style(f"border:1px solid {BORDER}")
    return tile


def emoji_tile(emoji: str, *, size: int = 64, on_click: Callable[[], object] | None = None) -> None:
    tile = _tile(size, SURFACE_2, on_click=on_click).style(f"border:1px solid {BORDER}")
    if on_click is not None:
        tile.on("click", on_click)
    with tile:
        ui.label(emoji).classes("text-3xl")


def icon_tile(
    icon: str, *, color: str, size: int = 52, on_click: Callable[[], object] | None = None
) -> None:
    """A rounded tile holding a Lucide icon on a translucent accent fill."""
    tile = _tile(size, f"{color}22", on_click=on_click)
    if on_click is not None:
        tile.on("click", on_click)
    with tile:
        licon(icon, color=color, size=max(20, size // 2 - 4))


def icon_tile_spec(
    spec: str, *, color: str, size: int = 52, on_click: Callable[[], object] | None = None
) -> None:
    """A tile rendering a stored icon spec (`fa:`/`ms:`/`lc:`)."""
    tile = _tile(size, f"{color}22", on_click=on_click)
    if on_click is not None:
        tile.on("click", on_click)
    with tile:
        render_icon(spec, color=color, size=max(20, size // 2 - 4))


def icon_picker(on_select: Callable[[str], object]) -> None:
    """Searchable icon picker over the bundled Material Symbols + Font Awesome catalog. Renders
    only the filtered matches (capped) so it stays responsive. `on_select` may be sync or async."""
    with (
        ui.dialog() as dialog,
        ui.card().classes("v2-panel p-6 gap-3").props("flat").style("width:640px;max-width:92vw"),
    ):
        ui.label("Choose an icon").classes("text-lg font-semibold").style(f"color:{TEXT}")
        search = (
            ui.input(placeholder="Search icons…")
            .props("clearable autofocus debounce=200 outlined dense")
            .classes("w-full v2-field")
        )
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
                            "overflow-hidden v2-row"
                        )
                        .style("width:44px;height:44px")
                        .tooltip(icon_search_name(spec))
                    )
                    cell.on("click", lambda s=spec: pick(s))
                    with cell:
                        render_icon(spec, color=TEXT, size=20)

        search.on_value_change(render)
        render()
    dialog.open()


def branch_chip(branch: str) -> None:
    with (
        ui.row()
        .classes("items-center gap-1.5 px-2.5 py-1 rounded-lg no-wrap w-fit")
        .style(f"background:{SURFACE_2};border:1px solid {BORDER}")
    ):
        licon("git-branch", color=GREEN, size=13)
        ui.label(branch).classes("text-sm font-mono").style(f"color:{TEXT}")


def gh_repo(repo: str) -> None:
    with ui.row().classes("items-center gap-2 no-wrap"):
        licon("github", color=TEXT, size=18)
        ui.label(repo).classes("text-base font-medium").style(f"color:{TEXT}")


def copy_chip(text: str) -> None:
    def copy() -> None:
        ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(text)})")
        ui.notify("Copied")

    chip = (
        ui.row()
        .classes("items-center gap-2 px-2.5 py-1 rounded-lg w-fit cursor-pointer no-wrap")
        .style(f"background:{SURFACE_2};border:1px solid {BORDER}")
        .tooltip("Copy")
    )
    chip.on("click", copy)
    with chip:
        licon("copy", color=FAINT, size=12)
        ui.label(text).classes("text-xs font-mono").style(f"color:{MUTED}")


@dataclass
class MenuAction:
    label: str
    icon: str  # Lucide name
    on_click: Callable[[], object]
    danger: bool = False


def kebab(actions: Sequence[MenuAction]) -> None:
    with (
        ui.button()
        .props("flat round dense no-caps")
        .classes("v2-btn-ghost")
        .style("min-height:36px;width:36px"),
    ):
        licon("ellipsis-vertical", color=MUTED, size=18)
        with ui.menu():
            for action in actions:
                color = RED if action.danger else TEXT
                with (
                    ui.menu_item(on_click=action.on_click),
                    ui.row().classes("items-center gap-3 no-wrap"),
                ):
                    licon(action.icon, color=color, size=16)
                    ui.label(action.label).style(f"color:{color}")


# ── buttons ──────────────────────────────────────────────────────────────────────
def _button(
    label: str, *, icon: str | None, on_click: Callable[[], object], variant: str
) -> ui.button:
    # color=None keeps Quasar from adding its `bg-primary !important` class, which would beat
    # the variant styles below; the kit CSS owns the button colors entirely.
    button = (
        ui.button(on_click=on_click, color=None)
        .props("unelevated no-caps")
        .classes(f"v2-btn {variant}")
    )
    with button, ui.row().classes("items-center gap-2 no-wrap"):
        if icon:
            licon(icon, size=16)
        ui.label(label)
    return button


def primary_button(
    label: str, *, icon: str | None = None, on_click: Callable[[], object]
) -> ui.button:
    return _button(label, icon=icon, on_click=on_click, variant="v2-btn-primary")


def secondary_button(
    label: str, *, icon: str | None = None, on_click: Callable[[], object]
) -> ui.button:
    return _button(label, icon=icon, on_click=on_click, variant="v2-btn-secondary")


def ghost_button(
    label: str, *, icon: str | None = None, on_click: Callable[[], object]
) -> ui.button:
    return _button(label, icon=icon, on_click=on_click, variant="v2-btn-ghost")


def danger_button(
    label: str, *, icon: str | None = None, on_click: Callable[[], object]
) -> ui.button:
    return _button(label, icon=icon, on_click=on_click, variant="v2-btn-danger")


@contextmanager
def dialog_card(title: str, *, min_width: int = 420) -> Iterator[ui.dialog]:
    """A styled modal: panel look, titled, caller fills the body and closes the dialog."""
    with (
        ui.dialog() as dialog,
        ui.card()
        .classes("v2-panel p-6 gap-4")
        .props("flat")
        .style(f"min-width:{min_width}px;max-width:92vw"),
    ):
        ui.label(title).classes("text-lg font-semibold").style(f"color:{TEXT}")
        yield dialog
