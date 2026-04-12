"""
ui/pages/network.py — M2M / Agent-Netzwerk Übersicht.
Zeigt verbundene Nodes und verteilte Agents via /api/m2m/*.
"""
import logging
from nicegui import ui
from ui.layout import create_layout
from ui.theme import apply_theme

logger = logging.getLogger(__name__)

_NETWORK_JS = r"""
(function() {

  async function loadNodes() {
    try {
      const r = await fetch('/api/m2m/nodes');
      const data = await r.json();
      return data.nodes || [];
    } catch(e) {
      return [];
    }
  }

  async function loadAllAgents() {
    try {
      const r = await fetch('/api/m2m/agents');
      const data = await r.json();
      return data;
    } catch(e) {
      return { local: [], remote: [] };
    }
  }

  async function loadDiscovery() {
    try {
      const r = await fetch('/.well-known/martin-agent.json');
      return await r.json();
    } catch(e) {
      return {};
    }
  }

  async function syncNode(nodeId, btn) {
    btn.disabled = true;
    btn.textContent = '...';
    try {
      const r = await fetch('/api/m2m/nodes/' + nodeId + '/sync', { method: 'POST' });
      if (r.ok) {
        btn.textContent = '✓';
        btn.style.color = 'var(--green)';
        setTimeout(() => { btn.textContent = 'Sync'; btn.style.color = ''; btn.disabled = false; }, 2000);
        await renderNodes();
      } else {
        btn.textContent = '✗';
        btn.style.color = '#ef4444';
        setTimeout(() => { btn.textContent = 'Sync'; btn.style.color = ''; btn.disabled = false; }, 2000);
      }
    } catch(e) {
      btn.textContent = '✗';
      btn.disabled = false;
    }
  }

  async function removeNode(nodeId, name) {
    if (!confirm('Node "' + name + '" entfernen?')) return;
    try {
      await fetch('/api/m2m/nodes/' + nodeId, { method: 'DELETE' });
      await renderNodes();
    } catch(e) {}
  }

  async function addNode() {
    const nameEl = document.getElementById('add-node-name');
    const urlEl = document.getElementById('add-node-url');
    const statusEl = document.getElementById('add-node-status');
    const name = (nameEl?.value || '').trim();
    const url = (urlEl?.value || '').trim();

    if (!name || !url) {
      statusEl.textContent = 'Name und URL sind erforderlich.';
      statusEl.style.color = '#ef4444';
      return;
    }

    statusEl.textContent = 'Verbinde...';
    statusEl.style.color = 'var(--textdim)';

    try {
      const r = await fetch('/api/m2m/nodes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, url }),
      });
      if (r.ok) {
        statusEl.textContent = '✓ Node hinzugefügt';
        statusEl.style.color = 'var(--green)';
        nameEl.value = '';
        urlEl.value = '';
        await renderNodes();
        await renderRemoteAgents();
      } else {
        const err = await r.json().catch(() => ({}));
        statusEl.textContent = '✗ ' + (err.detail || 'Fehler');
        statusEl.style.color = '#ef4444';
      }
    } catch(e) {
      statusEl.textContent = '✗ Fehler: ' + e.message;
      statusEl.style.color = '#ef4444';
    }
  }

  async function renderNodes() {
    const container = document.getElementById('nodes-list');
    if (!container) return;

    const nodes = await loadNodes();

    if (!nodes.length) {
      container.innerHTML = '<div class="net-empty">Keine verbundenen Nodes. Node oben hinzufügen.</div>';
      return;
    }

    container.innerHTML = '';
    nodes.forEach(node => {
      const online = node.status === 'online';
      const agentCount = (node.agent_cache || []).length;
      const lastSeen = node.last_seen ? new Date(node.last_seen).toLocaleString('de-DE') : '–';

      const el = document.createElement('div');
      el.className = 'net-node-card';
      el.innerHTML = `
        <div class="net-node-header">
          <div class="net-dot ${online ? 'online' : ''}"></div>
          <div class="net-node-info">
            <div class="net-node-name">${node.node_name || node.name || node.id}</div>
            <div class="net-node-url">${node.base_url || node.url || ''}</div>
          </div>
          <div class="net-node-badges">
            <span class="net-badge">${agentCount} Agents</span>
            <span class="net-badge ${online ? 'online' : ''}">${online ? 'Online' : 'Offline'}</span>
          </div>
          <div class="net-node-actions">
            <button class="net-btn net-btn-sync" data-id="${node.id}">Sync</button>
            <button class="net-btn net-btn-remove" data-id="${node.id}" data-name="${node.node_name || node.id}">✕</button>
          </div>
        </div>
        <div class="net-node-footer">Zuletzt gesehen: ${lastSeen}</div>
      `;

      el.querySelector('.net-btn-sync').addEventListener('click', function() {
        syncNode(node.id, this);
      });
      el.querySelector('.net-btn-remove').addEventListener('click', function() {
        removeNode(node.id, this.dataset.name);
      });

      container.appendChild(el);
    });
  }

  async function renderRemoteAgents() {
    const container = document.getElementById('remote-agents-list');
    if (!container) return;

    const data = await loadAllAgents();
    const remote = data.remote || [];

    if (!remote.length) {
      container.innerHTML = '<div class="net-empty">Keine Remote-Agents. Node synchronisieren.</div>';
      return;
    }

    container.innerHTML = '';
    remote.forEach(agent => {
      const online = !!agent.node_online;
      const el = document.createElement('div');
      el.className = 'net-agent-row';
      el.innerHTML = `
        <div class="net-dot ${online ? 'online' : ''}" style="margin-top:2px"></div>
        <div style="flex:1;min-width:0;">
          <div style="font-size:13px;font-weight:500;color:var(--textbr)">${agent.name}</div>
          <div style="font-size:10px;color:var(--textdim);font-family:var(--mono)">${agent.node_name || ''}  ·  @${agent.mention_prefix || ''}</div>
        </div>
        <div style="font-size:10px;font-family:var(--mono);color:var(--textdim);padding:2px 6px;
                    background:var(--b1);border-radius:3px;flex-shrink:0">${agent.model || '–'}</div>
      `;
      container.appendChild(el);
    });
  }

  async function renderDiscovery() {
    const container = document.getElementById('discovery-info');
    if (!container) return;
    const info = await loadDiscovery();
    container.innerHTML = `
      <div class="net-discovery-row"><span>Node ID</span><code>${info.node_id || '–'}</code></div>
      <div class="net-discovery-row"><span>Node Name</span><code>${info.node_name || '–'}</code></div>
      <div class="net-discovery-row"><span>Public URL</span><code>${info.public_url || '(nicht konfiguriert)'}</code></div>
      <div class="net-discovery-row"><span>Version</span><code>${info.version || '–'}</code></div>
    `;
  }

  function init() {
    const addBtn = document.getElementById('btn-add-node');
    if (addBtn) addBtn.addEventListener('click', addNode);

    const urlInput = document.getElementById('add-node-url');
    if (urlInput) {
      urlInput.addEventListener('keydown', e => { if (e.key === 'Enter') addNode(); });
    }

    renderDiscovery();
    renderNodes();
    renderRemoteAgents();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    setTimeout(init, 100);
  }
})();
"""

