"""
ui/pages/chat.py — Chat-Interface.
Layout: Links Agenten-Sidebar (228px) | Rechts Chat-Bereich.
"""
import logging
from nicegui import ui, app
from ui.layout import create_layout
from ui.theme import apply_theme

logger = logging.getLogger(__name__)



@ui.page("/chat/{agent_id}")
def chat_page(agent_id: str):
    apply_theme()
    create_layout("chat")

    from services import get_services
    services = get_services()
    agent = services.agents.get(agent_id)
    agents = services.agents.list_all()
    agents_sorted = sorted(agents, key=lambda a: (not a.get("favorite"), a.get("name", "").lower()))

    if not agent:
        with ui.column().classes("items-center justify-center w-full").style("height: calc(100vh - 44px)"):
            ui.icon("person_off").style("font-size: 48px; color: #3a5a3a;")
            ui.label("Agent nicht gefunden").style("color: #ef4444; font-size: 18px; margin-top: 12px;")
            ui.button("← Zurück", on_click=lambda: ui.run_javascript("window.location.href='/'")) \
                .props("flat").style("color: #00e676;")
        return

    # ─── 2-Spalten-Layout ────────────────────────────────────────────────────
    # Quasar .q-page auf volle Breite/Höhe setzen
    ui.add_css("""
        .q-page { min-height: unset !important; }
        .q-page-container { padding-bottom: 0 !important; }
        body > div#app > div { height: 100vh; overflow: hidden; }
    """)

    with ui.element("div").style(
        "display: flex; width: 100%; height: calc(100vh - 44px); overflow: hidden; gap: 0;"
    ):
        # ─── Linke Sidebar: Agenten-Liste ────────────────────────────────────
        with ui.element("div").style(
            "width: 228px; min-width: 228px; flex-shrink: 0; "
            "background: #070d08; border-right: 1px solid #0f2010; "
            "display: flex; flex-direction: column; overflow: hidden;"
        ):
            # Sidebar Header
            with ui.element("div").style(
                "padding: 10px 14px 8px; font-size: 10px; font-weight: 700; "
                "color: #3a5a3a; text-transform: uppercase; letter-spacing: 1.2px; "
                "font-family: 'SF Mono','Fira Code',monospace; flex-shrink: 0; "
                "border-bottom: 1px solid #0f2010; display: flex; "
                "align-items: center; justify-content: space-between;"
            ):
                ui.label("Agenten")
                ui.button(icon="add", on_click=_show_create_dialog) \
                    .props("flat dense round") \
                    .style("color: #00e676; font-size: 12px; width: 24px; height: 24px;") \
                    .tooltip("Neuer Agent")

            # Agent-List
            with ui.scroll_area().style("flex: 1; min-height: 0;"):
                with ui.column().style("padding: 4px 6px; gap: 0;"):
                    if not agents_sorted:
                        ui.label("Noch keine Agenten") \
                            .style("font-size: 11px; color: #3a5a3a; padding: 16px 8px; "
                                   "text-align: center;")
                    for ag in agents_sorted:
                        _render_sidebar_agent(ag, agent_id)

        # ─── Rechter Bereich: Chat ────────────────────────────────────────────
        with ui.element("div").style(
            "flex: 1; display: flex; flex-direction: column; "
            "overflow: hidden; background: #050a06;"
        ):
            # Chat-Topbar
            color = agent.get("color", "#00e676")
            _render_chat_topbar(agent, agent_id, color)

            # Nachrichten
            messages_scroll = ui.scroll_area().style(
                "flex: 1; min-height: 0; padding: 0;"
            ).props("id=msg-scroll")
            with messages_scroll:
                msg_col = ui.column().style(
                    "padding: 16px 24px; gap: 10px; "
                    "display: flex; flex-direction: column;"
                )
                _load_history(msg_col, agent_id)
            # Scroll to bottom on load (via timer, da WebSocket beim Render noch nicht bereit)
            def _initial_scroll():
                try:
                    ui.run_javascript(
                        "const el = document.querySelector('#msg-scroll .q-scrollarea__container');"
                        "if (el) el.scrollTop = el.scrollHeight;"
                    )
                except Exception:
                    pass
            ui.timer(0.5, _initial_scroll, once=True)

            # Eingabe
            _render_input_area(agent_id, msg_col, agent)


