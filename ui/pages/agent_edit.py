"""
ui/pages/agent_edit.py — Agent bearbeiten / erstellen.

Komplett HTML/CSS-basiert (kein Quasar-Mix) wegen NiceGUI core.loop Bug v1.89
und weil der Quasar-Dark-Look nicht zum Rest der App passt.
Save: JS fetch() liest Werte per ID aus dem DOM.
"""
import json
import logging
from nicegui import ui
from ui.layout import create_layout
from ui.theme import apply_theme

logger = logging.getLogger(__name__)

# ── Shared Form-CSS ──────────────────────────────────────────────────────────
_FORM_CSS = """
    .q-page { min-height: unset !important; }
    .q-page-container { padding-bottom: 0 !important; }

    .ae-label {
        display: block;
        font-size: 11px; font-weight: 700;
        color: #6a8a6a;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        margin-bottom: 6px;
    }

    .ae-input,
    .ae-textarea,
    .ae-select {
        width: 100%;
        padding: 10px 12px;
        background: #0a1410;
        color: #e4f4e4;
        border: 1px solid #1a3020;
        border-radius: 8px;
        font-size: 14px;
        font-family: inherit;
        box-sizing: border-box;
        transition: border-color .12s, background .12s;
        outline: none;
    }
    .ae-input:focus,
    .ae-textarea:focus,
    .ae-select:focus {
        border-color: #00e676;
        background: #0c1a12;
    }
    .ae-textarea { resize: vertical; line-height: 1.5; min-height: 120px; }
    .ae-select { cursor: pointer; appearance: none;
        background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='%236a8a6a'><path d='M7 10l5 5 5-5z'/></svg>");
        background-repeat: no-repeat; background-position: right 10px center;
        padding-right: 32px;
    }

    .ae-row { display: grid; gap: 14px; margin-bottom: 18px; }
    .ae-row-2 { grid-template-columns: 1fr 1fr; }
    .ae-row-3 { grid-template-columns: 1fr 2fr; }

    .ae-field { margin-bottom: 18px; }
    .ae-section {
        margin-top: 24px; padding-top: 20px;
        border-top: 1px solid #0f2010;
    }

    /* Skill-Pills */
    .ae-skill-cb { display: none; }
    .ae-skill-label {
        display: inline-flex; align-items: center;
        padding: 6px 12px; border-radius: 20px;
        cursor: pointer; user-select: none;
        font-size: 12px; color: #8aac8a;
        border: 1px solid #1a3020; background: #0a1410;
        transition: all .12s;
    }
    .ae-skill-label:hover { border-color: #2a5030; color: #b8d4b8; }
    .ae-skill-cb:checked + .ae-skill-label {
        background: rgba(0,230,118,0.12);
        border-color: #00e676; color: #00e676;
    }

    /* Optionen (Favorit/Web-Suche) */
    .ae-opt { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; user-select: none; }
    .ae-opt-cb {
        appearance: none; -webkit-appearance: none;
        width: 18px; height: 18px; border-radius: 4px;
        border: 1px solid #2a5030; background: #0a1410;
        cursor: pointer; position: relative; flex-shrink: 0;
        transition: all .12s;
    }
    .ae-opt-cb:checked { background: #00e676; border-color: #00e676; }
    .ae-opt-cb:checked::after {
        content: ''; position: absolute; left: 5px; top: 1px;
        width: 5px; height: 10px; border: solid #000;
        border-width: 0 2px 2px 0; transform: rotate(45deg);
    }
    .ae-opt-label { font-size: 13px; color: #b8d4b8; }

    /* Color-Picker */
    .ae-color-wrap { position: relative; display: flex; align-items: center; gap: 10px; }
    .ae-color-swatch {
        width: 40px; height: 40px; border-radius: 8px;
        border: 1px solid #1a3020; flex-shrink: 0; cursor: pointer;
    }
    .ae-color-input { flex: 1; }

    /* Buttons */
    .ae-btn-primary {
        padding: 10px 22px; border-radius: 8px;
        background: #00e676; color: #000;
        font-size: 14px; font-weight: 600;
        border: none; cursor: pointer;
        display: inline-flex; align-items: center; gap: 8px;
        transition: filter .12s;
    }
    .ae-btn-primary:hover:not(:disabled) { filter: brightness(1.1); }
    .ae-btn-primary:disabled { opacity: .6; cursor: not-allowed; }

    .ae-btn-ghost {
        padding: 10px 22px; border-radius: 8px;
        color: #8aac8a; text-decoration: none;
        font-size: 14px; font-weight: 500;
        border: 1px solid #1a3020; background: transparent;
        display: inline-flex; align-items: center;
        transition: all .12s; cursor: pointer;
    }
    .ae-btn-ghost:hover { border-color: #2a5030; color: #b8d4b8; }

    .ae-hint { font-size: 11px; color: #3a5a3a; margin-top: 4px; }

    /* Heartbeat-Section */
    .ae-hb {
        padding: 16px; border-radius: 10px;
        background: #060c07; border: 1px solid #132418;
    }
    .ae-hb-title {
        font-size: 11px; font-weight: 700; color: #6a8a6a;
        text-transform: uppercase; letter-spacing: 0.8px;
        margin-bottom: 14px; display: flex; align-items: center; gap: 8px;
    }
    .ae-hb-row {
        display: flex; align-items: center; gap: 18px;
        margin-bottom: 12px; flex-wrap: wrap;
    }
    .ae-hb-last {
        margin-top: 10px; padding: 10px 12px;
        background: #050a06; border-left: 2px solid #1a3a1a;
        border-radius: 6px; font-size: 11px; color: #6a8a6a;
        line-height: 1.5;
    }
"""

