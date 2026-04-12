"""
ui/pages/agent_edit.py — Agent bearbeiten / erstellen als eigene Seite.
Komplett HTML/JS-basiert wegen NiceGUI core.loop Bug v1.89:
- Checkboxen als HTML <input type="checkbox"> (NiceGUI ui.checkbox Klicks kommen nicht durch)
- Save via JS fetch() zum API-Endpoint (kein Timer/Flag nötig)
- NiceGUI nur für Textfelder (initiales Rendering funktioniert)
"""
import json
import logging
from nicegui import ui
from ui.layout import create_layout
from ui.theme import apply_theme

logger = logging.getLogger(__name__)

_COMMON_CSS = """
    .q-page { min-height: unset !important; }
    .q-page-container { padding-bottom: 0 !important; }
    .ae-skill-cb { display: none; }
    .ae-skill-label {
        display: inline-flex; align-items: center; gap: 5px;
        padding: 4px 10px; border-radius: 6px; cursor: pointer;
        font-size: 12px; color: #b8d4b8; border: 1px solid #182e18;
        background: #0a1a0c; user-select: none; transition: all .15s;
    }
    .ae-skill-label:hover { border-color: #00e676; }
    .ae-skill-cb:checked + .ae-skill-label {
        background: rgba(0,230,118,0.15); border-color: #00e676; color: #00e676;
    }
    .ae-opt-cb { margin-right: 6px; accent-color: #00e676; width: 16px; height: 16px; cursor: pointer; }
    .ae-opt-label { color: #b8d4b8; font-size: 13px; cursor: pointer; user-select: none; }
"""

_CONTAINER_STYLE = (
    "height: calc(100vh - 44px); overflow-y: auto; background: #050a06; "
    "padding: 24px; width: 100%; box-sizing: border-box;"
)

_FORM_STYLE = (
    "max-width: 700px; margin: 0 auto; background: #070d08; "
    "border: 1px solid #0f2010; border-radius: 12px; padding: 24px;"
)


def _build_skills_html(all_skills, enabled_skills: set) -> str:
    """Skills als HTML-Checkboxen (nicht NiceGUI)."""
    parts = []
    for skill in all_skills:
        checked = "checked" if skill.id in enabled_skills else ""
        parts.append(
            f'<input type="checkbox" id="sk-{skill.id}" class="ae-skill-cb" '
            f'value="{skill.id}" {checked}>'
            f'<label for="sk-{skill.id}" class="ae-skill-label">{skill.name}</label>'
        )
    return (
        '<div style="font-size:12px;font-weight:700;color:#b8d4b8;'
        'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">SKILLS</div>'
        '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px">'
        + "\n".join(parts)
        + "</div>"
    )


def _build_voice_html(current_voice: str) -> str:
    """Voice-Dropdown als reines HTML (core.loop Bug — kein ui.select!)."""
    mac_voices = [
        ("mac:Flo",     "Flo (DE)"),
        ("mac:Anna",    "Anna (DE)"),
        ("mac:Markus",  "Markus (DE)"),
        ("mac:Petra",   "Petra (DE)"),
        ("mac:Yannick", "Yannick (FR)"),
    ]

    def _opt(value: str, label: str) -> str:
        sel = ' selected' if value == current_voice else ''
        return f'<option value="{value}"{sel}>{label}</option>'

    no_voice_sel = ' selected' if not current_voice else ''
    options = f'<option value=""{no_voice_sel}>Keine Stimme</option>'
    options += '<optgroup label="macOS">'
    for val, lbl in mac_voices:
        options += _opt(val, lbl)
    options += '</optgroup>'

    return f'''
        <div style="margin-bottom:16px">
            <label style="font-size:12px;font-weight:700;color:#b8d4b8;
                text-transform:uppercase;letter-spacing:0.5px;
                display:block;margin-bottom:8px">STIMME (TTS)</label>
            <select id="ae-voice" style="width:100%;padding:8px 12px;
                background:#0a1a0c;color:#b8d4b8;
                border:1px solid #182e18;border-radius:6px;
                font-size:13px;cursor:pointer">
                {options}
            </select>
            <div id="ae-voice-loading" style="font-size:11px;color:#3a5a3a;margin-top:4px">
                Mistral-Stimmen werden geladen…
            </div>
        </div>
    '''


