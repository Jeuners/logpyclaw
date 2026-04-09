/**
 * saved-prompts.js — Eigenständiges Prompt-Speicher-Modul.
 * Keinerlei Abhängigkeiten zum Projektcode. Nur localStorage + DOM.
 *
 * Verhalten:
 *  - ★ Icon an jeder User-Chatbox → einmal klicken speichert den Text
 *  - Kleiner Zähler-Button oben rechts am #mainInput → zeigt Anzahl + öffnet Liste
 */
(function () {
  'use strict';

  const KEY     = 'agentclaw_saved_prompts';
  const MAX     = 20;

  // ── Storage ─────────────────────────────────────────────────────────────────

  function load()        { try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch { return []; } }
  function persist(arr)  { localStorage.setItem(KEY, JSON.stringify(arr)); }

  function savePrompt(text) {
    text = text.trim();
    if (!text || text.length < 4) return false;
    let arr = load().filter(p => p.text !== text);
    arr.unshift({ id: Date.now(), text, ts: new Date().toISOString() });
    if (arr.length > MAX) arr = arr.slice(0, MAX);
    persist(arr);
    return true;
  }

  function deletePrompt(id) { persist(load().filter(p => p.id !== id)); }

  // ── Popup (vom Zähler-Button) ────────────────────────────────────────────────

  function closePopup() { document.getElementById('sp-popup')?.remove(); }

  function renderPopup(anchor) {
    closePopup();
    const prompts = load();

    const popup = document.createElement('div');
    popup.id = 'sp-popup';

    // Position: über dem Anchor-Button, fixiert am Body
    const rect = anchor.getBoundingClientRect();
    Object.assign(popup.style, {
      position:     'fixed',
      bottom:       (window.innerHeight - rect.top + 6) + 'px',
      right:        (window.innerWidth - rect.right) + 'px',
      width:        '290px',
      maxHeight:    '60vh',
      overflowY:    'auto',
      background:   'var(--bg2,#1e1e2e)',
      border:       '1px solid var(--b2,#333)',
      borderRadius: '10px',
      boxShadow:    '0 8px 24px rgba(0,0,0,.55)',
      zIndex:       '99999',
      padding:      '10px',
      fontFamily:   'var(--mono,monospace)',
      fontSize:     '12px',
      color:        'var(--text,#cdd6f4)',
    });

    if (prompts.length === 0) {
      const empty = document.createElement('p');
      empty.textContent = 'Keine gespeicherten Prompts.';
      empty.style.cssText = 'color:var(--textdim,#6c7086);text-align:center;margin:8px 0';
      popup.appendChild(empty);
    } else {
      const hdr = document.createElement('div');
      hdr.textContent = `Gespeicherte Prompts (${prompts.length})`;
      hdr.style.cssText = 'color:var(--textdim,#6c7086);font-size:10px;margin-bottom:7px;text-transform:uppercase;letter-spacing:.05em';
      popup.appendChild(hdr);

      prompts.forEach(p => {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:flex-start;gap:5px;margin-bottom:5px';

        const btn = document.createElement('button');
        btn.textContent = p.text.length > 65 ? p.text.slice(0, 65) + '…' : p.text;
        btn.title = p.text;
        Object.assign(btn.style, {
          flex: '1', padding: '5px 8px',
          background: 'var(--bg3,#313244)', color: 'var(--text,#cdd6f4)',
          border: '1px solid var(--b2,#333)', borderRadius: '6px',
          cursor: 'pointer', textAlign: 'left',
          fontSize: '11px', lineHeight: '1.4', wordBreak: 'break-word',
        });
        btn.onclick = () => {
          const inp = document.getElementById('mainInput');
          if (inp) { inp.value = p.text; inp.dispatchEvent(new Event('input')); inp.focus(); }
          closePopup();
        };

        const del = document.createElement('button');
        del.textContent = '✕';
        Object.assign(del.style, {
          background: 'transparent', border: 'none',
          color: 'var(--textdim,#6c7086)', cursor: 'pointer',
          fontSize: '11px', padding: '4px', flexShrink: '0',
        });
        del.onclick = e => { e.stopPropagation(); deletePrompt(p.id); renderPopup(anchor); };

        row.append(btn, del);
        popup.appendChild(row);
      });
    }

    document.body.appendChild(popup);

    setTimeout(() => {
      document.addEventListener('click', function h(e) {
        if (!popup.contains(e.target) && e.target !== anchor) {
          closePopup();
          document.removeEventListener('click', h);
        }
      });
    }, 0);
  }

  // ── Zähler-Button am Input ───────────────────────────────────────────────────

  function updateCounter() {
    const btn = document.getElementById('sp-counter-btn');
    if (!btn) return;
    const n = load().length;
    btn.textContent = n > 0 ? `★ ${n}` : '★';
    btn.title = n > 0 ? `${n} gespeicherte Prompts` : 'Keine gespeicherten Prompts';
  }

  function injectCounterButton() {
    if (document.getElementById('sp-counter-btn')) { updateCounter(); return; }
    const inputRow = document.querySelector('.input-row');
    if (!inputRow) return;

    const btn = document.createElement('button');
    btn.id = 'sp-counter-btn';
    const n = load().length;
    btn.textContent = n > 0 ? `★ ${n}` : '★';
    btn.title = n > 0 ? `${n} gespeicherte Prompts` : 'Keine gespeicherten Prompts';
    Object.assign(btn.style, {
      background:   'var(--bg3,#313244)',
      border:       '1px solid var(--b2,#333)',
      color:        'var(--accent,#7c3aed)',
      borderRadius: '6px',
      padding:      '5px 8px',
      cursor:       'pointer',
      fontSize:     '12px',
      fontFamily:   'var(--mono,monospace)',
      whiteSpace:   'nowrap',
      flexShrink:   '0',
    });

    btn.onclick = e => {
      e.stopPropagation();
      document.getElementById('sp-popup') ? closePopup() : renderPopup(btn);
    };

    // Vor dem Senden-Button einfügen
    const sendBtn = document.getElementById('sendBtn');
    if (sendBtn) inputRow.insertBefore(btn, sendBtn);
    else inputRow.appendChild(btn);
  }

  // ── Stern-Icon an User-Bubbles ───────────────────────────────────────────────

  function injectStarButtons() {
    document.querySelectorAll('.msg.user').forEach(msgEl => {
      if (msgEl.querySelector('.sp-star-btn')) return;
      const bubble = msgEl.querySelector('.msg-bubble');
      if (!bubble) return;
      const text = bubble.innerText?.trim();
      if (!text || text.length < 4) return;

      const star = document.createElement('button');
      star.className = 'sp-star-btn';
      star.textContent = '★';
      star.title = 'Prompt speichern';
      Object.assign(star.style, {
        position:   'absolute',
        top:        '4px',
        right:      '4px',
        background: 'transparent',
        border:     'none',
        cursor:     'pointer',
        fontSize:   '14px',
        color:      'var(--textdim,#6c7086)',
        opacity:    '0',
        transition: 'opacity .15s, color .15s',
        padding:    '2px 4px',
        lineHeight: '1',
      });

      star.onclick = e => {
        e.stopPropagation();
        const saved = savePrompt(text);
        if (saved) {
          star.style.color = 'var(--accent,#7c3aed)';
          star.title = 'Gespeichert!';
          updateCounter();
          // kurze visuelle Bestätigung
          setTimeout(() => {
            star.style.color = 'var(--textdim,#6c7086)';
            star.title = 'Prompt speichern';
          }, 1500);
        }
      };

      msgEl.style.position = 'relative';
      msgEl.addEventListener('mouseenter', () => star.style.opacity = '0.6');
      msgEl.addEventListener('mouseleave', () => { star.style.opacity = '0'; });

      msgEl.appendChild(star);
    });
  }

  // ── Init ─────────────────────────────────────────────────────────────────────

  let _debounceTimer = null;
  function init() {
    injectCounterButton();
    injectStarButtons();
  }

  const observer = new MutationObserver(() => {
    clearTimeout(_debounceTimer);
    _debounceTimer = setTimeout(init, 200);
  });
  observer.observe(document.body, { childList: true, subtree: true });

  document.readyState === 'loading'
    ? document.addEventListener('DOMContentLoaded', init)
    : init();

})();
