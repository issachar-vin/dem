"""Console shell: brand + sidebar navigation, slim header with the user menu, and the page
container. Built on the design kit (docs/UI_DESIGN.md)."""

from __future__ import annotations

from nicegui import app, ui
from starlette.requests import Request

from conductor import __version__
from conductor.ui import kit

# label, path, Lucide icon
NAV = (
    ("Wizard", "/", "wand-sparkles"),
    ("Configuration", "/config", "sliders-horizontal"),
    ("Projects", "/projects", "layout-grid"),
    ("States", "/states", "workflow"),
    ("Jobs", "/jobs", "inbox"),
)


def theme() -> None:
    ui.dark_mode(True)
    ui.colors(
        primary=kit.ORANGE,
        secondary=kit.ORANGE,
        accent=kit.ORANGE,
        positive=kit.GREEN,
        negative=kit.RED,
        dark=kit.SURFACE,  # cards, drawer, panels
        dark_page=kit.PAGE_BG,  # page background
    )
    kit.load_head()


def _logout() -> None:
    app.storage.user.clear()
    ui.navigate.to("/login")


async def _toggle_nav(drawer: ui.left_drawer) -> None:
    # Below Quasar's drawer breakpoint the drawer is a hidden overlay where `mini` is ignored,
    # so on mobile the button must toggle visibility, not the mini state.
    if await ui.run_javascript("window.innerWidth <= 1023"):
        drawer.toggle()
        return
    collapsed = not bool(app.storage.user.get("nav_collapsed", False))
    app.storage.user["nav_collapsed"] = collapsed
    drawer.props(remove="mini")
    if collapsed:
        drawer.props("mini")


def _brand() -> None:
    with ui.row().classes("items-center gap-3 no-wrap px-4 pt-4 pb-2 w-full v2-brand"):
        with (
            ui.element("div")
            .classes("flex items-center justify-center rounded-xl shrink-0")
            .style(f"width:34px;height:34px;background:{kit.ORANGE}22")
        ):
            kit.licon("bot", color=kit.ORANGE, size=20)
        with ui.column().classes("gap-0 v2-brand-text"):
            ui.label("D.E.M.").classes("font-semibold leading-none").style(f"color:{kit.TEXT}")
            ui.label("Console").classes("text-xs leading-none mt-0.5").style(f"color:{kit.MUTED}")


def layout(active: str) -> None:
    theme()
    collapsed = bool(app.storage.user.get("nav_collapsed", False))
    drawer = (
        ui.left_drawer(value=False, bordered=True)
        .props("show-if-above mini-width=72")
        .style(f"background:{kit.SIDEBAR_BG}")
    )
    if collapsed:
        drawer.props("mini")
    with drawer, ui.column().classes("h-full w-full gap-0"):
        _brand()
        with ui.list().props("padding").classes("w-full v2-nav"):
            for label, path, icon in NAV:
                item = ui.item(on_click=lambda p=path: ui.navigate.to(p)).props("clickable")
                is_active = path == active
                if is_active:
                    item.classes("v2-nav-active")
                with item:
                    with ui.item_section().props("avatar"):
                        kit.licon(icon, color=kit.ORANGE if is_active else kit.MUTED, size=18)
                    with ui.item_section():
                        ui.item_label(label).classes("text-sm font-medium")
        ui.space()
        ui.label(f"v{__version__}").classes("text-xs w-full text-center pb-4").style(
            f"color:{kit.FAINT}"
        )

    with (
        ui.header()
        .classes("items-center justify-between px-4 py-2")
        .style(f"background:{kit.PAGE_BG};border-bottom:1px solid #1F1F24")
    ):
        with (
            ui.button(on_click=lambda: _toggle_nav(drawer))
            .props("flat round dense")
            .classes("v2-btn-ghost")
        ):
            kit.licon("panel-left", color=kit.MUTED, size=18)
        username = str(app.storage.user.get("username", "user"))
        with (
            ui.button().props("flat no-caps dense").classes("v2-btn v2-btn-ghost px-2"),
        ):
            with ui.row().classes("items-center gap-2 no-wrap"):
                with (
                    ui.element("div")
                    .classes("flex items-center justify-center rounded-full shrink-0")
                    .style(f"width:28px;height:28px;background:{kit.ORANGE}22")
                ):
                    ui.label(username[:1].upper()).classes("text-xs font-semibold").style(
                        f"color:{kit.ORANGE}"
                    )
                ui.label(username).classes("text-sm").style(f"color:{kit.TEXT}")
                kit.licon("chevron-down", color=kit.FAINT, size=14)
            with (
                ui.menu(),
                ui.menu_item(on_click=_logout),
                ui.row().classes("items-center gap-3 no-wrap"),
            ):
                kit.licon("log-out", color=kit.TEXT, size=16)
                ui.label("Sign out").style(f"color:{kit.TEXT}")


def page(*, wide: bool = False) -> ui.column:
    """Page content container. Default caps width for readable forms (wizard/config); `wide=True`
    fills the viewport for data-dense screens like the jobs table."""
    width = "w-full" if wide else "w-full max-w-3xl mx-auto"
    return ui.column().classes(f"p-6 gap-6 {width}")


def origin(request: Request) -> str:
    """Public origin of this page as the browser reached it. Reads proxy headers (Caddy sets
    X-Forwarded-* and preserves Host) rather than request.url, which carries the internal bind."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    )
    return f"{proto}://{host}"
