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

        /* ── Sidebar: Compact-Mode + Activity-Pulse ── */
        @keyframes ac-pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50%      { opacity: .55; transform: scale(1.12); }
        }
        @keyframes ac-border-pulse {
            0%, 100% { box-shadow: 0 0 0 1px rgba(0,230,118,.12), 0 0 12px rgba(0,230,118,.05); }
            50%      { box-shadow: 0 0 0 1px rgba(0,230,118,.5),  0 0 22px rgba(0,230,118,.25); }
        }
        .ac-agent-item.is-active {
            border-color: rgba(0,230,118,.45) !important;
            animation: ac-border-pulse 1.6s ease-in-out infinite;
        }
        .ac-agent-item.is-active .ac-skills-row {
            animation: ac-pulse 1.2s ease-in-out infinite;
        }
        .ac-activity-chip {
            display: none;
            font-size: 10px; font-family: monospace;
            padding: 2px 6px; border-radius: 4px; margin-top: 4px;
            background: rgba(0,230,118,.12); color: #00e676;
            border: 1px solid rgba(0,230,118,.3);
            align-self: flex-start;
            letter-spacing: .3px;
            max-width: 100%; overflow: hidden;
            text-overflow: ellipsis; white-space: nowrap;
        }
        .ac-agent-item.is-active .ac-activity-chip { display: inline-block; }

        /* Compact-Mode: alles ausblenden außer selected + active */
        .ac-sidebar.compact .ac-agent-item:not(.is-selected):not(.is-active) {
            display: none;
        }
        .ac-sidebar.compact #ac-filter-wrap { display: none; }

        /* Sidebar-Header Controls */
        .ac-sidebar-head-btn {
            width: 20px; height: 20px; display: inline-flex;
            align-items: center; justify-content: center; border-radius: 4px;
            cursor: pointer; color: #3a5a3a; background: transparent;
            border: none; transition: all .15s;
        }
        .ac-sidebar-head-btn:hover { color: #00e676; background: rgba(0,230,118,.08); }
        .ac-sidebar-head-btn.active { color: #00e676; }
        .ac-sidebar-head-btn .material-icons { font-size: 14px; }

        #ac-filter-wrap {
            padding: 6px 10px;
            border-bottom: 1px solid #0f2010;
            flex-shrink: 0;
        }
        #ac-filter-input {
            width: 100%; background: #0a130c; border: 1px solid #132418;
            border-radius: 6px; padding: 5px 10px;
            color: #e4f4e4; font-size: 12px; font-family: inherit;
            outline: none; transition: border-color .15s;
        }
        #ac-filter-input:focus { border-color: #00e676; }
        #ac-filter-input::placeholder { color: #2a4a2a; }

        .ac-empty-hint {
            color: #3a5a3a; font-size: 11px; font-style: italic;
            text-align: center; padding: 18px 8px; font-family: monospace;
        }
    """)

    # ─── 2-Spalten-Layout ────────────────────────────────────────────────
    with ui.element("div").style(
        "display: flex; width: 100%; height: calc(100vh - 44px); overflow: hidden; gap: 0;"
    ):
        # Linke Sidebar
        with ui.element("div").classes("ac-sidebar compact").props('id="ac-sidebar"').style(
            "width: 228px; min-width: 228px; flex-shrink: 0; "
            "background: #070d08; border-right: 1px solid #0f2010; "
            "display: flex; flex-direction: column; overflow: hidden;"
        ):
            ui.html('''
                <div style="padding: 10px 14px 8px; font-size: 10px; font-weight: 700;
                    color: #3a5a3a; text-transform: uppercase; letter-spacing: 1.2px;
                    font-family: 'SF Mono','Fira Code',monospace; flex-shrink: 0;
                    border-bottom: 1px solid #0f2010; display: flex;
                    align-items: center; justify-content: space-between; gap: 6px;">
                    <span id="ac-sidebar-title">Aktiv</span>
                    <div style="display:flex;gap:4px;align-items:center">
                        <button id="ac-toggle-mode" class="ac-sidebar-head-btn"
                            title="Alle Agenten anzeigen">
                            <span class="material-icons">groups</span>
                        </button>
                        <a href="/agent/new" class="ac-sidebar-head-btn" title="Neuer Agent">
                            <span class="material-icons">add</span>
                        </a>
                    </div>
                </div>
                <div id="ac-filter-wrap">
                    <input id="ac-filter-input" type="text"
                        placeholder="Filter (Name, Skill, Rolle…)" autocomplete="off">
                </div>
            ''')

            with ui.scroll_area().style("flex: 1; min-height: 0;"):
                with ui.column().props('id="ac-agents-list"').style("padding: 4px 6px; gap: 0;"):
                    for ag in agents_sorted:
                        _render_sidebar_agent(ag, agent_id)
                    ui.html('<div id="ac-sidebar-empty" class="ac-empty-hint" style="display:none">Keine aktiven Agenten.<br>Auf <span class="material-icons" style="font-size:12px;vertical-align:middle">groups</span> klicken, um alle zu sehen.</div>')

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

    // ─── Sidebar: Compact-Toggle + Filter + Activity-Polling ─────────
    var sidebar = document.getElementById('ac-sidebar');
    var toggleBtn = document.getElementById('ac-toggle-mode');
    var filterInput = document.getElementById('ac-filter-input');
    var titleEl = document.getElementById('ac-sidebar-title');
    var emptyHint = document.getElementById('ac-sidebar-empty');

    function setMode(mode) {{
        if (!sidebar) return;
        if (mode === 'all') {{
            sidebar.classList.remove('compact');
            if (toggleBtn) toggleBtn.classList.add('active');
            if (toggleBtn) toggleBtn.title = 'Nur aktive anzeigen';
            if (titleEl) titleEl.textContent = 'Alle Agenten';
        }} else {{
            sidebar.classList.add('compact');
            if (toggleBtn) toggleBtn.classList.remove('active');
            if (toggleBtn) toggleBtn.title = 'Alle Agenten anzeigen';
            if (titleEl) titleEl.textContent = 'Aktiv';
        }}
        try {{ localStorage.setItem('ac.sidebarMode', mode); }} catch(e) {{}}
        updateEmptyHint();
    }}

    function updateEmptyHint() {{
        if (!sidebar || !emptyHint) return;
        var isCompact = sidebar.classList.contains('compact');
        if (!isCompact) {{ emptyHint.style.display = 'none'; return; }}
        // Im Compact-Mode: visible wenn keine Agent-Card angezeigt wird
        var visible = sidebar.querySelectorAll('.ac-agent-item.is-selected, .ac-agent-item.is-active');
        emptyHint.style.display = visible.length === 0 ? 'block' : 'none';
    }}

    // Initialer Mode aus localStorage (default: compact)
    var savedMode = 'compact';
    try {{ savedMode = localStorage.getItem('ac.sidebarMode') || 'compact'; }} catch(e) {{}}
    setMode(savedMode);

    if (toggleBtn) {{
        toggleBtn.addEventListener('click', function(ev) {{
            ev.preventDefault();
            var isCompact = sidebar.classList.contains('compact');
            setMode(isCompact ? 'all' : 'compact');
        }});
    }}

    // Filter-Input (funktioniert nur im 'all'-Mode weil sonst alle hidden)
    if (filterInput) {{
        filterInput.addEventListener('input', function() {{
            var q = filterInput.value.trim().toLowerCase();
            var items = sidebar.querySelectorAll('.ac-agent-item');
            items.forEach(function(el) {{
                if (!q) {{ el.style.removeProperty('display'); return; }}
                var hay = el.getAttribute('data-haystack') || '';
                el.style.display = hay.indexOf(q) >= 0 ? '' : 'none';
            }});
        }});
    }}

    // Activity-Polling: /api/activity alle 2s
    // Markiert Agenten mit Klasse .is-active + füllt Chip mit Label.
    var activityTimer = null;
    function pollActivity() {{
        fetch('/api/activity', {{ cache: 'no-store' }})
            .then(function(r) {{ return r.ok ? r.json() : {{}}; }})
            .then(function(data) {{
                var activeIds = Object.keys(data || {{}});
                var items = sidebar.querySelectorAll('.ac-agent-item');
                items.forEach(function(el) {{
                    var id = el.getAttribute('data-agent-id');
                    var info = data[id];
                    var chip = el.querySelector('.ac-activity-chip');
                    if (info) {{
                        el.classList.add('is-active');
                        if (chip) {{
                            var lbl = (info.label || info.type || 'aktiv').toString();
                            if (lbl.length > 22) lbl = lbl.slice(0, 22) + '…';
                            chip.textContent = '⚡ ' + lbl;
                        }}
                    }} else {{
                        el.classList.remove('is-active');
                        if (chip) chip.textContent = '⚡';
                    }}
                }});
                updateEmptyHint();
            }})
            .catch(function() {{}});
    }}
    pollActivity();
    activityTimer = setInterval(pollActivity, 2000);
    window.addEventListener('beforeunload', function() {{
        if (activityTimer) clearInterval(activityTimer);
    }});
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

    # Halluzinations-Warnung: Execution-Intent erkannt, aber kein Exec-Skill gefeuert
    # (siehe core.intent_detector + chat_service Guard). Reply beginnt mit ⚠ Banner.
    is_halted = (not is_user) and isinstance(content, str) and (
        "[NICHT AUSGEFÜHRT]" in content[:200] or skill == "intent_guard"
    )

    if is_halted:
        bbl = (
            "background:rgba(255,176,32,.08);border:1px solid #5a4210;"
            "color:#ffcf70;border-bottom-left-radius:3px;"
        )
    else:
        bbl = (
            "background:rgba(0,230,118,.08);border:1px solid #182e18;color:#e4f4e4;border-bottom-right-radius:3px;"
            if is_user else
            "background:#0d1a0e;border:1px solid #0f2010;color:#b8d4b8;border-bottom-left-radius:3px;"
        )
    label = "Du" if is_user else "Assistant"
    skill_text = f" · {html_mod.escape(skill)}" if skill else ""
    halt_badge = (
        '<span style="display:inline-block;margin-left:6px;padding:1px 6px;'
        'background:#4a3410;color:#ffb020;border:1px solid #6a4a18;'
        'border-radius:3px;font-size:9px;font-family:monospace;'
        'letter-spacing:.5px">⚠ NICHT AUSGEFÜHRT</span>'
        if is_halted else ""
    )

    h = (
        f'<div style="display:flex;flex-direction:column;gap:3px;max-width:820px;'
        f'align-self:{align};align-items:{align};width:100%">'
        f'<span style="font-size:10px;font-family:monospace;color:#3a5a3a;padding:0 4px">'
        f'{label}{skill_text}{halt_badge}</span>'
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


# Skill-ID → Emoji für die Sidebar-Karten
_SKILL_EMOJI = {
    "image_gen": "🎨", "image_edit": "🖌️",
    "video_gen": "🎬", "talking_video": "🎥",
    "youtube": "📺", "transcription": "🎤",
    "file_access": "📄", "linkedin": "💼",
    "prompt_optimize": "✨", "url_fetch": "🔗",
    "mac_mail": "📧", "gmail": "✉️",
    "coding": "💻", "chrome_browser": "🌐",
    "hacker_news": "📰", "tagesschau": "📡",
    "whatsapp": "💬", "screenshot": "📸",
}


def _render_sidebar_agent(agent: dict, current_agent_id: str):
    ag_id = agent["id"]
    name = agent.get("name", "?")
    color = agent.get("color", "#00e676")
    model = agent.get("model", "")
    role = agent.get("role", "")
    is_fav = agent.get("favorite", False)
    is_selected = ag_id == current_agent_id

    # Karten-Style: selected → grüner Rahmen + leichter Glow
    if is_selected:
        card_style = (
            "background: rgba(0,230,118,.05); "
            "border: 1px solid #00e676; "
            "box-shadow: 0 0 0 1px rgba(0,230,118,.15), 0 0 16px rgba(0,230,118,.08);"
        )
    else:
        card_style = (
            "background: #0a130c; "
            "border: 1px solid #132418; "
            "box-shadow: none;"
        )

    # Searchable-Haystack für den Filter (name + role + skills)
    import html as _h
    haystack = " ".join([
        (agent.get("name") or ""),
        (agent.get("role") or ""),
        " ".join(agent.get("skills", []) or []),
    ]).lower()
    selected_class = " is-selected" if is_selected else ""
    with ui.element("a").props(
        f'href="/chat/{ag_id}" data-agent-id="{ag_id}" data-agent-name="{_h.escape(name, quote=True)}" data-haystack="{_h.escape(haystack, quote=True)}"'
    ).style(
        f"display: flex; align-items: flex-start; gap: 10px; padding: 10px 12px; "
        f"border-radius: 10px; cursor: pointer; transition: all .15s; "
        f"margin-bottom: 6px; text-decoration: none; position: relative; "
        f"width: 100%; box-sizing: border-box; min-height: 68px; {card_style}"
    ).classes(f"ac-agent-item{selected_class}"):
        # Avatar — zentrale Komponente (agent['avatar'] steuert Bild/Initialien)
        from ui.components.avatar import render_avatar
        ui.html(render_avatar(agent, size=44))

        # Text-Stack
        with ui.column().style("flex: 1; min-width: 0; gap: 2px;"):
            name_color = "color: #00e676;" if is_selected else "color: #e4f4e4;"
            ui.label(name).style(
                f"font-size: 14px; font-weight: 600; {name_color} "
                f"overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
            )
            if role:
                ui.label(role).style(
                    "font-size: 11px; color: #6a8a6a; font-family: monospace; "
                    "overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                )
            if model:
                short = model.split("/")[-1][:16]
                ui.label(short).style("font-size: 10px; color: #3a5a3a; font-family: monospace;")
            # Skill-Emoji-Reihe (pulst wenn Agent aktiv)
            skills = agent.get("skills", []) or []
            emojis = "".join(_SKILL_EMOJI.get(s, "") for s in skills)
            if emojis:
                ui.html(
                    f'<div class="ac-skills-row" style="font-size:13px;line-height:1;'
                    f'margin-top:4px;letter-spacing:2px">{emojis}</div>'
                )
            # Activity-Chip (wird via JS gefüllt bei laufender Activity)
            ui.html(f'<span class="ac-activity-chip" data-agent-activity-for="{ag_id}">⚡</span>')

        # Favorit-Stern oben rechts
        if is_fav:
            ui.html(
                '<span class="material-icons" style="position:absolute;top:10px;right:10px;'
                'font-size:14px;color:#ffd700">star</span>'
            )


# ─── Chat Topbar ──────────────────────────────────────────────────────────────


def _build_topbar_html(agent: dict, agent_id: str, color: str) -> str:
    """Topbar als reiner HTML-String — wird sowohl beim initialen Render als auch
    vom /api/chat/context Endpoint für den JS-basierten Agentenwechsel genutzt."""
    name = agent.get("name", "?")
    role = agent.get("role", "")
    skills = agent.get("skills", [])
    model = agent.get("model", "")
    is_fav = agent.get("favorite", False)
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

    from ui.components.avatar import render_avatar
    avatar_html = render_avatar(agent, size=44)
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

        # Avatar (zentrale Komponente)
        f'{avatar_html}'

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


