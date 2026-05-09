"""
lab/ui/dilation_demo_page.py — Time Dilation Demo UI (NiceGUI, JS-only).

URL: /dilation-demo
Zeigt zwei parallele LLM-Agenten mit verschiedenen γ-Faktoren live,
CDC-Clock-Vergleich und Frame-Transformation.
"""
from nicegui import ui
from ui.layout import create_layout

_CSS = """
<style>
body { background: #050a06 !important; }

.td-wrap { padding: 18px 20px; max-width: 1400px; margin: 0 auto; }

.td-header { margin-bottom: 18px; }
.td-title { font-size: 18px; font-weight: 700; color: #b8d4b8; font-family: monospace; }
.td-sub { font-size: 11px; color: #3a5a3a; margin-top: 3px; font-family: monospace; }

/* prompt bar */
.td-prompt-row { display: flex; gap: 10px; margin-bottom: 18px; align-items: flex-end; }
.td-inp {
  flex: 1; background: #070d08; border: 1px solid #0f2010;
  border-radius: 8px; color: #e2e8f0; padding: 9px 14px;
  font-size: 13px; font-family: monospace; outline: none;
}
.td-inp:focus { border-color: #00e676; }
.td-run {
  background: #14532d; color: #4ade80; border: 1px solid #166534;
  border-radius: 8px; padding: 9px 22px; font-size: 13px;
  font-family: monospace; cursor: pointer; white-space: nowrap;
  transition: background .15s;
}
.td-run:hover { background: #166534; }
.td-run:disabled { opacity: .4; cursor: not-allowed; }

/* two-col grid */
.td-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; }

/* agent card */
.td-agent {
  background: #070d08; border: 1px solid #0f2010;
  border-radius: 10px; padding: 14px; display: flex; flex-direction: column; gap: 10px;
  min-height: 320px;
}
.td-agent.agent-a { border-color: rgba(0,230,118,.25); }
.td-agent.agent-b { border-color: rgba(59,130,246,.25); }

.td-agent-hdr { display: flex; justify-content: space-between; align-items: center; }
.td-agent-name { font-size: 12px; font-weight: 700; letter-spacing: .08em; font-family: monospace; }
.td-agent-a .td-agent-name { color: #00e676; }
.td-agent-b .td-agent-name { color: #3b82f6; }
.td-agent-meta { font-size: 10px; color: #3a5a3a; font-family: monospace; }

/* eigenzeit display */
.td-ez-row { display: flex; gap: 16px; }
.td-ez-box { background: #0a150b; border: 1px solid #0f2010; border-radius: 6px; padding: 6px 10px; flex: 1; }
.td-ez-val { font-size: 22px; font-weight: 700; font-family: monospace; }
.td-ez-val-a { color: #00e676; }
.td-ez-val-b { color: #3b82f6; }
.td-ez-lbl { font-size: 9px; color: #3a5a3a; text-transform: uppercase; letter-spacing: .5px; }

/* tau bar */
.td-tau-track { background: #0a150b; border: 1px solid #0f2010; border-radius: 3px; height: 6px; overflow: hidden; }
.td-tau-fill-a { height: 100%; background: #00e676; width: 0%; transition: width .2s; border-radius: 3px; }
.td-tau-fill-b { height: 100%; background: #3b82f6; width: 0%; transition: width .2s; border-radius: 3px; }

/* response box */
.td-resp {
  background: #0a150b; border: 1px solid #0f2010; border-radius: 6px;
  padding: 10px 12px; flex: 1; font-size: 11px; font-family: monospace;
  color: #b8d4b8; line-height: 1.7; white-space: pre-wrap; overflow-y: auto;
  min-height: 120px; max-height: 200px;
}
.td-resp-placeholder { color: #1a3a1a; font-style: italic; }

.td-tokens { font-size: 9px; color: #3a5a3a; font-family: monospace; }

/* CDC panel */
.td-cdc {
  background: #070d08; border: 1px solid #0f2010;
  border-radius: 10px; padding: 16px; display: none;
}
.td-cdc.visible { display: block; }
.td-cdc-title { font-size: 11px; font-weight: 700; color: #b8d4b8; letter-spacing: .06em; text-transform: uppercase; margin-bottom: 14px; font-family: monospace; }

.td-cdc-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-bottom: 14px; }
.td-cdc-card { background: #0a150b; border: 1px solid #0f2010; border-radius: 8px; padding: 12px; }
.td-cdc-card-title { font-size: 9px; color: #3a5a3a; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 8px; font-family: monospace; }

.td-relation-badge {
  display: inline-block; padding: 4px 12px; border-radius: 20px;
  font-size: 11px; font-weight: 700; font-family: monospace; letter-spacing: .05em;
}
.rel-ordered           { background: rgba(0,200,83,.15); color: #00c853; border: 1px solid #00c85333; }
.rel-causal_drift      { background: rgba(255,193,7,.12); color: #ffc107; border: 1px solid #ffc10733; }
.rel-concurrent_drift  { background: rgba(255,152,0,.12); color: #ff9800; border: 1px solid #ff980033; }
.rel-inconsistent      { background: rgba(244,67,54,.15); color: #f44336; border: 1px solid #f4433633; }

.td-tau-compare { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
.td-tau-bar-wrap { flex: 1; }
.td-tau-bar-label { font-size: 9px; color: #3a5a3a; font-family: monospace; margin-bottom: 3px; }
.td-tau-bar-track { background: #0a150b; height: 14px; border-radius: 4px; overflow: hidden; display: flex; }
.td-tau-seg-a { background: rgba(0,230,118,.7); height: 100%; }
.td-tau-seg-gap { background: rgba(255,152,0,.4); height: 100%; }

.td-formula { font-size: 10px; color: #6b7280; font-family: monospace; background: #0a150b; border: 1px solid #0f2010; border-radius: 6px; padding: 10px; margin-top: 10px; line-height: 1.8; }
.td-formula .f-highlight { color: #fbbf24; }
.td-formula .f-green   { color: #00e676; }
.td-formula .f-blue    { color: #3b82f6; }
</style>
"""

