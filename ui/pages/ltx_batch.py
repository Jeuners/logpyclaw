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
  const LS_PREP = 'ltx_prep';
  let es = null;
  let PREP = null;   // { job_id, total, segments: [{idx, segment, duration, prompt, image_mode, custom_fn}] }

  const qs = (s, r) => (r||document).querySelector(s);
  const qsa = (s, r) => Array.from((r||document).querySelectorAll(s));

  // ── Job-State ─────────────────────────────────────────────────────────────
  function loadJob()    { try { return JSON.parse(localStorage.getItem(LS_JOB) || 'null'); } catch(e) { return null; } }
  function saveJob(j)   { localStorage.setItem(LS_JOB, JSON.stringify(j)); }
  function clearJob()   { localStorage.removeItem(LS_JOB); }

  // ── PREP-Persistenz (Segmente, Transkript, Bild-Beschreibung) ─────────────
  function loadPrep()   { try { return JSON.parse(localStorage.getItem(LS_PREP) || 'null'); } catch(e) { return null; } }
  function savePrep()   { if (PREP) localStorage.setItem(LS_PREP, JSON.stringify(PREP)); }
  function clearPrep()  { localStorage.removeItem(LS_PREP); }

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
  function refreshConcatButton() {
    const btn = qs('#ltx-concat-btn');
    if (!btn) return;
    const finished = document.querySelectorAll('#ltx-videos .ltx-video-card').length;
    btn.disabled = finished < 2;
    btn.textContent = finished < 2
      ? '🎬 Alle fertigen Segmente zusammenschneiden'
      : `🎬 ${finished} fertige Segmente zusammenschneiden`;
  }

  window._ltxConcat = async function() {
    if (!PREP || !PREP.job_id) return;
    const btn = qs('#ltx-concat-btn');
    const status = qs('#ltx-concat-status');
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ verbinde Segmente...';
    if (status) status.textContent = '';
    try {
      const r = await fetch(API + '/concat/' + PREP.job_id, { method: 'POST' });
      const data = await r.json();
      if (!r.ok || data.error) {
        const detail = data.stderr ? ' — ' + data.stderr.slice(-200) : '';
        log('❌ Concat-Fehler: ' + (data.error || 'unbekannt') + detail, 'ltx-error');
        if (status) status.textContent = data.error || 'Fehler';
        return;
      }
      log(`🎬 Master-MP4 erzeugt aus ${data.segments_used} Segmenten (${(data.size_bytes/1024/1024).toFixed(1)} MB)`, 'ltx-ok');
      const wrap = qs('#ltx-master-video');
      if (wrap) {
        wrap.innerHTML = `
          <div class="ltx-video-card">
            <div class="ltx-video-header">🎬 Master — ${data.segments_used} Segmente</div>
            <video controls preload="metadata" class="ltx-video-el">
              <source src="${data.url}" type="video/mp4">
            </video>
            <div class="ltx-video-actions">
              <a href="${data.url}" download="${data.filename}" class="ltx-dl-btn">⬇ Master-Download (${(data.size_bytes/1024/1024).toFixed(1)} MB)</a>
            </div>
          </div>
        `;
      }
      if (status) status.textContent = `✓ ${data.filename}`;
    } catch(e) {
      log('❌ Concat-Fehler: ' + e.message, 'ltx-error');
      if (status) status.textContent = e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = orig;
      refreshConcatButton();
    }
  };

  function renderVideo(segment, total, url, prompt, persist=true) {
    const el = qs('#ltx-videos'); if (!el) return;
    const ph = el.querySelector('.ltx-placeholder'); if (ph) ph.remove();
    const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    const existing = el.querySelector(`[data-segment="${segment}"]`);
    const card = document.createElement('div');
    card.className = 'ltx-video-card';
    card.dataset.segment = segment;
    card.innerHTML = `
      <div class="ltx-video-header">Segment ${segment} / ${total}</div>
      <video controls preload="metadata" muted loop class="ltx-video-el">
        <source src="${esc(url)}" type="video/mp4">
      </video>
      <div class="ltx-video-prompt">${esc(prompt)}</div>
      <div class="ltx-video-actions">
        <a href="${esc(url)}" download="segment_${segment}.mp4" class="ltx-dl-btn">⬇ Download</a>
        <button class="ltx-btn-mini ltx-video-edit-btn" data-segment="${segment}">✏ Edit &amp; Re-Render</button>
      </div>
    `;
    if (existing) {
      existing.replaceWith(card);
    } else {
      el.appendChild(card);
    }
    // Edit-Button: scrollt zur entsprechenden Review-Card und fokussiert das Prompt-Feld
    const editBtn = card.querySelector('.ltx-video-edit-btn');
    if (editBtn) {
      editBtn.addEventListener('click', () => {
        const idx = segment - 1;
        const reviewCard = document.querySelector(`.ltx-seg-card[data-idx="${idx}"]`);
        if (!reviewCard) {
          log('⚠ Review-Card für Segment ' + segment + ' nicht gefunden', 'ltx-warn');
          return;
        }
        reviewCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
        reviewCard.classList.add('ltx-flash');
        setTimeout(() => reviewCard.classList.remove('ltx-flash'), 1200);
        const ta = reviewCard.querySelector('.ltx-seg-prompt');
        if (ta) { ta.focus(); ta.select(); }
      });
    }
    if (persist) {
      const j = loadJob();
      if (j) {
        j.videos = (j.videos || []).filter(v => v.segment !== segment);
        j.videos.push({segment, total, url, prompt});
        saveJob(j);
      }
    }
    refreshConcatButton();
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
      c.className = 'ltx-seg-card' + (seg.prompt_locked ? ' locked' : '');
      c.dataset.idx = seg.idx;
      const isFirst = seg.idx === 0;
      const audioHtml = seg.audio_url
        ? `<audio controls class="ltx-seg-audio" src="${seg.audio_url}"></audio>`
        : `<div class="ltx-seg-no-audio">🔇 Audio nicht verfügbar</div>`;
      const lockState = seg.prompt_locked ? 'locked' : '';
      const lockLabel = seg.prompt_locked ? '🔒 Prompt gelockt' : '🔓 Auto-Refine an';
      const lockTitle = isFirst
        ? 'Segment 1 wird nicht refined (erstes Bild ist immer das Start-Bild)'
        : 'Wenn aktiv (🔓), wird der Prompt vor dem Render anhand des echten Eingangsbilds verfeinert. Sperren mit 🔒 um den eigenen Wortlaut zu schützen.';
      c.innerHTML = `
        <div class="ltx-seg-head">
          <span class="ltx-seg-badge">Seg ${seg.segment}</span>
          <span class="ltx-seg-dur">${seg.duration}s</span>
        </div>
        ${audioHtml}
        <textarea class="ltx-seg-prompt" rows="3">${seg.prompt.replace(/</g,'&lt;')}</textarea>
        <div class="ltx-refined-note" data-refined-for="${seg.idx}"></div>
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
        <div class="ltx-seg-foot">
          <button class="ltx-lock-btn ${lockState}" title="${lockTitle}" ${isFirst?'disabled':''}>${lockLabel}</button>
          <button class="ltx-btn-mini ltx-seg-rerun-btn">↻ Nur dieses rendern</button>
        </div>
      `;
      box.appendChild(c);

      // Prompt live in PREP
      qs('.ltx-seg-prompt', c).addEventListener('input', (ev) => {
        seg.prompt = ev.target.value;
        savePrep();
      });
      // Mode-Radio
      qsa(`input[name="mode-${seg.idx}"]`, c).forEach(r => {
        r.addEventListener('change', (ev) => {
          seg.image_mode = ev.target.value;
          qs('.ltx-seg-upload', c).style.display = (seg.image_mode==='custom') ? 'flex' : 'none';
          savePrep();
        });
      });
      // 🔒 Lock-Toggle
      qs('.ltx-lock-btn', c).addEventListener('click', () => {
        if (isFirst) return;
        seg.prompt_locked = !seg.prompt_locked;
        const btn = qs('.ltx-lock-btn', c);
        btn.classList.toggle('locked', seg.prompt_locked);
        btn.textContent = seg.prompt_locked ? '🔒 Prompt gelockt' : '🔓 Auto-Refine an';
        c.classList.toggle('locked', seg.prompt_locked);
        savePrep();
      });
      // ↻ Nur dieses Segment rendern
      qs('.ltx-seg-rerun-btn', c).addEventListener('click', () => window._ltxRenderSegment(seg.idx));
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
            savePrep();
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

  // ── prompt_refined SSE-Handler (in beiden Streams identisch) ───────────────
  function applyPromptRefinement(msg) {
    // msg = { segment, total, prompt, image_mode, image_desc, reason, refined, switched_mode }
    const idx = msg.segment - 1;
    if (PREP && PREP.segments) {
      const s = PREP.segments.find(x => x.idx === idx);
      if (s) {
        if (msg.refined && msg.prompt) s.prompt = msg.prompt;
        if (msg.image_mode) s.image_mode = msg.image_mode;
        savePrep();
      }
    }
    const card = document.querySelector(`.ltx-seg-card[data-idx="${idx}"]`);
    if (!card) return;
    if (msg.refined && msg.prompt) {
      const ta = card.querySelector('.ltx-seg-prompt');
      if (ta) ta.value = msg.prompt;
    }
    if (msg.image_mode) {
      const r = card.querySelector(`input[name="mode-${idx}"][value="${msg.image_mode}"]`);
      if (r) r.checked = true;
      const up = card.querySelector('.ltx-seg-upload');
      if (up) up.style.display = (msg.image_mode === 'custom') ? 'flex' : 'none';
    }
    const note = card.querySelector('.ltx-refined-note');
    if (note) {
      const esc = s => (s||'').replace(/</g,'&lt;');
      const head = msg.refined
        ? (msg.switched_mode
            ? `<strong>🔍 Auto-Refine:</strong> Prompt verfeinert · Frame → <code>${esc(msg.switched_mode)}</code>`
            : `<strong>🔍 Auto-Refine:</strong> Prompt verfeinert`)
        : (msg.switched_mode
            ? `<strong>🔍 Frame-Switch:</strong> → <code>${esc(msg.switched_mode)}</code>`
            : `<strong>🔍 Auto-Refine:</strong> ${esc(msg.reason || 'kein Update nötig')}`);
      const desc = msg.image_desc ? `<div class="ltx-refined-desc">Vision: ${esc(msg.image_desc)}</div>` : '';
      const reason = (msg.refined || msg.switched_mode) && msg.reason
        ? `<div class="ltx-refined-desc">${esc(msg.reason)}</div>` : '';
      note.innerHTML = head + reason + desc;
      note.classList.add('visible');
    }
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
        case 'prompt_refined':
          log('🔍 Seg ' + msg.segment + ': ' + (msg.refined ? 'Prompt verfeinert' : (msg.reason || 'kein Refine')), msg.refined ? 'ltx-ok' : 'ltx-warn');
          applyPromptRefinement(msg);
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

    pollPrepare(jobId, '✅ {n} Segmente vorbereitet — Prompts/Bilder reviewen, dann ▶ Rendern');
  };

  // ── REPREPARE (ohne Re-Upload) ─────────────────────────────────────────────
  window._ltxReprepare = async function() {
    if (!PREP || !PREP.job_id) {
      log('⚠ Erst regulär vorbereiten — Re-Prepare braucht eine bestehende Job-ID', 'ltx-warn');
      return;
    }
    saveDefaults();
    setBusy('prepare', true);
    log('🔄 Re-Prepare läuft (cached: WAV + Bild + Whisper + Vision)...');
    try {
      const r = await fetch(API + '/reprepare', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          job_id: PREP.job_id,
          concept: qs('#ltx-concept').value.trim(),
          ollama_model: qs('#ltx-model').value,
          chunk_sec: parseFloat(qs('#ltx-chunk').value),
        }),
      });
      if (!r.ok) { const t = await r.text(); throw new Error(t); }
      const data = await r.json();
      if (data.error) throw new Error(data.error);
    } catch(e) {
      log('❌ Re-Prepare-Fehler: ' + e.message, 'ltx-error');
      setBusy('prepare', false);
      return;
    }
    pollPrepare(PREP.job_id, '✅ Re-Prepare fertig: {n} Segmente');
  };

  // Geteilter Poll-Loop für Prepare + Reprepare. Übersteht Netzwerk-Glitches.
  function pollPrepare(jobId, doneTpl) {
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
          savePrep();
          log(doneTpl.replace('{n}', s.total), 'ltx-ok');
          renderReview();
          setBusy('prepare', false);
          return;
        }
        if (s.status === 'error') {
          log('❌ ' + (s.error || 'unbekannt'), 'ltx-error');
          setBusy('prepare', false);
          return;
        }
        setTimeout(poll, 2000);
      } catch(e) {
        log(`⚠ Poll-Fehler (${e.message}) — versuche in 5s erneut`, 'ltx-warn');
        setTimeout(poll, 5000);
      }
    };
    poll();
  }

  // ── RENDER ────────────────────────────────────────────────────────────────
  window._ltxRender = async function() {
    if (!PREP) { log('⚠ Erst vorbereiten', 'ltx-warn'); return; }
    setBusy('render', true);
    const edits = PREP.segments.map(s => ({
      idx: s.idx,
      prompt: s.prompt,
      image_mode: s.image_mode,
      custom_fn: s.custom_fn || null,
      prompt_locked: !!s.prompt_locked,
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

  // ── SINGLE-SEGMENT RE-RENDER ──────────────────────────────────────────────
  window._ltxRenderSegment = async function(idx) {
    if (!PREP) { log('⚠ Erst vorbereiten', 'ltx-warn'); return; }
    const seg = PREP.segments.find(s => s.idx === idx);
    if (!seg) return;
    const card = document.querySelector(`.ltx-seg-card[data-idx="${idx}"]`);
    const btn = card?.querySelector('.ltx-seg-rerun-btn');
    const setBtn = (label, disabled) => { if (btn) { btn.textContent = label; btn.disabled = disabled; } };
    setBtn('⏳ rendert...', true);
    log(`↻ Re-Render Segment ${seg.segment} startet...`);
    let subId = null;
    try {
      const r = await fetch(API + '/render-segment', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          job_id: PREP.job_id,
          idx: seg.idx,
          prompt: seg.prompt,
          image_mode: seg.image_mode,
          custom_fn: seg.custom_fn || null,
          prompt_locked: !!seg.prompt_locked,
        }),
      });
      if (!r.ok) { const t = await r.text(); throw new Error(t); }
      const data = await r.json();
      if (data.error) throw new Error(data.error);
      subId = data.sub_job_id;
    } catch(e) {
      log('❌ Re-Render-Fehler: ' + e.message, 'ltx-error');
      setBtn('↻ Nur dieses rendern', false);
      return;
    }
    const subEs = new EventSource(API + '/progress/' + subId);
    subEs.onmessage = function(e) {
      const msg = JSON.parse(e.data);
      switch(msg.event) {
        case 'status':
          log('ℹ ' + msg.msg);
          break;
        case 'prompt_refined':
          log('🔍 Seg ' + msg.segment + ': ' + (msg.refined ? 'Prompt verfeinert' : (msg.reason || 'kein Refine')), msg.refined ? 'ltx-ok' : 'ltx-warn');
          applyPromptRefinement(msg);
          break;
        case 'segment_done':
          log('🎬 Segment ' + msg.segment + ' neu gerendert!', 'ltx-ok');
          renderVideo(msg.segment, msg.total, msg.url, msg.prompt);
          break;
        case 'segment_error':
          log('⚠ Segment ' + msg.segment + ': ' + msg.msg, 'ltx-warn');
          break;
        case 'error':
          log('❌ ' + msg.msg, 'ltx-error');
          subEs.close();
          setBtn('↻ Nur dieses rendern', false);
          break;
        case 'complete':
          subEs.close();
          setBtn('↻ Nur dieses rendern', false);
          break;
        case 'done':
          subEs.close();
          setBtn('↻ Nur dieses rendern', false);
          break;
      }
    };
    subEs.onerror = function() {
      log('⚠ SSE-Verbindung (Re-Render) unterbrochen', 'ltx-warn');
      subEs.close();
      setBtn('↻ Nur dieses rendern', false);
    };
  };

  // ── Reload-Restore: PREP + Logs + Videos + SSE-Reconnect ──────────────────
  function restoreState() {
    loadDefaults();
    const p = loadPrep();
    if (p && p.segments) {
      PREP = p;
      renderReview();
      log('🔄 Vorherige Segmente wiederhergestellt (' + p.segments.length + ' Stück)', '', false);
    }
    const j = loadJob();
    if (j) {
      renderLogs(j.logs || []);
      (j.videos || []).forEach(v => renderVideo(v.segment, v.total, v.url, v.prompt, false));
    }
    // SSE öffnen wenn ein Job bekannt ist — Server replayt JSONL und holt
    // Events nach, die während der Tab geschlossen war eingegangen sind.
    const job_id = (j && j.job_id) || (PREP && PREP.job_id);
    if (job_id) {
      if (j && j.running) setBusy('render', true);
      log('🔄 Hole Events vom Server (Job ' + job_id.substring(0,8) + ')...', '', false);
      connectSSE(job_id);
    }
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  setTimeout(() => {
    ['#ltx-concept','#ltx-model','#ltx-chunk'].forEach(sel => {
      qs(sel)?.addEventListener('change', saveDefaults);
    });
    qs('#ltx-concept')?.addEventListener('input', saveDefaults);
    qs('#ltx-prepare-btn')?.addEventListener('click', window._ltxPrepare);
    qs('#ltx-reprepare-btn')?.addEventListener('click', window._ltxReprepare);
    qs('#ltx-render-btn')?.addEventListener('click', window._ltxRender);
    qs('#ltx-export-btn')?.addEventListener('click', () => {
      if (!PREP || !PREP.segments) { alert('Keine Segmente geladen.'); return; }
      const lines = PREP.segments.map(s =>
        `Segment ${s.segment} (${s.duration}s)\n${s.prompt}\n`
      ).join('\n');
      const blob = new Blob([lines], {type: 'text/plain'});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `ltx_prompts_${(PREP.job_id||'').substring(0,8)}.txt`;
      a.click();
    });
    restoreState();
  }, 300);

  // ── Nuke: alles löschen, sauber neu starten ──────────────────────────
  window._ltxNuke = async function() {
    const btn = qs('#ltx-nuke-btn');
    if (!confirm('🧹 Alles zurücksetzen?\n\nLöscht:\n• Aktuellen Job & Prompts\n• Log & Videos\n• LocalStorage\n\nDer Server-Job wird ebenfalls gestoppt.')) return;

    btn.classList.add('nuking');
    btn.querySelector('.ltx-nuke-label').textContent = 'Räume auf...';

    // 1. SSE schließen
    if (es) { try { es.close(); } catch(e){} es = null; }
    if (PREP && PREP.job_id) {
      // Server-Job cancel (best-effort)
      try { await fetch(API + '/cancel/' + PREP.job_id, {method:'POST'}); } catch(e){}
    }

    // 2. LocalStorage leeren
    localStorage.removeItem(LS_JOB);
    localStorage.removeItem(LS_PREP);

    // 3. UI zurücksetzen
    PREP = null;
    const ids = ['ltx-log','ltx-videos','ltx-prompts'];
    ids.forEach(id => { const el = qs('#'+id); if (el) el.innerHTML = ''; });
    const review = qs('#ltx-review');
    if (review) { review.style.display = 'none'; }
    const renderRow = qs('#ltx-render-row');
    if (renderRow) renderRow.style.display = 'none';
    const concatBtn = qs('#ltx-concat-btn');
    if (concatBtn) { concatBtn.disabled = true; }
    ['#ltx-wav-input','#ltx-img-input'].forEach(sel => {
      const el = qs(sel);
      if (el) el.value = '';
    });

    // 4. Spinner kurz zeigen, dann fertig
    await new Promise(r => setTimeout(r, 600));
    btn.classList.remove('nuking');
    btn.querySelector('.ltx-nuke-label').textContent = '✓ Sauber!';
    btn.querySelector('.ltx-nuke-icon').textContent = '✨';
    setTimeout(() => {
      btn.querySelector('.ltx-nuke-label').textContent = 'Reset & Clean';
      btn.querySelector('.ltx-nuke-icon').textContent = '🧹';
    }, 2000);
  };
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

/* ── Nuke / Reset Button ── */
.ltx-nuke-btn {
  display: flex; align-items: center; gap: 8px;
  background: #0d1f0e; border: 1px solid #1a3a1a; border-radius: 10px;
  padding: 10px 16px; cursor: pointer; color: #9ca3af; font-size: 13px; font-weight: 600;
  transition: all 0.2s; white-space: nowrap; flex-shrink: 0;
  position: relative; overflow: hidden;
}
.ltx-nuke-btn::before {
  content: ''; position: absolute; inset: 0;
  background: linear-gradient(135deg, #7f1d1d00, #7f1d1d00);
  transition: background 0.3s;
}
.ltx-nuke-btn:hover { border-color: #ef444460; color: #ef4444; }
.ltx-nuke-btn:hover::before { background: linear-gradient(135deg, #7f1d1d20, #7f1d1d10); }
.ltx-nuke-btn:hover .ltx-nuke-icon { animation: spin 0.6s ease-in-out; }
.ltx-nuke-btn.nuking { border-color: #ef4444; color: #ef4444; background: #7f1d1d20; }
.ltx-nuke-btn.nuking .ltx-nuke-icon { animation: spin 0.5s linear infinite; }
.ltx-nuke-icon { font-size: 16px; display: inline-block; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(-360deg); } }
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
.ltx-seg-foot { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-top: 4px; }
.ltx-btn-mini {
  background: #14532d; color: #4ade80; border: 1px solid #166534;
  border-radius: 6px; padding: 5px 12px; font-size: 11px; font-weight: 500;
  cursor: pointer; transition: background 0.15s;
}
.ltx-btn-mini:disabled { opacity: 0.5; cursor: not-allowed; }
.ltx-btn-mini:hover:not(:disabled) { background: #166534; }
/* Lock-Toggle */
.ltx-lock-btn {
  background: transparent; color: #6b7280; border: 1px solid #1a3a1a;
  border-radius: 6px; padding: 4px 10px; font-size: 11px; cursor: pointer;
  transition: all 0.15s;
}
.ltx-lock-btn:hover { color: #4ade80; border-color: #166534; }
.ltx-lock-btn.locked { color: #fbbf24; border-color: #92400e; background: #1f1407; }
.ltx-seg-card.locked { border-color: #92400e; }
/* Refined-Banner */
.ltx-refined-note {
  font-size: 10px; line-height: 1.35; padding: 6px 8px; border-radius: 5px;
  background: rgba(74,222,128,0.06); border: 1px solid rgba(74,222,128,0.2);
  color: #9ca3af; display: none;
}
.ltx-refined-note.visible { display: block; }
.ltx-refined-note strong { color: #4ade80; font-weight: 600; }
.ltx-refined-note .ltx-refined-desc { color: #6b7280; font-style: italic; margin-top: 3px; }

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
.ltx-video-actions {
  display: flex; gap: 6px; padding: 0 12px 10px;
}
.ltx-dl-btn {
  flex: 1; text-align: center;
  background: #14532d; color: #4ade80; border-radius: 6px; padding: 5px;
  font-size: 12px; text-decoration: none;
}
.ltx-dl-btn:hover { background: #166534; }
.ltx-video-edit-btn {
  flex: 1;
}
.ltx-flash {
  animation: ltx-flash-anim 1.2s ease-out;
}
@keyframes ltx-flash-anim {
  0%   { box-shadow: 0 0 0 2px #4ade80, 0 0 16px rgba(74,222,128,0.6); }
  100% { box-shadow: 0 0 0 0 transparent; }
}
/* Concat-Button + Master-Video */
.ltx-concat-bar {
  display: flex; align-items: center; gap: 8px; margin: 4px 0 12px;
  font-size: 12px; color: #6b7280;
}
.ltx-concat-btn {
  background: #14532d; color: #4ade80; border: 1px solid #166534;
  border-radius: 6px; padding: 6px 14px; font-size: 12px; font-weight: 500;
  cursor: pointer; transition: background 0.15s;
}
.ltx-concat-btn:hover:not(:disabled) { background: #166534; }
.ltx-concat-btn:disabled { opacity: 0.5; cursor: not-allowed; }
#ltx-master-video { margin-top: 12px; }
</style>
"""


@ui.page("/ltx-batch")
def ltx_batch_page():
    apply_theme()
    ui.add_head_html(_PAGE_CSS)
    ui.add_head_html(_PAGE_JS)

    ui.html("""
<div class="ltx-wrap">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;">
    <div>
      <div class="ltx-title">🎬 LTX 2.3 Batch Renderer</div>
      <div class="ltx-sub">WAV + Bild → Segmente + Prompts vorbereiten → pro Segment entscheiden → rendern</div>
    </div>
    <button id="ltx-nuke-btn" class="ltx-nuke-btn" title="Alles löschen und sauber neu starten" onclick="window._ltxNuke()">
      <span class="ltx-nuke-icon">🧹</span>
      <span class="ltx-nuke-label">Reset &amp; Clean</span>
    </button>
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
      <button id="ltx-reprepare-btn" class="ltx-btn" title="Concept/Modell/Segment-Länge ändern und Prompts neu generieren — ohne WAV/Bild erneut hochzuladen">🔄 Prompts neu</button>
      <button id="ltx-export-btn" class="ltx-btn">📋 Prompts exportieren</button>
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
    <div class="ltx-concat-bar">
      <button id="ltx-concat-btn" class="ltx-concat-btn" disabled onclick="window._ltxConcat()">🎬 Alle fertigen Segmente zusammenschneiden</button>
      <span id="ltx-concat-status"></span>
    </div>
    <div id="ltx-master-video"></div>
    <div id="ltx-videos"><span class="ltx-placeholder">Noch keine Videos gerendert...</span></div>
  </div>
</div>
""")
