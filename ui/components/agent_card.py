"""
ui/components/agent_card.py — Agent-Karte für das Dashboard.
"""
from nicegui import ui


class AgentCard:
    def __init__(self, agent: dict, on_click=None):
        color = agent.get("color", "#00e676")
        name = agent.get("name", "?")
        role = agent.get("role", "")
        skills = agent.get("skills", [])
        agent_id = agent["id"]
        is_fav = agent.get("favorite", False)

        click_fn = on_click or (lambda: ui.navigate.to(f"/chat/{agent_id}"))

        with ui.card().classes("cursor-pointer p-4 w-full").on("click", click_fn):
            with ui.row().classes("items-center gap-3 w-full"):
                # Avatar
                ui.avatar(name[0].upper(), color=color, text_color="black") \
                    .classes("text-xl font-bold shrink-0")

                # Name + Role
                with ui.column().classes("gap-0 flex-1 min-w-0"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(name).classes("ac-agent-name text-base truncate")
                        if is_fav:
                            ui.icon("star", size="xs").classes("text-yellow-400")

                    if role:
                        ui.label(role).classes("ac-role truncate")

            # Skills
            if skills:
                with ui.row().classes("gap-1 mt-2 flex-wrap"):
                    for skill_id in skills[:4]:
                        ui.badge(skill_id, color="green").classes("text-xs")
                    if len(skills) > 4:
                        ui.badge(f"+{len(skills) - 4}").classes("text-xs")
