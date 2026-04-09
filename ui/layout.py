"""
ui/layout.py — Haupt-Layout: Header-Navigation (44px).
Alle Pages nutzen create_layout() als Wrapper.
"""
import logging
from nicegui import ui

logger = logging.getLogger(__name__)


def create_layout(page_name: str = "home"):
    """Erstellt den Header. Muss am Anfang jeder Page aufgerufen werden."""
    ui.dark_mode(True)

    with ui.header().classes("items-center justify-between px-3 py-0").style(
        "height: 44px; min-height: 44px; background: #070d08; "
        "border-bottom: 1px solid #0f2010;"
    ):
        # Logo — klickbar → Home
        with ui.element("a").props('href="/"').style(
            "display: flex; align-items: center; gap: 8px; cursor: pointer; "
            "flex-shrink: 0; text-decoration: none;"
        ):
            ui.icon("precision_manufacturing").style("color: #00e676; font-size: 18px;")
            ui.html('<span class="ac-logo">AGENT CLAW</span>')

        # Navigation
        with ui.row().classes("items-center gap-1"):
            _nav_link("Home",     "home",       "/",         page_name)
            _nav_link("Chat",     "chat",       "/chat",     page_name)
            _nav_link("Tasks",    "assignment", "/tasks",    page_name)
            _nav_link("Settings", "settings",   "/settings", page_name)

        # Rechte Seite
        with ui.row().classes("items-center gap-1 ml-auto shrink-0"):
            _icon_link("memory",  "/memory",  "Memory")
            _icon_link("backup",  "/backup",  "Backup")
            _icon_link("hub",     "/network", "M2M Netzwerk")


def _nav_link(label: str, icon_name: str, path: str, current: str):
    is_active = (
        (path == "/" and current == "home") or
        (path != "/" and current in path)
    )
    active_style = (
        "color: #00e676; box-shadow: inset 0 -2px 0 #00e676; "
        "background: rgba(0,230,118,.08);"
        if is_active else
        "color: #3a5a3a;"
    )
    # <a href> für native Browser-Navigation — zuverlässiger als ui.navigate.to()
    link = ui.element("a").props(f'href="{path}"').style(
        f"display: inline-flex; align-items: center; gap: 5px; "
        f"height: 32px; padding: 0 8px; border-radius: 6px; "
        f"font-size: 10px; font-weight: 600; text-transform: uppercase; "
        f"letter-spacing: 0.5px; text-decoration: none; cursor: pointer; "
        f"transition: background .15s, color .15s; {active_style}"
    )
    with link:
        ui.html(
            f'<span class="material-icons" style="font-size:14px;vertical-align:middle">'
            f'{icon_name}</span>'
            f'<span style="vertical-align:middle;margin-left:3px">{label}</span>'
        )


def _icon_link(icon_name: str, path: str, tooltip_text: str):
    link = ui.element("a").props(f'href="{path}"').style(
        "display: inline-flex; align-items: center; justify-content: center; "
        "width: 32px; height: 32px; border-radius: 50%; "
        "color: #3a5a3a; text-decoration: none; cursor: pointer; "
        "transition: color .15s, background .15s;"
    ).tooltip(tooltip_text)
    with link:
        ui.html(f'<span class="material-icons" style="font-size:18px">{icon_name}</span>')