_CONTAINER_STYLE = (
    "height: calc(100vh - 44px); overflow-y: auto; background: #050a06; "
    "padding: 32px 24px; width: 100%; box-sizing: border-box;"
)

_FORM_STYLE = (
    "max-width: 720px; margin: 0 auto; background: #080f09; "
    "border: 1px solid #0f2010; border-radius: 14px; padding: 28px 32px;"
)


# ── Builder-Funktionen ───────────────────────────────────────────────────────

def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _build_header_html(back_href: str, title: str) -> str:
    return f'''
        <div style="max-width:720px;margin:0 auto 20px;display:flex;align-items:center;gap:14px">
            <a href="{back_href}" class="ae-btn-ghost" style="padding:6px 14px;font-size:13px">← Zurück</a>
            <h1 style="font-size:20px;font-weight:700;color:#e4f4e4;margin:0">{_esc(title)}</h1>
        </div>
    '''


def _build_name_role_html(name: str, role: str) -> str:
    return f'''
        <div class="ae-row ae-row-2">
            <div>
                <label class="ae-label">Name</label>
                <input id="ae-name" type="text" class="ae-input" value="{_esc(name)}" placeholder="Agent-Name" autocomplete="off">
            </div>
            <div>
                <label class="ae-label">Rolle</label>
                <input id="ae-role" type="text" class="ae-input" value="{_esc(role)}" placeholder="z.B. Developer, Redakteur" autocomplete="off">
            </div>
        </div>
    '''


def _build_soul_html(soul: str) -> str:
    return f'''
        <div class="ae-field">
            <label class="ae-label">System-Prompt (Soul)</label>
            <textarea id="ae-soul" class="ae-textarea" rows="10" placeholder="Persönlichkeit & Verhalten des Agents…">{_esc(soul)}</textarea>
            <div class="ae-hint">Nur Persona/Rolle — Sprache und Operator-Identität sind global.</div>
        </div>
    '''


def _build_color_tokens_html(color: str, max_tokens: int) -> str:
    return f'''
        <div class="ae-row ae-row-2">
            <div>
                <label class="ae-label">Farbe</label>
                <div class="ae-color-wrap">
                    <input id="ae-color-picker" type="color" value="{_esc(color)}" class="ae-color-swatch" style="padding:0;border:1px solid #1a3020;background:none">
                    <input id="ae-color" type="text" class="ae-input ae-color-input" value="{_esc(color)}" placeholder="#00e676">
                </div>
            </div>
            <div>
                <label class="ae-label">Max Tokens</label>
                <input id="ae-max-tokens" type="number" class="ae-input" value="{int(max_tokens)}" min="256" max="32768" step="256">
            </div>
        </div>
    '''


