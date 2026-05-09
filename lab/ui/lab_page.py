"""
lab/ui/lab_page.py — NiceGUI Page für das Communication Lab.

Komplett JS-basiert (NiceGUI core.loop Bug-Workaround).
URL: /lab — klar als 🧪 LAB markiert um Verwechslung mit echten Agenten zu vermeiden.
"""
from nicegui import ui
from ui.theme import apply_theme


_PAGE_CSS = """
<style>
body { background: #050a06 !important; color: #e2e8f0; margin: 0; font-family: system-ui, sans-serif; }
.lab-wrap { display: flex; flex-direction: column; gap: 16px; padding: 20px; max-width: 1400px; margin: 0 auto; }
.lab-banner {
  background: linear-gradient(135deg, #7c2d12, #9a3412);
  color: #fed7aa; padding: 8px 16px; border-radius: 8px; font-size: 13px;
  display: flex; align-items: center; gap: 10px;
}
.lab-title { font-size: 22px; font-weight: 700; color: #4ade80; margin-bottom: 2px; }
.lab-sub { font-size: 13px; color: #6b7280; }
.lab-grid { display: grid; grid-template-columns: 320px 1fr 380px; gap: 14px; align-items: start; }
@media (max-width: 1100px) { .lab-grid { grid-template-columns: 1fr; } }

.lab-card {
  background: #0d1f0e; border: 1px solid #1a3a1a; border-radius: 10px; padding: 14px;
}
.lab-card-title {
  font-size: 12px; font-weight: 600; color: #4ade80; margin-bottom: 10px;
  letter-spacing: 0.06em; text-transform: uppercase;
}

.lab-input, .lab-select, .lab-textarea {
  background: #070d08; border: 1px solid #1a3a1a; border-radius: 6px;
  color: #e2e8f0; padding: 6px 10px; font-size: 13px; width: 100%; box-sizing: border-box;
}
.lab-textarea { resize: vertical; min-height: 50px; font-family: inherit; }
.lab-row { display: flex; gap: 8px; flex-wrap: wrap; }
.lab-field { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 100px; }
.lab-label { font-size: 11px; color: #9ca3af; }

.lab-btn {
  background: #14532d; color: #4ade80; border: 1px solid #166534;
  border-radius: 6px; padding: 6px 14px; font-size: 13px; cursor: pointer;
  transition: opacity .15s;
}
.lab-btn:hover { background: #166534; }
.lab-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.lab-btn-danger { background: #7f1d1d; color: #fca5a5; border-color: #991b1b; }
.lab-btn-danger:hover { background: #991b1b; }
.lab-btn-mini { padding: 2px 8px; font-size: 11px; }

/* Agent-Liste */
.lab-agent-item {
  background: #050a06; border: 1px solid #1a3a1a; border-radius: 6px;
  padding: 8px 10px; margin-bottom: 6px; font-size: 12px;
}
.lab-agent-row { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
.lab-agent-name { font-weight: 600; color: #fbbf24; }
.lab-agent-meta { color: #6b7280; font-size: 11px; margin-top: 2px; }
.lab-policy-badge {
  display: inline-block; padding: 1px 6px; border-radius: 4px;
  font-size: 10px; background: #14532d; color: #4ade80;
}
.lab-policy-badge.silent { background: #7f1d1d; color: #fca5a5; }
.lab-policy-badge.flaky  { background: #78350f; color: #fcd34d; }
.lab-policy-badge.delegator { background: #1e3a8a; color: #93c5fd; }
.lab-policy-badge.slow { background: #4a1d96; color: #c4b5fd; }

/* Trace */
#lab-trace {
  background: #050a06; border-radius: 6px; padding: 10px;
  height: 600px; overflow-y: auto; font-family: monospace; font-size: 11px;
}
.lab-evt { padding: 3px 0; border-bottom: 1px solid #0a1408; display: flex; gap: 8px; }
.lab-evt-time { color: #4b5563; flex-shrink: 0; }
.lab-evt-type { font-weight: 600; flex-shrink: 0; min-width: 130px; }
.lab-evt-body { color: #d1d5db; word-break: break-all; }
.lab-evt-type.message_sent, .lab-evt-type.message { color: #93c5fd; }
.lab-evt-type.task_assigned, .lab-evt-type.task_started { color: #4ade80; }
.lab-evt-type.task_completed { color: #22d3ee; }
.lab-evt-type.task_failed, .lab-evt-type.task_timeout { color: #f87171; }
.lab-evt-type.task_waiting, .lab-evt-type.task_heartbeat { color: #fbbf24; }
.lab-evt-type.mission_started, .lab-evt-type.mission_finished { color: #c084fc; font-weight: 700; }
.lab-evt-feel { color: #f59e0b; font-size: 10px; margin-left: 8px; opacity: 0.8; font-style: italic; }
.lab-agent-clock { font-size: 10px; color: #6b7280; margin-top: 3px; font-family: monospace; }
.lab-time-feel { color: #f59e0b; }

/* Tasks */
.lab-task-item {
  background: #050a06; border: 1px solid #1a3a1a; border-left: 3px solid #4ade80;
  border-radius: 4px; padding: 6px 10px; margin-bottom: 4px; font-size: 11px;
}
.lab-task-item.failed { border-left-color: #f87171; }
.lab-task-item.timeout { border-left-color: #f97316; }
.lab-task-item.waiting { border-left-color: #fbbf24; }
.lab-task-item.completed { border-left-color: #22d3ee; }
.lab-task-state { font-weight: 700; text-transform: uppercase; font-size: 10px; }
.lab-task-content { color: #d1d5db; margin-top: 3px; }
.lab-task-meta { color: #6b7280; font-size: 10px; margin-top: 3px; }

/* Mission-Liste */
.lab-mission-item {
  background: #050a06; border: 1px solid #1a3a1a; border-radius: 6px;
  padding: 8px 10px; margin-bottom: 5px; font-size: 12px; cursor: pointer;
}
.lab-mission-item.active { border-color: #4ade80; background: #0a2010; }
.lab-mission-state { font-size: 10px; padding: 1px 6px; border-radius: 3px; }
.lab-mission-state.running { background: #14532d; color: #4ade80; }
.lab-mission-state.completed { background: #1e40af; color: #93c5fd; }
.lab-mission-state.failed, .lab-mission-state.timeout { background: #7f1d1d; color: #fca5a5; }
</style>
"""

