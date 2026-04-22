/**
 * chat.js — AgentClaw Chat Client-seitiges JavaScript
 *
 * Architektur:
 * - Lädt Konfiguration aus window._acConfig (wird per inline-Script in chat.py gesetzt)
 * - Send/Streaming komplett via fetch() + SSE (Server-Sent Events)
 * - DOM-Updates via insertAdjacentHTML (kein NiceGUI/Vue nötig)
 * - Umgeht den NiceGUI core.loop Bug (v1.89): KEIN ui.run_javascript() aus Event-Handlern
 */

// ─── Konfiguration aus window._acConfig lesen ────────────────────────────────
const _cfg = window._acConfig || {};
const _agentId = _cfg.agentId || '';
const _agentName = _cfg.agentName || '';
const _agentVoice = _cfg.agentVoice || '';

// ─── Globaler TTS-Controller — stoppt alle Audio-Ausgaben auf einmal ─────────
window._acTts = (function() {
    let _speaking = false;
    let _audio = null;   // laufendes Audio-Element (Server-TTS)

    function _setSpeaking(on) {
        _speaking = on;
        const btn = document.getElementById('ac-tts-stop');
        if (btn) btn.style.display = on ? 'flex' : 'none';
    }

    function stop() {
        // Web Speech API stoppen
        if (window.speechSynthesis) window.speechSynthesis.cancel();
        // Server-Audio stoppen
        if (_audio) { _audio.pause(); _audio = null; }
        _setSpeaking(false);
    }

    return {
        stop,
        setSpeaking: _setSpeaking,
        get speaking() { return _speaking; },
        get audio() { return _audio; },
        set audio(a) { _audio = a; },
    };
})();

// ─── TTS via Web Speech API (nur für mac:-Stimmen) ───────────────────────────
window._acSpeak = (function() {
    // Gecachte Stimmen-Liste (wird nach onvoiceschanged befüllt)
    let _voices = [];

    function _loadVoices() {
        _voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
    }

    if (window.speechSynthesis) {
        window.speechSynthesis.onvoiceschanged = _loadVoices;
        _loadVoices();
    }

    return function speakText(text, voiceName) {
        if (!window.speechSynthesis || !text) return;
        const clean = text
            .replace(/```[\s\S]*?```/g, '')
            .replace(/`[^`]*`/g, '')
            .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
            .replace(/[#*_~>|\\]/g, '')
            .replace(/\s{2,}/g, ' ')
            .trim();
        if (!clean) return;
        const short = clean.length > 500 ? clean.substring(0, 497) + '...' : clean;
        const utter = new SpeechSynthesisUtterance(short);
        if (_voices.length === 0) _loadVoices();
        const match = _voices.find(v => v.name === voiceName);
        if (match) { utter.voice = match; utter.lang = match.lang || 'de-DE'; }
        else { utter.lang = 'de-DE'; }
        utter.rate = 1.0;
        utter.onend   = () => window._acTts.setSpeaking(false);
        utter.onerror = () => window._acTts.setSpeaking(false);
        window.speechSynthesis.cancel();
        window._acTts.setSpeaking(true);
        window.speechSynthesis.speak(utter);
    };
})();

// ─── Server-TTS (Mistral Voxtral / Google Cloud) ─────────────────────────────
window._acServerSpeak = (function() {
    function _cleanText(text) {
        return text
            .replace(/```[\s\S]*?```/g, '')
            .replace(/`[^`]*`/g, '')
            .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
            .replace(/[#*_~>|\\]/g, '')
            .replace(/\s{2,}/g, ' ')
            .trim();
    }

    return async function serverSpeak(text, voice) {
        if (!text || !voice) return;
        const clean = _cleanText(text);
        if (!clean) return;
        // Laufende Wiedergabe stoppen
        window._acTts.stop();
        try {
            const resp = await fetch('/api/tts', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text: clean, voice: voice}),
            });
            if (!resp.ok || resp.status === 204) return;
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audio.onended = () => { URL.revokeObjectURL(url); window._acTts.setSpeaking(false); };
            audio.onerror = () => { URL.revokeObjectURL(url); window._acTts.setSpeaking(false); };
            window._acTts.audio = audio;
            window._acTts.setSpeaking(true);
            audio.play().catch(() => window._acTts.setSpeaking(false));
        } catch(e) {
            window._acTts.setSpeaking(false);
        }
    };
})();

// ─── STT: Web Speech API (online/live) + MediaRecorder→Whisper (offline) ─────
window._acMic = (function() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

    let active = false;
    let rec = null;
    let mr = null, mrChunks = [], mrStream = null;

    function _btn() { return document.getElementById('ac-mic-btn'); }
    function _ta()  { return document.getElementById('ac-input'); }

    function _setActive(on) {
        active = on;
        const b = _btn();
        if (!b) return;
        const l = b.querySelector('.ac-mic-label');
        b.classList.toggle('recording', on);
        b.disabled = false;
        if (l) l.textContent = on ? 'Stopp' : 'Sprache';
        b.title = on ? 'Aufnahme läuft — klicken zum Stoppen' : 'Spracheingabe';
    }

    // ── Web Speech API (online, live) ─────────────────────────────────────────
    function _startWebSpeech() {
        const ta = _ta();
        if (!ta) return;
        const baseText = ta.value;

        rec = new SR();
        rec.lang = 'de-DE';
        rec.continuous = false;
        rec.interimResults = true;
        rec.maxAlternatives = 1;

        rec.onstart  = () => _setActive(true);

        rec.onresult = function(e) {
            let interim = '', final = '';
            for (let i = e.resultIndex; i < e.results.length; i++) {
                const t = e.results[i][0].transcript;
                e.results[i].isFinal ? (final += t) : (interim += t);
            }
            const sep = baseText.trim() ? ' ' : '';
            ta.value = baseText + sep + (final || interim);
            ta.style.height = 'auto';
            ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
        };

        rec.onerror = function(e) {
            _setActive(false); rec = null;
            if (e.error === 'network') {
                // Kein Internet → Offline-Fallback mit MediaRecorder
                ta.placeholder = 'Offline — starte lokale Aufnahme…';
                setTimeout(_startMediaRecorder, 200);
            } else if (e.error === 'not-allowed') {
                ta.placeholder = 'Mikrofon-Zugriff verweigert.';
            }
        };

        rec.onend = () => { _setActive(false); rec = null; };
        rec.start();
    }

    // ── MediaRecorder → /api/transcribe (offline, Whisper lokal) ─────────────
    async function _startMediaRecorder() {
        if (mrStream) { mrStream.getTracks().forEach(t => t.stop()); mrStream = null; }
        mr = null; mrChunks = [];

        try {
            mrStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch(e) {
            const ta = _ta();
            if (ta) ta.placeholder = e.name === 'NotAllowedError'
                ? 'Mikrofon-Zugriff verweigert.' : 'Kein Mikrofon gefunden.';
            _setActive(false); return;
        }

        const mime = ['audio/webm;codecs=opus','audio/webm','audio/ogg']
            .find(m => MediaRecorder.isTypeSupported(m)) || '';
        try { mr = mime ? new MediaRecorder(mrStream, {mimeType: mime}) : new MediaRecorder(mrStream); }
        catch(e) { mr = new MediaRecorder(mrStream); }

        mr.ondataavailable = e => { if (e.data?.size > 0) mrChunks.push(e.data); };

        mr.onstop = async function() {
            const mimeUsed = mr.mimeType || 'audio/webm';
            const ext = mimeUsed.includes('ogg') ? '.ogg' : '.webm';
            const chunks = mrChunks.splice(0);
            if (mrStream) { mrStream.getTracks().forEach(t => t.stop()); mrStream = null; }
            mr = null;

            const blob = new Blob(chunks, { type: mimeUsed });
            if (blob.size < 100) { _setActive(false); return; }

            const b = _btn(), l = b ? b.querySelector('.ac-mic-label') : null;
            if (b) b.disabled = true;
            if (l) l.textContent = '⏳';
            const ta = _ta();
            if (ta) ta.placeholder = 'Transkription läuft…';

            try {
                const fd = new FormData();
                fd.append('file', blob, 'recording' + ext);
                const resp = await fetch('/api/transcribe', { method: 'POST', body: fd });
                if (resp.ok) {
                    const d = await resp.json();
                    if (d.text && ta) {
                        const sep = ta.value.trim() ? ' ' : '';
                        ta.value += sep + d.text;
                        ta.placeholder = '';
                        ta.style.height = 'auto';
                        ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
                        ta.focus();
                    } else if (ta) ta.placeholder = 'Kein Text erkannt.';
                } else if (ta) {
                    const err = await resp.json().catch(() => ({}));
                    ta.placeholder = 'Fehler: ' + (err.detail || resp.status);
                }
            } catch(e) {
                if (ta) ta.placeholder = 'Verbindungsfehler: ' + e.message;
            }
            _setActive(false);
        };

        mr.start(200);
        _setActive(true);
        const ta = _ta();
        if (ta) ta.placeholder = 'Offline-Aufnahme läuft — Stopp drücken…';
    }

    // ── Toggle ────────────────────────────────────────────────────────────────
    function toggle() {
        if (!active) {
            SR ? _startWebSpeech() : _startMediaRecorder();
        } else {
            if (rec) { rec.stop(); rec = null; }
            if (mr && mr.state !== 'inactive') mr.stop();
        }
    }

    return { toggle, supported: !!(SR || (navigator.mediaDevices && window.MediaRecorder)) };
})();