def _render_sidebar_agent(agent: dict, current_agent_id: str):
    """Rendert einen Agenten-Eintrag in der Sidebar."""
    ag_id = agent["id"]
    name = agent.get("name", "?")
    color = agent.get("color", "#00e676")
    model = agent.get("model", "")
    is_fav = agent.get("favorite", False)
    is_selected = ag_id == current_agent_id

    # Initiale
    initials = name[:2].upper() if len(name) >= 2 else name[0].upper()

    border_style = "border-left: 2px solid #ffd700;" if is_fav else "border-left: 2px solid transparent;"
    bg_style = (
        f"background: rgba(0,230,118,.08); {border_style}"
        if is_selected
        else f"background: transparent; {border_style}"
    )

    link = ui.element("a").props(f'href="/chat/{ag_id}"').style(
        f"display: flex; align-items: center; gap: 8px; padding: 8px 8px; "
        f"border-radius: 6px; cursor: pointer; transition: background .12s; "
        f"margin-bottom: 2px; text-decoration: none; {bg_style}"
    ).classes("ac-agent-item")

    with link:
        # Avatar
        with ui.element("div").style(
            f"width: 30px; height: 30px; border-radius: 50%; "
            f"background: {color}; display: flex; align-items: center; "
            f"justify-content: center; font-size: 11px; font-weight: 700; "
            f"color: #000; flex-shrink: 0; text-transform: uppercase;"
        ):
            ui.label(initials)

        # Name + Model
        with ui.column().style("flex: 1; min-width: 0; gap: 0;"):
            color_style = "color: #00e676;" if is_selected else "color: #b8d4b8;"
            ui.label(name).style(
                f"font-size: 13px; font-weight: 500; {color_style} "
                f"overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
            )
            if model:
                short_model = model.split(":")[-1][:12] if ":" in model else model[:12]
                ui.label(short_model).style(
                    "font-size: 9px; color: #3a5a3a; font-family: 'SF Mono',monospace;"
                )

        # Fav-Stern
        if is_fav:
            ui.icon("star").style("font-size: 12px; color: #ffd700; flex-shrink: 0;")


def _render_chat_topbar(agent: dict, agent_id: str, color: str):
    """Rendert die Chat-Topbar mit Avatar, Name, Buttons."""
    name = agent.get("name", "?")
    role = agent.get("role", "")
    skills = agent.get("skills", [])
    model = agent.get("model", "")

    with ui.element("div").style(
        "display: flex; align-items: center; gap: 16px; padding: 0 24px; "
        "height: 72px; background: #070d08; border-bottom: 1px solid #0f2010; "
        "flex-shrink: 0;"
    ):
        # Avatar
        initials = name[:2].upper() if len(name) >= 2 else name[0].upper()
        with ui.element("div").style(
            f"width: 44px; height: 44px; border-radius: 50%; background: {color}; "
            f"display: flex; align-items: center; justify-content: center; "
            f"font-size: 18px; font-weight: 700; color: #000; flex-shrink: 0;"
        ):
            ui.label(initials)

        # Name + Info
        with ui.column().style("gap: 2px; flex: 1; min-width: 0;"):
            with ui.row().classes("items-center gap-2"):
                ui.label(name).style(
                    "font-size: 16px; font-weight: 600; color: #e4f4e4;"
                )
                if agent.get("favorite"):
                    ui.icon("star").style("font-size: 14px; color: #ffd700;")
            if role:
                ui.label(role).style(
                    "font-size: 12px; color: #3a5a3a; "
                    "font-family: 'SF Mono',monospace;"
                )

        # Skill-Badges
        if skills:
            with ui.row().style("gap: 4px; flex-wrap: wrap; max-width: 300px;"):
                for sk in skills[:5]:
                    ui.label(sk).style(
                        "font-size: 10px; font-family: 'SF Mono',monospace; "
                        "padding: 2px 6px; border-radius: 3px; "
                        "background: rgba(0,230,118,0.08); color: #00e676; "
                        "border: 1px solid rgba(0,230,118,0.2);"
                    )
                if len(skills) > 5:
                    ui.label(f"+{len(skills) - 5}").style(
                        "font-size: 10px; color: #3a5a3a; padding: 2px 4px;"
                    )

        # Buttons
        with ui.row().style("gap: 6px; margin-left: auto; flex-shrink: 0;"):
            if model:
                ui.label(model.split("/")[-1][:16]).style(
                    "font-size: 10px; font-family: 'SF Mono',monospace; "
                    "padding: 2px 7px; border-radius: 3px; "
                    "background: #0f2010; color: #3a5a3a; "
                    "border: 1px solid #182e18; align-self: center;"
                )
            _topbar_btn(
                "delete_sweep",
                "History",
                lambda: _clear_history(agent_id)
            )
            _topbar_btn(
                "edit",
                "Bearbeiten",
                lambda: _edit_agent(agent)
            )
            _topbar_btn(
                "task_alt",
                "Tasks",
                _show_task_monitor
            )


