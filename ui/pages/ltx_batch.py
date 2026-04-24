"""
ui/pages/ltx_batch.py — LTX 2.3 Batch Video Renderer.
WAV + Bild → Prepare (Segmente + Prompts) → User-Review → Render → ComfyUI.
Komplett JS-basiert (umgeht NiceGUI core.loop Bug).
"""
from nicegui import ui
from ui.theme import apply_theme

_PAGE_JS = r"""
<script>
(function() {
  const API = '/api/ltx-batch';
  const LS_DEFAULTS = 'ltx_defaults';
  const LS_JOB = 'ltx_job';
  let es = null;
  let PREP = null;   // { job_id, total, segments: [{idx, segment, duration, prompt, image_mode, custom_fn}] }

  const qs = (s, r) => (r||document).querySelector(s);
  const qsa = (s, r) => Array.from((r||document).querySelectorAll(s));

  // ── Job-State ─────────────────────────────────────────────────────────────
  function loadJob()    { try { return JSON.parse(localStorage.getItem(LS_JOB) || 'null'); } catch(e) { return null; } }
  function saveJob(j)   { localStorage.setItem(LS_JOB, JSON.stringify(j)); }
  function clearJob()   { localStorage.removeItem(LS_JOB); }

  // ── Log / Videos ──────────────────────────────────────────────────────────
  function log(msg, cls='', persist=true) {
    const el = qs('#ltx-log');
    if (!el) return;
    const line = document.createElement('div');
    line.className = 'ltx-log-line' + (cls ? ' ' + cls : '');
    line.textContent = msg;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
    if (persist) {
      const j = loadJob();
      if (j) { j.logs.push({msg, cls}); saveJob(j); }
    }
  }
  function renderLogs(logs) {
    const el = qs('#ltx-log'); if (!el) return;
    el.innerHTML = '';
    logs.forEach(({msg, cls}) => log(msg, cls, false));
  }
  function renderVideo(segment, total, url, prompt, persist=true) {
    const el = qs('#ltx-videos'); if (!el) return;
    const ph = el.querySelector('.ltx-placeholder'); if (ph) ph.remove();
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
    if (persist) {
      const j = loadJob();
      if (j) { j.videos.push({segment, total, url, prompt}); saveJob(j); }
    }
  }

  function setBusy(which, busy) {
    // which: 'prepare' | 'render'
    const btn = qs(which==='prepare' ? '#ltx-prepare-btn' : '#ltx-render-btn');
    const sp  = qs(which==='prepare' ? '#ltx-prep-spinner' : '#ltx-render-spinner');
    if (btn) btn.disabled = busy;
    if (sp)  sp.style.display = busy ? 'flex' : 'none';
  }

  // ── Defaults ──────────────────────────────────────────────────────────────
  function saveDefaults() {
    localStorage.setItem(LS_DEFAULTS, JSON.stringify({
      concept:      qs('#ltx-concept').value,
      ollama_model: qs('#ltx-model').value,
      chunk_sec:    qs('#ltx-chunk').value,
    }));
  }
  function loadDefaults() {
    try {
      const d = JSON.parse(localStorage.getItem(LS_DEFAULTS) || '{}');
      if (d.concept      !== undefined) qs('#ltx-concept').value = d.concept;
      if (d.ollama_model !== undefined) qs('#ltx-model').value   = d.ollama_model;
      if (d.chunk_sec    !== undefined) qs('#ltx-chunk').value   = d.chunk_sec;
    } catch(e) {}
  }

  // ── Review-Cards: pro Segment eine Card mit Prompt-Textarea + Radio + File ─
  function renderReview() {
    const wrap = qs('#ltx-review');
    const box  = qs('#ltx-review-list');
    if (!wrap || !box || !PREP) return;
    wrap.style.display = 'block';
    // Context (Transkript + Bild-Beschreibung) oben einblenden
    const ctx = qs('#ltx-context');
    if (ctx) {
      const esc = s => (s||'').replace(/</g,'&lt;');
      const t = PREP.transcript ? esc(PREP.transcript) : '<em style="color:#6b7280">— keine Transkription —</em>';
      const d = PREP.image_desc ? esc(PREP.image_desc) : '<em style="color:#6b7280">— keine Bild-Beschreibung —</em>';
      ctx.innerHTML = `
        <div class="ltx-ctx-block">
          <div class="ltx-ctx-label">🎙 Transkript (Whisper)</div>
          <div class="ltx-ctx-text">${t}</div>
        </div>
        <div class="ltx-ctx-block">
          <div class="ltx-ctx-label">🖼 Start-Bild (Vision)</div>
          <div class="ltx-ctx-text">${d}</div>
        </div>
      `;
    }
    box.innerHTML = '';
    PREP.segments.forEach(seg => {
      const c = document.createElement('div');
      c.className = 'ltx-seg-card';
      c.dataset.idx = seg.idx;
      const isFirst = seg.idx === 0;
      const audioHtml = seg.audio_url
        ? `<audio controls class="ltx-seg-audio" src="${seg.audio_url}"></audio>`
        : `<div class="ltx-seg-no-audio">🔇 Audio nicht verfügbar</div>`;
      c.innerHTML = `
        <div class="ltx-seg-head">
          <span class="ltx-seg-badge">Seg ${seg.segment}</span>
          <span class="ltx-seg-dur">${seg.duration}s</span>
        </div>
        ${audioHtml}
        <textarea class="ltx-seg-prompt" rows="3">${seg.prompt.replace(/</g,'&lt;')}</textarea>
        <div class="ltx-seg-modes">
          ${isFirst ? '' : `
          <label class="ltx-mode">
            <input type="radio" name="mode-${seg.idx}" value="prev" ${seg.image_mode==='prev'?'checked':''}>
            <span>🔗 Last-Frame vom Vorgänger</span>
          </label>`}
          <label class="ltx-mode">
            <input type="radio" name="mode-${seg.idx}" value="start" ${seg.image_mode==='start'?'checked':''}>
            <span>🏁 Start-Bild</span>
          </label>
          <label class="ltx-mode">
            <input type="radio" name="mode-${seg.idx}" value="custom" ${seg.image_mode==='custom'?'checked':''}>
            <span>🖼 Eigenes Bild hochladen</span>
          </label>
        </div>
        <div class="ltx-seg-upload" style="display:${seg.image_mode==='custom'?'flex':'none'}">
          <input type="file" accept="image/*" class="ltx-seg-file">
          <span class="ltx-seg-uploaded">${seg.custom_fn ? '✓ '+seg.custom_fn : ''}</span>
        </div>
      `;
      box.appendChild(c);

      // Prompt live in PREP
      qs('.ltx-seg-prompt', c).addEventListener('input', (ev) => {
        seg.prompt = ev.target.value;
      });
      // Mode-Radio
      qsa(`input[name="mode-${seg.idx}"]`, c).forEach(r => {
        r.addEventListener('change', (ev) => {
          seg.image_mode = ev.target.value;
          qs('.ltx-seg-upload', c).style.display = (seg.image_mode==='custom') ? 'flex' : 'none';
        });
      });
      // File → upload-ref sofort hochladen
      qs('.ltx-seg-file', c).addEventListener('change', async (ev) => {
        const f = ev.target.files[0];
        if (!f) return;
        const fd = new FormData();
        fd.append('job_id', PREP.job_id);
        fd.append('idx', String(seg.idx));
        fd.append('image', f);
        qs('.ltx-seg-uploaded', c).textContent = '⏳ hochladen...';
        try {
          const r = await fetch(API + '/upload-ref', { method:'POST', body: fd });
          const data = await r.json();
          if (data.ok) {
            seg.custom_fn = data.custom_fn;
            seg.image_mode = 'custom';
            qs('.ltx-seg-uploaded', c).textContent = '✓ ' + data.custom_fn;
            const rad = qs(`input[name="mode-${seg.idx}"][value="custom"]`, c);
            if (rad) rad.checked = true;
          } else {
            qs('.ltx-seg-uploaded', c).textContent = '❌ ' + (data.error || 'Upload-Fehler');
          }
        } catch(e) {
          qs('.ltx-seg-uploaded', c).textContent = '❌ ' + e.message;
        }
      });
    });
  }

  // ── SSE ───────────────────────────────────────────────────────────────────
  function connectSSE(job_id) {
    if (es) { es.close(); es = null; }
    es = new EventSource(API + '/progress/' + job_id);
    es.onmessage = function(e) {
      const msg = JSON.parse(e.data);
      switch(msg.event) {
        case 'status':
          log('ℹ ' + msg.msg);
          break;
        case 'segment_done':
          log('🎬 Segment ' + msg.segment + '/' + msg.total + ' fertig!', 'ltx-ok');
          renderVideo(msg.segment, msg.total, msg.url, msg.prompt);
          break;
        case 'segment_error':
          log('⚠ Segment ' + msg.segment + ': ' + msg.msg, 'ltx-warn');
          break;
        case 'error':
          log('❌ ' + msg.msg, 'ltx-error');
          setBusy('render', false);
          es.close();
          break;
        case 'complete':
          log('🎉 Alle ' + msg.total + ' Segmente gerendert!', 'ltx-ok');
          setBusy('render', false);
          es.close();
          break;
        case 'done':
          setBusy('render', false);
          if (es) es.close();
          break;
      }
    };
    es.onerror = function() {
      log('⚠ SSE-Verbindung unterbrochen', 'ltx-warn');
      setBusy('render', false);
      if (es) es.close();
    };
  }

  // ── PREPARE ───────────────────────────────────────────────────────────────
  window._ltxPrepare = async function() {
    const wavFile = qs('#ltx-wav-input').files[0];
    const imgFile = qs('#ltx-img-input').files[0];
    const concept = qs('#ltx-concept').value.trim();
    const ollama_model = qs('#ltx-model').value;
    const chunk_sec = qs('#ltx-chunk').value;

    if (!wavFile) { log('⚠ Bitte WAV wählen', 'ltx-warn'); return; }
    if (!imgFile) { log('⚠ Bitte Start-Bild wählen', 'ltx-warn'); return; }
    saveDefaults();

    // Review + Logs + Videos zurücksetzen
    qs('#ltx-log').innerHTML = '';
    qs('#ltx-review-list').innerHTML = '';
    qs('#ltx-videos').innerHTML = '<span class="ltx-placeholder">Noch keine Videos gerendert...</span>';
    qs('#ltx-review').style.display = 'none';
    PREP = null;
    clearJob();

    const fd = new FormData();
    fd.append('wav', wavFile);
    fd.append('image', imgFile);
    fd.append('concept', concept || '');
    fd.append('ollama_model', ollama_model);
    fd.append('chunk_sec', chunk_sec);

    setBusy('prepare', true);
    log('📤 Upload läuft...');
    let jobId = null;
    try {
      const r = await fetch(API + '/prepare', { method:'POST', body: fd });
      if (!r.ok) { const t = await r.text(); throw new Error(t); }
      const data = await r.json();
      jobId = data.job_id;
      log(`🚀 Prepare-Job ${jobId.substring(0,8)}... gestartet — polle Status`);
    } catch(e) {
      log('❌ Upload-Fehler: ' + e.message, 'ltx-error');
      setBusy('prepare', false);
      return;
    }

    // Polling (2s) bis ready oder error. Übersteht Connection-Glitches (Starlink etc.).
    let lastMsg = '';
    const poll = async () => {
      try {
        const r = await fetch(`${API}/prepare-status/${jobId}`);
        if (!r.ok) throw new Error('HTTP '+r.status);
        const s = await r.json();
        if (s.progress_msg && s.progress_msg !== lastMsg) {
          log('ℹ ' + s.progress_msg);
          lastMsg = s.progress_msg;
        }
        if (s.status === 'ready') {
          PREP = s;
          log(`✅ ${s.total} Segmente vorbereitet — Prompts/Bilder reviewen, dann ▶ Rendern`, 'ltx-ok');
          renderReview();
          setBusy('prepare', false);
          return;
        }
        if (s.status === 'error') {
          log('❌ Prepare-Fehler: ' + (s.error || 'unbekannt'), 'ltx-error');
          setBusy('prepare', false);
          return;
        }
        setTimeout(poll, 2000);
      } catch(e) {
        // Netzwerk-Glitch → einfach weiterpollen, nicht aufgeben
        log(`⚠ Poll-Fehler (${e.message}) — versuche in 5s erneut`, 'ltx-warn');
        setTimeout(poll, 5000);
      }
    };
    poll();
  };

  // ── RENDER ────────────────────────────────────────────────────────────────
  window._ltxRender = async function() {
    if (!PREP) { log('⚠ Erst vorbereiten', 'ltx-warn'); return; }
    setBusy('render', true);
    const edits = PREP.segments.map(s => ({
      idx: s.idx,
      prompt: s.prompt,
      image_mode: s.image_mode,
      custom_fn: s.custom_fn || null,
    }));
    // Job-State für Reload-Recovery
    saveJob({ job_id: PREP.job_id, running: true, logs: [], prompts: [], videos: [] });
    log('🚀 Rendering startet...');
    try {
      const r = await fetch(API + '/render', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ job_id: PREP.job_id, segments: edits }),
      });
      if (!r.ok) { const t = await r.text(); throw new Error(t); }
      const data = await r.json();
      if (data.error) throw new Error(data.error);
      connectSSE(PREP.job_id);
    } catch(e) {
      log('❌ Render-Fehler: ' + e.message, 'ltx-error');
      setBusy('render', false);
    }
  };

  // ── Reload-Restore (nur Logs + Videos, nicht PREP) ────────────────────────
  function restoreState() {
    loadDefaults();
    const j = loadJob();
    if (!j) return;
    renderLogs(j.logs || []);
    (j.videos || []).forEach(v => renderVideo(v.segment, v.total, v.url, v.prompt, false));
    if (j.running && j.job_id) {
      setBusy('render', true);
      log('🔄 Verbindung wiederhergestellt...', '', false);
      connectSSE(j.job_id);
    }
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  setTimeout(() => {
    ['#ltx-concept','#ltx-model','#ltx-chunk'].forEach(sel => {
      qs(sel)?.addEventListener('change', saveDefaults);
    });
    qs('#ltx-concept')?.addEventListener('input', saveDefaults);
    qs('#ltx-prepare-btn')?.addEventListener('click', window._ltxPrepare);
    qs('#ltx-render-btn')?.addEventListener('click', window._ltxRender);
    restoreState();
  }, 300);
})();
</script>
"""