_PAGE_JS = r"""
<script>
(function() {
  const API = '/api/lab';
  const qs = (s) => document.querySelector(s);
  const qsa = (s) => Array.from(document.querySelectorAll(s));
  let currentMissionId = null;
  let es = null;

  function escape(s) {
    return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── Agenten ─────────────────────────────────────────────────────────────
  async function loadAgents() {
    const r = await fetch(API + '/agents');
    const agents = await r.json();
    const el = qs('#lab-agents');
    if (!agents.length) {
      el.innerHTML = '<div style="color:#6b7280;font-size:12px">Noch keine Agenten gespawnt.</div>';
      // Auch Start-Agent dropdown leeren
      qs('#lab-start-agent').innerHTML = '<option value="">Erst Agent spawnen...</option>';
      return;
    }
    el.innerHTML = '';
    agents.forEach(a => {
      const row = document.createElement('div');
      row.className = 'lab-agent-item';
      const policyClass = ['silent','flaky','delegator','slow'].includes(a.policy) ? a.policy : '';
      row.innerHTML = `
        <div class="lab-agent-row">
          <span class="lab-agent-name">${escape(a.label || a.name)} <span style="color:#6b7280;font-weight:400">(${escape(a.id)})</span></span>
          <button class="lab-btn lab-btn-mini lab-btn-danger" data-name="${escape(a.name)}">✕</button>
        </div>
        <div class="lab-agent-meta">
          <span class="lab-policy-badge ${policyClass}">${escape(a.policy)}</span>
          ${a.delegates_to.length ? '→ ' + a.delegates_to.map(escape).join(', ') : ''}
          ${a.delay_sec > 0 ? ' ⏱'+a.delay_sec+'s' : ''}
          ${a.error_prob > 0 ? ' ⚠'+(a.error_prob*100).toFixed(0)+'%' : ''}
          · inbox: ${a.inbox_size}
        </div>
        ${a.ops > 0 ? `<div class="lab-agent-clock">
          ⏱ ez=${a.eigenzeit} ops · ${a.dilation_rate.toFixed(2)} ops/s
          · <span class="lab-time-feel">${escape(a.time_feel)}</span>
        </div>` : ''}
      `;
      el.appendChild(row);
      row.querySelector('button').addEventListener('click', async (e) => {
        const name = e.target.dataset.name;
        if (confirm(`Agent ${name} entfernen?`)) {
          await fetch(API + '/agents/' + name, {method:'DELETE'});
          loadAgents();
        }
      });
    });
    // Start-Agent Dropdown füllen
    const sel = qs('#lab-start-agent');
    sel.innerHTML = agents.map(a => `<option value="${escape(a.name)}">${escape(a.label||a.name)} (${escape(a.policy)})</option>`).join('');
  }

  async function spawnAgent() {
    const name = qs('#lab-spawn-name').value.trim();
    const policy = qs('#lab-spawn-policy').value;
    const delegatesRaw = qs('#lab-spawn-delegates').value.trim();
    const delay = parseFloat(qs('#lab-spawn-delay').value) || 0;
    const errProb = parseFloat(qs('#lab-spawn-errprob').value) || 0;
    if (!name) { alert('Name?'); return; }
    const body = {
      name, policy,
      delegates_to: delegatesRaw ? delegatesRaw.split(',').map(s=>s.trim()).filter(Boolean) : [],
      delay_sec: delay,
      error_prob: errProb,
    };
    const r = await fetch(API + '/agents/spawn', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    if (!r.ok) { alert(await r.text()); return; }
    qs('#lab-spawn-name').value = '';
    qs('#lab-spawn-delegates').value = '';
    loadAgents();
  }

  // ── Missionen ───────────────────────────────────────────────────────────
  async function loadMissions() {
    const r = await fetch(API + '/missions');
    const missions = await r.json();
    const el = qs('#lab-missions');
    if (!missions.length) {
      el.innerHTML = '<div style="color:#6b7280;font-size:12px">Noch keine Missionen.</div>';
      return;
    }
    el.innerHTML = '';
    missions.slice().reverse().forEach(m => {
      const item = document.createElement('div');
      item.className = 'lab-mission-item' + (m.id === currentMissionId ? ' active' : '');
      item.dataset.id = m.id;
      const state = m.final_state || 'running';
      item.innerHTML = `
        <div style="display:flex;justify-content:space-between;gap:6px;align-items:center">
          <span style="color:#e2e8f0;font-weight:600">${escape(m.title)}</span>
          <span class="lab-mission-state ${state}">${state}</span>
        </div>
        <div style="color:#6b7280;font-size:10px;margin-top:3px">${escape(m.id)} · start: ${escape(m.start_agent)}</div>
        ${m.final_result ? '<div style="color:#9ca3af;font-size:11px;margin-top:3px">→ '+escape(m.final_result.substring(0,100))+'</div>' : ''}
      `;
      item.addEventListener('click', () => selectMission(m.id));
      el.appendChild(item);
    });
  }

  async function startMission() {
    const startAgent = qs('#lab-start-agent').value;
    const content = qs('#lab-mission-content').value.trim();
    const title = qs('#lab-mission-title').value.trim() || 'Mission';
    if (!startAgent) { alert('Erst einen Agent spawnen'); return; }
    if (!content) { alert('Auftrag?'); return; }
    const r = await fetch(API + '/missions/start', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        title, start_agent: startAgent, initial_content: content,
        timeout_sec: 60, heartbeat_timeout_sec: 15,
      }),
    });
    if (!r.ok) { alert(await r.text()); return; }
    const mission = await r.json();
    selectMission(mission.id);
    loadMissions();
  }

  function selectMission(id) {
    currentMissionId = id;
    qs('#lab-trace').innerHTML = '';
    qs('#lab-tasks').innerHTML = '<div style="color:#6b7280">Lade...</div>';
    qs('#lab-mission-header').textContent = 'Mission: ' + id;
    if (es) { es.close(); es = null; }
    es = new EventSource(API + '/missions/' + id + '/stream');
    es.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      addTraceEvent(msg);
      // Periodisch Tasks neu holen
      if (['task_assigned','task_completed','task_failed','task_timeout','task_canceled','mission_finished'].includes(msg.event)) {
        loadTasks();
        loadMissions();
      }
    };
    es.onerror = () => { /* EventSource reconnected automatisch */ };
    loadTasks();
    loadMissions();
  }

  function addTraceEvent(msg) {
    const el = qs('#lab-trace');
    const div = document.createElement('div');
    div.className = 'lab-evt';
    const t = new Date(msg.ts * 1000).toLocaleTimeString('de-DE', {hour12:false}) + '.' + String(Math.floor((msg.ts % 1)*1000)).padStart(3,'0');
    let body = '';
    if (msg.event === 'message') {
      body = `${escape(msg.sender)} → ${escape(msg.recipient)} [${escape(msg.type)}] ${escape(JSON.stringify(msg.payload).substring(0,100))}`;
    } else {
      const fields = ['agent','task_id','sender','recipient','requester','sub_task','content','result','error','reason','state','duration','age','title','start_agent'];
      body = fields.filter(k => msg[k] !== undefined).map(k => `${k}=${escape(JSON.stringify(msg[k]).replace(/^"|"$/g,'').substring(0,60))}`).join(' ');
    }
    // Zeitgefühl anhängen wenn vorhanden
    const feel = msg.time_feel || (msg.clock && msg.clock.dilation && Object.keys(msg.clock.dilation).length
      ? Object.entries(msg.clock.dilation).map(([k,v]) => `${k.replace('lab:','')}:${v.toFixed(1)}`).join(' ')
      : '');
    const feelHtml = feel ? `<span class="lab-evt-feel">${escape(feel)}</span>` : '';
    div.innerHTML = `<span class="lab-evt-time">${t}</span><span class="lab-evt-type ${escape(msg.event)}">${escape(msg.event)}</span><span class="lab-evt-body">${body}</span>${feelHtml}`;
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;
  }

  async function loadTasks() {
    if (!currentMissionId) return;
    const r = await fetch(API + '/missions/' + currentMissionId + '/tasks');
    const tasks = await r.json();
    const el = qs('#lab-tasks');
    if (!tasks.length) {
      el.innerHTML = '<div style="color:#6b7280">Keine Tasks.</div>';
      return;
    }
    el.innerHTML = '';
    tasks.sort((a,b) => a.created_at - b.created_at).forEach(t => {
      const div = document.createElement('div');
      div.className = 'lab-task-item ' + t.state;
      div.innerHTML = `
        <div style="display:flex;justify-content:space-between;gap:6px;align-items:center">
          <span class="lab-task-state">${escape(t.state)}</span>
          <span style="color:#6b7280;font-size:10px">${escape(t.task_id)}</span>
        </div>
        <div class="lab-task-content">${escape(t.content)}</div>
        <div class="lab-task-meta">
          owner: ${escape(t.owner)} · req: ${escape(t.requester)}
          ${t.parent_task_id ? '<br>parent: '+escape(t.parent_task_id) : ''}
          ${t.sub_task_ids.length ? '<br>subs: '+t.sub_task_ids.length : ''}
          ${t.result ? '<br>→ '+escape(String(t.result).substring(0,80)) : ''}
          ${t.error ? '<br>✗ '+escape(t.error) : ''}
        </div>
      `;
      el.appendChild(div);
    });
  }

  async function resetLab() {
    if (!confirm('🧪 LAB komplett zurücksetzen? Alle Agenten + Missionen weg.')) return;
    await fetch(API + '/reset', {method: 'POST'});
    currentMissionId = null;
    if (es) { es.close(); es = null; }
    qs('#lab-trace').innerHTML = '';
    qs('#lab-tasks').innerHTML = '';
    qs('#lab-mission-header').textContent = 'Keine Mission ausgewählt';
    loadAgents();
    loadMissions();
  }

  // ── Init ───────────────────────────────────────────────────────────────
  setTimeout(() => {
    qs('#lab-spawn-btn').addEventListener('click', spawnAgent);
    qs('#lab-mission-start-btn').addEventListener('click', startMission);
    qs('#lab-reset-btn').addEventListener('click', resetLab);
    loadAgents();
    loadMissions();
    setInterval(loadAgents, 5000);  // Inbox-Größen aktualisieren
  }, 200);
})();
</script>
"""