_COLOR_SYNC_SCRIPT = """<script>
document.addEventListener('DOMContentLoaded', function() {
    const p = document.getElementById('ae-color-picker');
    const t = document.getElementById('ae-color');
    if (p && t) {
        p.addEventListener('input', () => { t.value = p.value; });
        t.addEventListener('input', () => {
            if (/^#[0-9a-fA-F]{6}$/.test(t.value)) p.value = t.value;
        });
    }
});
</script>"""


def _build_skills_html(all_skills, enabled_skills: set) -> str:
    parts = []
    for skill in all_skills:
        checked = "checked" if skill.id in enabled_skills else ""
        parts.append(
            f'<input type="checkbox" id="sk-{skill.id}" class="ae-skill-cb" value="{skill.id}" {checked}>'
            f'<label for="sk-{skill.id}" class="ae-skill-label">{_esc(skill.name)}</label>'
        )
    return (
        '<div class="ae-section">'
        '<label class="ae-label">Skills</label>'
        '<div style="display:flex;flex-wrap:wrap;gap:6px">'
        + "".join(parts)
        + '</div></div>'
    )


def _build_options_html(favorite: bool, web_search: bool) -> str:
    fav_chk = "checked" if favorite else ""
    web_chk = "checked" if web_search else ""
    return f'''
        <div class="ae-field" style="display:flex;gap:24px;margin-top:18px">
            <label class="ae-opt">
                <input type="checkbox" id="ae-fav" class="ae-opt-cb" {fav_chk}>
                <span class="ae-opt-label">Favorit</span>
            </label>
            <label class="ae-opt">
                <input type="checkbox" id="ae-web" class="ae-opt-cb" {web_chk}>
                <span class="ae-opt-label">Web-Suche</span>
            </label>
        </div>
    '''


def _build_model_provider_html(current_model: str, current_provider: str) -> str:
    providers = ["ollama", "openrouter", "mistral", "google"]
    prov_opts = "".join(
        f'<option value="{p}"{" selected" if p == current_provider else ""}>{p}</option>'
        for p in providers
    )
    return f'''
        <div class="ae-row ae-row-3">
            <div>
                <label class="ae-label">Provider</label>
                <select id="ae-provider" class="ae-select">{prov_opts}</select>
            </div>
            <div style="position:relative">
                <label class="ae-label">
                    Modell
                    <span id="ae-model-loading" style="font-weight:400;font-size:10px;color:#3a5a3a;text-transform:none;letter-spacing:0;margin-left:8px">lädt…</span>
                </label>
                <input id="ae-model-input" type="text" class="ae-input" value="{_esc(current_model)}" placeholder="Modell suchen oder eingeben…" autocomplete="off">
                <div id="ae-model-dropdown" style="display:none;position:absolute;top:100%;left:0;right:0;z-index:999;background:#0c1510;border:1px solid #1a3020;border-radius:8px;max-height:260px;overflow-y:auto;margin-top:4px;box-shadow:0 8px 24px rgba(0,0,0,.6)"></div>
            </div>
        </div>
    '''