_PAGE_CSS = """
<style>
/* Theme-Override: globales html/body overflow:hidden aufheben */
html, body { overflow: auto !important; height: auto !important; }
.q-page-container, .q-page { overflow: visible !important; min-height: unset !important; }
body { background: #050a06 !important; color: #e2e8f0; margin: 0; font-family: system-ui, sans-serif; }
.ltx-wrap { display: flex; flex-direction: column; gap: 20px; padding: 24px; max-width: 1200px; margin: 0 auto; }
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
.ltx-btn-row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.ltx-btn {
  background: linear-gradient(135deg, #166534, #14532d);
  color: #4ade80; border: 1px solid #166534; border-radius: 8px;
  padding: 10px 28px; font-size: 14px; font-weight: 600; cursor: pointer;
  transition: opacity 0.2s;
}
.ltx-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.ltx-btn.primary { background: linear-gradient(135deg, #22c55e, #16a34a); color: #042f11; border-color: #22c55e; }
.ltx-spinner { display: none; align-items: center; gap: 8px; color: #4ade80; font-size: 13px; }
.ltx-spinner-dot { width: 8px; height: 8px; background: #4ade80; border-radius: 50%; animation: ltxpulse 1s infinite alternate; }
@keyframes ltxpulse { from { opacity: 0.3; } to { opacity: 1; } }

/* Context-Box (Transkript + Bild-Beschreibung) */
#ltx-context { display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px; }
.ltx-ctx-block { background: #050a06; border: 1px solid #1a3a1a; border-radius: 8px; padding: 10px 12px; }
.ltx-ctx-label { font-size: 11px; font-weight: 600; color: #4ade80; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
.ltx-ctx-text { font-size: 12px; color: #d1d5db; line-height: 1.5; white-space: pre-wrap; max-height: 140px; overflow-y: auto; }

/* Review-Grid */
#ltx-review-list {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px;
}
.ltx-seg-card {
  background: #050a06; border: 1px solid #1a3a1a; border-radius: 10px;
  padding: 12px; display: flex; flex-direction: column; gap: 10px;
}
.ltx-seg-head { display: flex; align-items: center; gap: 8px; }
.ltx-seg-badge {
  background: #14532d; color: #4ade80; border-radius: 4px;
  padding: 2px 8px; font-size: 11px; font-weight: 600;
}
.ltx-seg-dur { color: #6b7280; font-size: 11px; }
.ltx-seg-prompt {
  width: 100%; box-sizing: border-box;
  background: #070d08; border: 1px solid #1a3a1a; border-radius: 6px;
  color: #e2e8f0; padding: 8px; font-size: 12px; font-family: inherit;
  resize: vertical; min-height: 68px;
}
.ltx-seg-modes { display: flex; flex-direction: column; gap: 4px; }
.ltx-mode {
  display: flex; align-items: center; gap: 6px; font-size: 12px;
  color: #d1d5db; cursor: pointer; padding: 2px 0;
}
.ltx-mode input[type=radio] { accent-color: #4ade80; }
.ltx-seg-audio {
  width: 100%; height: 32px; border-radius: 6px;
  accent-color: #4ade80; background: #070d08;
  filter: invert(1) hue-rotate(100deg) brightness(0.85);
}
.ltx-seg-no-audio { font-size: 11px; color: #374151; }
.ltx-seg-upload { display: flex; align-items: center; gap: 8px; }
.ltx-seg-upload input[type=file] {
  flex: 1; background: #070d08; border: 1px solid #1a3a1a; border-radius: 6px;
  color: #9ca3af; padding: 4px; font-size: 11px;
}
.ltx-seg-uploaded { font-size: 11px; color: #4ade80; }

/* Log + Videos */
#ltx-log {
  background: #050a06; border-radius: 8px; padding: 12px; height: 180px;
  overflow-y: auto; font-size: 12px; font-family: monospace;
}
.ltx-log-line { padding: 1px 0; color: #9ca3af; }
.ltx-log-line.ltx-ok { color: #4ade80; }
.ltx-log-line.ltx-warn { color: #fbbf24; }
.ltx-log-line.ltx-error { color: #f87171; }
.ltx-placeholder { color: #374151; font-size: 12px; }
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
    <div class="ltx-sub">WAV + Bild → Segmente + Prompts vorbereiten → pro Segment entscheiden → rendern</div>
  </div>

  <!-- 1. Eingaben -->
  <div class="ltx-card">
    <div class="ltx-card-title">1 · Eingaben</div>
    <div class="ltx-row">
      <div class="ltx-field">
        <label class="ltx-label">WAV-Datei</label>
        <input type="file" id="ltx-wav-input" accept=".wav,audio/wav" class="ltx-file-input">
      </div>
      <div class="ltx-field">
        <label class="ltx-label">Start-Bild</label>
        <input type="file" id="ltx-img-input" accept="image/*" class="ltx-file-input">
      </div>
    </div>
    <div class="ltx-field" style="margin-top:12px">
      <label class="ltx-label">Video-Konzept (Ollama generiert daraus pro Segment einen Prompt)</label>
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
        <label class="ltx-label">Segment-Länge</label>
        <select id="ltx-chunk" class="ltx-select">
          <option value="5">5s</option>
          <option value="7">7s</option>
          <option value="9" selected>9s (Standard)</option>
          <option value="12">12s</option>
          <option value="15">15s</option>
        </select>
      </div>
    </div>
    <div class="ltx-btn-row" style="margin-top:14px">
      <button id="ltx-prepare-btn" class="ltx-btn">⚙ Vorbereiten</button>
      <div id="ltx-prep-spinner" class="ltx-spinner">
        <div class="ltx-spinner-dot"></div>
        <span>WAV splitten + Prompts generieren...</span>
      </div>
    </div>
  </div>

  <!-- 2. Review (erst nach Prepare sichtbar) -->
  <div id="ltx-review" class="ltx-card" style="display:none">
    <div class="ltx-card-title">2 · Segmente prüfen & anpassen</div>
    <div style="font-size:12px;color:#9ca3af;margin-bottom:10px">
      Pro Segment: Prompt editieren, Bildquelle wählen (Last-Frame des vorigen Segments, Start-Bild, oder eigenes Bild).
    </div>
    <div id="ltx-context"></div>
    <div id="ltx-review-list"></div>
    <div class="ltx-btn-row" style="margin-top:14px">
      <button id="ltx-render-btn" class="ltx-btn primary">▶ Rendern starten</button>
      <div id="ltx-render-spinner" class="ltx-spinner">
        <div class="ltx-spinner-dot"></div>
        <span>Rendering läuft...</span>
      </div>
    </div>
  </div>

  <!-- 3. Log -->
  <div class="ltx-card">
    <div class="ltx-card-title">Log</div>
    <div id="ltx-log"></div>
  </div>

  <!-- 4. Videos -->
  <div class="ltx-card">
    <div class="ltx-card-title">Ergebnisse</div>
    <div id="ltx-videos"><span class="ltx-placeholder">Noch keine Videos gerendert...</span></div>
  </div>
</div>
""")