_JS = """
<script>
const API = '/api/lab/dilation/run';
let running = false;

function esc(s){ return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

const state = {
  tokA: 0, tokB: 0, tauA: 0, tauB: 0, msA: 0, msB: 0,
  respA: '', respB: '',
};

function resetState(){
  Object.assign(state, {tokA:0,tokB:0,tauA:0,tauB:0,msA:0,msB:0,respA:'',respB:''});
  document.getElementById('resp-a').innerHTML = '<span class="td-resp-placeholder">Wartet auf Start…</span>';
  document.getElementById('resp-b').innerHTML = '<span class="td-resp-placeholder">Wartet auf Start…</span>';
  document.getElementById('tau-val-a').textContent = '0.0';
  document.getElementById('tau-val-b').textContent = '0.0';
  document.getElementById('tok-a').textContent = '0 Token · 0ms';
  document.getElementById('tok-b').textContent = '0 Token · 0ms';
  document.getElementById('tau-fill-a').style.width = '0%';
  document.getElementById('tau-fill-b').style.width = '0%';
  document.getElementById('ms-a').textContent = '0ms';
  document.getElementById('ms-b').textContent = '0ms';
  document.getElementById('td-cdc').classList.remove('visible');
}

function handleToken(e){
  const isA = e.agent === 'A';
  if(isA){ state.tokA++; state.tauA = e.tau; state.msA = e.ms; state.respA += e.tok; }
  else    { state.tokB++; state.tauB = e.tau; state.msB = e.ms; state.respB += e.tok; }

  const TAU_MAX = 400;
  if(isA){
    document.getElementById('resp-a').textContent = state.respA;
    document.getElementById('resp-a').scrollTop = 9999;
    document.getElementById('tau-val-a').textContent = e.tau.toFixed(1);
    document.getElementById('tok-a').textContent = state.tokA + ' Token · ' + e.ms + 'ms';
    document.getElementById('ms-a').textContent = e.ms + 'ms';
    document.getElementById('tau-fill-a').style.width = Math.min(100, e.tau / TAU_MAX * 100) + '%';
  } else {
    document.getElementById('resp-b').textContent = state.respB;
    document.getElementById('resp-b').scrollTop = 9999;
    document.getElementById('tau-val-b').textContent = e.tau.toFixed(1);
    document.getElementById('tok-b').textContent = state.tokB + ' Token · ' + e.ms + 'ms';
    document.getElementById('ms-b').textContent = e.ms + 'ms';
    document.getElementById('tau-fill-b').style.width = Math.min(100, e.tau / TAU_MAX * 100) + '%';
  }
}

function relLabel(r){
  const map = {
    'causally_and_temporally_ordered': {cls:'rel-ordered', label:'ORDERED'},
    'causally_ordered_temporally_divergent': {cls:'rel-causal_drift', label:'CAUSAL DRIFT'},
    'concurrent_with_divergence': {cls:'rel-concurrent_drift', label:'CONCURRENT DRIFT'},
    'inconsistent': {cls:'rel-inconsistent', label:'INCONSISTENT'},
  };
  return map[r] || {cls:'rel-concurrent_drift', label: r};
}

function handleCDC(e){
  const rl = relLabel(e.relation);
  const TAU_MAX = Math.max(e.tau_a, e.tau_b, 1);
  const widthA = (e.tau_a / TAU_MAX * 100).toFixed(1);
  const gapW   = (Math.abs(e.tau_b_in_a - e.tau_a) / TAU_MAX * 100).toFixed(1);

  document.getElementById('td-cdc').classList.add('visible');
  document.getElementById('cdc-relation').innerHTML =
    `<span class="td-relation-badge ${rl.cls}">${rl.label}</span>`;

  document.getElementById('cdc-tau-a').innerHTML = `
    <div class="td-cdc-card-title">Agent A · γ=${e.gamma_a}</div>
    <div style="font-size:20px;font-weight:700;color:#00e676;font-family:monospace">${e.tau_a.toFixed(1)}</div>
    <div style="font-size:9px;color:#3a5a3a;font-family:monospace">Eigenzeit τ_A</div>
    <div style="font-size:9px;color:#3a5a3a;margin-top:4px;font-family:monospace">${state.tokA} Token · ${state.msA}ms Wall</div>`;

  document.getElementById('cdc-tau-b').innerHTML = `
    <div class="td-cdc-card-title">Agent B · γ=${e.gamma_b}</div>
    <div style="font-size:20px;font-weight:700;color:#3b82f6;font-family:monospace">${e.tau_b.toFixed(1)}</div>
    <div style="font-size:9px;color:#3a5a3a;font-family:monospace">Eigenzeit τ_B</div>
    <div style="font-size:9px;color:#3a5a3a;margin-top:4px;font-family:monospace">${state.tokB} Token · ${state.msB}ms Wall</div>`;

  document.getElementById('cdc-transform').innerHTML = `
    <div class="td-cdc-card-title">Frame-Transformation (§3.3)</div>
    <div style="font-size:13px;color:#fbbf24;font-family:monospace;margin-bottom:6px">τ_B→A = ${e.tau_b_in_a.toFixed(1)}</div>
    <div style="font-size:9px;color:#3a5a3a;font-family:monospace">γ_ratio = ${e.gamma_ratio.toFixed(3)}</div>
    <div style="font-size:9px;color:#3a5a3a;font-family:monospace">Δτ = ${e.delta_tau.toFixed(1)}</div>`;

  document.getElementById('cdc-tau-vis').innerHTML = `
    <div class="td-tau-bar-label">Eigenzeit-Vergleich (normiert)</div>
    <div class="td-tau-bar-track" style="height:16px;border-radius:4px;">
      <div class="td-tau-seg-a" style="width:${widthA}%;background:rgba(0,230,118,.7)" title="τ_A = ${e.tau_a.toFixed(1)}"></div>
    </div>
    <div style="margin-top:3px" class="td-tau-bar-track" style="height:16px;border-radius:4px;">
      <div style="width:${(e.tau_b/TAU_MAX*100).toFixed(1)}%;background:rgba(59,130,246,.7);height:100%" title="τ_B = ${e.tau_b.toFixed(1)}"></div>
    </div>
    <div style="margin-top:3px" class="td-tau-bar-track" style="height:16px;border-radius:4px;">
      <div style="width:${(e.tau_b_in_a/TAU_MAX*100).toFixed(1)}%;background:rgba(251,191,36,.6);height:100%" title="τ_B→A = ${e.tau_b_in_a.toFixed(1)}"></div>
    </div>
    <div style="font-size:9px;color:#3a5a3a;font-family:monospace;margin-top:6px;line-height:1.8">
      <span style="color:#00e676">█</span> A = ${e.tau_a.toFixed(1)}τ &nbsp;
      <span style="color:#3b82f6">█</span> B = ${e.tau_b.toFixed(1)}τ &nbsp;
      <span style="color:#fbbf24">█</span> B→A = ${e.tau_b_in_a.toFixed(1)}τ
    </div>`;

  document.getElementById('cdc-formula').innerHTML =
    `<span class="f-highlight">Φ(τ_B, B→A)</span> = τ_B × γ_ratio<br>` +
    `= <span class="f-blue">${e.tau_b.toFixed(1)}</span> × <span class="f-highlight">${e.gamma_ratio.toFixed(3)}</span>` +
    ` = <span class="f-highlight">${e.tau_b_in_a.toFixed(1)}</span><br>` +
    `Relation: <span class="f-highlight">${rl.label}</span> — ` +
    (e.delta_tau > 5
      ? `Δτ = ${e.delta_tau.toFixed(1)} überschreitet Drift-Toleranz → <span style="color:#ff9800">Koordinationsfehler möglich</span>`
      : `Δτ = ${e.delta_tau.toFixed(1)} innerhalb Toleranz → <span style="color:#00e676">kohärente Koordination</span>`);
}

async function runDemo(){
  if(running) return;
  const prompt = document.getElementById('td-prompt').value.trim();
  if(!prompt) return;
  running = true;
  resetState();
  document.getElementById('td-run-btn').disabled = true;
  document.getElementById('td-run-btn').textContent = '▸ LÄUFT…';

  try {
    const res = await fetch(API, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prompt}),
    });
    if(!res.ok){ alert('Fehler: ' + res.status); return; }
    if(!res.body){ alert('Kein Stream'); return; }

    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    while(true){
      const {done, value} = await reader.read();
      if(done) break;
      buf += dec.decode(value, {stream: true});
      const lines = buf.split('\\n');
      buf = lines.pop();
      for(const line of lines){
        if(!line.startsWith('data: ')) continue;
        try{
          const ev = JSON.parse(line.slice(6));
          if(ev.type === 'tok') handleToken(ev);
          else if(ev.type === 'done') { /* handled via tok accumulation */ }
          else if(ev.type === 'cdc') handleCDC(ev);
          else if(ev.type === 'err') console.warn('Agent', ev.agent, ':', ev.msg);
        } catch(e){}
      }
    }
  } catch(e){
    console.error(e);
    alert('Verbindungsfehler: ' + e.message);
  } finally {
    running = false;
    document.getElementById('td-run-btn').disabled = false;
    document.getElementById('td-run-btn').textContent = '▸ STARTEN';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('td-prompt').addEventListener('keydown', e => {
    if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); runDemo(); }
  });
});
</script>
"""