def _build_voice_script(current_voice: str) -> str:
    """JS für Mistral-Stimmen-Nachladen — via ui.add_head_html() einfügen."""
    import json as _json
    current_voice_js = _json.dumps(current_voice or "")
    return f"""<script>
document.addEventListener('DOMContentLoaded', function() {{
  setTimeout(async function() {{
    const sel = document.getElementById('ae-voice');
    const hint = document.getElementById('ae-voice-loading');
    const currentVoice = {current_voice_js};
    const LANG_MAP = {{
        'en_us':'EN-US','en_gb':'EN-GB','de_de':'DE',
        'fr_fr':'FR','es_es':'ES','it_it':'IT','pt_br':'PT'
    }};
    try {{
        const r = await fetch('/api/voices/mistral');
        const data = await r.json();
        const voices = data.voices || [];
        if (voices.length === 0) {{
            if (hint) hint.textContent = 'Kein Mistral API Key — nur macOS-Stimmen.';
            return;
        }}
        const byLang = {{}};
        for (const v of voices) {{
            const lang = LANG_MAP[v.lang] || v.lang.toUpperCase();
            if (!byLang[lang]) byLang[lang] = [];
            byLang[lang].push(v);
        }}
        for (const [lang, items] of Object.entries(byLang).sort()) {{
            const grp = document.createElement('optgroup');
            grp.label = 'Mistral \u00b7 ' + lang;
            for (const v of items) {{
                const opt = document.createElement('option');
                opt.value = v.slug;
                const gender = v.gender ? ' (' + v.gender + ')' : '';
                opt.textContent = v.name + gender;
                if (v.slug === currentVoice) opt.selected = true;
                grp.appendChild(opt);
            }}
            sel.appendChild(grp);
        }}
        if (hint) hint.textContent = voices.length + ' Mistral-Stimmen geladen.';
        setTimeout(() => {{ if (hint) hint.style.display = 'none'; }}, 2000);
    }} catch(e) {{
        if (hint) hint.textContent = 'Mistral-Stimmen konnten nicht geladen werden.';
    }}
  }}, 600);
}});
</script>"""


def _build_model_provider_html(current_model: str, current_provider: str) -> str:
    """Provider-Select + Modell-Suchfeld als reines HTML."""
    import json as _json
    providers = ["ollama", "openrouter", "mistral", "google"]
    prov_opts = ""
    for p in providers:
        sel = ' selected' if p == current_provider else ''
        prov_opts += f'<option value="{p}"{sel}>{p}</option>'

    return f'''
        <div style="display:flex;gap:12px;width:100%;margin-bottom:16px">
            <div style="flex:1">
                <label style="font-size:11px;font-weight:700;color:#b8d4b8;
                    text-transform:uppercase;letter-spacing:0.5px;
                    display:block;margin-bottom:6px">PROVIDER</label>
                <select id="ae-provider" style="width:100%;padding:8px 12px;
                    background:#0a1a0c;color:#b8d4b8;border:1px solid #182e18;
                    border-radius:6px;font-size:13px;cursor:pointer">
                    {prov_opts}
                </select>
            </div>
            <div style="flex:2;position:relative">
                <label style="font-size:11px;font-weight:700;color:#b8d4b8;
                    text-transform:uppercase;letter-spacing:0.5px;
                    display:block;margin-bottom:6px">MODELL
                    <span id="ae-model-loading" style="font-weight:400;font-size:10px;
                        color:#3a5a3a;text-transform:none;margin-left:8px">lädt…</span>
                </label>
                <input id="ae-model-input" type="text"
                    value="{current_model}"
                    placeholder="Modell suchen oder eingeben…"
                    autocomplete="off"
                    style="width:100%;padding:8px 12px;background:#0a1a0c;color:#b8d4b8;
                        border:1px solid #182e18;border-radius:6px;font-size:13px;
                        box-sizing:border-box">
                <div id="ae-model-dropdown" style="display:none;position:absolute;
                    top:100%;left:0;right:0;z-index:999;
                    background:#0d1a0e;border:1px solid #182e18;border-radius:6px;
                    max-height:260px;overflow-y:auto;margin-top:2px;box-shadow:0 8px 24px rgba(0,0,0,.5)">
                </div>
            </div>
        </div>
    '''


