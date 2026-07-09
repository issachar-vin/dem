from __future__ import annotations

from nicegui import app, ui
from starlette.requests import Request

from conductor import __version__

# label, path, Material icon
NAV = (
    ("Wizard", "/", "auto_fix_high"),
    ("Configuration", "/config", "tune"),
    ("Projects", "/projects", "hub"),
    ("States", "/states", "account_tree"),
    ("Jobs", "/jobs", "list_alt"),
)

# ── theme (dark grey surfaces, orange accent) ───────────────────────────────────
ORANGE = "#F97316"
HEADER_BG = "#161619"  # darker grey than the page/surfaces


def _theme() -> None:
    ui.dark_mode(True)
    ui.colors(
        primary=ORANGE,
        secondary=ORANGE,
        accent=ORANGE,
        dark="#2A2A2E",  # cards, drawer, panels
        dark_page="#1E1E22",  # page background
    )


# ── shell (header + collapsible drawer) ─────────────────────────────────────────
def _logout() -> None:
    app.storage.user.clear()
    ui.navigate.to("/login")


async def _toggle_nav(drawer: ui.left_drawer) -> None:
    # Below Quasar's drawer breakpoint the drawer is a hidden overlay where `mini` is
    # ignored, so on mobile the button must toggle visibility, not the mini state.
    if await ui.run_javascript("window.innerWidth <= 1023"):
        drawer.toggle()
        return
    collapsed = not bool(app.storage.user.get("nav_collapsed", False))
    app.storage.user["nav_collapsed"] = collapsed
    drawer.props(remove="mini")
    if collapsed:
        drawer.props("mini")


def _layout(active: str) -> None:
    _theme()
    collapsed = bool(app.storage.user.get("nav_collapsed", False))
    drawer = ui.left_drawer(value=False, bordered=True).props("show-if-above mini-width=72")
    if collapsed:
        drawer.props("mini")
    with drawer, ui.column().classes("h-full w-full gap-0"):
        with ui.list().props("padding").classes("w-full"):
            for label, path, icon in NAV:
                item = ui.item(on_click=lambda p=path: ui.navigate.to(p)).props("clickable")
                if path == active:
                    item.classes("bg-primary text-white")
                with item:
                    with ui.item_section().props("avatar"):
                        ui.icon(icon)
                    with ui.item_section():
                        ui.item_label(label)
        ui.space()
        ui.label(f"v{__version__}").classes("text-xs opacity-60 w-full text-center pb-3")

    with ui.header().classes("items-center justify-between").style(f"background-color:{HEADER_BG}"):
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="menu", on_click=lambda: _toggle_nav(drawer)).props(
                "flat round color=white"
            )
            ui.label("D.E.M. Console").classes("text-lg font-bold")
        username = str(app.storage.user.get("username", "user"))
        with ui.button(icon="account_circle").props("flat color=white no-caps"):
            ui.label(username).classes("ml-2")
            with ui.menu():
                ui.menu_item("Sign out", on_click=_logout)


def _page() -> ui.column:
    return ui.column().classes("p-4 w-full max-w-3xl mx-auto")


def _origin(request: Request) -> str:
    """Public origin of this page as the browser reached it. Reads proxy headers (Caddy sets
    X-Forwarded-* and preserves Host) rather than request.url, which carries the internal bind."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    )
    return f"{proto}://{host}"
