"""
ui/pages/chat.py — Chat-Interface.
Layout: Links Agenten-Sidebar (228px) | Rechts Chat-Bereich.

Architektur (v3 — full client-side):
- Send/Streaming komplett via JavaScript fetch() + SSE
- Kein Python Event-Handler für Send nötig (umgeht core.loop Bug)
- NiceGUI nur für Page-Render + Layout
- Navigation via <a href>
"""
import logging
import json
import html as html_mod
import re

from nicegui import ui
from ui.layout import create_layout
from ui.theme import apply_theme
from config.settings import settings

logger = logging.getLogger(__name__)


@ui.page("/chat/{agent_id}")
def chat_page(agent_id: str):
    apply_theme()
    create_layout("chat")

    from services import get_services
    services = get_services()
    agents = services.agents.list_all()  # einmal laden, agent daraus ableiten
    agent = next((a for a in agents if a["id"] == agent_id), None)
    agents_sorted = sorted(agents, key=lambda a: (not a.get("favorite"), a.get("name", "").lower()))

    if not agent:
        with ui.column().classes("items-center justify-center w-full").style("height: calc(100vh - 44px)"):
            ui.icon("person_off").style("font-size: 48px; color: #3a5a3a;")
            ui.label("Agent nicht gefunden").style("color: #ef4444; font-size: 18px; margin-top: 12px;")
        return

    agent_name = agent.get("name", "?")

    # ─── CSS Overrides ───────────────────────────────────────────────────
    ui.add_css("""
        .q-page { min-height: unset !important; }
        .q-page-container { padding-bottom: 0 !important; }
        body > div#app > div { height: 100vh; overflow: hidden; }

        /* ── Composer ── */
        .ac-composer-wrap {
            padding: 8px 16px 22px;
            border-top: 1px solid #0f2010;
            background: #070d08;
            flex-shrink: 0;
            position: relative;
        }
        .ac-composer-border {
            border-radius: 12px;
            padding: 1.5px;
            background: linear-gradient(135deg, #1a3a1a 0%, #0f2010 50%, #1a3a1a 100%);
            transition: background .3s;
        }
        .ac-composer-border:focus-within {
            background: linear-gradient(135deg, #00e676 0%, #00bcd4 50%, #8b5cf6 100%);
        }
        .ac-composer-inner {
            background: #080f09;
            border-radius: 11px;
            overflow: hidden;
        }
        #ac-input {
            width: 100%;
            background: transparent;
            border: none;
            outline: none;
            color: #e4f4e4;
            font-size: 14px;
            padding: 12px 16px 4px;
            resize: none;
            font-family: inherit;
            line-height: 1.6;
            min-height: 44px;
            max-height: 180px;
            box-sizing: border-box;
            display: block;
        }
        #ac-input::placeholder { color: #2a4a2a; }
        .ac-composer-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 4px 8px 8px;
        }
        .ac-composer-left { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
        .ac-composer-right { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }

        /* Quick-Action Chips */
        .ac-chip {
            display: inline-flex; align-items: center; gap: 4px;
            padding: 3px 9px; border-radius: 6px;
            font-size: 11px; font-weight: 600;
            cursor: pointer; border: 1px solid transparent;
            transition: all .15s; white-space: nowrap;
            font-family: monospace; letter-spacing: .3px;
        }
        .ac-chip .material-icons { font-size: 12px; }
        .ac-chip-mic  { color: #00e676; border-color: #00e67633; background: rgba(0,230,118,.06); }
        .ac-chip-mic:hover  { background: rgba(0,230,118,.14); border-color: #00e676; }
        .ac-chip-shot { color: #00bcd4; border-color: #00bcd433; background: rgba(0,188,212,.06); }
        .ac-chip-shot:hover { background: rgba(0,188,212,.14); border-color: #00bcd4; }
        .ac-chip-img  { color: #ab47bc; border-color: #ab47bc33; background: rgba(171,71,188,.06); }
        .ac-chip-img:hover  { background: rgba(171,71,188,.14); border-color: #ab47bc; }
        .ac-chip-web  { color: #ff9800; border-color: #ff980033; background: rgba(255,152,0,.06); }
        .ac-chip-web:hover  { background: rgba(255,152,0,.14); border-color: #ff9800; }
        .ac-chip-fav  { color: #ffd700; border-color: #ffd70033; background: rgba(255,215,0,.06); }
        .ac-chip-fav:hover  { background: rgba(255,215,0,.14); border-color: #ffd700; }
        .ac-chip-fav.has-text { color: #ffd700; border-color: #ffd700; background: rgba(255,215,0,.12); }
        .ac-chip-think { border-color: #b794f433; background: rgba(183,148,244,.06); }
        .ac-chip-think:hover { background: rgba(183,148,244,.14); border-color: #b794f4; }

        /* Favoriten-Panel */
        #ac-fav-panel {
            display: none;
            position: absolute;
            bottom: calc(100% + 6px);
            left: 16px;
            width: 340px;
            max-height: 280px;
            background: #080f09;
            border: 1px solid #1a3a1a;
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(0,0,0,.6);
            z-index: 800;
            overflow: hidden;
            flex-direction: column;
        }
        #ac-fav-panel.open { display: flex; }
        .ac-fav-head {
            display: flex; align-items: center; justify-content: space-between;
            padding: 8px 12px; border-bottom: 1px solid #0f2010;
            font-size: 11px; font-weight: 700; color: #3a5a3a;
            text-transform: uppercase; letter-spacing: .5px; font-family: monospace;
            flex-shrink: 0;
        }
        .ac-fav-list {
            flex: 1; overflow-y: auto; padding: 6px;
        }
        .ac-fav-item {
            display: flex; align-items: center; gap: 6px;
            padding: 7px 10px; border-radius: 8px; cursor: pointer;
            transition: background .12s; margin-bottom: 3px;
            border: 1px solid transparent;
        }
        .ac-fav-item:hover { background: rgba(255,215,0,.06); border-color: #ffd70033; }
        .ac-fav-item-text {
            flex: 1; font-size: 12px; color: #b8d4b8;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .ac-fav-del {
            font-size: 13px; color: #2a4a2a; cursor: pointer;
            background: none; border: none; padding: 2px;
            line-height: 1; flex-shrink: 0; transition: color .15s;
        }
        .ac-fav-del:hover { color: #ef4444; }
        .ac-fav-empty {
            font-size: 12px; color: #2a4a2a; text-align: center;
            padding: 24px 12px; font-style: italic;
        }

        /* Mic recording state */
        #ac-mic-btn.recording {
            background: rgba(239,68,68,.12) !important;
            color: #ef4444 !important;
            border-color: rgba(239,68,68,.5) !important;
        }
        .ac-mic-waves {
            display: none; align-items: center; gap: 2px; height: 12px;
        }
        .ac-mic-waves span {
            display: inline-block; width: 2px; height: 3px;
            background: #ef4444; border-radius: 1px;
            animation: ac-wave 0.8s ease-in-out infinite;
        }
        .ac-mic-waves span:nth-child(2) { animation-delay: .15s; }
        .ac-mic-waves span:nth-child(3) { animation-delay: .3s; }
        .ac-mic-waves span:nth-child(4) { animation-delay: .45s; }
        .ac-mic-waves span:nth-child(5) { animation-delay: .6s; }
        @keyframes ac-wave {
            0%, 100% { height: 2px; opacity: .4; }
            50% { height: 11px; opacity: 1; }
        }
        #ac-mic-btn.recording .ac-mic-waves { display: flex; }
        #ac-mic-btn.recording .ac-mic-icon { display: none; }

        /* Attachment-Liste (multi) */
        #ac-attach-list {
            display: flex; flex-wrap: wrap; gap: 6px;
            padding: 0 8px; margin: 6px 0 0;
        }
        #ac-attach-list:empty { display: none; }
        .ac-attach-item {
            display: flex; align-items: center; gap: 6px;
            padding: 4px 8px 4px 4px;
            background: rgba(0,230,118,.06);
            border: 1px solid rgba(0,230,118,.22);
            border-radius: 8px;
            max-width: 220px;
        }
        .ac-attach-item img {
            width: 32px; height: 32px; border-radius: 5px; object-fit: cover;
            flex-shrink: 0;
        }
        .ac-attach-item .ac-attach-icon {
            width: 32px; height: 32px; border-radius: 5px;
            background: rgba(100,181,246,.12); color: #64b5f6;
            display: flex; align-items: center; justify-content: center;
            font-size: 18px; flex-shrink: 0;
        }
        .ac-attach-item .ac-attach-name {
            font-size: 11px; color: #b8d4b8; flex: 1;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .ac-attach-item .ac-attach-del {
            background: none; border: none; color: #3a5a3a; cursor: pointer;
            padding: 0; line-height: 1; flex-shrink: 0;
            transition: color .15s;
        }
        .ac-attach-item .ac-attach-del:hover { color: #ef4444; }
        .ac-chip-attach.has-image { background: rgba(0,230,118,.12) !important;
            color: #00e676 !important; border-color: rgba(0,230,118,.4) !important; }

        /* Model-Chip */
        .ac-model-chip {
            font-size: 10px; font-family: monospace;
            color: #1a3a1a; padding: 2px 7px;
            border-radius: 5px;
            border: 1px solid #0f2010;
            white-space: nowrap;
        }

        /* Send-Button */
        #ac-send-btn {
            display: flex; align-items: center; gap: 4px;
            padding: 6px 14px; border-radius: 8px;
            background: linear-gradient(135deg, #00e676, #00c853);
            color: #000; border: none; cursor: pointer;
            font-size: 12px; font-weight: 700;
            box-shadow: 0 2px 10px rgba(0,230,118,.25);
            transition: all .2s;
        }
        #ac-send-btn:hover {
            box-shadow: 0 4px 20px rgba(0,230,118,.45);
            transform: translateY(-1px);
        }
        #ac-send-btn:active { transform: translateY(0); }
        #ac-send-btn.busy {
            background: #0f2010; color: #3a5a3a;
            box-shadow: none; cursor: default;
        }
    """)

    # ─── 2-Spalten-Layout ────────────────────────────────────────────────
    with ui.element("div").style(
        "display: flex; width: 100%; height: calc(100vh - 44px); overflow: hidden; gap: 0;"
    ):
        # Linke Sidebar
        with ui.element("div").style(
            "width: 228px; min-width: 228px; flex-shrink: 0; "
            "background: #070d08; border-right: 1px solid #0f2010; "
            "display: flex; flex-direction: column; overflow: hidden;"
        ):
            with ui.element("div").style(
                "padding: 10px 14px 8px; font-size: 10px; font-weight: 700; "
                "color: #3a5a3a; text-transform: uppercase; letter-spacing: 1.2px; "
                "font-family: 'SF Mono','Fira Code',monospace; flex-shrink: 0; "
                "border-bottom: 1px solid #0f2010; display: flex; "
                "align-items: center; justify-content: space-between;"
            ):
                ui.label("Agenten")
                ui.html('''<a href="/agent/new" title="Neuer Agent"
                    style="color:#00e676;width:24px;height:24px;display:inline-flex;
                    align-items:center;justify-content:center;border-radius:50%;
                    text-decoration:none">
                    <span class="material-icons" style="font-size:16px">add</span>
                </a>''')

            with ui.scroll_area().style("flex: 1; min-height: 0;"):
                with ui.column().style("padding: 4px 6px; gap: 0;"):
                    for ag in agents_sorted:
                        _render_sidebar_agent(ag, agent_id)

        # Rechter Bereich: Chat
        with ui.element("div").style(
            "flex: 1; display: flex; flex-direction: column; overflow: hidden; background: #050a06; position: relative;"
        ):
            _render_chat_topbar(agent, agent_id, agent.get("color", "#00e676"))

            # Messages Container
            messages_html = _build_history_html(agent_id)
            ui.html(
                f'<div id="ac-scroll" style="flex:1;overflow-y:auto;min-height:0">'
                f'<div id="ac-messages" style="padding:16px 24px;display:flex;flex-direction:column;gap:10px">'
                f'{messages_html}'
                f'</div></div>'
            ).style("flex:1;display:flex;flex-direction:column;min-height:0;overflow:hidden")

            # Input Area — Composer 2029
            model_short = html_mod.escape(agent.get("model", "").split("/")[-1][:18])
            model_chip = f'<span class="ac-model-chip">{model_short}</span>' if model_short else ''
            ui.html(f'''
                <div class="ac-composer-wrap">
                    <div id="ac-fav-panel">
                        <div class="ac-fav-head">
                            <span>⭐ Favoriten</span>
                            <button id="ac-fav-close" style="background:none;border:none;color:#3a5a3a;cursor:pointer;font-size:14px;line-height:1;padding:0">✕</button>
                        </div>
                        <div class="ac-fav-list" id="ac-fav-list">
                            <div class="ac-fav-empty">Noch keine Favoriten gespeichert.</div>
                        </div>
                    </div>
                    <div class="ac-composer-border">
                        <div class="ac-composer-inner">
                            <input type="file" id="ac-file-input" accept="image/*,audio/*,video/*" multiple style="display:none">
                            <div id="ac-attach-list"></div>
                            <textarea id="ac-input" rows="1"
                                placeholder="Nachricht an {html_mod.escape(agent_name)}…"></textarea>
                            <div class="ac-composer-bar">
                                <div class="ac-composer-left">
                                    <button id="ac-chip-attach" class="ac-chip ac-chip-attach" title="Bild / Audio / Video anhängen">
                                        <span class="material-icons">attach_file</span>
                                        Anhang
                                    </button>
                                    <button id="ac-mic-btn" class="ac-chip ac-chip-mic" title="Spracheingabe">
                                        <div class="ac-mic-waves">
                                            <span></span><span></span><span></span>
                                            <span></span><span></span>
                                        </div>
                                        <span class="material-icons ac-mic-icon">mic</span>
                                        <span class="ac-mic-label">Sprache</span>
                                    </button>
                                    <button id="ac-chip-fav" class="ac-chip ac-chip-fav" title="Prompt speichern / Favoriten">
                                        <span class="material-icons" style="font-size:12px">star</span>
                                        <span class="ac-fav-label">Favoriten</span>
                                    </button>
                                    <button class="ac-chip ac-chip-shot" id="ac-chip-shot" title="Screenshot einer URL">
                                        <span class="material-icons">photo_camera</span>
                                        Shot
                                    </button>
                                    <button class="ac-chip ac-chip-img" id="ac-chip-img" title="Bild generieren">
                                        <span class="material-icons">auto_awesome</span>
                                        Bild
                                    </button>
                                    <button class="ac-chip ac-chip-web" id="ac-chip-web" title="Web-Suche">
                                        <span class="material-icons">travel_explore</span>
                                        Web
                                    </button>
                                    <button class="ac-chip ac-chip-think" id="ac-think-btn" title="Thinking AN — klicken zum Deaktivieren">
                                        <span class="material-icons">psychology</span>
                                        Think
                                    </button>
                                </div>
                                <div class="ac-composer-right">
                                    <button id="ac-tts-stop" class="tts-stop-btn" title="Audio-Ausgabe stoppen" style="display:none">
                                        <span class="material-icons" style="font-size:13px">stop_circle</span>
                                        Stopp
                                    </button>
                                    {model_chip}
                                    <button id="ac-send-btn">
                                        <span class="material-icons" style="font-size:14px">arrow_upward</span>
                                        Senden
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            ''').style("flex-shrink:0")

            # Livelog-Panel (overlay, initial hidden)
            if settings.LIVELOG:
                ui.html('''<div id="ac-livelog" style="display:none;flex-direction:column;
                    position:absolute;bottom:80px;right:20px;width:480px;max-height:320px;
                    background:#070d08;border:1px solid #1a3a1a;border-radius:10px;
                    box-shadow:0 8px 32px rgba(0,0,0,0.6);z-index:900;overflow:hidden">
                    <div style="display:flex;align-items:center;justify-content:space-between;
                        padding:8px 12px;background:#0a150b;border-bottom:1px solid #0f2010;flex-shrink:0">
                        <span style="font-size:11px;font-weight:700;color:#3a5a3a;text-transform:uppercase;
                            letter-spacing:0.8px;font-family:monospace">
                            <span class="material-icons" style="font-size:14px;vertical-align:middle;margin-right:4px;color:#00e676">terminal</span>
                            Live-Log
                            <span id="ac-livelog-count" style="margin-left:6px;color:#00e676;font-weight:400">0</span>
                        </span>
                        <button id="ac-livelog-clear" style="font-size:10px;color:#3a5a3a;background:transparent;
                            border:1px solid #182e18;border-radius:4px;padding:2px 8px;cursor:pointer;
                            font-family:monospace">clear</button>
                    </div>
                    <div id="ac-livelog-body" style="flex:1;overflow-y:auto;padding:6px 10px;
                        font-family:'SF Mono','Fira Code',monospace;min-height:0"></div>
                </div>''')

    # ─── JavaScript: Config injizieren + externe Datei laden ─────────────
    escaped_agent_id = json.dumps(agent_id)
    escaped_agent_name = json.dumps(html_mod.escape(agent_name))
    escaped_agent_voice = json.dumps(agent.get("voice", ""))
    import time as _time
    _v = int(_time.time())
    livelog_script = f'<script src="/static/js/livelog.js?v={_v}"></script>' if settings.LIVELOG else ''
    ui.add_head_html(f"""<script>window._acConfig = {{ agentId: {escaped_agent_id}, agentName: {escaped_agent_name}, agentVoice: {escaped_agent_voice} }};</script>
<script src="/static/js/chat.js?v={_v}"></script>
{livelog_script}
<script>
document.addEventListener('DOMContentLoaded', function() {{
    var stopBtn = document.getElementById('ac-tts-stop');
    if (stopBtn) {{
        stopBtn.addEventListener('click', function() {{
            if (window._acTts) window._acTts.stop();
        }});
    }}
}});
</script>""")


