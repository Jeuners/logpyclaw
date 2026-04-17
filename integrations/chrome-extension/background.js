/**
 * AgentClaw Browser Skill — Service Worker (background.js)
 *
 * Verbindet sich per WebSocket mit AgentClaw (ws://localhost:5050/api/chrome/ws).
 * Empfängt Befehle, führt sie aus, sendet Ergebnisse zurück.
 */

const WS_URL = 'ws://localhost:5050/api/chrome/ws';
const RECONNECT_BASE_MS = 2000;
const MAX_RECONNECT_ATTEMPTS = 20;
const KEEPALIVE_INTERVAL_MS = 20000; // 20s — hält Service Worker am Leben

let ws = null;
let reconnectAttempts = 0;
let connectionState = 'disconnected';
let reconnectTimer = null;
let keepaliveTimer = null;

function startKeepalive() {
  if (keepaliveTimer) return;
  // Chrome.alarms nicht verfügbar in allen Kontexten, daher setInterval
  keepaliveTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      // Ping senden (AgentClaw ignoriert unbekannte Messages)
      ws.send(JSON.stringify({ type: 'ping' }));
    } else if (connectionState === 'disconnected') {
      connect();
    }
  }, KEEPALIVE_INTERVAL_MS);
}

function stopKeepalive() {
  if (keepaliveTimer) { clearInterval(keepaliveTimer); keepaliveTimer = null; }
}

// ── WebSocket-Verbindung ────────────────────────────────────────────────────

function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }

  setState('connecting');

  try {
    ws = new WebSocket(WS_URL);
  } catch (e) {
    console.error('[AgentClaw] WS Konstruktor-Fehler:', e);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    console.log('[AgentClaw] Verbunden mit', WS_URL);
    reconnectAttempts = 0;
    setState('connected');
    startKeepalive();
  };

  ws.onmessage = async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      console.error('[AgentClaw] Ungültiges JSON:', event.data?.slice(0, 100));
      return;
    }

    const { request_id, command, ...params } = msg;
    console.log('[AgentClaw] Command:', command, 'id:', request_id?.slice(0, 8));

    const result = await handleCommand(command, params);
    const response = JSON.stringify({ request_id, ...result });

    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(response);
    } else {
      console.warn('[AgentClaw] WS nicht offen — Antwort verloren:', request_id?.slice(0, 8));
    }
  };

  ws.onerror = (err) => {
    console.error('[AgentClaw] WS Fehler:', err.type);
  };

  ws.onclose = (event) => {
    console.log('[AgentClaw] WS geschlossen:', event.code, event.reason);
    setState('disconnected');
    stopKeepalive();
    ws = null;
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
    console.warn('[AgentClaw] Max Reconnect-Versuche erreicht');
    return;
  }
  const delay = RECONNECT_BASE_MS * Math.min(Math.pow(1.5, reconnectAttempts), 15);
  reconnectAttempts++;
  console.log(`[AgentClaw] Reconnect in ${Math.round(delay / 1000)}s (Versuch ${reconnectAttempts})`);
  reconnectTimer = setTimeout(connect, delay);
}

function setState(state) {
  connectionState = state;
  // Popup informieren (falls offen)
  chrome.runtime.sendMessage({ type: 'connection_state', state }).catch(() => {});
}

// ── Befehlsverarbeitung ─────────────────────────────────────────────────────

async function handleCommand(command, params) {
  try {
    switch (command) {
      case 'screenshot':   return await cmdScreenshot(params);
      case 'get_content':  return await cmdGetContent(params);
      case 'navigate':     return await cmdNavigate(params);
      case 'click':        return await cmdClick(params);
      case 'fill_form':    return await cmdFillForm(params);
      case 'evaluate_js':  return await cmdEvaluateJs(params);
      default:             return { error: `Unbekannter Befehl: ${command}` };
    }
  } catch (e) {
    console.error('[AgentClaw] Command-Fehler:', command, e);
    return { error: e.message || String(e) };
  }
}