// ─── Prompt-Favoriten ─────────────────────────────────────────────────────────
window._acFav = (function() {
    const KEY = 'ac_fav_prompts';

    function load() {
        try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch { return []; }
    }

    function save(arr) {
        localStorage.setItem(KEY, JSON.stringify(arr));
    }

    function render() {
        const list = document.getElementById('ac-fav-list');
        if (!list) return;
        const items = load();
        if (!items.length) {
            list.innerHTML = '<div class="ac-fav-empty">Noch keine Favoriten gespeichert.<br>Text eingeben + ⭐ klicken.</div>';
            return;
        }
        list.innerHTML = items.map((text, idx) =>
            '<div class="ac-fav-item" data-idx="' + idx + '">' +
            '<span class="ac-fav-item-text" title="' + _esc(text) + '">' + _esc(text.length > 60 ? text.substring(0, 58) + '…' : text) + '</span>' +
            '<button class="ac-fav-del material-icons" data-idx="' + idx + '" title="Löschen">close</button>' +
            '</div>'
        ).join('');

        // Click: Text einfügen
        list.querySelectorAll('.ac-fav-item').forEach(function(el) {
            el.addEventListener('click', function(e) {
                if (e.target.classList.contains('ac-fav-del')) return;
                const idx = parseInt(el.dataset.idx);
                const text = load()[idx];
                if (!text) return;
                const ta = document.getElementById('ac-input');
                if (ta) {
                    ta.value = text;
                    ta.style.height = 'auto';
                    ta.style.height = Math.min(ta.scrollHeight, 180) + 'px';
                    ta.focus();
                }
                close();
            });
        });

        // Löschen
        list.querySelectorAll('.ac-fav-del').forEach(function(btn) {
            btn.addEventListener('click', function(e) {
                e.stopPropagation();
                const idx = parseInt(btn.dataset.idx);
                const arr = load();
                arr.splice(idx, 1);
                save(arr);
                render();
            });
        });
    }

    function _esc(s) {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    function open() {
        const panel = document.getElementById('ac-fav-panel');
        if (panel) { render(); panel.classList.add('open'); }
    }

    function close() {
        const panel = document.getElementById('ac-fav-panel');
        if (panel) panel.classList.remove('open');
    }

    function toggle() {
        const panel = document.getElementById('ac-fav-panel');
        if (!panel) return;
        if (panel.classList.contains('open')) { close(); } else { open(); }
    }

    function addCurrent() {
        const ta = document.getElementById('ac-input');
        if (!ta) return;
        const text = ta.value.trim();
        if (!text) { toggle(); return; }
        const arr = load();
        if (!arr.includes(text)) {
            arr.unshift(text);
            if (arr.length > 30) arr.length = 30;
            save(arr);
        }
        // Visuelles Feedback
        const btn = document.getElementById('ac-chip-fav');
        if (btn) {
            const orig = btn.innerHTML;
            btn.innerHTML = '<span class="material-icons" style="font-size:12px;color:#ffd700">star</span><span>Gespeichert!</span>';
            btn.style.borderColor = '#ffd700';
            setTimeout(function() { btn.innerHTML = orig; btn.style.borderColor = ''; }, 1200);
        }
    }

    function _injectDOM() {
        // Favoriten-Panel in ac-composer-wrap einfügen (falls nicht vorhanden)
        const wrap = document.querySelector('.ac-composer-wrap');
        if (wrap && !document.getElementById('ac-fav-panel')) {
            const panelHTML = [
                '<style>',
                '#ac-fav-panel{display:none;position:absolute;bottom:calc(100% + 6px);left:16px;width:340px;max-height:280px;',
                'background:#080f09;border:1px solid #1a3a1a;border-radius:12px;',
                'box-shadow:0 8px 32px rgba(0,0,0,.6);z-index:800;overflow:hidden;flex-direction:column;}',
                '#ac-fav-panel.open{display:flex;}',
                '.ac-fav-head{display:flex;align-items:center;justify-content:space-between;',
                'padding:8px 12px;border-bottom:1px solid #0f2010;font-size:11px;font-weight:700;',
                'color:#3a5a3a;text-transform:uppercase;letter-spacing:.5px;font-family:monospace;flex-shrink:0;}',
                '.ac-fav-list{flex:1;overflow-y:auto;padding:6px;}',
                '.ac-fav-item{display:flex;align-items:center;gap:6px;padding:7px 10px;border-radius:8px;',
                'cursor:pointer;transition:background .12s;margin-bottom:3px;border:1px solid transparent;}',
                '.ac-fav-item:hover{background:rgba(255,215,0,.06);border-color:#ffd70033;}',
                '.ac-fav-item-text{flex:1;font-size:12px;color:#b8d4b8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
                '.ac-fav-del{font-size:13px;color:#2a4a2a;cursor:pointer;background:none;border:none;',
                'padding:2px;line-height:1;flex-shrink:0;transition:color .15s;}',
                '.ac-fav-del:hover{color:#ef4444;}',
                '.ac-fav-empty{font-size:12px;color:#2a4a2a;text-align:center;padding:24px 12px;font-style:italic;}',
                '.ac-chip-fav{color:#ffd700;border-color:#ffd70033;background:rgba(255,215,0,.06);}',
                '.ac-chip-fav:hover{background:rgba(255,215,0,.14);border-color:#ffd700;}',
                '</style>',
                '<div id="ac-fav-panel">',
                '<div class="ac-fav-head">',
                '<span>⭐ Favoriten</span>',
                '<button id="ac-fav-close" style="background:none;border:none;color:#3a5a3a;cursor:pointer;font-size:14px;line-height:1;padding:0">✕</button>',
                '</div>',
                '<div class="ac-fav-list" id="ac-fav-list"></div>',
                '</div>'
            ].join('');
            wrap.insertAdjacentHTML('afterbegin', panelHTML);
            wrap.style.position = 'relative';
        }

        // Favoriten-Button in ac-composer-left einfügen (nach dem Mic-Button)
        const micBtn = document.getElementById('ac-mic-btn');
        if (micBtn && !document.getElementById('ac-chip-fav')) {
            micBtn.insertAdjacentHTML('afterend',
                '<button id="ac-chip-fav" class="ac-chip ac-chip-fav" title="Prompt speichern / Favoriten">' +
                '<span class="material-icons" style="font-size:12px">star</span>' +
                '<span>Favoriten</span>' +
                '</button>'
            );
        }
    }

    function init() { _injectDOM(); }

    return { init: init, injectDOM: _injectDOM, open: open, close: close, toggle: toggle, addCurrent: addCurrent };
})();

// ─── Haupt-Objekt ─────────────────────────────────────────────────────────────
window._ac = {
    sending: false,
    agentId: _agentId,
    agentName: _agentName,
    agentVoice: _agentVoice,
    _attachments: [],  // [{kind:'image'|'audio'|'video', dataUrl, name}]

    init: function() {
        // Panel injizieren (Favoriten-Overlay) — nur DOM, keine Listener
        window._acFav.injectDOM();
        // Mic sichtbar schalten wenn nicht unterstützt
        if (!window._acMic.supported) {
            const mb = document.getElementById('ac-mic-btn');
            if (mb) mb.style.visibility = 'hidden';
        }
        // File-Input Listener (multi: Bilder / Audio / Video)
        const fileInput = document.getElementById('ac-file-input');
        if (fileInput) {
            fileInput.addEventListener('change', (e) => {
                const files = Array.from(e.target.files || []);
                files.forEach((file) => {
                    const ext = (file.name.split('.').pop() || '').toLowerCase();
                    const kind = file.type.startsWith('image/') ? 'image'
                               : file.type.startsWith('audio/') || /^(mp3|wav|ogg|m4a|aac|flac|opus)$/.test(ext) ? 'audio'
                               : file.type.startsWith('video/') || /^(mp4|mov|mkv|webm|m4v|avi)$/.test(ext) ? 'video'
                               : 'file';
                    const reader = new FileReader();
                    reader.onload = (ev) => {
                        this._attachments.push({ kind, dataUrl: ev.target.result, name: file.name });
                        this._renderAttachments();
                    };
                    reader.readAsDataURL(file);
                });
                fileInput.value = '';
            });
        }
        // Zum Ende scrollen
        setTimeout(() => this.scroll(), 300);
        // WhatsApp Echtzeit-Eingang
        this._initWhatsAppStream();
    },

    clearAttachment: function() {
        this._attachments = [];
        this._renderAttachments();
    },

    _renderAttachments: function() {
        const list = document.getElementById('ac-attach-list');
        const chip = document.getElementById('ac-chip-attach');
        if (!list) return;
        if (!this._attachments.length) {
            list.innerHTML = '';
            if (chip) chip.classList.remove('has-image');
            return;
        }
        const esc = (s) => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        list.innerHTML = this._attachments.map((a, idx) => {
            const thumb = a.kind === 'image'
                ? `<img src="${esc(a.dataUrl)}">`
                : `<span class="ac-attach-icon material-icons">${a.kind === 'audio' ? 'audiotrack' : a.kind === 'video' ? 'movie' : 'insert_drive_file'}</span>`;
            return `<div class="ac-attach-item" data-idx="${idx}">
                ${thumb}
                <span class="ac-attach-name" title="${esc(a.name)}">${esc(a.name)}</span>
                <button class="ac-attach-del" data-idx="${idx}" title="Entfernen"><span class="material-icons" style="font-size:14px">close</span></button>
            </div>`;
        }).join('');
        if (chip) chip.classList.add('has-image');
    },

    removeAttachment: function(idx) {
        this._attachments.splice(idx, 1);
        this._renderAttachments();
    },

    _initWhatsAppStream: function() {
        if (this._waEs) { this._waEs.close(); this._waEs = null; }
        const es = new EventSource('/api/whatsapp/events');
        es.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                if (data.type === 'whatsapp_incoming') this._showWhatsAppMsg(data);
            } catch(_) {}
        };
        es.onerror = () => { /* keepalive-Kommentare ignorieren */ };
        this._waEs = es;
    },

    _showWhatsAppMsg: function(data) {
        const c = document.getElementById('ac-messages');
        if (!c) return;
        const html = `<div class="msg-row" style="justify-content:flex-start;margin-bottom:14px">
            <div style="max-width:80%;background:#0d1f0d;border-left:3px solid #25d366;
                        border-radius:0 8px 8px 0;padding:10px 14px">
                <div style="font-size:11px;color:#25d366;font-weight:600;margin-bottom:5px">
                    📱 WhatsApp · ${data.sender} · ${data.ts}
                </div>
                <div style="font-size:13px;color:#c8e6c9;line-height:1.5">${this.renderMd(data.text)}</div>
            </div>
        </div>`;
        c.insertAdjacentHTML('beforeend', html);
        this.scroll();
    },

    send: function() {
        if (this.sending) return;
        const ta = document.getElementById('ac-input');
        if (!ta) return;
        const msg = ta.value.trim();
        if (!msg) return;

        this.sending = true;
        ta.value = '';
        ta.style.height = 'auto';
        const sendBtn = document.getElementById('ac-send-btn');
        if (sendBtn) sendBtn.classList.add('busy');

        // Anhänge sichern + UI zurücksetzen
        const attachments = this._attachments.slice();
        this.clearAttachment();
        const images = attachments.filter(a => a.kind === 'image').map(a => a.dataUrl);
        const audios = attachments.filter(a => a.kind === 'audio').map(a => a.dataUrl);

        // User-Nachricht anzeigen (mit Vorschau wenn vorhanden)
        let previewHtml = '';
        attachments.forEach(a => {
            if (a.kind === 'image') {
                previewHtml += '<img src="' + a.dataUrl + '" style="max-width:200px;max-height:150px;border-radius:6px;display:inline-block;margin:0 6px 6px 0">';
            } else if (a.kind === 'audio') {
                previewHtml += '<div style="font-size:11px;color:#64b5f6;margin-bottom:4px">🎵 ' + this.escHtml(a.name) + '</div>';
                previewHtml += '<audio controls src="' + a.dataUrl + '" style="max-width:280px;display:block;margin-bottom:6px"></audio>';
            } else {
                previewHtml += '<div style="font-size:11px;color:#b8d4b8;margin-bottom:4px">📎 ' + this.escHtml(a.name) + '</div>';
            }
        });
        this.addMsg('user', previewHtml + this.escHtml(msg));

        // Typing-Indicator einblenden
        this.addTyping();

        // SSE-Stream starten — POST wenn Anhang dabei, GET sonst
        const thinkFlag = localStorage.getItem('ac_think') === '0' ? 0 : 1;

        let fetchPromise;
        if (attachments.length) {
            const body = { agent_id: this.agentId, message: msg, think: thinkFlag };
            if (images.length) body.images = images;
            if (audios.length) body.audio = audios;
            fetchPromise = fetch('/api/chat/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
        } else {
            const url = '/api/chat/stream?agent_id=' + encodeURIComponent(this.agentId)
                      + '&message=' + encodeURIComponent(msg)
                      + '&think=' + thinkFlag;
            fetchPromise = fetch(url);
        }

        let accumulated = '';
        let accumulatedThinking = '';
        let replyStarted = false;
        let thinkingStarted = false;

        fetchPromise.then(response => {
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            const processStream = () => {
                reader.read().then(({ done, value }) => {
                    if (done) {
                        this.sending = false;
                        this.removeTyping();
                        if (accumulated && !replyStarted) {
                            // Sollte nicht vorkommen, aber als Sicherheitsnetz
                            this.addReply(accumulated);
                        }
                        this.scroll();
                        return;
                    }

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop(); // Unvollständige Zeile aufbewahren

                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;
                        try {
                            const data = JSON.parse(line.substring(6));

                            if (data.error) {
                                this.removeTyping();
                                this.addError(data.error);
                                this.sending = false;
                                return;
                            }

                            if (data.thinking) {
                                accumulatedThinking += data.thinking;
                                if (!thinkingStarted) {
                                    thinkingStarted = true;
                                    this.removeTyping();
                                    this.startThinking();
                                }
                                this.updateThinking(accumulatedThinking);
                            }

                            if (data.progress) {
                                // Skill-Progress (z.B. YouTube-Download)
                                if (thinkingStarted) {
                                    this.finishThinking();
                                    thinkingStarted = false;
                                }
                                this.updateProgress(data.progress);
                            }

                            if (data.chunk) {
                                accumulated += data.chunk;
                                if (thinkingStarted) {
                                    this.finishThinking();
                                    thinkingStarted = false;
                                }
                                this.finishProgress();
                                if (!replyStarted) {
                                    replyStarted = true;
                                    this.removeTyping();
                                    this.startReply();
                                }
                                this.updateReply(accumulated);
                            }

                            if (data.image) {
                                // Skill-Bild direkt nach dem Text anhängen
                                this.appendSkillImage(data.image);
                            }

                            if (data.done) {
                                this.removeTyping();
                                this.finishProgress();
                                if (thinkingStarted) {
                                    this.finishThinking();
                                    thinkingStarted = false;
                                }
                                const displayReply = data.display_reply || accumulated;
                                if (displayReply) {
                                    if (!replyStarted) this.startReply();
                                    this.finishReply(displayReply);
                                }
                                    // A2A-Dispatches anzeigen + auf Ergebnis warten
                                if (data.a2a_dispatches) {
                                    for (const d of data.a2a_dispatches) {
                                        this.addA2A(this.agentName, d.recipient_name, d.task_text, d.task_id);
                                        if (d.task_id) this.subscribeTask(d.task_id, d.recipient_name);
                                    }
                                }
                                // Task-Chain anzeigen
                                if (data.chain_steps) {
                                    this.addChain(data.chain_steps);
                                }
                                this.sending = false;
                                const sb = document.getElementById('ac-send-btn');
                                if (sb) sb.classList.remove('busy');
                                this.scroll();
                            }
                        } catch(e) { /* Parse-Fehler überspringen */ }
                    }

                    processStream();
                }).catch(err => {
                    this.sending = false;
                    const sb = document.getElementById('ac-send-btn');
                    if (sb) sb.classList.remove('busy');
                    this.removeTyping();
                    this.addError(String(err));
                });
            };

            processStream();
        }).catch(err => {
            this.sending = false;
            const sb = document.getElementById('ac-send-btn');
            if (sb) sb.classList.remove('busy');
            this.removeTyping();
            this.addError(String(err));
        });
    },

    // ─── DOM Hilfsfunktionen ──────────────────────────────────────────────────
    addMsg: function(role, html, image) {
        const c = document.getElementById('ac-messages');
        if (!c) return;
        const isUser = role === 'user';
        const align = isUser ? 'flex-end' : 'flex-start';

        // Halluzinations-Warnung (Intent-Guard): Reply beginnt mit "[NICHT AUSGEFÜHRT]"
        // → gelber Warn-Stil + Badge im Label.
        const isHalted = !isUser && typeof html === 'string' &&
            (html.indexOf('[NICHT AUSGEFÜHRT]') >= 0 && html.indexOf('[NICHT AUSGEFÜHRT]') < 400);
        const haltBadge = isHalted
            ? '<span style="display:inline-block;margin-left:6px;padding:1px 6px;background:#4a3410;color:#ffb020;border:1px solid #6a4a18;border-radius:3px;font-size:9px;font-family:monospace;letter-spacing:.5px">⚠ NICHT AUSGEFÜHRT</span>'
            : '';
        let bbl;
        if (isHalted) {
            bbl = 'background:rgba(255,176,32,.08);border:1px solid #5a4210;color:#ffcf70;border-bottom-left-radius:3px;';
        } else {
            bbl = isUser
                ? 'background:rgba(0,230,118,.08);border:1px solid #182e18;color:#e4f4e4;border-bottom-right-radius:3px;'
                : 'background:#0d1a0e;border:1px solid #0f2010;color:#b8d4b8;border-bottom-left-radius:3px;';
        }
        const label = isUser ? 'Du' : 'Assistant';
        let h = '<div style="display:flex;flex-direction:column;gap:3px;max-width:820px;align-self:'+align+';align-items:'+align+';width:100%">';
        h += '<span style="font-size:10px;font-family:monospace;color:#3a5a3a;padding:0 4px">'+label+haltBadge+'</span>';
        if (image) {
            const isVideo = image.startsWith('data:video') || image.includes('.mp4');
            if (isVideo) {
                h += '<video src="'+image+'" controls style="max-width:480px;border-radius:8px;margin-bottom:4px;display:block" preload="metadata"></video>';
            } else {
                h += '<img src="'+image+'" style="max-width:320px;border-radius:8px;margin-bottom:4px">';
            }
        }
        if (html) h += '<div style="padding:10px 14px;border-radius:10px;font-size:14px;line-height:1.6;word-break:break-word;'+bbl+'">'+html+'</div>';
        h += '</div>';
        // Leerer-Hinweis entfernen falls vorhanden
        const hint = document.getElementById('ac-empty-hint');
        if (hint) hint.remove();
        c.insertAdjacentHTML('beforeend', h);
        this.scroll();
    },

    addTyping: function() {
        const c = document.getElementById('ac-messages');
        if (!c) return;
        c.insertAdjacentHTML('beforeend',
            '<div id="ac-typing" style="align-self:flex-start;padding:4px">' +
            '<span style="font-size:14px;color:#3a5a3a;animation:ac-pulse 1.2s ease-in-out infinite">● ● ●</span></div>');
        this.scroll();
    },

    removeTyping: function() {
        const el = document.getElementById('ac-typing');
        if (el) el.remove();
    },

    // ─── Thinking-Panel (Chain-of-Thought von Reasoning-Modellen) ─────────────
    startThinking: function() {
        const c = document.getElementById('ac-messages');
        if (!c) return;
        c.insertAdjacentHTML('beforeend',
            '<details id="ac-thinking-wrap" open style="max-width:820px;align-self:flex-start;width:100%;' +
            'background:rgba(120,90,200,.06);border:1px solid rgba(120,90,200,.25);border-radius:10px;' +
            'padding:6px 12px;font-size:12px;color:#9a86c4">' +
            '<summary style="cursor:pointer;user-select:none;font-family:monospace;font-size:11px;' +
            'color:#8a76b4;outline:none">💭 <span id="ac-thinking-label">denkt …</span></summary>' +
            '<div id="ac-thinking" style="margin-top:6px;white-space:pre-wrap;line-height:1.5;' +
            'font-family:ui-monospace,Menlo,monospace;font-size:11px;color:#b5a5d8;' +
            'max-height:220px;overflow-y:auto"></div></details>');
    },

    updateThinking: function(text) {
        const el = document.getElementById('ac-thinking');
        if (el) { el.textContent = text; el.scrollTop = el.scrollHeight; this.scroll(); }
    },

    finishThinking: function() {
        const lbl = document.getElementById('ac-thinking-label');
        if (lbl) lbl.textContent = 'Gedanken';
        const wrap = document.getElementById('ac-thinking-wrap');
        if (wrap) { wrap.open = false; wrap.removeAttribute('id'); }
        const el = document.getElementById('ac-thinking');
        if (el) el.removeAttribute('id');
        if (lbl) lbl.removeAttribute('id');
    },

    startReply: function() {
        const c = document.getElementById('ac-messages');
        if (!c) return;
        c.insertAdjacentHTML('beforeend',
            '<div id="ac-reply-wrap" style="display:flex;flex-direction:column;gap:3px;max-width:820px;align-self:flex-start;align-items:flex-start;width:100%">' +
            '<span style="font-size:10px;font-family:monospace;color:#3a5a3a;padding:0 4px">Assistant</span>' +
            '<div id="ac-reply" style="padding:10px 14px;border-radius:10px;font-size:14px;line-height:1.6;word-break:break-word;' +
            'background:#0d1a0e;border:1px solid #0f2010;color:#b8d4b8;min-width:40px;white-space:pre-wrap"></div></div>');
    },

    updateProgress: function(text) {
        let el = document.getElementById('ac-progress');
        if (!el) {
            const c = document.getElementById('ac-messages');
            if (!c) return;
            this.removeTyping();
            c.insertAdjacentHTML('beforeend',
                '<div id="ac-progress" style="max-width:820px;align-self:flex-start;padding:6px 12px;' +
                'border-radius:8px;font-size:12px;font-family:monospace;color:#8ab88a;' +
                'background:#0a140b;border:1px solid #1a2e1c"></div>');
            el = document.getElementById('ac-progress');
        }
        if (el) { el.textContent = text; this.scroll(); }
    },

    finishProgress: function() {
        const el = document.getElementById('ac-progress');
        if (el) el.remove();
    },

    updateReply: function(text) {
        const el = document.getElementById('ac-reply');
        if (el) { el.textContent = text; this.scroll(); }
    },

    finishReply: function(text) {
        const el = document.getElementById('ac-reply');
        if (el) {
            el.style.whiteSpace = 'normal';
            el.innerHTML = this.renderMd(text);
            el.removeAttribute('id');
        }
        const wrap = document.getElementById('ac-reply-wrap');
        if (wrap) wrap.removeAttribute('id');
        this.scroll();
        // TTS: mac:-Stimmen → Web Speech API, alles andere → /api/tts (Mistral/Google)
        if (this.agentVoice && text) {
            if (this.agentVoice.startsWith('mac:')) {
                const voiceName = this.agentVoice.replace('mac:', '');
                window._acSpeak(text, voiceName);
            } else {
                window._acServerSpeak(text, this.agentVoice);
            }
        }
    },

    addReply: function(text) {
        this.startReply();
        this.finishReply(text);
    },

    appendSkillImage: function(imageSrc) {
        const c = document.getElementById('ac-messages');
        if (!c) return;
        const wrap = document.getElementById('ac-reply-wrap') || c.lastElementChild;
        if (!wrap) return;
        const isVideo = imageSrc.startsWith('data:video') || imageSrc.includes('.mp4');
        const mediaHtml = isVideo
            ? '<video src="' + imageSrc + '" controls style="max-width:480px;border-radius:8px;margin-top:6px;display:block" preload="metadata"></video>'
            : '<img src="' + imageSrc + '" style="max-width:480px;border-radius:8px;margin-top:6px;display:block">';
        wrap.insertAdjacentHTML('beforeend', mediaHtml);
        this.scroll();
    },

    addError: function(msg) {
        const c = document.getElementById('ac-messages');
        if (!c) return;
        c.insertAdjacentHTML('beforeend',
            '<div style="padding:10px 14px;border-radius:10px;font-size:14px;background:rgba(239,68,68,.08);' +
            'border:1px solid rgba(239,68,68,.3);color:#ef4444;align-self:flex-start;max-width:820px">' +
            '<strong>Fehler:</strong> ' + this.escHtml(msg) + '</div>');
        this.scroll();
    },

    // ─── A2A Task-Karte erstellen (animiert, live-Status) ────────────────────
    addA2A: function(sender, recipient, taskText, taskId) {
        const c = document.getElementById('ac-messages');
        if (!c) return;
        const cardId = taskId ? 'a2a-' + taskId : 'a2a-' + Date.now();

        const h = `
        <div id="${cardId}" style="
            align-self:flex-start;max-width:600px;width:100%;
            border:1px solid #0f2e38;border-radius:10px;
            background:rgba(0,188,212,0.04);
            overflow:hidden;transition:border-color .3s">
          <!-- Header -->
          <div style="display:flex;align-items:center;gap:8px;padding:9px 14px;
              border-bottom:1px solid #0f2e38;background:rgba(0,188,212,0.06)">
            <span id="${cardId}-icon" class="material-icons"
                style="font-size:15px;color:#00bcd4;animation:a2a-spin 1.2s linear infinite">
              autorenew
            </span>
            <span style="font-size:12px;font-weight:600;color:#00bcd4">${this.escHtml(sender)} → @${this.escHtml(recipient)}</span>
            <span style="margin-left:auto;font-size:9px;padding:2px 6px;border-radius:3px;
                border:1px solid #00bcd4;color:#00bcd4;font-family:monospace;letter-spacing:.5px">A2A</span>
            <span id="${cardId}-status" style="font-size:10px;color:#3a8a9a;font-style:italic">working…</span>
          </div>
          <!-- Task-Text -->
          <div style="padding:8px 14px 10px;font-size:12px;color:#4a7a8a;line-height:1.5">
            ${this.escHtml(taskText.substring(0, 220))}${taskText.length > 220 ? '…' : ''}
          </div>
          <!-- Ergebnis-Bereich (zunächst leer) -->
          <div id="${cardId}-result" style="display:none;
              border-top:1px solid #0f2e38;padding:10px 14px"></div>
        </div>`;
        c.insertAdjacentHTML('beforeend', h);

        // CSS-Keyframes einmalig einfügen
        if (!document.getElementById('a2a-styles')) {
            const s = document.createElement('style');
            s.id = 'a2a-styles';
            s.textContent = `
                @keyframes a2a-spin { to { transform: rotate(360deg); } }
                @keyframes a2a-fadein { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:none; } }
            `;
            document.head.appendChild(s);
        }
        this.scroll();
    },

    // ─── Task-Chain: mehrstufige Fortschrittsanzeige ─────────────────────────
    addChain: function(steps) {
        const c = document.getElementById('ac-messages');
        if (!c) return;
        const chainId = 'chain-' + Date.now();

        const stepsHtml = steps.map((s, i) => `
            <div id="${chainId}-step-${i}" style="
                display:flex;align-items:flex-start;gap:10px;
                padding:8px 0;${i < steps.length - 1 ? 'border-bottom:1px solid #0a1e0a;' : ''}">
              <!-- Status-Icon -->
              <div style="flex-shrink:0;width:22px;display:flex;justify-content:center;padding-top:1px">
                <span id="${chainId}-icon-${i}" class="material-icons"
                    style="font-size:16px;color:${i === 0 ? '#00bcd4' : '#2a4a2a'};
                    ${i === 0 ? 'animation:a2a-spin 1.2s linear infinite' : ''}">
                  ${i === 0 ? 'autorenew' : 'radio_button_unchecked'}
                </span>
              </div>
              <!-- Step-Info -->
              <div style="flex:1;min-width:0">
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px">
                  <span style="font-size:10px;font-weight:700;color:#2a5a6a;font-family:monospace">
                    SCHRITT ${s.step}/${steps.length}
                  </span>
                  <span style="font-size:11px;font-weight:600;color:#00bcd4">@${this.escHtml(s.agent_name)}</span>
                  <span id="${chainId}-status-${i}" style="font-size:10px;color:#2a5a3a;font-style:italic">
                    ${i === 0 ? 'läuft…' : 'wartet'}
                  </span>
                </div>
                <div style="font-size:12px;color:#4a7a8a;line-height:1.4">
                  ${this.escHtml(s.text.substring(0, 160))}${s.text.length > 160 ? '…' : ''}
                </div>
                <!-- Ergebnis-Bereich -->
                <div id="${chainId}-result-${i}" style="display:none;margin-top:6px"></div>
              </div>
            </div>`).join('');

        const h = `
        <div id="${chainId}" style="
            align-self:flex-start;max-width:660px;width:100%;
            border:1px solid #0d2a38;border-radius:10px;
            background:rgba(0,188,212,0.03);overflow:hidden">
          <!-- Header -->
          <div style="display:flex;align-items:center;gap:8px;padding:9px 14px;
              border-bottom:1px solid #0d2a38;background:rgba(0,188,212,0.06)">
            <span class="material-icons" style="font-size:15px;color:#00bcd4">account_tree</span>
            <span style="font-size:12px;font-weight:700;color:#00bcd4">Task-Chain · ${steps.length} Schritte</span>
            <span id="${chainId}-overall" style="margin-left:auto;font-size:10px;color:#2a8a9a;font-style:italic">Schritt 1 läuft…</span>
          </div>
          <!-- Schritte -->
          <div style="padding:10px 14px">${stepsHtml}</div>
        </div>`;
        c.insertAdjacentHTML('beforeend', h);

        // CSS einmalig einbinden
        if (!document.getElementById('a2a-styles')) {
            const s = document.createElement('style');
            s.id = 'a2a-styles';
            s.textContent = `
                @keyframes a2a-spin { to { transform: rotate(360deg); } }
                @keyframes a2a-fadein { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:none; } }
            `;
            document.head.appendChild(s);
        }

        // Jeden Task abonnieren
        const self = this;
        steps.forEach((s, i) => {
            if (s.task_id) this._subscribeChainStep(chainId, i, s.task_id, s.agent_name, steps.length);
        });

        this.scroll();
    },

    _subscribeChainStep: function(chainId, stepIdx, taskId, agentName, totalSteps) {
        const self = this;
        let done = false;
        const deadline = Date.now() + 30 * 60 * 1000;

        function setStepStatus(status) {
            const icon   = document.getElementById(chainId + '-icon-' + stepIdx);
            const label  = document.getElementById(chainId + '-status-' + stepIdx);
            const overall = document.getElementById(chainId + '-overall');
            if (!icon || !label) return;

            if (status === 'working') {
                icon.textContent = 'autorenew';
                icon.style.color = '#00bcd4';
                icon.style.animation = 'a2a-spin 1.2s linear infinite';
                label.textContent = 'läuft…';
                if (overall) overall.textContent = `Schritt ${stepIdx + 1}/${totalSteps} läuft…`;
            } else if (status === 'waiting') {
                icon.textContent = 'hourglass_top';
                icon.style.color = '#ffa726';
                icon.style.animation = 'none';
                label.textContent = 'wartet…';
            } else if (status === 'queued') {
                icon.textContent = 'schedule';
                icon.style.color = '#ffa726';
                icon.style.animation = 'none';
                label.textContent = 'in Warteschlange';
            } else if (status === 'completed') {
                icon.textContent = 'check_circle';
                icon.style.color = '#00e676';
                icon.style.animation = 'none';
                label.textContent = 'abgeschlossen';
                if (stepIdx + 1 === totalSteps && overall) {
                    overall.textContent = '✓ Alle Schritte abgeschlossen';
                    overall.style.color = '#00e676';
                }
            } else if (status === 'failed') {
                icon.textContent = 'error';
                icon.style.color = '#ef4444';
                icon.style.animation = 'none';
                label.textContent = 'fehlgeschlagen';
                if (overall) { overall.textContent = `⚠ Fehler bei Schritt ${stepIdx + 1}`; overall.style.color = '#ef4444'; }
            }
        }

        function showStepResult(text, img, isError) {
            const el = document.getElementById(chainId + '-result-' + stepIdx);
            if (!el) return;
            el.style.display = 'block';
            el.style.animation = 'a2a-fadein .3s ease';
            if (isError) {
                el.innerHTML = `<div style="color:#ef4444;font-size:11px;margin-top:4px">⚠ ${self.escHtml(text)}</div>`;
                return;
            }
            let inner = '';
            if (img) {
                const isVideo = img.startsWith('data:video');
                inner += isVideo
                    ? `<video src="${img}" controls style="max-width:100%;border-radius:5px;display:block;margin-bottom:4px" preload="metadata"></video>`
                    : `<img src="${img}" style="max-width:100%;border-radius:5px;display:block;margin-bottom:4px">`;
            }
            if (text) inner += `<div style="font-size:12px;color:#a0c4a0;line-height:1.5">${self.renderMd(text)}</div>`;
            el.innerHTML = inner;
        }

        function connect() {
            if (done || Date.now() > deadline) return;
            const es = new EventSource('/api/a2a/tasks/' + taskId + '/subscribe');

            es.onmessage = function(e) {
                try {
                    const data = JSON.parse(e.data);
                    // statusUpdate: intermediate state change
                    if (data.statusUpdate) setStepStatus(data.statusUpdate.state);
                    // task: initial or final state
                    const task = data.task;
                    if (task) {
                        setStepStatus(task.status);
                        if (task.status === 'completed') {
                            showStepResult(task.result_text || '', task.result_image || null, false);
                            done = true; es.close();
                        } else if (task.status === 'failed') {
                            showStepResult(task.error || 'Unbekannter Fehler', null, true);
                            done = true; es.close();
                        }
                    }
                } catch(e) {}
                self.scroll();
            };
            es.onerror = function() {
                es.close();
                if (!done && Date.now() < deadline) setTimeout(connect, 3000);
            };
        }
        connect();
    },

    // ─── Task-Karte live aktualisieren ───────────────────────────────────────
    subscribeTask: function(taskId, recipientName) {
        const self = this;
        let done = false;
        const deadline = Date.now() + 25 * 60 * 1000;
        const cardId = 'a2a-' + taskId;

        const STATUS_LABELS = {
            submitted: 'gesendet', queued: 'in Warteschlange',
            working: 'läuft…', completed: 'fertig', failed: 'fehlgeschlagen',
        };

        function setCardStatus(status) {
            const icon   = document.getElementById(cardId + '-icon');
            const label  = document.getElementById(cardId + '-status');
            const card   = document.getElementById(cardId);
            if (!icon || !label || !card) return;

            if (status === 'working') {
                icon.textContent = 'autorenew';
                icon.style.color = '#00bcd4';
                icon.style.animation = 'a2a-spin 1.2s linear infinite';
            } else if (status === 'completed') {
                icon.textContent = 'check_circle';
                icon.style.color = '#00e676';
                icon.style.animation = 'none';
                card.style.borderColor = '#1a4a2a';
            } else if (status === 'failed') {
                icon.textContent = 'error';
                icon.style.color = '#ef4444';
                icon.style.animation = 'none';
                card.style.borderColor = '#4a1a1a';
            } else if (status === 'queued') {
                icon.textContent = 'schedule';
                icon.style.color = '#ffa726';
                icon.style.animation = 'none';
            }
            label.textContent = STATUS_LABELS[status] || status;
        }

        function showResult(text, img, isError) {
            const resultEl = document.getElementById(cardId + '-result');
            if (!resultEl) return;
            resultEl.style.display = 'block';
            resultEl.style.animation = 'a2a-fadein .3s ease';

            if (isError) {
                resultEl.innerHTML = `<span style="color:#ef4444;font-size:12px">⚠ ${self.escHtml(text)}</span>`;
                return;
            }
            let inner = '';
            if (img) {
                const isVideo = img.startsWith('data:video') || img.includes('.mp4');
                if (isVideo) {
                    inner += `<video src="${img}" controls style="max-width:100%;border-radius:6px;display:block;margin-bottom:6px" preload="metadata"></video>`;
                } else {
                    inner += `<img src="${img}" style="max-width:100%;border-radius:6px;display:block;margin-bottom:6px">`;
                }
            }
            if (text) {
                inner += `<div style="font-size:13px;color:#b8d4b8;line-height:1.6">${self.renderMd(text)}</div>`;
            }
            resultEl.innerHTML = inner;
        }

        function connect() {
            if (done || Date.now() > deadline) return;
            const es = new EventSource('/api/a2a/tasks/' + taskId + '/subscribe');

            es.onmessage = function(e) {
                try {
                    const data = JSON.parse(e.data);

                    // Status-Update
                    if (data.statusUpdate) {
                        setCardStatus(data.statusUpdate.state);
                    }

                    // Finaler Task-State
                    const task = data.task;
                    if (task) {
                        setCardStatus(task.status);
                        if (task.status === 'completed' || task.status === 'failed') {
                            done = true;
                            es.close();

                            if (task.status === 'completed') {
                                const text = task.result_text || '';
                                const img  = task.result_image || null;
                                if (text || img) {
                                    showResult(text, img, false);
                                    self.scroll();
                                    if (self.agentVoice && text) {
                                        if (self.agentVoice.startsWith('mac:')) {
                                            window._acSpeak(text, self.agentVoice.replace('mac:', ''));
                                        } else {
                                            window._acServerSpeak(text, self.agentVoice);
                                        }
                                    }
                                }
                            } else {
                                showResult(task.error || 'Unbekannter Fehler', null, true);
                                self.scroll();
                            }
                        }
                    }
                } catch(err) {}
            };

            es.onerror = function() {
                es.close();
                if (!done && Date.now() < deadline) setTimeout(connect, 3000);
                else if (!done) setCardStatus('failed');
            };
        }

        connect();
    },

    scroll: function() {
        const el = document.getElementById('ac-scroll');
        if (el) setTimeout(() => { el.scrollTop = el.scrollHeight; }, 50);
    },

    escHtml: function(s) {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    },

    renderMd: function(text) {
        let t = this.escHtml(text);
        // Code-Blöcke
        t = t.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre style="background:#0a150b;padding:8px;border-radius:4px;overflow-x:auto;font-size:12px"><code>$2</code></pre>');
        // Inline-Code
        t = t.replace(/`([^`]+)`/g, '<code style="background:#0a150b;padding:1px 4px;border-radius:3px;font-size:12px">$1</code>');
        // Fett
        t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        // Kursiv
        t = t.replace(/\*(.+?)\*/g, '<em>$1</em>');
        // Links
        t = t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" style="color:#00e676">$1</a>');
        // Zeilenumbrüche
        t = t.replace(/\n/g, '<br>');
        return t;
    }
};

// ─── Globale Event-Delegation ─────────────────────────────────────────────────
// Alle Button-Clicks werden hier zentral behandelt.
// Vorteil: Überlebt Vue-Re-Renders — kein direktes addEventListener auf Elementen.
(function() {
    function _chipPrefix(ta, prefix) {
        if (!ta) return;
        if (!ta.value.startsWith(prefix)) ta.value = prefix + ta.value;
        ta.focus();
        ta.style.height = 'auto';
        ta.style.height = Math.min(ta.scrollHeight, 180) + 'px';
    }

    document.addEventListener('click', function(e) {
        const t = e.target;

        if (t.closest('#ac-send-btn')) {
            e.preventDefault(); e.stopPropagation();
            window._ac && window._ac.send();
            return;
        }
        if (t.closest('#ac-mic-btn')) {
            e.preventDefault(); e.stopPropagation();
            window._acMic && window._acMic.toggle();
            return;
        }
        if (t.closest('#ac-chip-fav')) {
            e.preventDefault(); e.stopPropagation();
            window._acFav && window._acFav.addCurrent();
            return;
        }
        if (t.closest('#ac-fav-close')) {
            e.preventDefault(); e.stopPropagation();
            window._acFav && window._acFav.close();
            return;
        }
        if (t.closest('#ac-chip-attach')) {
            e.preventDefault(); e.stopPropagation();
            document.getElementById('ac-file-input') && document.getElementById('ac-file-input').click();
            return;
        }
        const delBtn = t.closest('.ac-attach-del');
        if (delBtn) {
            e.preventDefault(); e.stopPropagation();
            const idx = parseInt(delBtn.dataset.idx || '-1', 10);
            if (idx >= 0 && window._ac) window._ac.removeAttachment(idx);
            return;
        }
        if (t.closest('#ac-chip-shot')) {
            e.preventDefault(); e.stopPropagation();
            _chipPrefix(document.getElementById('ac-input'), 'screenshot ');
            return;
        }
        if (t.closest('#ac-chip-img')) {
            e.preventDefault(); e.stopPropagation();
            _chipPrefix(document.getElementById('ac-input'), 'generiere ein bild: ');
            return;
        }
        if (t.closest('#ac-chip-web')) {
            e.preventDefault(); e.stopPropagation();
            _chipPrefix(document.getElementById('ac-input'), 'suche im web nach: ');
            return;
        }
        // Klick außerhalb Favoriten-Panel schließt es
        const favPanel = document.getElementById('ac-fav-panel');
        if (favPanel && favPanel.classList.contains('open')) {
            if (!t.closest('#ac-fav-panel') && !t.closest('#ac-chip-fav')) {
                window._acFav && window._acFav.close();
            }
        }
    });

    document.addEventListener('keydown', function(e) {
        if (e.target && e.target.id === 'ac-input' && e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            window._ac && window._ac.send();
        }
    });

    document.addEventListener('input', function(e) {
        if (e.target && e.target.id === 'ac-input') {
            e.target.style.height = 'auto';
            e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px';
        }
    });
})();

// ─── Topbar-Logik (Delete-Confirm, Edit-Button) ──────────────────────────────
function _initTopbar() {
    // Delete-Confirm Popup
    var btn = document.getElementById('ac-clear-btn');
    var popup = document.getElementById('ac-clear-confirm');
    var yesBtn = document.getElementById('ac-clear-yes');
    var noBtn = document.getElementById('ac-clear-no');
    if (btn && popup) {
        btn.addEventListener('click', function() {
            popup.style.display = popup.style.display === 'none' ? 'block' : 'none';
        });
        if (noBtn) noBtn.addEventListener('click', function() { popup.style.display = 'none'; });
        if (yesBtn) yesBtn.addEventListener('click', function() {
            yesBtn.textContent = 'Lösche...';
            yesBtn.disabled = true;
            // agentId dynamisch aus _acConfig — funktioniert auch nach Agent-Switch
            fetch('/api/history/' + (window._acConfig && window._acConfig.agentId || ''), {method: 'DELETE'})
                .then(function() {
                    document.getElementById('ac-messages').innerHTML =
                        '<div style="color:#3a5a3a;font-size:13px;font-style:italic;text-align:center;padding:32px 0;width:100%">Noch keine Nachrichten. Starte eine Unterhaltung!</div>';
                    popup.style.display = 'none';
                    yesBtn.textContent = 'Löschen';
                    yesBtn.disabled = false;
                });
        });
        document.addEventListener('click', function(e) {
            if (btn && popup && !btn.contains(e.target) && !popup.contains(e.target)) {
                popup.style.display = 'none';
            }
        });
    }
    // Edit-Button — navigiert dynamisch zum aktuellen Agenten
    var editBtn = document.getElementById('ac-edit-btn');
    if (editBtn) {
        editBtn.addEventListener('click', function() {
            var agId = window._acConfig && window._acConfig.agentId;
            if (agId) window.location.href = '/agent/edit/' + agId;
        });
    }
    // Thinking-Toggle
    var thinkBtn = document.getElementById('ac-think-btn');
    if (thinkBtn) {
        function applyThinkState() {
            var on = localStorage.getItem('ac_think') !== '0';  // default: an
            thinkBtn.style.color = on ? '#b794f4' : '#3a5a3a';
            thinkBtn.title = on ? 'Thinking AN — klicken zum Deaktivieren' : 'Thinking AUS — klicken zum Aktivieren';
        }
        applyThinkState();
        thinkBtn.addEventListener('click', function() {
            var on = localStorage.getItem('ac_think') !== '0';
            localStorage.setItem('ac_think', on ? '0' : '1');
            applyThinkState();
        });
    }
}

// ─── SPA Agent-Switch — kein Page-Reload beim Agentenwechsel ─────────────────
function _switchAgent(agentId) {
    // Sidebar: aktiven Agenten visuell markieren
    document.querySelectorAll('[data-agent-id]').forEach(function(el) {
        var isActive = el.dataset.agentId === agentId;
        el.style.background = isActive ? 'rgba(0,230,118,.08)' : 'transparent';
    });

    // Placeholder + Config sofort updaten (optimistic)
    var input = document.getElementById('ac-input');

    fetch('/api/chat/context/' + agentId)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            // Messages austauschen
            var msgs = document.getElementById('ac-messages');
            if (msgs) msgs.innerHTML = data.messages_html;

            // Topbar austauschen
            var topbar = document.getElementById('ac-topbar');
            if (topbar && data.topbar_html) {
                topbar.outerHTML = data.topbar_html;
                _initTopbar();  // Event-Listener neu binden
            }

            // Config updaten
            if (window._acConfig) {
                window._acConfig.agentId = data.agent.id;
                window._acConfig.agentName = data.agent.name;
                window._acConfig.agentVoice = data.agent.voice || '';
            }

            // Textarea Placeholder
            if (input) input.placeholder = 'Nachricht an ' + data.agent.name + '\u2026';

            // URL ohne Page-Reload aktualisieren
            history.pushState({agentId: agentId}, '', '/chat/' + agentId);

            // Scroll to bottom
            var scroll = document.getElementById('ac-scroll');
            if (scroll) scroll.scrollTop = scroll.scrollHeight;
        })
        .catch(function(e) { console.error('Agent-Switch Fehler:', e); });
}

// Sidebar-Klicks abfangen
document.addEventListener('click', function(e) {
    var link = e.target.closest('[data-agent-id]');
    if (!link) return;
    var agentId = link.dataset.agentId;
    if (!agentId) return;
    if (agentId === (window._acConfig && window._acConfig.agentId)) return;
    e.preventDefault();
    _switchAgent(agentId);
});

// Browser Zurück/Vor nach pushState
window.addEventListener('popstate', function(e) {
    var agentId = e.state && e.state.agentId;
    if (agentId) _switchAgent(agentId);
});

// ─── Initialisierung nach DOM ready ──────────────────────────────────────────
// Vue rendert verzögert — mit Retry bis #ac-input im DOM ist
(function initWhenReady() {
    const MAX_TRIES = 30;   // max 3 Sekunden warten
    let tries = 0;
    function tryInit() {
        tries++;
        if (document.getElementById('ac-input')) {
            window._ac.init();
            _initTopbar();
        } else if (tries < MAX_TRIES) {
            setTimeout(tryInit, 100);
        }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', tryInit);
    } else {
        tryInit();
    }
})();
