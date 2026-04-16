"""
ui/pages/insights.py — App Insights: Read-only Monitoring Dashboard.
Zeigt Agenten, Tasks, Skills, Provider-Status — greift nur lesend auf APIs zu.
"""
from nicegui import ui
from ui.layout import create_layout

_INSIGHTS_HTML = """
<style>
  .ins-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .ins-card { background: #0a150b; border: 1px solid #0f2010; border-radius: 10px; padding: 16px 18px; }
  .ins-card-big { background: #0a150b; border: 1px solid #0f2010; border-radius: 10px; padding: 16px 18px; margin-bottom: 16px; }
  .ins-num { font-size: 28px; font-weight: 700; color: #00e676; font-family: monospace; }
  .ins-label { font-size: 10px; color: #3a5a3a; text-transform: uppercase; letter-spacing: .5px; margin-top: 4px; }
  .ins-section { font-size: 11px; font-weight: 700; color: #00e676; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 10px; }
  .ins-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .ins-table th { text-align: left; color: #3a5a3a; font-weight: 600; padding: 4px 8px; border-bottom: 1px solid #0f2010; }
  .ins-table td { padding: 6px 8px; border-bottom: 1px solid #0a150b; color: #b8d4b8; vertical-align: top; }
  .ins-table tr:hover td { background: #0d1a0e; }
  .ins-badge { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 10px; font-family: monospace; }
  .badge-green { background: rgba(0,230,118,.1); color: #00e676; border: 1px solid #00e67644; }
  .badge-blue  { background: rgba(100,181,246,.1); color: #64b5f6; border: 1px solid #64b5f644; }
  .badge-red   { background: rgba(239,68,68,.1); color: #ef4444; border: 1px solid #ef444444; }
  .badge-gray  { background: rgba(100,100,100,.1); color: #888; border: 1px solid #44444444; }
  .badge-yellow{ background: rgba(255,235,59,.1); color: #ffeb3b; border: 1px solid #ffeb3b44; }
  .ins-dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; margin-right: 5px; }
  .ins-skill-pill { display: inline-block; padding: 1px 7px; border-radius: 10px; font-size: 10px; font-family: monospace; background: rgba(0,230,118,.06); color: #00c853; border: 1px solid #00c85322; margin: 1px; }
  .ins-refresh { float: right; font-size: 10px; color: #3a5a3a; font-family: monospace; cursor: pointer; padding: 3px 8px; border: 1px solid #0f2010; border-radius: 4px; background: none; }
  .ins-refresh:hover { color: #00e676; border-color: #00e67644; }
  #ins-status { font-size: 10px; color: #3a5a3a; font-family: monospace; margin-bottom: 14px; }
</style>

<div style="padding: 16px 20px; max-width: 1200px;">
  <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:16px;">
    <span style="font-size:16px;font-weight:700;color:#b8d4b8;font-family:monospace;">INSIGHTS</span>
    <button class="ins-refresh" onclick="loadAll()">⟳ Aktualisieren</button>
  </div>
  <div id="ins-status">Lade Daten...</div>

  <!-- KPI-Karten -->
  <div class="ins-grid" id="ins-kpis"></div>

  <!-- Agenten -->
  <div class="ins-card-big">
    <div class="ins-section">Agenten</div>
    <table class="ins-table">
      <thead><tr>
        <th>Name</th><th>Modell</th><th>Provider</th><th>Skills</th><th>Heartbeat</th>
      </tr></thead>
      <tbody id="ins-agents-body"></tbody>
    </table>
  </div>

  <!-- Tasks + Skills nebeneinander -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
    <div class="ins-card-big" style="margin-bottom:0">
      <div class="ins-section">Task-Aktivität</div>
      <div id="ins-task-bars" style="margin-bottom:12px"></div>
      <div class="ins-section" style="margin-top:14px">Letzte Tasks</div>
      <table class="ins-table">
        <thead><tr><th>Sender</th><th>Empfänger</th><th>Status</th><th>Skill</th></tr></thead>
        <tbody id="ins-tasks-body"></tbody>
      </table>
    </div>
    <div class="ins-card-big" style="margin-bottom:0">
      <div class="ins-section">Registrierte Skills</div>
      <div id="ins-skills-list"></div>
      <div class="ins-section" style="margin-top:16px">Provider Status</div>
      <div id="ins-providers"></div>
    </div>
  </div>

  <!-- Agent-Statistiken -->
  <div class="ins-card-big">
    <div class="ins-section">Agent-Auslastung</div>
    <table class="ins-table">
      <thead><tr><th>Agent</th><th>Tasks gesamt</th><th>Abgeschlossen</th><th>Fehlgeschlagen</th><th>Erfolgsrate</th></tr></thead>
      <tbody id="ins-agent-stats-body"></tbody>
    </table>
  </div>
</div>

<script>
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function statusBadge(s) {
  const cls = {
    completed: 'badge-green', working: 'badge-blue', failed: 'badge-red',
    waiting: 'badge-yellow', pending: 'badge-gray'
  }[s] || 'badge-gray';
  return `<span class="ins-badge ${cls}">${esc(s)}</span>`;
}

function providerBadge(p) {
  const cls = { ollama: 'badge-green', openrouter: 'badge-blue' }[p] || 'badge-gray';
  return `<span class="ins-badge ${cls}">${esc(p||'?')}</span>`;
}

async function loadAll() {
  document.getElementById('ins-status').textContent = 'Lade...';
  try {
    const [agentsRes, statsRes, skillsRes, providersRes, healthRes] = await Promise.all([
      fetch('/api/agents'),
      fetch('/api/stats'),
      fetch('/api/skills'),
      fetch('/api/providers'),
      fetch('/api/health'),
    ]);
    const agentsData  = await agentsRes.json();
    const stats       = await statsRes.json();
    const skillsData  = await skillsRes.json();
    const providers   = await providersRes.json();
    const health      = await healthRes.json();

    const agents = agentsData.agents || [];
    const skills = skillsData.skills || skillsData || [];

    renderKPIs(agents, stats, skills, health);
    renderAgents(agents, skills);
    renderTasks(stats);
    renderSkills(skills);
    renderProviders(providers);
    renderAgentStats(stats);

    const now = new Date().toLocaleTimeString('de-DE');
    document.getElementById('ins-status').textContent = `Zuletzt aktualisiert: ${now}`;
  } catch(e) {
    document.getElementById('ins-status').textContent = 'Fehler beim Laden: ' + e.message;
  }
}

function renderKPIs(agents, stats, skills, health) {
  const activeTasks = (stats.status_counts || {});
  const pending = (activeTasks.pending || 0) + (activeTasks.waiting || 0);
  const working = activeTasks.working || 0;
  const total   = stats.total_tasks || 0;
  const successRate = stats.success_rate != null ? stats.success_rate.toFixed(1) + '%' : '—';
  const qdrant  = health.qdrant === 'ok' ? '✓' : '✗';
  const qdrantCol = health.qdrant === 'ok' ? '#00e676' : '#ef4444';

  const kpis = [
    { num: agents.length, label: 'Agenten', color: '#00e676' },
    { num: skills.length || Object.keys(skills).length, label: 'Skills', color: '#64b5f6' },
    { num: total, label: 'Tasks gesamt', color: '#b8d4b8' },
    { num: pending, label: 'Tasks ausstehend', color: '#ffeb3b' },
    { num: working, label: 'Tasks aktiv', color: '#00bcd4' },
    { num: successRate, label: 'Erfolgsrate', color: '#00e676' },
    { num: qdrant, label: 'Qdrant Memory', color: qdrantCol },
    { num: (stats.avg_duration_sec || 0).toFixed(1) + 's', label: 'Ø Task-Dauer', color: '#ab47bc' },
  ];

  document.getElementById('ins-kpis').innerHTML = kpis.map(k => `
    <div class="ins-card">
      <div class="ins-num" style="color:${k.color}">${esc(String(k.num))}</div>
      <div class="ins-label">${esc(k.label)}</div>
    </div>
  `).join('');
}

function renderAgents(agents, skills) {
  const skillMap = {};
  (Array.isArray(skills) ? skills : []).forEach(s => { skillMap[s.id] = s.name; });

  document.getElementById('ins-agents-body').innerHTML = agents.map(a => {
    const agentSkills = (a.skills || []).map(sid =>
      `<span class="ins-skill-pill">${esc(skillMap[sid] || sid)}</span>`
    ).join('') || '<span style="color:#3a5a3a;font-size:10px">—</span>';
    const hb = a.heartbeat || {};
    const hbBadge = hb.active
      ? `<span class="ins-badge badge-green">aktiv · ${hb.interval_min}min</span>`
      : `<span class="ins-badge badge-gray">inaktiv</span>`;
    return `<tr>
      <td><strong style="color:#b8d4b8">${esc(a.name)}</strong>
          <div style="font-size:10px;color:#3a5a3a;margin-top:2px">${esc(a.role||'')}</div></td>
      <td><span style="font-family:monospace;font-size:11px">${esc(a.model||'—')}</span></td>
      <td>${providerBadge(a.provider)}</td>
      <td>${agentSkills}</td>
      <td>${hbBadge}</td>
    </tr>`;
  }).join('');
}

function renderTasks(stats) {
  // Status-Balken
  const counts = stats.status_counts || {};
  const total  = Object.values(counts).reduce((a,b) => a+b, 0) || 1;
  const colors = { completed:'#00e676', working:'#64b5f6', failed:'#ef4444', waiting:'#ffeb3b', pending:'#888' };
  let bars = '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">';
  for (const [status, count] of Object.entries(counts)) {
    const pct = Math.max(4, Math.round((count / total) * 100));
    bars += `<div style="flex:1;min-width:60px">
      <div style="height:6px;border-radius:3px;background:${colors[status]||'#444'};opacity:.8;width:${pct}%"></div>
      <div style="font-size:10px;color:#3a5a3a;margin-top:3px">${esc(status)} <span style="color:#b8d4b8">${count}</span></div>
    </div>`;
  }
  bars += '</div>';
  document.getElementById('ins-task-bars').innerHTML = bars;

  // Letzte Tasks
  const recent = (stats.recent_tasks || []).slice(0, 8);
  document.getElementById('ins-tasks-body').innerHTML = recent.map(t => `
    <tr>
      <td style="font-size:10px;color:#3a5a3a">${esc(t.sender||'?')}</td>
      <td><strong style="font-size:11px">${esc(t.recipient||'?')}</strong></td>
      <td>${statusBadge(t.status)}</td>
      <td><span class="ins-skill-pill">${esc(t.skill||'llm')}</span></td>
    </tr>
  `).join('');
}

function renderSkills(skills) {
  const list = Array.isArray(skills) ? skills : [];
  document.getElementById('ins-skills-list').innerHTML = list.map(s => `
    <div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #0f2010">
      <span class="material-icons" style="font-size:14px;color:#3a5a3a">${esc(s.icon||'extension')}</span>
      <div>
        <span style="font-size:12px;color:#b8d4b8">${esc(s.name)}</span>
        <span style="font-size:10px;color:#3a5a3a;font-family:monospace;margin-left:6px">[${esc(s.id)}]</span>
      </div>
    </div>
  `).join('') || '<span style="color:#3a5a3a;font-size:11px">Keine Skills registriert</span>';
}

function renderProviders(providers) {
  const checks = {
    ollama:      { label: 'Ollama (lokal)',   key: v => v.url,                   extra: v => v.url },
    openrouter:  { label: 'OpenRouter',       key: v => v.api_key_masked,         extra: v => v.api_key_masked ? '●●●●' + v.api_key_masked.slice(-4) : null },
    comfyui:     { label: 'ComfyUI',          key: v => v.url,                   extra: v => v.url },
    qdrant:      { label: 'Qdrant (Vector)',  key: v => v.url,                   extra: v => v.url },
    telegram:    { label: 'Telegram',         key: v => v.enabled && v.bot_token, extra: v => v.enabled ? 'aktiv' : 'inaktiv' },
    redis:       { label: 'Redis',            key: v => v.enabled,               extra: v => v.enabled ? `${v.host}:${v.port}` : 'inaktiv' },
  };
  let html = '';
  for (const [pid, cfg] of Object.entries(checks)) {
    const pData = providers[pid] || {};
    const hasVal = cfg.key(pData);
    const extra  = cfg.extra(pData);
    const dot    = hasVal ? '#00e676' : '#3a5a3a';
    html += `<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #0f2010">
      <span class="ins-dot" style="background:${dot};flex-shrink:0"></span>
      <span style="font-size:12px;color:#b8d4b8;flex:1">${esc(cfg.label)}</span>
      ${extra ? `<span style="font-size:10px;color:#3a5a3a;font-family:monospace">${esc(String(extra).slice(0,40))}</span>` : ''}
    </div>`;
  }
  document.getElementById('ins-providers').innerHTML = html;
}

function renderAgentStats(stats) {
  const agentStats = stats.agent_stats || [];
  document.getElementById('ins-agent-stats-body').innerHTML = agentStats.map(a => `
    <tr>
      <td><strong style="color:#b8d4b8">${esc(a.name)}</strong></td>
      <td style="text-align:right;font-family:monospace">${a.total}</td>
      <td style="text-align:right;font-family:monospace;color:#00e676">${a.completed}</td>
      <td style="text-align:right;font-family:monospace;color:#ef4444">${a.failed||0}</td>
      <td>
        <div style="display:flex;align-items:center;gap:6px">
          <div style="flex:1;height:4px;background:#0f2010;border-radius:2px">
            <div style="height:100%;border-radius:2px;background:#00e676;width:${Math.round(a.success_rate||0)}%"></div>
          </div>
          <span style="font-size:10px;font-family:monospace;color:#3a5a3a">${(a.success_rate||0).toFixed(0)}%</span>
        </div>
      </td>
    </tr>
  `).join('') || '<tr><td colspan="5" style="color:#3a5a3a;font-size:11px">Keine Daten</td></tr>';
}

// Initial laden
loadAll();
// Alle 30s auto-refresh
setInterval(loadAll, 30000);
</script>
"""


_INSIGHTS_JS, _INSIGHTS_HTML_CLEAN = _INSIGHTS_HTML.split("<script>", 1)
_INSIGHTS_JS_BODY = "<script>" + _INSIGHTS_JS.rsplit("</script>", 1)[0] if False else (
    "<script>" + _INSIGHTS_HTML.split("<script>", 1)[1].rsplit("</script>", 1)[0] + "</script>"
)
_INSIGHTS_HTML_CLEAN = _INSIGHTS_HTML.split("<script>")[0]


@ui.page("/insights")
def insights_page():
    create_layout("insights")
    ui.add_head_html(
        '<link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">'
    )
    ui.html(_INSIGHTS_HTML_CLEAN)
    ui.add_body_html(_INSIGHTS_JS_BODY)
