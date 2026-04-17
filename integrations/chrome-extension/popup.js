/**
 * AgentClaw Browser Skill — Popup Script
 */

const dot        = document.getElementById('dot');
const statusLabel = document.getElementById('status-label');
const statusUrl   = document.getElementById('status-url');
const logEl       = document.getElementById('log');

const STATE_LABELS = {
  connected:    'Verbunden ✓',
  disconnected: 'Nicht verbunden',
  connecting:   'Verbinde…',
};

// ── UI-Helfer ────────────────────────────────────────────────────────────────

function setConnectionState(state) {
  dot.className = 'dot ' + state;
  statusLabel.textContent = STATE_LABELS[state] ?? state;

  const connected = state === 'connected';
  document.querySelectorAll('.cmd-btn').forEach(btn => btn.disabled = !connected);
}

function log(msg, type = '') {
  const el = document.createElement('div');
  el.className = 'entry ' + type;
  el.textContent = `${new Date().toLocaleTimeString()} ${msg}`;
  logEl.prepend(el);
  // Max 50 Einträge
  while (logEl.children.length > 50) logEl.removeChild(logEl.lastChild);
}

// ── Initialisierung ──────────────────────────────────────────────────────────

// Status vom Background abrufen — mit Retry falls SW noch startet
function queryState(retries = 3) {
  chrome.runtime.sendMessage({ type: 'get_state' }, (resp) => {
    if (chrome.runtime.lastError) {
      if (retries > 0) {
        setTimeout(() => queryState(retries - 1), 500);
      } else {
        log('Service Worker nicht erreichbar — Extension neu laden?', 'err');
      }
      return;
    }
    if (resp?.state) {
      setConnectionState(resp.state);
      log(`Status: ${resp.state}`, resp.state === 'connected' ? 'ok' : '');
    }
  });
}
queryState();

// Status-Updates vom Background empfangen
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'connection_state') {
    setConnectionState(msg.state);
    log(STATE_LABELS[msg.state] ?? msg.state, msg.state === 'connected' ? 'ok' : msg.state === 'disconnected' ? 'err' : 'inf');
  }
});

// ── Buttons ──────────────────────────────────────────────────────────────────

document.getElementById('btn-connect').addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'reset_reconnect' });
  log('Verbindungsversuch…', 'inf');
});

document.getElementById('btn-disconnect').addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'disconnect' });
  log('Manuell getrennt', 'err');
});

document.getElementById('log-clear').addEventListener('click', () => {
  logEl.innerHTML = '';
});

// ── Schnellbefehle ────────────────────────────────────────────────────────────

async function sendCommand(command, params = {}) {
  try {
    const resp = await fetch('http://localhost:5050/api/chrome/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command, params }),
    });
    const data = await resp.json();
    if (data.error) {
      log(`Fehler: ${data.error}`, 'err');
    } else {
      log(data.text || `${command} OK`, 'ok');
    }
    return data;
  } catch (e) {
    log(`Anfrage-Fehler: ${e.message}`, 'err');
    return null;
  }
}

document.getElementById('cmd-screenshot').addEventListener('click', async () => {
  log('Screenshot…', 'inf');
  const result = await sendCommand('screenshot');
  if (result?.screenshot) {
    // Screenshot im neuen Tab öffnen
    const tab = await chrome.tabs.create({ url: result.screenshot });
    log(`Screenshot geöffnet (Tab ${tab.id})`, 'ok');
  }
});

document.getElementById('cmd-content').addEventListener('click', async () => {
  log('Lese Seiteninhalt…', 'inf');
  await sendCommand('get_content');
});

document.getElementById('cmd-url').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  log(`URL: ${tab?.url || '?'}`, 'inf');
});

document.getElementById('cmd-title').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  log(`Titel: ${tab?.title || '?'}`, 'inf');
});