_NETWORK_CSS = """
<style>
.net-section {
  background: var(--bg2);
  border: 1px solid var(--b1);
  border-radius: 8px;
  padding: 20px;
}
.net-section-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--textdim);
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 14px;
}
.net-node-card {
  border: 1px solid var(--b1);
  border-radius: 6px;
  padding: 12px 14px;
  margin-bottom: 8px;
  background: var(--bg3);
  transition: border-color .15s;
}
.net-node-card:hover {
  border-color: var(--b2);
}
.net-node-header {
  display: flex;
  align-items: center;
  gap: 10px;
}
.net-node-info {
  flex: 1;
  min-width: 0;
}
.net-node-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--textbr);
}
.net-node-url {
  font-size: 10px;
  font-family: var(--mono);
  color: var(--textdim);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.net-node-badges {
  display: flex;
  gap: 5px;
  flex-shrink: 0;
}
.net-badge {
  font-size: 10px;
  font-family: var(--mono);
  padding: 2px 6px;
  border-radius: 3px;
  background: var(--b1);
  color: var(--textdim);
  border: 1px solid var(--b2);
}
.net-badge.online {
  color: var(--green);
  border-color: rgba(0,230,118,.3);
  background: rgba(0,230,118,.08);
}
.net-node-actions {
  display: flex;
  gap: 5px;
  flex-shrink: 0;
}
.net-node-footer {
  font-size: 10px;
  color: var(--textdim);
  font-family: var(--mono);
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--b1);
}
.net-btn {
  font-size: 11px;
  padding: 3px 9px;
  border-radius: 4px;
  border: 1px solid var(--b2);
  background: transparent;
  color: var(--textdim);
  cursor: pointer;
  transition: border-color .15s, color .15s;
}
.net-btn:hover:not([disabled]) { border-color: var(--green); color: var(--green); }
.net-btn-remove:hover:not([disabled]) { border-color: #ef4444; color: #ef4444; }
.net-btn[disabled] { opacity: 0.4; cursor: default; }
.net-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--textdim);
  flex-shrink: 0;
}
.net-dot.online { background: var(--green); box-shadow: 0 0 5px var(--green); }
.net-empty {
  color: var(--textdim);
  font-size: 12px;
  padding: 20px;
  text-align: center;
}
.net-add-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.net-input {
  flex: 1;
  min-width: 140px;
  padding: 7px 10px;
  border-radius: 5px;
  border: 1px solid var(--b2);
  background: var(--bg3);
  color: var(--textbr);
  font-size: 12px;
  outline: none;
  font-family: var(--mono);
  transition: border-color .15s;
}
.net-input:focus { border-color: var(--green); }
.net-input::placeholder { color: var(--textdim); }
#btn-add-node {
  padding: 7px 14px;
  border-radius: 5px;
  border: 1px solid var(--green);
  background: rgba(0,230,118,.08);
  color: var(--green);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: background .15s;
  white-space: nowrap;
}
#btn-add-node:hover { background: rgba(0,230,118,.16); }
#add-node-status {
  font-size: 11px;
  font-family: var(--mono);
  color: var(--textdim);
  min-height: 16px;
  width: 100%;
}
.net-agent-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 5px;
  transition: background .12s;
}
.net-agent-row:hover { background: var(--bg3); }
.net-discovery-row {
  display: flex;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px solid var(--b1);
  font-size: 12px;
  align-items: baseline;
}
.net-discovery-row:last-child { border-bottom: none; }
.net-discovery-row span { color: var(--textdim); width: 100px; flex-shrink: 0; }
.net-discovery-row code {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--textbr);
  word-break: break-all;
}
</style>
"""


