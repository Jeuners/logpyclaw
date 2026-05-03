"""
ui/pages/settings.py — Provider-Konfiguration und App-Einstellungen.
"""
import logging
from nicegui import ui
from ui.layout import create_layout
from ui.theme import apply_theme

logger = logging.getLogger(__name__)


@ui.page("/settings")
def settings_page():
    apply_theme()
    create_layout("settings")

    with ui.column().classes("w-full max-w-4xl mx-auto p-6 gap-6"):
        ui.label("Einstellungen").classes("text-2xl font-bold")

        # Tabs für verschiedene Einstellungsbereiche
        with ui.tabs().classes("w-full") as tabs:
            tab_providers = ui.tab("Providers")
            tab_app = ui.tab("App")
            tab_debug = ui.tab("Debug")

        with ui.tab_panels(tabs, value=tab_providers).classes("w-full"):
            with ui.tab_panel(tab_providers):
                _render_providers()
            with ui.tab_panel(tab_app):
                _render_app_settings()
            with ui.tab_panel(tab_debug):
                _render_debug()


def _render_providers():
    from services import get_services
    services = get_services()
    providers = services.agents.get_providers()

    CHECKABLE = {"ollama", "mistral", "openrouter"}

    provider_configs = [
        ("ollama",     "Ollama",           "🖥",  [("url",          "Server URL",   "http://localhost:11434")]),
        ("openrouter", "OpenRouter",        "🔀",  [("api_key",      "API Key",      "sk-or-...")]),
        ("mistral",    "Mistral AI",        "⚡",  [("api_key",      "API Key",      "...")]),
        ("google_api", "Google API",        "🔍",  [("api_key",      "API Key",      "...")]),
        ("telegram",   "Telegram",          "✈",  [
            ("bot_token", "Bot Token", "123456:ABC..."),
            ("chat_id",   "Chat ID",   "-100..."),
        ]),
        ("gmail",      "Gmail",             "📧",  [
            ("email",        "E-Mail",        "user@gmail.com"),
            ("app_password", "App-Passwort",  "xxxx xxxx xxxx xxxx"),
        ]),
        ("comfyui",    "ComfyUI",           "🎨",  [
            ("url",   "Server URL", "http://localhost:8188"),
            ("model", "Modell",     "flux2pro"),
        ]),
    ]

    # Gesamte Provider-UI als HTML — kein NiceGUI on_click nötig
    S = "background:#0a1a0c;color:#b8d4b8;border:1px solid #182e18;border-radius:6px;padding:8px 12px;font-size:13px;width:100%;box-sizing:border-box"

    cards_html = '<div style="display:flex;flex-direction:column;gap:12px">'
    for provider_id, title, icon, fields in provider_configs:
        cfg = providers.get(provider_id, {})
        checkable = provider_id in CHECKABLE

        status_html = ""
        if checkable:
            status_html = (
                f'<span id="pstatus-{provider_id}" style="display:inline-flex;align-items:center;'
                f'gap:5px;font-size:11px;color:#3a5a3a;margin-left:auto">'
                f'<span style="width:7px;height:7px;border-radius:50%;background:#3a5a3a;display:inline-block"></span>'
                f'prüfe…</span>'
            )

        fields_html = ""
        for field_id, label, placeholder in fields:
            is_secret = any(k in field_id.lower() for k in ("key", "password", "token"))
            val = cfg.get(field_id, "")
            input_type = "password" if is_secret else "text"
            toggle = (
                f'<button type="button" onclick="togglePwd(this)" '
                f'style="position:absolute;right:10px;top:50%;transform:translateY(-50%);'
                f'background:none;border:none;color:#3a5a3a;cursor:pointer;font-size:13px">👁</button>'
            ) if is_secret else ""
            fields_html += f"""
              <div style="margin-bottom:10px">
                <label style="font-size:11px;font-weight:700;color:#b8d4b8;
                    text-transform:uppercase;letter-spacing:.5px;display:block;margin-bottom:5px">
                  {label}
                </label>
                <div style="position:relative">
                  <input id="pf-{provider_id}-{field_id}" type="{input_type}"
                    value="{val}" placeholder="{placeholder}"
                    style="{S}{';padding-right:36px' if is_secret else ''}">
                  {toggle}
                </div>
              </div>"""

        cards_html += f"""
        <details style="border:1px solid #182e18;border-radius:10px;overflow:hidden">
          <summary style="display:flex;align-items:center;gap:10px;padding:12px 16px;
              cursor:pointer;background:#0a1a0c;list-style:none;user-select:none;
              font-size:14px;font-weight:600;color:#b8d4b8">
            <span style="font-size:18px">{icon}</span>
            {title}
            {status_html}
          </summary>
          <div style="padding:16px;background:#070d08;border-top:1px solid #182e18">
            {fields_html}
            <div style="display:flex;align-items:center;gap:12px;margin-top:4px">
              <button onclick="saveProvider('{provider_id}')"
                  style="padding:7px 20px;border-radius:6px;background:#00e676;color:#000;
                      font-size:13px;font-weight:600;border:none;cursor:pointer">
                Speichern
              </button>
              <span id="pmsg-{provider_id}" style="font-size:12px;color:#3a5a3a"></span>
            </div>
          </div>
        </details>"""

    cards_html += "</div>"
    ui.html(cards_html)

    ui.add_head_html("""<script>
function togglePwd(btn) {
    var inp = btn.previousElementSibling;
    if (!inp) return;
    inp.type = inp.type === 'password' ? 'text' : 'password';
    btn.textContent = inp.type === 'password' ? '👁' : '🙈';
}

function saveProvider(pid) {
    // Alle Felder dieses Providers einsammeln
    var data = {};
    document.querySelectorAll('[id^="pf-' + pid + '-"]').forEach(function(inp) {
        var fieldId = inp.id.replace('pf-' + pid + '-', '');
        data[fieldId] = inp.value;
    });

    var msg = document.getElementById('pmsg-' + pid);
    if (msg) { msg.textContent = 'Speichere…'; msg.style.color = '#3a5a3a'; }

    fetch('/api/providers', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({[pid]: data})
    })
    .then(function(r) { return r.json(); })
    .then(function() {
        if (msg) { msg.textContent = '✓ Gespeichert'; msg.style.color = '#00e676'; }
        setTimeout(function() { if (msg) msg.textContent = ''; }, 3000);
        loadProviderStatus();
    })
    .catch(function(e) {
        if (msg) { msg.textContent = '✗ Fehler: ' + e.message; msg.style.color = '#ef4444'; }
    });
}

function loadProviderStatus() {
    fetch('/api/providers/status')
    .then(function(r) { return r.json(); })
    .then(function(data) {
        Object.keys(data).forEach(function(pid) {
            var el = document.getElementById('pstatus-' + pid);
            if (!el) return;
            var info = data[pid];
            var ok = info.ok;
            var noKey = !ok && info.info && info.info.indexOf('Kein') !== -1;
            var color = ok ? '#00e676' : (noKey ? '#ffa726' : '#ef4444');
            var label = ok ? 'verbunden' : (noKey ? 'kein Key' : 'nicht erreichbar');
            if (info.info) label += ' — ' + info.info;
            var dot = el.querySelector('span');
            if (dot) dot.style.background = color;
            el.style.color = color;
            el.childNodes[1] && (el.childNodes[1].textContent = ' ' + label);
            el.lastChild.textContent = label;
        });
    })
    .catch(function() {});
}

document.addEventListener('DOMContentLoaded', function() {
    setTimeout(loadProviderStatus, 500);
});
</script>""")