def _build_model_script(current_model: str, current_provider: str) -> str:
    """JS: lädt /api/models, filtert nach Provider, Suchfeld mit Dropdown."""
    import json as _json
    cm = _json.dumps(current_model or "")
    cp = _json.dumps(current_provider or "ollama")
    return f"""<script>
document.addEventListener('DOMContentLoaded', function() {{
  setTimeout(async function() {{
    const provSel  = document.getElementById('ae-provider');
    const modelInp = document.getElementById('ae-model-input');
    const dropdown = document.getElementById('ae-model-dropdown');
    const loading  = document.getElementById('ae-model-loading');
    if (!provSel || !modelInp || !dropdown) return;

    let _allModels = {{}};   // {{ollama: [...], openrouter: [{{id,name,free}},...]}}
    let _filtered  = [];
    let _open      = false;

    // Modelle laden
    try {{
      const r = await fetch('/api/models');
      _allModels = await r.json();
      if (loading) loading.textContent = '';
    }} catch(e) {{
      if (loading) loading.textContent = '(Fehler beim Laden)';
    }}

    function getModelsForProvider(prov) {{
      const raw = _allModels[prov] || [];
      // Ollama: Array von Strings; OpenRouter: Array von {{id,name,free}}
      return raw.map(m => typeof m === 'string'
        ? {{id: m, label: m, free: false}}
        : {{id: m.id, label: (m.free ? '★ ' : '') + (m.name || m.id), free: m.free}}
      );
    }}

    function renderDropdown(items) {{
      dropdown.innerHTML = '';
      if (!items.length) {{
        dropdown.innerHTML = '<div style="padding:10px 14px;color:#3a5a3a;font-size:13px">Keine Treffer</div>';
        return;
      }}
      items.forEach(function(m) {{
        const div = document.createElement('div');
        div.style.cssText = 'padding:8px 14px;cursor:pointer;font-size:13px;color:#b8d4b8;' +
          'border-bottom:1px solid #0f2010;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' +
          (m.free ? 'color:#00e676;' : '');
        div.textContent = m.label;
        div.title = m.id;
        div.addEventListener('mousedown', function(e) {{
          e.preventDefault();
          modelInp.value = m.id;
          closeDropdown();
        }});
        div.addEventListener('mouseover', function() {{ div.style.background='#182e18'; }});
        div.addEventListener('mouseout',  function() {{ div.style.background=''; }});
        dropdown.appendChild(div);
      }});
    }}

    function openDropdown() {{
      _open = true;
      dropdown.style.display = 'block';
    }}
    function closeDropdown() {{
      _open = false;
      dropdown.style.display = 'none';
    }}

    function updateList() {{
      const prov = provSel.value;
      const q = modelInp.value.toLowerCase().trim();
      const all = getModelsForProvider(prov);
      _filtered = q
        ? all.filter(m => m.label.toLowerCase().includes(q) || m.id.toLowerCase().includes(q))
        : all;
      renderDropdown(_filtered);
    }}

    // Provider wechseln → Modell-Feld leeren + Liste aktualisieren
    provSel.addEventListener('change', function() {{
      modelInp.value = '';
      updateList();
      openDropdown();
    }});

    // Tippen → filtern
    modelInp.addEventListener('input', function() {{
      updateList();
      openDropdown();
    }});

    // Fokus → Liste anzeigen
    modelInp.addEventListener('focus', function() {{
      updateList();
      openDropdown();
    }});

    // Blur → schließen (kurz verzögert wg. mousedown)
    modelInp.addEventListener('blur', function() {{
      setTimeout(closeDropdown, 150);
    }});

    // Tastatur-Navigation
    modelInp.addEventListener('keydown', function(e) {{
      if (e.key === 'Escape') {{ closeDropdown(); modelInp.blur(); }}
      if (e.key === 'ArrowDown') {{
        const first = dropdown.querySelector('div');
        if (first) first.focus();
        e.preventDefault();
      }}
    }});

    // Initial laden
    updateList();

  }}, 400);
}});
</script>"""