def _topbar_btn(icon_name: str, tooltip_text: str, callback):
    ui.button(icon=icon_name, on_click=callback) \
        .props("flat dense round") \
        .style("color: #3a5a3a; width: 32px; height: 32px;") \
        .tooltip(tooltip_text)


def _render_input_area(agent_id: str, msg_col, agent: dict):
    """Rendert den Eingabebereich."""
    _uploaded_image = {"data": None}

    # async-Wrapper für on_click/on_keydown (NiceGUI erkennt lambda→coroutine nicht als async)
    async def _do_send():
        await _send(agent_id, text_input, msg_col, _uploaded_image, agent)

    with ui.element("div").style(
        "padding: 12px 20px 16px; border-top: 1px solid #0f2010; "
        "background: #070d08; flex-shrink: 0;"
    ):
        with ui.row().style("gap: 8px; align-items: flex-end;"):
            # Upload als verstecktes File-Input + Icon-Button
            with ui.element("div").style("position: relative; flex-shrink: 0;"):
                ui.upload(
                    on_upload=lambda e: _handle_upload(e, _uploaded_image),
                    auto_upload=True
                ).props("flat dense accept='image/*'") \
                 .style("opacity: 0; position: absolute; width: 36px; height: 36px; cursor: pointer; z-index: 2;")
                ui.button(icon="attach_file") \
                    .props("flat dense round") \
                    .style("color: #3a5a3a; width: 36px; height: 36px; pointer-events: none;") \
                    .tooltip("Bild anhängen")

            # Texteingabe
            text_input = ui.textarea(placeholder="Nachricht… (Ctrl+Enter senden)") \
                .style(
                    "flex: 1; background: #0d1a0e; border: 1px solid #182e18; "
                    "border-radius: 8px;"
                ).props("rows=1 autogrow outlined dense")
            text_input.on("keydown.ctrl.enter", _do_send)

            # Send-Button
            ui.button(icon="send", on_click=_do_send).style(
                "width: 40px; height: 40px; border-radius: 20px; "
                "background: #00e676; color: #000; flex-shrink: 0;"
            ).props("flat dense")


def _handle_upload(e, uploaded_image: dict):
    import base64
    data = base64.b64encode(e.content.read()).decode()
    mime = e.type or "image/png"
    uploaded_image["data"] = f"data:{mime};base64,{data}"
    ui.notify("Bild geladen", type="positive")


def _load_history(container, agent_id: str):
    from services import get_services
    try:
        services = get_services()
        history = services.agents.get_history(agent_id)
        with container:
            if not history:
                ui.label("Noch keine Nachrichten. Starte eine Unterhaltung!") \
                    .style(
                        "color: #3a5a3a; font-size: 13px; font-style: italic; "
                        "text-align: center; padding: 32px 0; width: 100%;"
                    )
                return
            for msg in history[-50:]:
                _render_message(
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    image=msg.get("image") or msg.get("task_image"),
                    skill=msg.get("skill_used"),
                )
    except Exception as e:
        logger.error("History laden fehlgeschlagen: %s", e)