@ui.page("/network")
def network_page():
    apply_theme()
    create_layout("network")

    ui.add_head_html(_NETWORK_CSS)

    with ui.column().classes("w-full max-w-5xl mx-auto p-6 gap-6"):
        # Header
        with ui.row().classes("items-center justify-between w-full"):
            with ui.column().classes("gap-1"):
                ui.label("M2M Netzwerk").classes("text-2xl font-bold").style("color: var(--textbr)")
                ui.label("Machine-to-Machine Peer-Nodes und verteilte Agents").style(
                    "font-size: 12px; color: var(--textdim); font-family: var(--mono)"
                )

        # Node hinzufügen
        with ui.element("div").classes("net-section"):
            ui.html('<div class="net-section-title">Node hinzufügen</div>')
            ui.html("""
                <div class="net-add-row">
                    <input id="add-node-name" class="net-input" placeholder="Name (z.B. Server-2)" style="max-width:180px">
                    <input id="add-node-url" class="net-input" placeholder="URL (z.B. https://my-server:5050)">
                    <button id="btn-add-node">+ Verbinden</button>
                </div>
                <div id="add-node-status" style="margin-top:6px"></div>
            """)

        # Verbundene Nodes
        with ui.element("div").classes("net-section"):
            ui.html('<div class="net-section-title">Verbundene Nodes</div>')
            ui.html('<div id="nodes-list"></div>')

        # Remote Agents
        with ui.element("div").classes("net-section"):
            ui.html('<div class="net-section-title">Remote Agents</div>')
            ui.html('<div id="remote-agents-list"></div>')

        # Discovery Info
        with ui.element("div").classes("net-section"):
            ui.html('<div class="net-section-title">Dieser Node (Discovery)</div>')
            ui.html('<div id="discovery-info"></div>')
            ui.html("""
                <div style="margin-top:10px;font-size:11px;color:var(--textdim)">
                    Discovery-Endpoint: <code style="font-family:var(--mono)">/.well-known/martin-agent.json</code>
                    &nbsp;·&nbsp;
                    <a href="/settings" style="color:var(--textdim);text-decoration:none">
                        Node-URL konfigurieren →
                    </a>
                </div>
            """)

    ui.add_body_html(f"<script>{_NETWORK_JS}</script>")
