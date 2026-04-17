"""
ui/pages/ltx_batch.py — LTX 2.3 Batch Video Renderer.
WAV + Bild → automatische Segment-Aufteilung + Prompt-Generierung + ComfyUI Render.
Komplett JS-basiert (umgeht NiceGUI core.loop Bug).
"""
from nicegui import ui
from ui.theme import apply_theme

_PAGE_JS = r"""
<script>
(function() {
  const API = '/api/ltx-batch';
  let es = null;

  function qs(sel) { return document.querySelector(sel); }

  function log(msg, cls='') {
    const el = qs('#ltx-log');
    if (!el) return;
    const line = document.createElement('div');
    line.className = 'ltx-log-line' + (cls ? ' ' + cls : '');
    line.textContent = msg;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
  }

  function showPrompts(items) {
    const el = qs('#ltx-prompts');
    if (!el) return;
    el.innerHTML = '';
    items.forEach(({segment, text}) => {
      const d = document.createElement('div');
      d.className = 'ltx-prompt-item';
      d.innerHTML = `<span class="ltx-seg-badge">Seg ${segment}</span><span class="ltx-prompt-text">${text}</span>`;
      el.appendChild(d);
    });
  }

  const _prompts = [];

  function addVideo(segment, total, url, prompt) {
    const el = qs('#ltx-videos');
    if (!el) return;
    const card = document.createElement('div');
    card.className = 'ltx-video-card';
    card.innerHTML = `
      <div class="ltx-video-header">Segment ${segment} / ${total}</div>
      <video controls autoplay muted loop class="ltx-video-el">
        <source src="${url}" type="video/mp4">
      </video>
      <div class="ltx-video-prompt">${prompt}</div>
      <a href="${url}" download="segment_${segment}.mp4" class="ltx-dl-btn">⬇ Download</a>
    `;
    el.appendChild(card);
  }

  function setRunning(running) {
    const btn = qs('#ltx-start-btn');
    const spinner = qs('#ltx-spinner');
    if (btn) btn.disabled = running;
    if (spinner) spinner.style.display = running ? 'flex' : 'none';
  }

  // ── localStorage persistence ──────────────────────────────────────────────
  const LS_KEY = 'ltx_defaults';
  function saveDefaults() {
    localStorage.setItem(LS_KEY, JSON.stringify({
      concept:      qs('#ltx-concept').value,
      ollama_model: qs('#ltx-model').value,
      chunk_sec:    qs('#ltx-chunk').value,
    }));
  }
  function loadDefaults() {
    try {
      const d = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
      if (d.concept      !== undefined) qs('#ltx-concept').value = d.concept;
      if (d.ollama_model !== undefined) qs('#ltx-model').value   = d.ollama_model;
      if (d.chunk_sec    !== undefined) qs('#ltx-chunk').value   = d.chunk_sec;
    } catch(e) {}
  }
  document.addEventListener('DOMContentLoaded', loadDefaults);
  // Event-Listener nach DOM-Aufbau setzen (kein inline onclick wegen Vue-Sanitizing)
  setTimeout(() => {
    ['#ltx-concept','#ltx-model','#ltx-chunk'].forEach(sel => {
      const el = qs(sel);
      if (el) el.addEventListener('change', saveDefaults);
    });
    qs('#ltx-concept')?.addEventListener('input', saveDefaults);
    qs('#ltx-start-btn')?.addEventListener('click', window._ltxStart);
    loadDefaults();
  }, 300);

  window._ltxStart = async function() {
    const wavFile = qs('#ltx-wav-input').files[0];
    const imgFile = qs('#ltx-img-input').files[0];
    const concept = qs('#ltx-concept').value.trim();
    const ollama_model = qs('#ltx-model').value;
    const chunk_sec = qs('#ltx-chunk').value;

    if (!wavFile) { log('⚠ Bitte WAV-Datei auswählen', 'ltx-warn'); return; }
    if (!imgFile) { log('⚠ Bitte Bild auswählen', 'ltx-warn'); return; }

    saveDefaults();

    // reset
    qs('#ltx-log').innerHTML = '';
    qs('#ltx-prompts').innerHTML = '';
    qs('#ltx-videos').innerHTML = '';
    _prompts.length = 0;
    if (es) { es.close(); es = null; }
    setRunning(true);

    const fd = new FormData();
    fd.append('wav', wavFile);
    fd.append('image', imgFile);
    fd.append('concept', concept || '');
    fd.append('ollama_model', ollama_model);
    fd.append('chunk_sec', chunk_sec);

    log('📤 Upload läuft...');
    let job_id;
    try {
      const r = await fetch(API + '/start', { method: 'POST', body: fd });
      if (!r.ok) { const t = await r.text(); throw new Error(t); }
      const data = await r.json();
      job_id = data.job_id;
      log('✅ Job gestartet: ' + job_id.substring(0,8) + '...');
    } catch(e) {
      log('❌ Fehler: ' + e.message, 'ltx-error');
      setRunning(false);
      return;
    }

    es = new EventSource(API + '/progress/' + job_id);

    es.onmessage = function(e) {
      const msg = JSON.parse(e.data);
      switch(msg.event) {
        case 'status':
          log('ℹ ' + msg.msg);
          break;
        case 'prompt':
          _prompts.push({segment: msg.segment, text: msg.text});
          showPrompts(_prompts);
          break;
        case 'segment_done':
          log('🎬 Segment ' + msg.segment + '/' + msg.total + ' fertig!', 'ltx-ok');
          addVideo(msg.segment, msg.total, msg.url, msg.prompt);
          break;
        case 'segment_error':
          log('⚠ Segment ' + msg.segment + ': ' + msg.msg, 'ltx-warn');
          break;
        case 'error':
          log('❌ ' + msg.msg, 'ltx-error');
          setRunning(false);
          es.close();
          break;
        case 'complete':
          log('🎉 Alle ' + msg.total + ' Segmente gerendert!', 'ltx-ok');
          setRunning(false);
          es.close();
          break;
        case 'done':
          setRunning(false);
          if (es) es.close();
          break;
      }
    };

    es.onerror = function() {
      log('⚠ SSE-Verbindung unterbrochen', 'ltx-warn');
      setRunning(false);
      if (es) es.close();
    };
  };
})();
</script>
"""