def _build_heartbeat_html(heartbeat: dict, agent_id: str = "") -> str:
    """Heartbeat-Sektion als reines HTML."""
    active      = heartbeat.get("active", False)
    prompt      = heartbeat.get("prompt", "") or ""
    interval    = int(heartbeat.get("interval_min", 60))
    last_run    = heartbeat.get("last_run", "")
    last_result = heartbeat.get("last_result", "")

    active_chk   = "checked" if active else ""
    last_run_str = last_run[:16].replace("T", " ") if last_run else "noch nie"
    prompt_esc   = prompt.replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    result_esc   = last_result.replace("<", "&lt;").replace(">", "&gt;")

    test_btn = (
        f'<button id="ae-hb-test-btn"'
        f' style="padding:5px 14px;border-radius:6px;background:#0a1a0c;color:#00e676;'
        f'font-size:12px;font-weight:600;border:1px solid #1a3a1a;cursor:pointer">'
        f'▶ Jetzt testen</button>'
        if agent_id else ""
    )

    last_result_html = (
        f'<div style="margin-top:8px;padding:8px 10px;background:#050a06;border-radius:6px;'
        f'border-left:2px solid #1a3a1a;font-size:11px;color:#3a5a3a;line-height:1.4">'
        f'{result_esc[:200]}</div>'
        if last_result else ""
    )

    return f'''
        <div style="margin-bottom:16px;padding:14px;background:#070f08;
            border-radius:8px;border:1px solid #0f2010">
            <div style="font-size:12px;font-weight:700;color:#b8d4b8;
                text-transform:uppercase;letter-spacing:0.5px;margin-bottom:12px">
                💓 HEARTBEAT
            </div>
            <div style="display:flex;align-items:center;gap:20px;margin-bottom:12px;flex-wrap:wrap">
                <label class="ae-opt-label">
                    <input type="checkbox" id="ae-hb-active" class="ae-opt-cb" {active_chk}> Aktiv
                </label>
                <div style="display:flex;align-items:center;gap:8px">
                    <label style="font-size:12px;color:#b8d4b8">Intervall:</label>
                    <input type="number" id="ae-hb-interval" value="{interval}" min="1" max="10080"
                        style="width:72px;padding:5px 8px;background:#0a1a0c;color:#b8d4b8;
                            border:1px solid #182e18;border-radius:6px;font-size:13px;text-align:center">
                    <span style="font-size:12px;color:#3a5a3a">Min</span>
                </div>
            </div>
            <textarea id="ae-hb-prompt" rows="3"
                placeholder="Was soll der Agent regelmäßig tun? (z.B. '@Flo fasse die News zusammen')"
                style="width:100%;padding:8px 12px;background:#0a1a0c;color:#b8d4b8;
                    border:1px solid #182e18;border-radius:6px;font-size:13px;resize:vertical;
                    box-sizing:border-box;margin-bottom:8px;font-family:inherit"
            >{prompt_esc}</textarea>
            <div style="display:flex;align-items:center;justify-content:space-between">
                <span style="font-size:11px;color:#3a5a3a">Letzter Run: {last_run_str}</span>
                {test_btn}
            </div>
            {last_result_html}
        </div>
    '''