def _render_app_settings():
    from config.settings import settings
    from ui.theme import list_themes

    # Theme-Picker
    ui.label("Theme").style("font-size:14px;font-weight:700;color:#b8d4b8;margin-bottom:8px")
    themes = list_themes()

    theme_html_parts = []
    for t in themes:
        active_style = "border:2px solid " + t["primary"] + ";" if t["active"] else "border:2px solid #182e18;"
        active_label = " ✓" if t["active"] else ""
        theme_html_parts.append(
            f'<button onclick="setTheme(\'{t["id"]}\')" '
            f'style="display:flex;flex-direction:column;align-items:center;gap:6px;padding:12px 16px;'
            f'border-radius:10px;background:{t["bg"]};{active_style}cursor:pointer;'
            f'min-width:120px;transition:border-color .2s" id="theme-btn-{t["id"]}">'
            f'<div style="width:32px;height:32px;border-radius:50%;background:{t["primary"]}"></div>'
            f'<span style="font-size:12px;color:#b8d4b8;font-weight:500">{t["name"]}{active_label}</span>'
            f'</button>'
        )

    ui.html(
        '<div style="display:flex;flex-wrap:wrap;gap:12px;margin-bottom:24px">'
        + "".join(theme_html_parts) +
        '</div>'
        '<div id="theme-msg" style="font-size:12px;color:#3a5a3a;margin-bottom:16px"></div>'
    )

    ui.add_head_html("""<script>
function setTheme(name) {
    fetch('/api/themes/' + name, {method: 'PUT'})
        .then(function(r) { return r.json(); })
        .then(function() {
            document.getElementById('theme-msg').textContent = 'Theme gespeichert — Seite neu laden zum Anwenden.';
            document.getElementById('theme-msg').style.color = '#00e676';
        })
        .catch(function() {
            document.getElementById('theme-msg').textContent = 'Fehler beim Speichern.';
            document.getElementById('theme-msg').style.color = '#ef4444';
        });
}
</script>""")

    ui.separator().classes("my-4")
    ui.label("App-Konfiguration").style("font-size:14px;font-weight:700;color:#b8d4b8;margin-bottom:8px")
    with ui.column().classes("gap-2"):
        ui.label(f"Port: {settings.PORT}").style("color:#6b7280;font-size:13px")
        ui.label(f"Native Mode: {settings.NATIVE_MODE}").style("color:#6b7280;font-size:13px")
        ui.label(f"Debug: {settings.DEBUG}").style("color:#6b7280;font-size:13px")
        ui.label(f"Livelog: {settings.LIVELOG}").style("color:#6b7280;font-size:13px")
        ui.label("Konfiguration über .env Datei im Projektordner ändern.") \
            .style("color:#3a5a3a;font-size:12px;font-style:italic;margin-top:4px")


def _render_debug():
    from services import get_services
    services = get_services()

    async def run_health():
        import httpx
        from config.settings import settings
        results = {}
        for name, url in [("Ollama", settings.OLLAMA_URL + "/api/tags")]:
            try:
                async with httpx.AsyncClient(timeout=3) as c:
                    r = await c.get(url)
                    results[name] = "OK" if r.status_code < 400 else f"{r.status_code}"
            except Exception as e:
                results[name] = str(e)[:50]
        for n, s in results.items():
            ui.notify(f"{n}: {s}")

    ui.button("Health-Check", on_click=run_health).props("flat").classes("text-[#00e676]")

    tasks = services.tasks.list_all()
    agents = services.agents.list_all()
    ui.label(f"Agents: {len(agents)} | Tasks: {len(tasks)}").classes("text-gray-400 mt-4")