_PAGE_CSS = """
<style>
body { background: #050a06 !important; color: #e2e8f0; margin: 0; font-family: system-ui, sans-serif; }
.ltx-wrap { display: flex; flex-direction: column; gap: 20px; padding: 24px; max-width: 1100px; margin: 0 auto; }
.ltx-title { font-size: 22px; font-weight: 700; color: #4ade80; margin-bottom: 4px; }
.ltx-sub { font-size: 13px; color: #6b7280; margin-bottom: 12px; }
.ltx-card { background: #0d1f0e; border: 1px solid #1a3a1a; border-radius: 12px; padding: 20px; }
.ltx-card-title { font-size: 14px; font-weight: 600; color: #4ade80; margin-bottom: 12px; letter-spacing: 0.05em; text-transform: uppercase; }
.ltx-row { display: flex; gap: 16px; flex-wrap: wrap; }
.ltx-field { display: flex; flex-direction: column; gap: 6px; flex: 1; min-width: 200px; }
.ltx-label { font-size: 12px; color: #9ca3af; font-weight: 500; }
.ltx-file-input, .ltx-textarea, .ltx-select {
  background: #070d08; border: 1px solid #1a3a1a; border-radius: 8px;
  color: #e2e8f0; padding: 8px 12px; font-size: 13px; width: 100%; box-sizing: border-box;
}
.ltx-textarea { min-height: 80px; resize: vertical; font-family: inherit; }
.ltx-select { cursor: pointer; appearance: none; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%234ade80' d='M6 8L1 3h10z'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 10px center; padding-right: 28px; }
.ltx-file-input::-webkit-file-upload-button {
  background: #14532d; color: #4ade80; border: none; border-radius: 6px;
  padding: 4px 10px; cursor: pointer; font-size: 12px; margin-right: 8px;
}
.ltx-start-wrap { display: flex; align-items: center; gap: 14px; }
.ltx-start-btn {
  background: linear-gradient(135deg, #166534, #14532d);
  color: #4ade80; border: 1px solid #166534; border-radius: 8px;
  padding: 10px 28px; font-size: 14px; font-weight: 600; cursor: pointer;
  transition: opacity 0.2s;
}
.ltx-start-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.ltx-spinner { display: none; align-items: center; gap: 8px; color: #4ade80; font-size: 13px; }
.ltx-spinner-dot { width: 8px; height: 8px; background: #4ade80; border-radius: 50%; animation: ltxpulse 1s infinite alternate; }
@keyframes ltxpulse { from { opacity: 0.3; } to { opacity: 1; } }
.ltx-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 700px) { .ltx-cols { grid-template-columns: 1fr; } }
#ltx-log {
  background: #050a06; border-radius: 8px; padding: 12px; height: 180px;
  overflow-y: auto; font-size: 12px; font-family: monospace;
}
.ltx-log-line { padding: 1px 0; color: #9ca3af; }
.ltx-log-line.ltx-ok { color: #4ade80; }
.ltx-log-line.ltx-warn { color: #fbbf24; }
.ltx-log-line.ltx-error { color: #f87171; }
#ltx-prompts { display: flex; flex-direction: column; gap: 6px; max-height: 200px; overflow-y: auto; }
.ltx-prompt-item { display: flex; gap: 8px; align-items: flex-start; font-size: 12px; }
.ltx-seg-badge {
  background: #14532d; color: #4ade80; border-radius: 4px;
  padding: 1px 6px; font-size: 11px; white-space: nowrap; flex-shrink: 0;
}
.ltx-prompt-text { color: #d1d5db; line-height: 1.4; }
#ltx-videos { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; margin-top: 4px; }
.ltx-video-card { background: #050a06; border: 1px solid #1a3a1a; border-radius: 10px; overflow: hidden; }
.ltx-video-header { padding: 8px 12px; font-size: 12px; font-weight: 600; color: #4ade80; background: #0d1f0e; }
.ltx-video-el { width: 100%; display: block; }
.ltx-video-prompt { padding: 8px 12px; font-size: 11px; color: #6b7280; line-height: 1.4; }
.ltx-dl-btn {
  display: block; margin: 0 12px 10px; text-align: center;
  background: #14532d; color: #4ade80; border-radius: 6px; padding: 5px;
  font-size: 12px; text-decoration: none;
}
.ltx-dl-btn:hover { background: #166534; }
</style>
"""


