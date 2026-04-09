"""
ui/components/chat_message.py — Chat-Nachricht Bubble.
"""
from nicegui import ui


class ChatMessage:
    def __init__(self, role: str, content: str, image: str | None = None, skill: str | None = None):
        is_user = role == "user"
        is_system = role == "system"
        css_class = "ac-message-user" if is_user else "ac-message-assistant"

        with ui.element("div").classes(f"{css_class} mb-3 max-w-4xl"):
            # Header
            with ui.row().classes("items-center gap-2 mb-1"):
                icon = "person" if is_user else ("info" if is_system else "smart_toy")
                icon_color = "text-[#00e676]" if is_user else ("text-blue-400" if is_system else "text-gray-400")
                ui.icon(icon, size="xs").classes(icon_color)
                ui.label("Du" if is_user else ("System" if is_system else "Agent")) \
                    .classes("text-xs text-gray-500")
                if skill:
                    ui.badge(skill, color="green").classes("text-xs")

            # Content (Markdown)
            if content:
                ui.markdown(content).classes("text-sm leading-relaxed")

            # Image
            if image:
                if image.startswith("data:video"):
                    ui.video(image).classes("max-w-full rounded mt-2")
                else:
                    ui.image(image).classes("max-w-full max-h-96 rounded mt-2 object-contain")