_HTML = """
<div class="td-wrap">
  <div class="td-header">
    <div class="td-title">TIME DILATION DEMO</div>
    <div class="td-sub">
      §3 Agent Proper Time (Eigenzeit) · §3.3 Frame-Transformation · §3.4 Causal-Dilation Clock<br>
      Zwei Agenten · gleicher Prompt · verschiedene γ-Faktoren → sichtbare Eigenzeit-Divergenz
    </div>
  </div>

  <div class="td-prompt-row">
    <input type="text" id="td-prompt" class="td-inp"
      value="Was bedeutet Eigenzeit in einem KI-Agentensystem — und warum ist sie wichtig?"
      placeholder="Prompt eingeben…">
    <button id="td-run-btn" class="td-run" onclick="runDemo()">▸ STARTEN</button>
  </div>

  <div class="td-grid">

    <!-- AGENT A -->
    <div class="td-agent agent-a" id="td-agent-a">
      <div class="td-agent-hdr">
        <span class="td-agent-name" style="color:#00e676">▸ AGENT A — SCHNELL</span>
        <span class="td-agent-meta">gemma3:latest · γ = 1.0 (Referenzrahmen)</span>
      </div>

      <div class="td-ez-row">
        <div class="td-ez-box">
          <div class="td-ez-val td-ez-val-a" id="tau-val-a">0.0</div>
          <div class="td-ez-lbl">Eigenzeit τ_A</div>
        </div>
        <div class="td-ez-box">
          <div style="font-size:18px;font-weight:700;font-family:monospace;color:#3a5a3a" id="ms-a">0ms</div>
          <div class="td-ez-lbl">Wall Clock</div>
        </div>
      </div>

      <div>
        <div class="td-tau-bar-label" style="font-size:9px;color:#3a5a3a;font-family:monospace;margin-bottom:3px">Eigenzeit-Akkumulation</div>
        <div class="td-tau-track"><div class="td-tau-fill-a" id="tau-fill-a"></div></div>
      </div>

      <div class="td-resp td-resp-placeholder" id="resp-a">Wartet auf Start…</div>
      <div class="td-tokens" id="tok-a">0 Token · 0ms</div>
    </div>

    <!-- AGENT B -->
    <div class="td-agent agent-b" id="td-agent-b">
      <div class="td-agent-hdr">
        <span class="td-agent-name" style="color:#3b82f6">▸ AGENT B — TIEF</span>
        <span class="td-agent-meta">gemma4:e4b · γ = 3.5 (3.5× Eigenzeit/Token)</span>
      </div>

      <div class="td-ez-row">
        <div class="td-ez-box">
          <div class="td-ez-val td-ez-val-b" id="tau-val-b">0.0</div>
          <div class="td-ez-lbl">Eigenzeit τ_B</div>
        </div>
        <div class="td-ez-box">
          <div style="font-size:18px;font-weight:700;font-family:monospace;color:#3a5a3a" id="ms-b">0ms</div>
          <div class="td-ez-lbl">Wall Clock</div>
        </div>
      </div>

      <div>
        <div class="td-tau-bar-label" style="font-size:9px;color:#3a5a3a;font-family:monospace;margin-bottom:3px">Eigenzeit-Akkumulation</div>
        <div class="td-tau-track"><div class="td-tau-fill-b" id="tau-fill-b"></div></div>
      </div>

      <div class="td-resp td-resp-placeholder" id="resp-b">Wartet auf Start…</div>
      <div class="td-tokens" id="tok-b">0 Token · 0ms</div>
    </div>

  </div>

  <!-- CDC PANEL -->
  <div class="td-cdc" id="td-cdc">
    <div class="td-cdc-title">
      CAUSAL-DILATION CLOCK — Vergleich (§3.4)
      <span id="cdc-relation" style="margin-left:12px"></span>
    </div>

    <div class="td-cdc-grid">
      <div class="td-cdc-card" id="cdc-tau-a">—</div>
      <div class="td-cdc-card" id="cdc-tau-b">—</div>
      <div class="td-cdc-card" id="cdc-transform">—</div>
    </div>

    <div id="cdc-tau-vis" style="margin-bottom:12px"></div>

    <div class="td-formula" id="cdc-formula">—</div>
  </div>

</div>
"""


@ui.page("/dilation-demo")
def dilation_demo_page():
    create_layout("dilation-demo")
    ui.add_head_html(_CSS)
    ui.add_head_html(_JS)
    ui.html(_HTML)