@ui.page("/ltx-batch")
def ltx_batch_page():
    apply_theme()
    ui.add_head_html(_PAGE_CSS)
    ui.add_head_html(_PAGE_JS)

    ui.html("""
<div class="ltx-wrap">
  <div>
    <div class="ltx-title">🎬 LTX 2.3 Batch Renderer</div>
    <div class="ltx-sub">WAV + Bild → automatische Segment-Aufteilung → Prompt-Generierung → ComfyUI Render</div>
  </div>

  <!-- Eingaben -->
  <div class="ltx-card">
    <div class="ltx-card-title">Eingaben</div>
    <div class="ltx-row">
      <div class="ltx-field">
        <label class="ltx-label">WAV-Datei (wird in 9s-Blöcke aufgeteilt)</label>
        <input type="file" id="ltx-wav-input" accept=".wav,audio/wav" class="ltx-file-input">
      </div>
      <div class="ltx-field">
        <label class="ltx-label">Start-Bild (wird für alle Segmente verwendet)</label>
        <input type="file" id="ltx-img-input" accept="image/*" class="ltx-file-input">
      </div>
    </div>
    <div class="ltx-field" style="margin-top:12px">
      <label class="ltx-label">Video-Konzept / Idee (Ollama generiert daraus die Segment-Prompts)</label>
      <textarea id="ltx-concept" class="ltx-textarea"
        placeholder="z.B. Ein sprechender Kaktus-Charakter erklärt eine Roadtrip-Geschichte...">Der Mann erzählt seine Geschichte... nach 3 Sekunden wechsel zu Superman im Flug... Zoom auf Supermanns Gesicht</textarea>
    </div>
    <div class="ltx-row" style="margin-top:12px">
      <div class="ltx-field">
        <label class="ltx-label">Ollama Modell</label>
        <select id="ltx-model" class="ltx-select">
          <option value="gemma4:e4b" selected>gemma4:e4b</option>
          <option value="gemma3:4b">gemma3:4b</option>
          <option value="llama3.2:3b">llama3.2:3b</option>
          <option value="mistral:7b">mistral:7b</option>
          <option value="qwen2.5:7b">qwen2.5:7b</option>
        </select>
      </div>
      <div class="ltx-field">
        <label class="ltx-label">Segment-Länge (Sekunden)</label>
        <select id="ltx-chunk" class="ltx-select">
          <option value="5">5s</option>
          <option value="7">7s</option>
          <option value="9" selected>9s (Standard)</option>
          <option value="12">12s</option>
          <option value="15">15s</option>
        </select>
      </div>
    </div>
    <div class="ltx-start-wrap" style="margin-top:14px">
      <button id="ltx-start-btn" class="ltx-start-btn">▶ Batch starten</button>
      <div id="ltx-spinner" class="ltx-spinner">
        <div class="ltx-spinner-dot"></div>
        <span>Rendering läuft...</span>
      </div>
    </div>
  </div>

  <!-- Log + Prompts -->
  <div class="ltx-cols">
    <div class="ltx-card">
      <div class="ltx-card-title">Log</div>
      <div id="ltx-log"></div>
    </div>
    <div class="ltx-card">
      <div class="ltx-card-title">Generierte Prompts</div>
      <div id="ltx-prompts"><span style="color:#374151;font-size:12px">Noch keine Prompts...</span></div>
    </div>
  </div>

  <!-- Video-Ergebnisse -->
  <div class="ltx-card">
    <div class="ltx-card-title">Ergebnisse</div>
    <div id="ltx-videos"><span style="color:#374151;font-size:12px">Noch keine Videos gerendert...</span></div>
  </div>
</div>
""")