def _build_heartbeat_script(agent_id: str) -> str:
    """JS für 'Jetzt testen'-Button."""
    if not agent_id:
        return ""
    return f"""<script>
document.addEventListener('DOMContentLoaded', function() {{
    const btn = document.getElementById('ae-hb-test-btn');
    if (!btn) return;
    btn.addEventListener('click', async function() {{
        btn.disabled = true;
        btn.textContent = '…';
        try {{
            const r = await fetch('/api/agents/{agent_id}/heartbeat/run', {{method: 'POST'}});
            btn.textContent = r.ok ? '✓ Gestartet' : '✗ Fehler';
        }} catch(e) {{
            btn.textContent = '✗ Fehler';
        }}
        setTimeout(() => {{ btn.disabled = false; btn.textContent = '▶ Jetzt testen'; }}, 2500);
    }});
}});
</script>"""


def _build_options_html(favorite: bool, web_search: bool) -> str:
    """Favorit + Web-Suche als HTML-Checkboxen."""
    fav_chk = "checked" if favorite else ""
    web_chk = "checked" if web_search else ""
    return f'''
        <div style="display:flex;gap:16px;margin-bottom:20px">
            <label class="ae-opt-label">
                <input type="checkbox" id="ae-fav" class="ae-opt-cb" {fav_chk}> Favorit
            </label>
            <label class="ae-opt-label">
                <input type="checkbox" id="ae-web" class="ae-opt-cb" {web_chk}> Web-Suche
            </label>
        </div>
    '''


def _build_save_js(agent_id: str, is_new: bool) -> str:
    """JS für Save-Button: liest alle Werte aus dem DOM und sendet per fetch()."""
    if is_new:
        endpoint = "'/api/agents'"
        method = "'POST'"
        redirect = """
            const newId = result.id || result.agent_id || '';
            window.location.href = newId ? '/chat/' + newId : '/';
        """
    else:
        endpoint = f"'/api/agents/{agent_id}'"
        method = "'PUT'"
        redirect = f"window.location.href = '/chat/{agent_id}';"

    return r"""
    <script>
    function aeCollectAndSave() {
        // NiceGUI Quasar inputs: Werte aus dem DOM lesen
        const getInput = (label) => {
            const labels = document.querySelectorAll('.q-field__label');
            for (const l of labels) {
                if (l.textContent.trim() === label) {
                    const field = l.closest('.q-field');
                    if (!field) continue;
                    const inp = field.querySelector('input, textarea, select');
                    if (inp) return inp.value || '';
                    // Quasar select: .q-field__native span
                    const sel = field.querySelector('.q-field__native');
                    if (sel) return sel.textContent.trim();
                }
            }
            return '';
        };

        // Skills aus HTML-Checkboxen
        const skills = [];
        document.querySelectorAll('.ae-skill-cb:checked').forEach(cb => skills.push(cb.value));

        const data = {
            name: getInput('Name'),
            role: getInput('Rolle'),
            model: document.getElementById('ae-model-input')?.value?.trim() || getInput('Modell'),
            provider: document.getElementById('ae-provider')?.value || getInput('Provider'),
            soul: getInput('System-Prompt (Soul)'),
            color: getInput('Farbe'),
            max_tokens: parseInt(getInput('Max Tokens')) || 2048,
            skills: skills,
            favorite: document.getElementById('ae-fav')?.checked || false,
            web_search: document.getElementById('ae-web')?.checked || false,
            voice: document.getElementById('ae-voice')?.value || '',
        };

        // Heartbeat separat (dedizierter Endpoint merged last_run/next_run korrekt)
        const heartbeatData = {
            active: document.getElementById('ae-hb-active')?.checked || false,
            prompt: document.getElementById('ae-hb-prompt')?.value?.trim() || '',
            interval_min: parseInt(document.getElementById('ae-hb-interval')?.value) || 60,
        };

        if (!data.name || !data.name.trim()) {
            alert('Name darf nicht leer sein');
            return;
        }

        const btn = document.getElementById('ae-save-btn');
        if (btn) { btn.disabled = true; btn.textContent = 'Speichere...'; }

        fetch(""" + endpoint + """, {
            method: """ + method + """,
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        })
        .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(async result => {
            // Heartbeat separat speichern (Endpoint merged last_run/next_run korrekt)
            const agentId = result.id || result.agent_id || '';
            if (agentId) {
                try {
                    await fetch('/api/agents/' + agentId + '/heartbeat', {
                        method: 'PUT',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(heartbeatData)
                    });
                } catch(e) { /* nicht kritisch */ }
            }
            """ + redirect + """
        })
        .catch(err => {
            alert('Fehler beim Speichern: ' + err.message);
            if (btn) { btn.disabled = false; btn.textContent = 'Speichern'; }
        });
    }
    // addEventListener statt onclick (Vue sanitisiert onclick in ui.html!)
    document.addEventListener('DOMContentLoaded', function() {
        const btn = document.getElementById('ae-save-btn');
        if (btn) btn.addEventListener('click', aeCollectAndSave);
    });
    </script>
    """