def _build_model_script(current_model: str, current_provider: str) -> str:
    return """<script>
document.addEventListener('DOMContentLoaded', function() {
  setTimeout(async function() {
    const provSel  = document.getElementById('ae-provider');
    const modelInp = document.getElementById('ae-model-input');
    const dropdown = document.getElementById('ae-model-dropdown');
    const loading  = document.getElementById('ae-model-loading');
    if (!provSel || !modelInp || !dropdown) return;

    let _allModels = {};
    let _filtered  = [];

    try {
      const r = await fetch('/api/models');
      _allModels = await r.json();
      if (loading) loading.textContent = '';
    } catch(e) {
      if (loading) loading.textContent = '(Fehler beim Laden)';
    }

    function getModelsForProvider(prov) {
      const raw = _allModels[prov] || [];
      return raw.map(m => typeof m === 'string'
        ? {id: m, label: m, free: false}
        : {id: m.id, label: (m.free ? '★ ' : '') + (m.name || m.id), free: m.free}
      );
    }
    function renderDropdown(items) {
      dropdown.innerHTML = '';
      if (!items.length) {
        dropdown.innerHTML = '<div style="padding:10px 14px;color:#3a5a3a;font-size:13px">Keine Treffer</div>';
        return;
      }
      items.forEach(m => {
        const div = document.createElement('div');
        div.style.cssText = 'padding:9px 14px;cursor:pointer;font-size:13px;color:#b8d4b8;border-bottom:1px solid #0f2010;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;' + (m.free ? 'color:#00e676;' : '');
        div.textContent = m.label;
        div.title = m.id;
        div.addEventListener('mousedown', e => { e.preventDefault(); modelInp.value = m.id; closeDropdown(); });
        div.addEventListener('mouseover', () => { div.style.background = '#132418'; });
        div.addEventListener('mouseout',  () => { div.style.background = ''; });
        dropdown.appendChild(div);
      });
    }
    function openDropdown()  { dropdown.style.display = 'block'; }
    function closeDropdown() { dropdown.style.display = 'none'; }
    function updateList() {
      const prov = provSel.value;
      const q = modelInp.value.toLowerCase().trim();
      const all = getModelsForProvider(prov);
      _filtered = q ? all.filter(m => m.label.toLowerCase().includes(q) || m.id.toLowerCase().includes(q)) : all;
      renderDropdown(_filtered);
    }
    provSel.addEventListener('change', () => { modelInp.value = ''; updateList(); openDropdown(); });
    modelInp.addEventListener('input', () => { updateList(); openDropdown(); });
    modelInp.addEventListener('focus', () => { updateList(); openDropdown(); });
    modelInp.addEventListener('blur',  () => { setTimeout(closeDropdown, 150); });
    modelInp.addEventListener('keydown', e => {
      if (e.key === 'Escape') { closeDropdown(); modelInp.blur(); }
    });
    updateList();
  }, 400);
});
</script>"""


