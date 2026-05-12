"""
lab/ui/spacetime_page.py — Spacetime-Diagramm für Mission-Zeitverläufe.

Zeigt Agent-Eigenzeiten auf Y-Achse, Agenten auf X-Achse.
Messages = Pfeile zwischen Agenten-Weltlinien.
Drift-Segmente = farblich hervorgehoben.
"""
from nicegui import ui


@ui.page("/lab/spacetime")
def spacetime_page():
    ui.add_head_html("""
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0a0a0f; color: #e0e0e0; font-family: 'JetBrains Mono', monospace, sans-serif; }

.st-header {
    display: flex; align-items: center; gap: 16px;
    padding: 14px 24px;
    background: #0f0f1a;
    border-bottom: 1px solid #1e1e3a;
}
.st-header a { color: #6366f1; text-decoration: none; font-size: 13px; }
.st-header h1 { font-size: 16px; color: #a5b4fc; font-weight: 600; letter-spacing: .05em; }

.st-controls {
    display: flex; gap: 12px; align-items: center;
    padding: 12px 24px;
    background: #0d0d1e;
    border-bottom: 1px solid #1e1e3a;
    flex-wrap: wrap;
}
.st-select {
    background: #1a1a2e; color: #c7d2fe; border: 1px solid #3730a3;
    border-radius: 6px; padding: 6px 12px; font-size: 13px; cursor: pointer;
}
.st-btn {
    background: #4f46e5; color: #fff; border: none; border-radius: 6px;
    padding: 6px 14px; font-size: 13px; cursor: pointer; font-weight: 500;
}
.st-btn:hover { background: #6366f1; }
.st-btn.secondary { background: #1e1b4b; color: #a5b4fc; border: 1px solid #3730a3; }
.st-btn.secondary:hover { background: #2d2b6b; }

.st-canvas-wrap {
    padding: 24px;
    overflow-x: auto;
}
#st-svg-container {
    background: #0d0d1e;
    border: 1px solid #1e1e3a;
    border-radius: 10px;
    overflow: hidden;
}

.st-legend {
    display: flex; gap: 20px; flex-wrap: wrap;
    padding: 12px 24px;
    font-size: 12px; color: #6b7280;
}
.st-legend-item { display: flex; align-items: center; gap: 6px; }
.st-legend-dot { width: 10px; height: 10px; border-radius: 50%; }

.st-info {
    padding: 16px 24px;
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px;
}
.st-stat {
    background: #0f0f1e; border: 1px solid #1e1e3a; border-radius: 8px; padding: 12px 16px;
}
.st-stat-label { font-size: 11px; color: #6b7280; margin-bottom: 4px; }
.st-stat-val { font-size: 18px; font-weight: 600; color: #a5b4fc; }

.st-recommend {
    margin: 0 24px 16px;
    background: #0f0f1e; border: 1px solid #3730a3; border-radius: 10px; padding: 16px;
}
.st-recommend h3 { font-size: 13px; color: #6366f1; margin-bottom: 10px; }
.st-gamma-row {
    display: flex; gap: 10px; flex-wrap: wrap;
}
.st-gamma-card {
    background: #1a1a2e; border-radius: 8px; padding: 10px 16px;
    font-size: 12px; border: 1px solid #2d2b6b; min-width: 140px;
}
.st-gamma-name { font-weight: 600; color: #c7d2fe; margin-bottom: 4px; }
.st-gamma-vals { color: #818cf8; font-size: 11px; line-height: 1.6; }
.st-gamma-badge {
    display: inline-block; border-radius: 4px; padding: 1px 6px;
    font-size: 10px; font-weight: 600; margin-left: 6px;
}
.badge-ok { background: #064e3b; color: #6ee7b7; }
.badge-drift { background: #7c2d12; color: #fca5a5; }
.badge-fast { background: #1e3a5f; color: #93c5fd; }
</style>
""")

    ui.add_body_html("""
<div class="st-header">
  <a href="/lab">← Lab</a>
  <h1>⏱ Spacetime-Diagram — Agenten-Eigenzeiten</h1>
</div>

<div class="st-controls">
  <select id="st-mission-sel" class="st-select">
    <option value="">Mission laden…</option>
  </select>
  <button class="st-btn" onclick="window._stLoad()">Laden</button>
  <button class="st-btn secondary" onclick="window._stRecommend()">🎯 Scheduler-Empfehlung</button>
  <span id="st-status" style="font-size:12px;color:#6b7280;margin-left:8px;"></span>
</div>

<div class="st-info" id="st-stats"></div>
<div class="st-recommend" id="st-recommend" style="display:none"></div>

<div class="st-canvas-wrap">
  <div id="st-svg-container">
    <svg id="st-svg" width="900" height="500" style="display:block"></svg>
  </div>
</div>

<div class="st-legend">
  <div class="st-legend-item"><div class="st-legend-dot" style="background:#6366f1"></div> REQUEST</div>
  <div class="st-legend-item"><div class="st-legend-dot" style="background:#10b981"></div> RESPONSE</div>
  <div class="st-legend-item"><div class="st-legend-dot" style="background:#ef4444"></div> ERROR</div>
  <div class="st-legend-item"><div class="st-legend-dot" style="background:#f59e0b"></div> HEARTBEAT</div>
  <div class="st-legend-item" style="gap:8px">
    <svg width="32" height="12"><line x1="0" y1="6" x2="32" y2="6" stroke="#ef4444" stroke-width="2" stroke-dasharray="4,3"/></svg>
    CAUSAL_DRIFT
  </div>
  <div class="st-legend-item" style="gap:8px">
    <svg width="32" height="12"><line x1="0" y1="6" x2="32" y2="6" stroke="#a78bfa" stroke-width="1.5"/></svg>
    ORDERED
  </div>
</div>

<script>
// ── Hilfsfunktionen ──────────────────────────────────────────────────────────
const MSG_COLOR = {
  request: '#6366f1', response: '#10b981', error: '#ef4444',
  heartbeat: '#f59e0b', cancel: '#f87171'
};
const DRIFT_COLOR = '#ef4444';
const ORDERED_COLOR = '#a78bfa';
const WORLDLINE_COLORS = ['#6366f1','#10b981','#f59e0b','#ec4899','#06b6d4','#84cc16'];

function stStatus(msg) { document.getElementById('st-status').textContent = msg; }

// ── Missions laden ───────────────────────────────────────────────────────────
fetch('/api/lab/missions')
  .then(r => r.json())
  .then(missions => {
    const sel = document.getElementById('st-mission-sel');
    missions.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = `${m.title} [${m.final_state}] ${new Date(m.started_at*1000).toLocaleTimeString()}`;
      sel.appendChild(opt);
    });
    // Letzte Mission auto-laden
    if (missions.length > 0) {
      sel.value = missions[missions.length-1].id;
      window._stLoad();
    }
  });

window._stLoad = async function() {
  const mid = document.getElementById('st-mission-sel').value;
  if (!mid) return;
  stStatus('Lade…');

  const [stData, tempData] = await Promise.all([
    fetch(`/api/lab/missions/${mid}/spacetime`).then(r=>r.json()),
    fetch(`/api/lab/missions/${mid}/temporal`).then(r=>r.json()),
  ]);

  stStatus(`${stData.total_messages} Messages`);
  renderStats(tempData);
  renderSVG(stData);
};

// ── Stats-Karten ─────────────────────────────────────────────────────────────
function renderStats(t) {
  const container = document.getElementById('st-stats');
  const dur = t.wall_duration_sec ? t.wall_duration_sec.toFixed(2) + 's' : '—';
  const agents = Object.keys(t.agent_eigenzeit || {}).length;
  const drifts = (t.drift_notes || []).length;

  container.innerHTML = `
    <div class="st-stat"><div class="st-stat-label">Wand-Dauer</div><div class="st-stat-val">${dur}</div></div>
    <div class="st-stat"><div class="st-stat-label">Agenten</div><div class="st-stat-val">${agents}</div></div>
    <div class="st-stat"><div class="st-stat-label">Drift-Beob.</div><div class="st-stat-val" style="color:${drifts>0?'#f87171':'#10b981'}">${drifts}</div></div>
    <div class="st-stat"><div class="st-stat-label">Final State</div><div class="st-stat-val" style="color:${t.final_state==='completed'?'#10b981':'#f87171'}">${t.final_state||'?'}</div></div>
  `;
}

// ── Spacetime SVG ─────────────────────────────────────────────────────────────
function renderSVG(data) {
  const svg = document.getElementById('st-svg');
  const agents = data.agents;
  if (!agents.length) { svg.innerHTML = '<text x="50" y="50" fill="#6b7280" font-size="14">Keine Daten</text>'; return; }

  const PAD = { top: 40, bottom: 30, left: 20, right: 20 };
  const COL_W = Math.max(160, Math.floor(820 / agents.length));
  const W = PAD.left + agents.length * COL_W + PAD.right;
  const H = 500;
  const PLOT_H = H - PAD.top - PAD.bottom;

  svg.setAttribute('width', W);
  svg.setAttribute('height', H);

  // Max eigenzeit
  const allEZ = data.nodes.map(n => n.eigenzeit).concat(data.edges.flatMap(e => [e.from_ez, e.to_ez]));
  const maxEZ = Math.max(...allEZ, 1);

  // Koordinaten
  const agentX = {};
  agents.forEach((a, i) => { agentX[a] = PAD.left + i * COL_W + COL_W / 2; });

  function ezY(ez) { return PAD.top + PLOT_H * (1 - ez / maxEZ); }

  let html = `
  <defs>
    <marker id="arr-ord" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
      <path d="M0,0 L6,3 L0,6 Z" fill="${ORDERED_COLOR}" opacity="0.7"/>
    </marker>
    <marker id="arr-drift" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
      <path d="M0,0 L6,3 L0,6 Z" fill="${DRIFT_COLOR}" opacity="0.9"/>
    </marker>
    <filter id="glow">
      <feGaussianBlur stdDeviation="2" result="coloredBlur"/>
      <feMerge><feMergeNode in="coloredBlur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>

  <!-- Hintergrund -->
  <rect width="${W}" height="${H}" fill="#0d0d1e"/>

  <!-- Grid-Linien (horizontale Eigenzeit-Marker) -->
  `;

  // Horizontale Grid-Linien
  for (let ez = 0; ez <= maxEZ; ez += Math.max(1, Math.floor(maxEZ/8))) {
    const y = ezY(ez);
    html += `<line x1="${PAD.left}" y1="${y}" x2="${W-PAD.right}" y2="${y}" stroke="#1e1e3a" stroke-width="1"/>`;
    html += `<text x="${PAD.left-2}" y="${y+4}" font-size="9" fill="#374151" text-anchor="end">${ez}</text>`;
  }

  // Weltlinien (vertikale Linien pro Agent)
  agents.forEach((a, i) => {
    const x = agentX[a];
    const color = WORLDLINE_COLORS[i % WORLDLINE_COLORS.length];
    // Schattige Weltlinie
    html += `<line x1="${x}" y1="${PAD.top}" x2="${x}" y2="${H-PAD.bottom}" stroke="${color}" stroke-width="1.5" opacity="0.3"/>`;
    // Agent-Label oben
    const label = a.replace('lab:', '');
    html += `<rect x="${x-40}" y="4" width="80" height="20" rx="4" fill="${color}22"/>`;
    html += `<text x="${x}" y="18" font-size="12" font-weight="600" fill="${color}" text-anchor="middle" font-family="monospace">${label}</text>`;
  });

  // Edges (Message-Pfeile)
  data.edges.forEach(e => {
    const fa = e.from_agent, ta = e.to_agent;
    if (!agentX[fa] || !agentX[ta]) return;
    const x1 = agentX[fa], y1 = ezY(e.from_ez);
    const x2 = agentX[ta], y2 = ezY(e.to_ez);
    const isDrift = e.relation && e.relation.toLowerCase().includes('drift');
    const color = isDrift ? DRIFT_COLOR : ORDERED_COLOR;
    const dash = isDrift ? '5,4' : '';
    const marker = isDrift ? 'arr-drift' : 'arr-ord';
    const opacity = isDrift ? 1.0 : 0.55;
    // Bezier-Kurve für schöne Pfeile
    const cx = (x1 + x2) / 2, cy1 = y1, cy2 = y2;
    html += `<path d="M${x1},${y1} C${cx},${cy1} ${cx},${cy2} ${x2},${y2}"
      fill="none" stroke="${color}" stroke-width="${isDrift?2:1.2}"
      stroke-dasharray="${dash}" opacity="${opacity}"
      marker-end="url(#${marker})"/>`;
  });

  // Nodes (Event-Punkte)
  const tooltip = (n) => `${n.label}\\n${n.payload_hint}\\nez=${n.eigenzeit}`;
  data.nodes.forEach(n => {
    if (!agentX[n.agent]) return;
    const x = agentX[n.agent], y = ezY(n.eigenzeit);
    const color = MSG_COLOR[n.type] || '#888';
    html += `<circle cx="${x}" cy="${y}" r="5" fill="${color}" filter="url(#glow)" opacity="0.9">
      <title>${tooltip(n)}</title>
    </circle>`;
  });

  // Y-Achsen-Label
  html += `<text x="10" y="${H/2}" font-size="11" fill="#374151" transform="rotate(-90,10,${H/2})" text-anchor="middle">Eigenzeit (ops)</text>`;

  svg.innerHTML = html;
}

// ── Scheduler-Empfehlung ─────────────────────────────────────────────────────
window._stRecommend = async function() {
  const box = document.getElementById('st-recommend');
  box.style.display = 'block';
  box.innerHTML = '<h3>🎯 Drift-Kompensierter Scheduler</h3><p style="color:#6b7280;font-size:12px">Lade…</p>';

  const data = await fetch('/api/lab/scheduler/recommend').then(r=>r.json());
  if (data.error) { box.innerHTML = `<h3>Fehler</h3><p>${data.error}</p>`; return; }

  const cards = (data.all_candidates || []).map(c => {
    const isRec = c.agent === data.recommended;
    const driftBadge = c.drift_score < 0.2
      ? '<span class="st-gamma-badge badge-ok">kein Drift</span>'
      : c.gamma > 1.5
        ? '<span class="st-gamma-badge badge-fast">schnell</span>'
        : '<span class="st-gamma-badge badge-drift">Drift</span>';
    return `<div class="st-gamma-card" style="${isRec?'border-color:#6366f1;background:#1a1a40':''}">
      <div class="st-gamma-name">${c.agent}${isRec?' ★':''}</div>
      <div class="st-gamma-vals">
        γ = ${c.gamma.toFixed(3)}<br>
        rate = ${c.avg_rate.toFixed(3)} ops/s<br>
        drift = ${c.drift_score.toFixed(3)}<br>
        ${c.busy ? '🔴 busy' : '🟢 frei'} ${driftBadge}
      </div>
    </div>`;
  }).join('');

  box.innerHTML = `
    <h3>🎯 Drift-Kompensierter Scheduler — γ_ij Frame-Transformation</h3>
    <p style="font-size:12px;color:#6b7280;margin-bottom:10px">
      Empfehlung: <strong style="color:#a5b4fc">${data.recommended || '—'}</strong>
      &nbsp;|&nbsp; ${data.note}
    </p>
    <div class="st-gamma-row">${cards}</div>
  `;
};
</script>
""")
