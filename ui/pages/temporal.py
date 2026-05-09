"""
ui/pages/temporal.py — Eigenzeit-Drift-Visualisierung (§4.4).

Zeigt:
- Orchestrator-Frame (γ, τ, wall vs. reference)
- Letzte N Tasks mit Frame-Daten und Drift-Sekunden
- Pro Agent gruppiert (collapse-fähig)

Liest read-only aus /api/temporal/* — keine Mutationen.
"""
from nicegui import ui
from ui.layout import create_layout


_TEMPORAL_HTML = """
<style>
  .tmp-wrap { padding: 16px 20px; max-width: 1200px; }
  .tmp-card { background: #0a150b; border: 1px solid #0f2010; border-radius: 10px; padding: 16px 18px; margin-bottom: 16px; }
  .tmp-section { font-size: 11px; font-weight: 700; color: #00e676; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 10px; }
  .tmp-kpi { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }
  .tmp-num { font-size: 20px; font-weight: 700; color: #00e676; font-family: monospace; }
  .tmp-label { font-size: 10px; color: #3a5a3a; text-transform: uppercase; letter-spacing: .5px; margin-top: 4px; }
  .tmp-table { width: 100%; border-collapse: collapse; font-size: 11px; font-family: monospace; }
  .tmp-table th { text-align: left; color: #3a5a3a; font-weight: 600; padding: 4px 8px; border-bottom: 1px solid #0f2010; text-transform: uppercase; letter-spacing: .4px; font-size: 10px; }
  .tmp-table td { padding: 5px 8px; border-bottom: 1px solid #0a150b; color: #b8d4b8; vertical-align: top; }
  .tmp-table tr:hover td { background: #0d1a0e; }
  .tmp-mono { font-family: monospace; color: #b8d4b8; }
  .tmp-mute { color: #3a5a3a; }
  .tmp-drift-ok   { color: #00e676; }
  .tmp-drift-warn { color: #ffeb3b; }
  .tmp-drift-bad  { color: #ef4444; }
  .tmp-refresh { float: right; font-size: 10px; color: #3a5a3a; font-family: monospace; cursor: pointer; padding: 3px 8px; border: 1px solid #0f2010; border-radius: 4px; background: none; }
  .tmp-refresh:hover { color: #00e676; border-color: #00e67644; }
  .tmp-pill { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px; font-family: monospace; background: rgba(0,230,118,.06); color: #00c853; border: 1px solid #00c85322; }
  .tmp-status { font-size: 10px; color: #3a5a3a; font-family: monospace; margin-bottom: 12px; }
  .tmp-empty { color: #3a5a3a; font-style: italic; padding: 16px; text-align: center; }
</style>

<div class="tmp-wrap">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;">
    <span style="font-size:16px;font-weight:700;color:#b8d4b8;font-family:monospace;">EIGENZEIT &amp; DRIFT</span>
    <button class="tmp-refresh" onclick="loadTemporal()">⟳ Aktualisieren</button>
  </div>
  <div id="tmp-status">Lade Daten...</div>

  <div class="tmp-card">
    <div class="tmp-section">Orchestrator-Frame (§3.2)</div>
    <div class="tmp-kpi" id="tmp-orch"></div>
  </div>

  <div class="tmp-card">
    <div class="tmp-section">Letzte Tasks — Frame &amp; Drift (§4.3)</div>
    <table class="tmp-table">
      <thead><tr>
        <th>Agent</th><th>γ</th><th>Frame</th><th>Reference Now</th>
        <th>Wall Clock</th><th>Drift (s)</th><th>Status</th>
      </tr></thead>
      <tbody id="tmp-frames"></tbody>
    </table>
    <div id="tmp-empty" class="tmp-empty" style="display:none">Noch keine Eigenzeit-Frames in der DB.</div>
  </div>
</div>

<script>
function _h(s){ return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function _fmtIso(s){ if(!s) return '-'; const t = String(s); return t.length > 19 ? t.slice(0,19).replace('T',' ') : t.replace('T',' '); }
function _driftClass(d){
  if(d === null || d === undefined) return 'tmp-mute';
  const a = Math.abs(d);
  if(a < 5) return 'tmp-drift-ok';
  if(a < 60) return 'tmp-drift-warn';
  return 'tmp-drift-bad';
}
function _kpi(label, value, mono = true){
  return `<div><div class="tmp-num">${_h(value)}</div><div class="tmp-label">${_h(label)}</div></div>`;
}

async function loadTemporal(){
  document.getElementById('tmp-status').textContent = 'Lade...';
  try {
    const [orchR, framesR] = await Promise.all([
      fetch('/api/temporal/orchestrator'),
      fetch('/api/temporal/frames?limit=100'),
    ]);
    const orch = await orchR.json();
    const fr   = await framesR.json();

    const taus = (orch.tau ?? 0).toFixed(2);
    const orchHtml = [
      _kpi('Agent ID',     orch.agent_id),
      _kpi('γ (Dilation)', (orch.dilation_factor ?? 1.0).toFixed(3)),
      _kpi('τ (Proper Time)', taus),
      _kpi('Wall Now',     _fmtIso(orch.wall_now)),
      _kpi('Reference Now',_fmtIso(orch.reference_now)),
      _kpi('Frame ID',     (orch.frame_id || '').slice(0,12)),
    ].join('');
    document.getElementById('tmp-orch').innerHTML = orchHtml;

    const frames = fr.frames || [];
    const tbody = document.getElementById('tmp-frames');
    if(frames.length === 0){
      tbody.innerHTML = '';
      document.getElementById('tmp-empty').style.display = 'block';
    } else {
      document.getElementById('tmp-empty').style.display = 'none';
      tbody.innerHTML = frames.map(f => {
        const dCls = _driftClass(f.drift_seconds);
        const dTxt = (f.drift_seconds === null || f.drift_seconds === undefined)
          ? '<span class="tmp-mute">-</span>'
          : `<span class="${dCls}">${f.drift_seconds.toFixed(2)}</span>`;
        const gamma = (f.dilation_factor === null || f.dilation_factor === undefined)
          ? '<span class="tmp-mute">-</span>'
          : `<span class="tmp-pill">${f.dilation_factor.toFixed(3)}</span>`;
        const frameTxt = f.frame_id ? `<span class="tmp-mono">${_h(f.frame_id.slice(0,10))}</span>` : '<span class="tmp-mute">-</span>';
        return `<tr>
          <td>${_h(f.agent_name || f.agent_id || '-')}</td>
          <td>${gamma}</td>
          <td>${frameTxt}</td>
          <td>${_fmtIso(f.reference_now)}</td>
          <td>${_fmtIso(f.wall_clock)}</td>
          <td>${dTxt}</td>
          <td>${_h(f.status)}</td>
        </tr>`;
      }).join('');
    }
    document.getElementById('tmp-status').textContent =
      `${frames.length} Frame(s) — geladen ${_fmtIso(fr.now)}`;
  } catch(e) {
    document.getElementById('tmp-status').textContent = 'Fehler: ' + e;
  }
}
window.addEventListener('DOMContentLoaded', loadTemporal);
</script>
"""


@ui.page("/temporal")
def temporal_page():
    create_layout(page_name="temporal")
    ui.add_body_html(_TEMPORAL_HTML)