# ─── History als HTML rendern ────────────────────────────────────────────────


def _build_history_html(agent_id: str) -> str:
    from services import get_services
    try:
        services = get_services()
        history = services.agents.get_history(agent_id)
        if not history:
            return (
                '<div id="ac-empty-hint" style="color:#3a5a3a;font-size:13px;font-style:italic;'
                'text-align:center;padding:32px 0;width:100%">'
                'Noch keine Nachrichten. Starte eine Unterhaltung!</div>'
            )
        parts = []
        for msg in history[-50:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            image = msg.get("image") or msg.get("task_image")
            skill = msg.get("skill_used")
            if not content and not image:
                continue
            parts.append(_msg_to_html(role, content, image, skill))
        return "\n".join(parts)
    except Exception as e:
        logger.error("History laden fehlgeschlagen: %s", e)
        return ""


def _msg_to_html(role: str, content: str, image=None, skill=None) -> str:
    is_user = role == "user"
    align = "flex-end" if is_user else "flex-start"
    bbl = (
        "background:rgba(0,230,118,.08);border:1px solid #182e18;color:#e4f4e4;border-bottom-right-radius:3px;"
        if is_user else
        "background:#0d1a0e;border:1px solid #0f2010;color:#b8d4b8;border-bottom-left-radius:3px;"
    )
    label = "Du" if is_user else "Assistant"
    skill_text = f" · {html_mod.escape(skill)}" if skill else ""

    h = (
        f'<div style="display:flex;flex-direction:column;gap:3px;max-width:820px;'
        f'align-self:{align};align-items:{align};width:100%">'
        f'<span style="font-size:10px;font-family:monospace;color:#3a5a3a;padding:0 4px">'
        f'{label}{skill_text}</span>'
    )
    if image:
        if image.startswith("data:video"):
            h += f'<video src="{html_mod.escape(image)}" controls style="max-width:480px;border-radius:8px;margin-bottom:4px"></video>'
        else:
            h += f'<img src="{html_mod.escape(image)}" style="max-width:320px;border-radius:8px;margin-bottom:4px">'
    if content:
        rendered = _simple_md(content)
        h += (
            f'<div style="padding:10px 14px;border-radius:10px;font-size:14px;'
            f'line-height:1.6;word-break:break-word;{bbl}">{rendered}</div>'
        )
    h += '</div>'
    return h


def _simple_md(text: str) -> str:
    t = html_mod.escape(text)
    t = re.sub(r'```(\w*)\n(.*?)```', lambda m: f'<pre style="background:#0a150b;padding:8px;border-radius:4px;overflow-x:auto;font-size:12px"><code>{m.group(2)}</code></pre>', t, flags=re.DOTALL)
    t = re.sub(r'`([^`]+)`', r'<code style="background:#0a150b;padding:1px 4px;border-radius:3px;font-size:12px">\1</code>', t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'\*(.+?)\*', r'<em>\1</em>', t)
    t = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" target="_blank" style="color:#00e676">\1</a>', t)
    t = t.replace('\n', '<br>')
    return t


# ─── Sidebar Agent ────────────────────────────────────────────────────────────


def _render_sidebar_agent(agent: dict, current_agent_id: str):
    ag_id = agent["id"]
    name = agent.get("name", "?")
    color = agent.get("color", "#00e676")
    model = agent.get("model", "")
    is_fav = agent.get("favorite", False)
    is_selected = ag_id == current_agent_id
    initials = name[:2].upper() if len(name) >= 2 else name[0].upper()

    border_style = "border-left: 2px solid #ffd700;" if is_fav else "border-left: 2px solid transparent;"
    bg_style = (
        f"background: rgba(0,230,118,.08); {border_style}"
        if is_selected else f"background: transparent; {border_style}"
    )

    with ui.element("a").props(f'href="/chat/{ag_id}" data-agent-id="{ag_id}"').style(
        f"display: flex; align-items: center; gap: 8px; padding: 8px; "
        f"border-radius: 6px; cursor: pointer; transition: background .12s; "
        f"margin-bottom: 2px; text-decoration: none; {bg_style}"
    ).classes("ac-agent-item"):
        with ui.element("div").style(
            f"width: 30px; height: 30px; border-radius: 50%; background: {color}; "
            f"display: flex; align-items: center; justify-content: center; "
            f"font-size: 11px; font-weight: 700; color: #000; flex-shrink: 0; text-transform: uppercase;"
        ):
            ui.label(initials)
        with ui.column().style("flex: 1; min-width: 0; gap: 0;"):
            name_color = "color: #00e676;" if is_selected else "color: #b8d4b8;"
            ui.label(name).style(
                f"font-size: 13px; font-weight: 500; {name_color} "
                f"overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
            )
            if model:
                short = model.split(":")[-1][:12] if ":" in model else model[:12]
                ui.label(short).style("font-size: 9px; color: #3a5a3a; font-family: monospace;")
        if is_fav:
            ui.icon("star").style("font-size: 12px; color: #ffd700; flex-shrink: 0;")


# ─── Chat Topbar ──────────────────────────────────────────────────────────────


def _build_topbar_html(agent: dict, agent_id: str, color: str) -> str:
    """Topbar als reiner HTML-String — wird sowohl beim initialen Render als auch
    vom /api/chat/context Endpoint für den JS-basierten Agentenwechsel genutzt."""
    name = agent.get("name", "?")
    role = agent.get("role", "")
    skills = agent.get("skills", [])
    model = agent.get("model", "")
    is_fav = agent.get("favorite", False)
    initials = html_mod.escape((name[:2] if len(name) >= 2 else name[:1]).upper())
    safe_name = html_mod.escape(name)
    safe_role = html_mod.escape(role)
    safe_model = html_mod.escape(model.split("/")[-1][:16]) if model else ""
    safe_color = html_mod.escape(color)

    skill_chips = "".join(
        f'<span style="font-size:10px;font-family:monospace;padding:2px 6px;border-radius:3px;'
        f'background:rgba(0,230,118,0.08);color:#00e676;border:1px solid rgba(0,230,118,0.2)">'
        f'{html_mod.escape(sk)}</span>'
        for sk in skills[:5]
    )

    star_html = '<span class="material-icons" style="font-size:14px;color:#ffd700">star</span>' if is_fav else ""
    role_html = f'<div style="font-size:12px;color:#3a5a3a;font-family:monospace">{safe_role}</div>' if role else ""
    model_html = (
        f'<span style="font-size:10px;font-family:monospace;padding:2px 7px;border-radius:3px;'
        f'background:#0f2010;color:#3a5a3a;border:1px solid #182e18;align-self:center">'
        f'{safe_model}</span>'
    ) if safe_model else ""

    return (
        f'<div id="ac-topbar" style="display:flex;align-items:center;gap:16px;padding:0 24px;'
        f'height:72px;background:#070d08;border-bottom:1px solid #0f2010;flex-shrink:0">'

        # Avatar
        f'<div style="width:44px;height:44px;border-radius:50%;background:{safe_color};'
        f'display:flex;align-items:center;justify-content:center;'
        f'font-size:18px;font-weight:700;color:#000;flex-shrink:0">{initials}</div>'

        # Name + Role
        f'<div style="gap:2px;flex:1;min-width:0;display:flex;flex-direction:column">'
        f'<div style="display:flex;align-items:center;gap:8px">'
        f'<span style="font-size:16px;font-weight:600;color:#e4f4e4">{safe_name}</span>'
        f'{star_html}</div>'
        f'{role_html}</div>'

        # Skills
        f'<div style="display:flex;gap:4px;flex-wrap:wrap;max-width:300px">{skill_chips}</div>'

        # Actions
        f'<div style="display:flex;gap:6px;margin-left:auto;flex-shrink:0;align-items:center">'
        f'{model_html}'
        # Delete
        f'<div style="position:relative;display:inline-flex">'
        f'<button id="ac-clear-btn" title="History löschen"'
        f' style="color:#3a5a3a;width:32px;height:32px;display:inline-flex;'
        f'align-items:center;justify-content:center;border-radius:50%;'
        f'background:transparent;border:none;cursor:pointer">'
        f'<span class="material-icons" style="font-size:18px">delete_sweep</span></button>'
        f'<div id="ac-clear-confirm" style="display:none;position:absolute;top:38px;right:0;'
        f'background:#0f1a10;border:1px solid #1a3a1a;border-radius:10px;padding:12px 16px;'
        f'box-shadow:0 8px 24px rgba(0,0,0,0.5);z-index:999;white-space:nowrap;min-width:200px">'
        f'<div style="font-size:13px;color:#e4f4e4;margin-bottom:10px">History löschen?</div>'
        f'<div style="display:flex;gap:8px;justify-content:flex-end">'
        f'<button id="ac-clear-no" style="padding:5px 14px;border-radius:6px;'
        f'background:transparent;color:#3a5a3a;border:1px solid #182e18;font-size:12px;cursor:pointer">Abbrechen</button>'
        f'<button id="ac-clear-yes" style="padding:5px 14px;border-radius:6px;'
        f'background:#ef4444;color:#fff;border:none;font-size:12px;cursor:pointer;font-weight:600">Löschen</button>'
        f'</div></div></div>'
        # Edit — JS-basiert, damit es nach Agent-Switch zum richtigen Agenten geht
        f'<button id="ac-edit-btn" title="Bearbeiten"'
        f' style="color:#3a5a3a;width:32px;height:32px;display:inline-flex;'
        f'align-items:center;justify-content:center;border-radius:50%;'
        f'background:transparent;border:none;cursor:pointer">'
        f'<span class="material-icons" style="font-size:18px">edit</span></button>'
        # Tasks
        f'<a href="/tasks" title="Tasks"'
        f' style="color:#3a5a3a;width:32px;height:32px;display:inline-flex;'
        f'align-items:center;justify-content:center;border-radius:50%;text-decoration:none">'
        f'<span class="material-icons" style="font-size:18px">task_alt</span></a>'
        f'</div>'  # actions
        f'</div>'  # topbar
    )


def _render_chat_topbar(agent: dict, agent_id: str, color: str):
    """Rendert die Topbar als NiceGUI-Element (initial page render)."""
    ui.html(_build_topbar_html(agent, agent_id, color))