async function getActiveTab() {
  // 1. Aktiver Tab in normalem Fenster
  let tabs = await chrome.tabs.query({ active: true, windowType: 'normal' });
  if (tabs.length) return tabs[0];

  // 2. Aktiver Tab in beliebigem Fenster
  tabs = await chrome.tabs.query({ active: true });
  if (tabs.length) return tabs[0];

  // 3. Neuester normaler Tab (sortiert nach lastAccessed)
  tabs = await chrome.tabs.query({ windowType: 'normal' });
  if (tabs.length) {
    tabs.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0));
    return tabs[0];
  }

  // 4. Absoluter Fallback: alle Tabs außer chrome:// internen Seiten
  tabs = await chrome.tabs.query({});
  tabs = tabs.filter(t => t.url && !t.url.startsWith('chrome://') && !t.url.startsWith('chrome-extension://'));
  if (tabs.length) {
    tabs.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0));
    return tabs[0];
  }

  throw new Error('Kein aktiver Tab gefunden');
}

async function cmdScreenshot() {
  const tab = await getActiveTab();
  // Screenshot des sichtbaren Bereichs
  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
    format: 'jpeg',
    quality: 80,
  });
  return {
    screenshot: dataUrl,
    text: 'Screenshot aufgenommen',
    url: tab.url,
    title: tab.title,
  };
}

async function cmdGetContent({ maxChars = 8000 } = {}) {
  const tab = await getActiveTab();
  const [result] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: (max) => {
      const text = document.body?.innerText || document.documentElement?.innerText || '';
      return {
        text: text.slice(0, max),
        title: document.title,
        url: location.href,
        charCount: text.length,
      };
    },
    args: [maxChars],
  });
  const data = result.result;
  return {
    text: data.text,
    url: data.url,
    title: data.title,
    meta: `${data.charCount} Zeichen gesamt, ${Math.min(data.charCount, maxChars)} zurückgegeben`,
  };
}

async function cmdNavigate({ url } = {}) {
  if (!url) return { error: 'url Parameter fehlt' };
  const tab = await getActiveTab();

  await chrome.tabs.update(tab.id, { url });

  // Warten bis Seite geladen ist (max 15s)
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(); // Timeout → trotzdem fortfahren
    }, 15000);

    function listener(tabId, info) {
      if (tabId === tab.id && info.status === 'complete') {
        clearTimeout(timeout);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
  });

  const updatedTab = await chrome.tabs.get(tab.id);
  return {
    text: `Navigiert zu: ${updatedTab.title || url}`,
    url: updatedTab.url || url,
  };
}

async function cmdClick({ selector } = {}) {
  if (!selector) return { error: 'selector Parameter fehlt' };
  const tab = await getActiveTab();
  const [result] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: (sel) => {
      const el = document.querySelector(sel);
      if (!el) return { error: `Element nicht gefunden: ${sel}` };
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.click();
      return {
        text: `Geklickt: ${sel}`,
        tagName: el.tagName,
        innerText: el.innerText?.slice(0, 100),
      };
    },
    args: [selector],
  });
  return result.result;
}

async function cmdFillForm({ selector, value = '' } = {}) {
  if (!selector) return { error: 'selector Parameter fehlt' };
  const tab = await getActiveTab();
  const [result] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: (sel, val) => {
      const el = document.querySelector(sel);
      if (!el) return { error: `Element nicht gefunden: ${sel}` };
      el.focus();
      el.value = val;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return { text: `Ausgefüllt: ${sel} = "${val.slice(0, 50)}"` };
    },
    args: [selector, String(value)],
  });
  return result.result;
}

async function cmdEvaluateJs({ code } = {}) {
  if (!code) return { error: 'code Parameter fehlt' };
  const tab = await getActiveTab();
  const [result] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: (jsCode) => {
      try {
        // eslint-disable-next-line no-new-func
        const fn = new Function(`return (${jsCode})`);
        const out = fn();
        if (out === undefined) return { text: '(kein Rückgabewert)' };
        if (typeof out === 'object') return { text: JSON.stringify(out, null, 2).slice(0, 4000) };
        return { text: String(out).slice(0, 4000) };
      } catch (e) {
        return { error: e.message };
      }
    },
    args: [code],
  });
  return result.result;
}

// ── Nachrichten von Popup ──────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'get_state') {
    sendResponse({ state: connectionState });
    return true;
  }
  if (msg.type === 'connect') {
    reconnectAttempts = 0;
    connect();
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === 'disconnect') {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    reconnectAttempts = MAX_RECONNECT_ATTEMPTS; // Verhindert Auto-Reconnect
    if (ws) ws.close(1000, 'Manuell getrennt');
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === 'reset_reconnect') {
    reconnectAttempts = 0;
    connect();
    sendResponse({ ok: true });
    return true;
  }
});

// ── Auto-Connect beim Start ─────────────────────────────────────────────────
connect();