@ui.page("/agent/edit/{agent_id}")
def agent_edit_page(agent_id: str):
    apply_theme()
    create_layout("settings")

    from services import get_services
    services = get_services()
    agent = services.agents.get(agent_id)
    all_skills = services.registry.all()

    if not agent:
        with ui.column().classes("items-center justify-center").style("height: calc(100vh - 44px)"):
            ui.label("Agent nicht gefunden").style("color: #ef4444; font-size: 18px;")
        return

    ui.add_css(_COMMON_CSS)
    ui.add_head_html(_build_save_js(agent_id, is_new=False))

    enabled_skills = set(agent.get("skills", []))

    with ui.element("div").style(_CONTAINER_STYLE):
        ui.html(f'''
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:24px">
                <a href="/chat/{agent_id}" style="color:#3a5a3a;font-size:14px;text-decoration:none">← Zurück</a>
                <span style="font-size:20px;font-weight:700;color:#e4f4e4">
                    Agent bearbeiten: {agent.get("name", "?")}
                </span>
            </div>
        ''')

        with ui.element("div").style(_FORM_STYLE):
            # NiceGUI Inputs (initiales Rendering funktioniert)
            with ui.row().style("gap: 12px; width: 100%; margin-bottom: 16px;"):
                ui.input("Name", value=agent.get("name", "")) \
                    .style("flex: 1;").props("outlined dense dark")
                ui.input("Rolle", value=agent.get("role", ""),
                         placeholder="z.B. Developer, Redakteur") \
                    .style("flex: 1;").props("outlined dense dark")

            ui.html(_build_model_provider_html(agent.get("model", ""), agent.get("provider", "ollama")))
            ui.add_head_html(_build_model_script(agent.get("model", ""), agent.get("provider", "ollama")))

            ui.textarea("System-Prompt (Soul)", value=agent.get("soul", "")) \
                .style("width: 100%; margin-bottom: 16px;") \
                .props("rows=8 outlined dense dark")

            with ui.row().style("gap: 12px; width: 100%; margin-bottom: 16px;"):
                ui.color_input("Farbe", value=agent.get("color", "#00e676")) \
                    .style("flex: 1;")
                ui.number("Max Tokens", value=agent.get("max_tokens", 2048),
                          min=256, max=32768, step=256) \
                    .style("flex: 1;").props("outlined dense dark")

            # Skills + Optionen als reines HTML (core.loop Bug!)
            ui.html(_build_skills_html(all_skills, enabled_skills))
            ui.html(_build_options_html(
                agent.get("favorite", False),
                agent.get("web_search", False),
            ))
            ui.html(_build_voice_html(agent.get("voice", "")))
            ui.add_head_html(_build_voice_script(agent.get("voice", "")))

            # Heartbeat
            ui.html(_build_heartbeat_html(agent.get("heartbeat", {}), agent_id))
            ui.add_head_html(_build_heartbeat_script(agent_id))

            # Separator
            ui.element("div").style("height: 1px; background: #0f2010; margin: 8px 0 16px;")

            # Buttons — Save als HTML onclick (kein on_click/Timer nötig)
            ui.html(f'''
                <div style="display:flex;gap:12px;justify-content:flex-end">
                    <a href="/chat/{agent_id}" style="padding:8px 20px;border-radius:8px;
                        color:#3a5a3a;text-decoration:none;font-size:14px;font-weight:500;
                        border:1px solid #182e18;display:inline-flex;align-items:center">
                        Abbrechen
                    </a>
                    <button id="ae-save-btn"                         style="padding:8px 20px;border-radius:8px;background:#00e676;color:#000;
                        font-size:14px;font-weight:600;border:none;cursor:pointer;
                        display:inline-flex;align-items:center;gap:6px">
                        <span class="material-icons" style="font-size:16px">save</span>
                        Speichern
                    </button>
                </div>
            ''')