def _build_voice_html(current_voice: str) -> str:
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
        <div class="ae-field" style="margin-top:18px">
            <label class="ae-label">Stimme (TTS)</label>
            <select id="ae-voice" class="ae-select">{options}</select>
            <div id="ae-voice-loading" class="ae-hint">Mistral-Stimmen werden geladen…</div>
        </div>
    '''


def _build_voice_script(current_voice: str) -> str:
    current_voice_js = json.dumps(current_voice or "")
    return f"""<script>
document.addEventListener('DOMContentLoaded', function() {{
  setTimeout(async function() {{
    const sel = document.getElementById('ae-voice');
    const hint = document.getElementById('ae-voice-loading');
    const currentVoice = {current_voice_js};
    const LANG_MAP = {{'en_us':'EN-US','en_gb':'EN-GB','de_de':'DE','fr_fr':'FR','es_es':'ES','it_it':'IT','pt_br':'PT'}};
    try {{
      const r = await fetch('/api/voices/mistral');
      const data = await r.json();
      const voices = data.voices || [];
      if (voices.length === 0) {{ if (hint) hint.textContent = 'Kein Mistral API Key — nur macOS-Stimmen.'; return; }}
      const byLang = {{}};
      for (const v of voices) {{
        const lang = LANG_MAP[v.lang] || v.lang.toUpperCase();
        if (!byLang[lang]) byLang[lang] = [];
        byLang[lang].push(v);
      }}
      for (const [lang, items] of Object.entries(byLang).sort()) {{
        const grp = document.createElement('optgroup');
        grp.label = 'Mistral · ' + lang;
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
    }} catch(e) {{ if (hint) hint.textContent = 'Mistral-Stimmen konnten nicht geladen werden.'; }}
  }}, 600);
}});
</script>"""


def _build_heartbeat_html(heartbeat: dict, agent_id: str = "") -> str:
    active      = heartbeat.get("active", False)
    prompt      = heartbeat.get("prompt", "") or ""
    interval    = int(heartbeat.get("interval_min", 60))
    last_run    = heartbeat.get("last_run", "")
    last_result = heartbeat.get("last_result", "")

    active_chk   = "checked" if active else ""
    last_run_str = last_run[:16].replace("T", " ") if last_run else "noch nie"

    test_btn = (
        '<button id="ae-hb-test-btn" class="ae-btn-ghost" style="padding:6px 14px;font-size:12px">'
        '▶ Jetzt testen</button>' if agent_id else ""
    )
    last_result_html = (
        f'<div class="ae-hb-last">{_esc(last_result[:300])}</div>' if last_result else ""
    )

    return f'''
        <div class="ae-section">
            <div class="ae-hb">
                <div class="ae-hb-title">
                    <span style="color:#ef4444">♥</span> Heartbeat
                </div>
                <div class="ae-hb-row">
                    <label class="ae-opt">
                        <input type="checkbox" id="ae-hb-active" class="ae-opt-cb" {active_chk}>
                        <span class="ae-opt-label">Aktiv</span>
                    </label>
                    <div style="display:flex;align-items:center;gap:8px">
                        <span style="font-size:13px;color:#b8d4b8">Intervall:</span>
                        <input type="number" id="ae-hb-interval" value="{interval}" min="1" max="10080"
                            class="ae-input" style="width:80px;padding:6px 8px;text-align:center">
                        <span style="font-size:12px;color:#3a5a3a">Min</span>
                    </div>
                </div>
                <textarea id="ae-hb-prompt" rows="3" class="ae-textarea"
                    placeholder="Was soll der Agent regelmäßig tun? (z.B. '@Flo fasse die News zusammen')"
                    style="min-height:72px;margin-bottom:10px">{_esc(prompt)}</textarea>
                <div style="display:flex;align-items:center;justify-content:space-between">
                    <span style="font-size:11px;color:#3a5a3a">Letzter Run: {_esc(last_run_str)}</span>
                    {test_btn}
                </div>
                {last_result_html}
            </div>
        </div>
    '''


def _build_heartbeat_script(agent_id: str) -> str:
    if not agent_id:
        return ""
    return f"""<script>
document.addEventListener('DOMContentLoaded', function() {{
    const btn = document.getElementById('ae-hb-test-btn');
    if (!btn) return;
    btn.addEventListener('click', async function(e) {{
        e.preventDefault();
        btn.disabled = true;
        const orig = btn.textContent;
        btn.textContent = '…';
        try {{
            const r = await fetch('/api/agents/{agent_id}/heartbeat/run', {{method: 'POST'}});
            btn.textContent = r.ok ? '✓ Gestartet' : '✗ Fehler';
        }} catch(e) {{ btn.textContent = '✗ Fehler'; }}
        setTimeout(() => {{ btn.disabled = false; btn.textContent = orig; }}, 2500);
    }});
}});
</script>"""


def _build_save_js(agent_id: str, is_new: bool) -> str:
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
        const val = (id) => document.getElementById(id)?.value?.trim() || '';
        const chk = (id) => !!document.getElementById(id)?.checked;

        const skills = [];
        document.querySelectorAll('.ae-skill-cb:checked').forEach(cb => skills.push(cb.value));

        const data = {
            name: val('ae-name'),
            role: val('ae-role'),
            model: val('ae-model-input'),
            provider: val('ae-provider'),
            soul: document.getElementById('ae-soul')?.value || '',
            color: val('ae-color') || '#00e676',
            max_tokens: parseInt(val('ae-max-tokens')) || 2048,
            skills: skills,
            favorite: chk('ae-fav'),
            web_search: chk('ae-web'),
            voice: val('ae-voice'),
        };

        const heartbeatData = {
            active: chk('ae-hb-active'),
            prompt: document.getElementById('ae-hb-prompt')?.value?.trim() || '',
            interval_min: parseInt(val('ae-hb-interval')) || 60,
        };

        if (!data.name) { alert('Name darf nicht leer sein'); return; }

        const btn = document.getElementById('ae-save-btn');
        if (btn) { btn.disabled = true; btn.textContent = 'Speichere…'; }

        fetch(""" + endpoint + """, {
            method: """ + method + """,
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        })
        .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(async result => {
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
    document.addEventListener('DOMContentLoaded', function() {
        const btn = document.getElementById('ae-save-btn');
        if (btn) btn.addEventListener('click', aeCollectAndSave);
    });
    </script>
    """


def _build_action_buttons_html(cancel_href: str, is_new: bool) -> str:
    label = "Agent erstellen" if is_new else "Speichern"
    icon = "add" if is_new else "save"
    return f'''
        <div class="ae-section" style="display:flex;gap:12px;justify-content:flex-end;align-items:center">
            <a href="{cancel_href}" class="ae-btn-ghost">Abbrechen</a>
            <button id="ae-save-btn" class="ae-btn-primary">
                <span class="material-icons" style="font-size:18px">{icon}</span>
                {label}
            </button>
        </div>
    '''


# ── Pages ────────────────────────────────────────────────────────────────────

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

    ui.add_css(_FORM_CSS)
    ui.add_head_html(_build_save_js(agent_id, is_new=False))
    ui.add_head_html(_COLOR_SYNC_SCRIPT)
    ui.add_head_html(_build_model_script(agent.get("model", ""), agent.get("provider", "ollama")))
    ui.add_head_html(_build_voice_script(agent.get("voice", "")))
    ui.add_head_html(_build_heartbeat_script(agent_id))

    enabled_skills = set(agent.get("skills", []))

    with ui.element("div").style(_CONTAINER_STYLE):
        ui.html(_build_header_html(f"/chat/{agent_id}", f"Agent bearbeiten: {agent.get('name', '?')}"))

        with ui.element("div").style(_FORM_STYLE):
            _form_html = (
                _build_name_role_html(agent.get("name", ""), agent.get("role", ""))
                + _build_model_provider_html(agent.get("model", ""), agent.get("provider", "ollama"))
                + _build_soul_html(agent.get("soul", ""))
                + _build_color_tokens_html(agent.get("color", "#00e676"), agent.get("max_tokens", 2048))
                + _build_skills_html(all_skills, enabled_skills)
                + _build_options_html(agent.get("favorite", False), agent.get("web_search", False))
                + _build_voice_html(agent.get("voice", ""))
                + _build_heartbeat_html(agent.get("heartbeat", {}), agent_id)
                + _build_action_buttons_html(f"/chat/{agent_id}", is_new=False)
            )
            import re as _re
            _hits = [(m.start(), _form_html[max(0,m.start()-40):m.end()+40]) for m in _re.finditer(r'</?script', _form_html, _re.I)]
            if _hits:
                logger.error("AGENT_EDIT has %d script hits: %s", len(_hits), _hits[:2])
            ui.html(_form_html)


@ui.page("/agent/new")
def agent_new_page(clone: str = ""):
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

    ui.add_css(_FORM_CSS)
    ui.add_head_html(_build_save_js("", is_new=True))
    ui.add_head_html(_COLOR_SYNC_SCRIPT)
    ui.add_head_html(_build_model_script(_val("model", "llama3"), _val("provider", "ollama")))
    ui.add_head_html(_build_voice_script(_val("voice", "")))

    with ui.element("div").style(_CONTAINER_STYLE):
        ui.html(_build_header_html("/", page_title))

        with ui.element("div").style(_FORM_STYLE):
            ui.html(
                _build_name_role_html(default_name, _val("role"))
                + _build_model_provider_html(_val("model", "llama3"), _val("provider", "ollama"))
                + _build_soul_html(_val("soul"))
                + _build_color_tokens_html(_val("color", "#00e676"), _val("max_tokens", 2048))
                + _build_skills_html(all_skills, cloned_skills)
                + _build_options_html(_val("favorite", False), False)
                + _build_voice_html(_val("voice", ""))
                + _build_heartbeat_html(clone_source.get("heartbeat", {}) if clone_source else {}, "")
                + _build_action_buttons_html("/", is_new=True)
            )