@ui.page("/lab")
def lab_page():
    apply_theme()
    ui.add_head_html(_PAGE_CSS)
    ui.add_head_html(_PAGE_JS)
    ui.html("""
<div class="lab-wrap">
  <div class="lab-banner">
    🧪 <strong>COMMUNICATION LAB</strong>
    <span style="color:#fed7aa;opacity:0.8">— isolierte Test-Umgebung. Mock-Agenten nur. Kein Bezug zu echten AgentClaw-Agenten.</span>
    <button id="lab-reset-btn" class="lab-btn lab-btn-mini lab-btn-danger" style="margin-left:auto">⟲ Reset</button>
  </div>

  <div>
    <div class="lab-title">A2A / M2M Protocol Lab</div>
    <div class="lab-sub">Spawne Agenten, definiere Missionen, beobachte den Loop.</div>
  </div>

  <div class="lab-grid">

    <!-- Linke Spalte: Spawn + Agenten + Mission -->
    <div style="display:flex;flex-direction:column;gap:14px">

      <!-- Spawn -->
      <div class="lab-card">
        <div class="lab-card-title">⚡ Agent spawnen</div>
        <div class="lab-row">
          <div class="lab-field"><label class="lab-label">Name</label>
            <input id="lab-spawn-name" class="lab-input" placeholder="martin"></div>
          <div class="lab-field"><label class="lab-label">Policy</label>
            <select id="lab-spawn-policy" class="lab-select">
              <option value="echo">echo (sofort done)</option>
              <option value="delegator">delegator</option>
              <option value="slow">slow</option>
              <option value="silent">silent (timeout)</option>
              <option value="flaky">flaky</option>
            </select>
          </div>
        </div>
        <div class="lab-field" style="margin-top:6px"><label class="lab-label">Delegates to (Komma-Liste, Namen)</label>
          <input id="lab-spawn-delegates" class="lab-input" placeholder="bob, charlie"></div>
        <div class="lab-row" style="margin-top:6px">
          <div class="lab-field"><label class="lab-label">Delay (s)</label>
            <input id="lab-spawn-delay" class="lab-input" type="number" min="0" step="0.5" value="0"></div>
          <div class="lab-field"><label class="lab-label">Error-Prob (0-1)</label>
            <input id="lab-spawn-errprob" class="lab-input" type="number" min="0" max="1" step="0.1" value="0"></div>
        </div>
        <button id="lab-spawn-btn" class="lab-btn" style="margin-top:10px;width:100%">+ Spawn</button>
      </div>

      <!-- Agenten-Liste -->
      <div class="lab-card">
        <div class="lab-card-title">👥 Aktive Agenten</div>
        <div id="lab-agents"></div>
      </div>

      <!-- Mission starten -->
      <div class="lab-card">
        <div class="lab-card-title">🎯 Mission starten</div>
        <div class="lab-field"><label class="lab-label">Titel</label>
          <input id="lab-mission-title" class="lab-input" placeholder="Test 1: Echo" value="Test"></div>
        <div class="lab-field" style="margin-top:6px"><label class="lab-label">Start-Agent</label>
          <select id="lab-start-agent" class="lab-select"></select></div>
        <div class="lab-field" style="margin-top:6px"><label class="lab-label">Auftrag</label>
          <textarea id="lab-mission-content" class="lab-textarea" placeholder="Mache X für mich..."></textarea></div>
        <button id="lab-mission-start-btn" class="lab-btn" style="margin-top:10px;width:100%">▶ Start</button>
      </div>
    </div>

    <!-- Mitte: Trace -->
    <div class="lab-card">
      <div class="lab-card-title" id="lab-mission-header">Keine Mission ausgewählt</div>
      <div id="lab-trace"></div>
    </div>

    <!-- Rechte Spalte: Missionen + Tasks -->
    <div style="display:flex;flex-direction:column;gap:14px">
      <div class="lab-card">
        <div class="lab-card-title">🗂 Missionen</div>
        <div id="lab-missions"></div>
      </div>
      <div class="lab-card">
        <div class="lab-card-title">📋 Tasks (aktuelle Mission)</div>
        <div id="lab-tasks"></div>
      </div>
    </div>

  </div>
</div>
""")