def _render_message(role: str, content: str, image=None, skill=None):
    """Rendert eine einzelne Nachricht im Chat-Stil."""
    # Leere Nachrichten überspringen
    if not content and not image:
        return

    is_user = role == "user"
    align = "flex-end" if is_user else "flex-start"
    bubble_style = (
        "background: rgba(0,230,118,.08); border: 1px solid #182e18; "
        "color: #e4f4e4; border-bottom-right-radius: 3px;"
        if is_user else
        "background: #0d1a0e; border: 1px solid #0f2010; "
        "color: #b8d4b8; border-bottom-left-radius: 3px;"
    )
    role_label = "Du" if is_user else "Assistant"

    with ui.element("div").style(
        f"display: flex; flex-direction: column; gap: 3px; "
        f"max-width: 820px; align-self: {align}; align-items: {align}; width: 100%;"
    ):
        # Meta
        skill_text = f" · {skill}" if skill else ""
        ui.label(f"{role_label}{skill_text}").style(
            "font-size: 10px; font-family: 'SF Mono',monospace; "
            "color: #3a5a3a; padding: 0 4px;"
        )
        # Bild
        if image:
            ui.image(image).style("max-width: 320px; border-radius: 8px; margin-bottom: 4px;")
        # Bubble
        if content:
            ui.markdown(content).style(
                f"padding: 10px 14px; border-radius: 10px; "
                f"font-size: 14px; line-height: 1.6; word-break: break-word; "
                f"{bubble_style}"
            )


