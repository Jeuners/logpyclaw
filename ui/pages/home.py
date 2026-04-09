"""
ui/pages/home.py — Dashboard: Agent-Cards + Activity-Feed.
"""
import logging
from nicegui import ui
from ui.layout import create_layout
from ui.theme import apply_theme
from ui.components.agent_card import AgentCard
from ui.components.activity_feed import ActivityFeed

logger = logging.getLogger(__name__)


@ui.page("/")
def home_page():
    apply_theme()
    create_layout("home")

    with ui.column().classes("w-full max-w-7xl mx-auto p-6 gap-6"):
        # Header
        with ui.row().classes("items-center justify-between w-full"):
            ui.label("Dashboard").classes("text-2xl font-bold")
            ui.button("+ Agent", icon="add", on_click=_show_create_dialog) \
                .props("flat").classes("text-[#00e676]")

        # Agent-Cards Grid
        _agent_grid = ui.element("div").classes("grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 w-full")
        _load_agents(_agent_grid)

        # Activity Section
        ui.separator()
        with ui.row().classes("items-center gap-2"):
            ui.icon("bolt").classes("text-[#00e676]")
            ui.label("Live-Aktivität").classes("text-xl font-bold")
        activity = ActivityFeed()
        ui.timer(2.0, activity.refresh)


def _load_agents(container):
    from services import get_services
    try:
        services = get_services()
        agents = services.agents.list_all()
        container.clear()
        with container:
            if not agents:
                ui.label("Noch keine Agenten. Erstelle deinen ersten Agenten!") \
                    .classes("text-gray-500 col-span-4 text-center py-12")
                return
            for agent in sorted(agents, key=lambda a: (not a.get("favorite"), a.get("name", ""))):
                AgentCard(agent)
    except Exception as e:
        logger.error("Agenten laden fehlgeschlagen: %s", e)


def _show_create_dialog():
    from ui.dialogs.agent_form import AgentFormDialog
    AgentFormDialog(on_save=lambda: ui.navigate.reload())