@ui.page("/agent/new")
def agent_new_page(clone: str = ""):
    """Neuen Agent erstellen — optional mit ?clone=<agent_id> zum Duplizieren."""
    apply_theme()
    create_layout("settings")

    from services import get_services
    services = get_services()
    all_skills = services.registry.all()

    clone_id = clone or None
    clone_source: dict | None = None
    if clone_id:
        clone_source = services.agents.get(clone_id)

    def _val(key, default=""):
        return clone_source.get(key, default) if clone_source else default

    page_title = f"Agent duplizieren: {_val('name')}" if clone_source else "Neuer Agent"
    default_name = f"{_val('name')} (Kopie)" if clone_source else ""
    cloned_skills: set = set(clone_source.get("skills", [])) if clone_source else set()

    ui.add_css(_COMMON_CSS)
    ui.add_head_html(_build_save_js("", is_new=True))

    with ui.element("div").style(_CONTAINER_STYLE):
        ui.html(f'''
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:24px">
                <a href="/" style="color:#3a5a3a;font-size:14px;text-decoration:none">← Zurück</a>
                <span style="font-size:20px;font-weight:700;color:#e4f4e4">{page_title}</span>
            </div>
        ''')

        with ui.element("div").style(_FORM_STYLE):
            with ui.row().style("gap: 12px; width: 100%; margin-bottom: 16px;"):
                ui.input("Name", value=default_name, placeholder="Agent-Name") \
                    .style("flex: 1;").props("outlined dense dark")
                ui.input("Rolle", value=_val("role"), placeholder="z.B. Developer") \
                    .style("flex: 1;").props("outlined dense dark")

            ui.html(_build_model_provider_html(_val("model", "llama3"), _val("provider", "ollama")))
            ui.add_head_html(_build_model_script(_val("model", "llama3"), _val("provider", "ollama")))

            ui.textarea("System-Prompt (Soul)",
                        value=_val("soul"),
                        placeholder="Beschreibe die Persönlichkeit...") \
                .style("width: 100%; margin-bottom: 16px;") \
                .props("rows=6 outlined dense dark")

            with ui.row().style("gap: 12px; width: 100%; margin-bottom: 16px;"):
                ui.color_input("Farbe", value=_val("color", "#00e676")).style("flex: 1;")
                ui.number("Max Tokens", value=_val("max_tokens", 2048),
                          min=256, max=32768, step=256) \
                    .style("flex: 1;").props("outlined dense dark")

            # Skills + Optionen als reines HTML
            ui.html(_build_skills_html(all_skills, cloned_skills))
            ui.html(_build_options_html(_val("favorite", False), False))
            ui.html(_build_voice_html(_val("voice", "")))
            ui.add_head_html(_build_voice_script(_val("voice", "")))

            # Heartbeat (kein Test-Button bei neuem Agent, da noch keine ID)
            clone_hb = clone_source.get("heartbeat", {}) if clone_source else {}
            ui.html(_build_heartbeat_html(clone_hb, ""))

            ui.element("div").style("height: 1px; background: #0f2010; margin: 16px 0;")

            ui.html('''
                <button id="ae-save-btn"                     style="padding:10px 24px;border-radius:8px;background:#00e676;color:#000;
                    font-size:14px;font-weight:600;border:none;cursor:pointer;
                    display:inline-flex;align-items:center;gap:6px">
                    <span class="material-icons" style="font-size:16px">add</span>
                    Agent erstellen
                </button>
            ''')