async def _send(agent_id: str, text_input, container, uploaded_image: dict, agent: dict):
    """Sendet Nachricht und streamt die Antwort via SSE."""
    import asyncio, json, urllib.parse

    message = text_input.value.strip()
    if not message:
        return
    text_input.value = ""
    images = [uploaded_image["data"]] if uploaded_image.get("data") else None
    uploaded_image["data"] = None

    # User-Nachricht anzeigen
    with container:
        _render_message(role="user", content=message, image=images[0] if images else None)

    # Antwort-Placeholder
    with container:
        reply_label = ui.markdown("").style(
            "padding: 10px 14px; border-radius: 10px 10px 10px 3px; "
            "font-size: 14px; line-height: 1.6; word-break: break-word; "
            "background: #0d1a0e; border: 1px solid #0f2010; color: #b8d4b8; "
            "min-width: 40px; align-self: flex-start;"
        )
        # Typing-Indicator
        typing = ui.label("● ● ●").style(
            "font-size: 10px; color: #3a5a3a; align-self: flex-start; "
            "padding: 0 4px; animation: ac-pulse 1.2s ease-in-out infinite;"
        )

    accumulated = []
    # Helper: scroll to bottom
    def _scroll_bottom():
        ui.run_javascript(
            "const el = document.querySelector('#msg-scroll .q-scrollarea__container');"
            "if (el) el.scrollTop = el.scrollHeight;"
        )

    try:
        if images:
            # Bilder → blockierender Fallback
            loop = asyncio.get_event_loop()
            from services import get_services
            from core.thread_pools import CHAT_POOL
            services = get_services()
            result = await loop.run_in_executor(
                CHAT_POOL,
                lambda: services.chat.handle_message(agent_id, message, images=images)
            )
            typing.delete()
            reply_label.content = result.get("reply", "")
            if result.get("image"):
                with container:
                    _render_message(role="assistant", content="", image=result["image"])
                reply_label.delete()
        else:
            # Text → Streaming via SSE
            import httpx
            params = urllib.parse.urlencode({"agent_id": agent_id, "message": message})
            stream_url = f"http://localhost:5050/api/chat/stream?{params}"

            timeout = httpx.Timeout(connect=5.0, read=360.0, write=5.0, pool=5.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("GET", stream_url) as resp:
                    resp.raise_for_status()
                    first_chunk = True
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        if data.get("done"):
                            typing.delete()
                            a2a = data.get("a2a_dispatches", [])
                            display_reply = data.get("display_reply")
                            if a2a:
                                if display_reply is not None:
                                    reply_label.content = display_reply
                                else:
                                    reply_label.content = "".join(accumulated)
                                if not display_reply and not accumulated:
                                    reply_label.delete()
                                with container:
                                    for d in a2a:
                                        _render_a2a_card(
                                            agent.get("name", ""),
                                            d["recipient_name"],
                                            d["task_text"],
                                        )
                            break

                        if data.get("error"):
                            typing.delete()
                            reply_label.content = f"**Fehler:** {data['error']}"
                            break

                        chunk = data.get("chunk", "")
                        if chunk:
                            if first_chunk:
                                typing.delete()
                                first_chunk = False
                            accumulated.append(chunk)
                            reply_label.content = "".join(accumulated)
                            _scroll_bottom()

    except Exception as e:
        try:
            typing.delete()
        except Exception:
            pass
        reply_label.content = f"**Fehler:** {str(e)}"
        logger.error("Chat-Streaming-Fehler: %s", e)


def _render_a2a_card(sender_name: str, recipient_name: str, task_text: str):
    """Rendert eine A2A-Delegations-Card."""
    preview = task_text[:180] + ("..." if len(task_text) > 180 else "")
    with ui.element("div").style(
        "border-left: 3px solid #00bcd4; background: rgba(0,188,212,0.06); "
        "border-radius: 0 6px 6px 0; padding: 8px 12px; "
        "align-self: flex-start; max-width: 600px;"
    ):
        with ui.row().style("align-items: center; gap: 6px; margin-bottom: 4px;"):
            ui.icon("arrow_forward").style("font-size: 14px; color: #00bcd4;")
            ui.label(f"{sender_name}  →  @{recipient_name}").style(
                "font-size: 12px; font-weight: 600; color: #00bcd4;"
            )
            ui.label("A2A").style(
                "font-size: 9px; padding: 1px 5px; border-radius: 3px; "
                "border: 1px solid #00bcd4; color: #00bcd4; "
                "font-family: 'SF Mono',monospace;"
            )
        ui.label(preview).style(
            "font-size: 12px; color: #3a5a3a; line-height: 1.5;"
        )


def _clear_history(agent_id: str):
    from services import get_services
    services = get_services()
    services.agents.clear_history(agent_id)
    ui.run_javascript("window.location.reload()")
    ui.notify("History gelöscht", type="positive")


def _edit_agent(agent: dict):
    from ui.dialogs.agent_form import AgentFormDialog
    AgentFormDialog(agent=agent, on_save=lambda: ui.run_javascript("window.location.reload()"))


def _show_create_dialog():
    from ui.dialogs.agent_form import AgentFormDialog
    AgentFormDialog(on_save=lambda: ui.run_javascript("window.location.reload()"))


def _show_task_monitor():
    """Öffnet den Task-Monitor als Modal."""
    from ui.components.task_monitor import TaskMonitor
    with ui.dialog().props("maximized").style(
        "background: #070d08;"
    ) as dlg:
        dlg.open()
        with ui.card().style(
            "width: 900px; max-width: 95vw; background: #070d08; "
            "border: 1px solid #182e18;"
        ):
            with ui.row().style(
                "align-items: center; justify-content: space-between; "
                "padding: 16px 20px; border-bottom: 1px solid #0f2010;"
            ):
                with ui.row().style("gap: 8px; align-items: center;"):
                    ui.icon("task_alt").style("color: #00e676; font-size: 20px;")
                    ui.label("Task Monitor").style(
                        "font-size: 16px; font-weight: 600; color: #e4f4e4;"
                    )
                ui.button(icon="close", on_click=dlg.close) \
                    .props("flat dense round").style("color: #3a5a3a;")
            with ui.element("div").style("padding: 16px;"):
                TaskMonitor()
