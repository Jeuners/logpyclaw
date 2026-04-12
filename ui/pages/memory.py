"""
ui/pages/memory.py — Memory/Qdrant Viewer.
Zeigt gespeicherte Erinnerungen (Vektoren) pro Agent als Übersicht.
"""
import logging
from nicegui import ui
from ui.layout import create_layout
from ui.theme import apply_theme

logger = logging.getLogger(__name__)

_MEMORY_JS = r"""
(function() {
  async function loadAgents() {
    try {
      const r = await fetch('/api/agents');
      const data = await r.json();
      return data || [];
    } catch(e) {
      return [];
    }
  }

  async function loadMemoryCount(agentId) {
    try {
      const r = await fetch('/api/memory/' + agentId);
      const data = await r.json();
      return data;
    } catch(e) {
      return { count: 0, error: 'Verbindungsfehler' };
    }
  }

  async function clearMemory(agentId, agentName, card) {
    if (!confirm('Memory von "' + agentName + '" wirklich löschen?')) return;
    try {
      const r = await fetch('/api/memory/' + agentId, { method: 'DELETE' });
      if (r.ok) {
        const countEl = card.querySelector('.mem-count');
        if (countEl) countEl.textContent = '0';
        const infoEl = card.querySelector('.mem-info');
        if (infoEl) infoEl.textContent = 'Gelöscht';
      }
    } catch(e) {
      alert('Fehler beim Löschen: ' + e.message);
    }
  }

  async function render() {
    const container = document.getElementById('memory-list');
    if (!container) return;

    container.innerHTML = '<div class="mem-loading">Lade Agenten...</div>';

    const agents = await loadAgents();
    if (!agents.length) {
      container.innerHTML = '<div class="mem-empty">Keine Agenten gefunden.</div>';
      return;
    }

    container.innerHTML = '';

    const promises = agents.map(async (agent) => {
      const memData = await loadMemoryCount(agent.id);
      const color = agent.color || '#3a5a3a';
      const initials = (agent.name || '?').slice(0, 2).toUpperCase();
      const count = memData.count || 0;
      const hasError = !!memData.error;

      const card = document.createElement('div');
      card.className = 'mem-card';
      card.innerHTML = \`
        <div class="mem-card-header">
          <div class="mem-avatar" style="background:\${color}">\${initials}</div>
          <div class="mem-card-info">
            <div class="mem-agent-name">\${agent.name}</div>
            <div class="mem-agent-model" style="font-family:var(--mono);font-size:10px;color:var(--textdim)">\${agent.model || 'kein Modell'}</div>
          </div>
          <div class="mem-count-badge">
            <span class="mem-count">\${count}</span>
            <span style="font-size:10px;color:var(--textdim);margin-left:3px">Vektoren</span>
          </div>
        </div>
        <div class="mem-card-footer">
          <span class="mem-info" style="font-size:11px;color:\${hasError ? '#ef4444' : 'var(--textdim)'}">
            \${hasError ? '⚠ ' + memData.error : (count > 0 ? 'Qdrant Collection aktiv' : 'Keine Einträge')}
          </span>
          <button class="mem-clear-btn" \${count === 0 ? 'disabled' : ''}>Löschen</button>
        </div>
      \`;

      card.querySelector('.mem-clear-btn').addEventListener('click', () => {
        clearMemory(agent.id, agent.name, card);
      });

      return card;
    });

    const cards = await Promise.all(promises);
    cards.forEach(c => container.appendChild(c));
  }

  // Warten bis DOM bereit
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render);
  } else {
    setTimeout(render, 100);
  }
})();
"""

_MEMORY_CSS = """
<style>
#memory-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
  padding: 0;
}
.mem-card {
  background: var(--bg2);
  border: 1px solid var(--b1);
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: border-color .15s, box-shadow .15s;
}
.mem-card:hover {
  border-color: var(--green);
  box-shadow: 0 0 16px rgba(0,230,118,.06);
}
.mem-card-header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.mem-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  color: #fff;
  flex-shrink: 0;
}
.mem-card-info {
  flex: 1;
  min-width: 0;
}
.mem-agent-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--textbr);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.mem-count-badge {
  display: flex;
  align-items: baseline;
  gap: 0;
  flex-shrink: 0;
}
.mem-count {
  font-size: 22px;
  font-weight: 700;
  color: var(--green);
  font-family: var(--mono);
}
.mem-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.mem-clear-btn {
  font-size: 11px;
  padding: 3px 10px;
  border-radius: 4px;
  border: 1px solid var(--b2);
  background: transparent;
  color: var(--textdim);
  cursor: pointer;
  transition: border-color .15s, color .15s;
}
.mem-clear-btn:hover:not([disabled]) {
  border-color: #ef4444;
  color: #ef4444;
}
.mem-clear-btn[disabled] {
  opacity: 0.35;
  cursor: default;
}
.mem-loading, .mem-empty {
  color: var(--textdim);
  font-size: 13px;
  padding: 32px;
  text-align: center;
}
</style>
"""


@ui.page("/memory")
def memory_page():
    apply_theme()
    create_layout("memory")

    ui.add_head_html(_MEMORY_CSS)

    with ui.column().classes("w-full max-w-6xl mx-auto p-6 gap-6"):
        # Header
        with ui.row().classes("items-center justify-between w-full"):
            with ui.column().classes("gap-1"):
                ui.label("Memory / Qdrant").classes("text-2xl font-bold").style("color: var(--textbr)")
                ui.label("Vektor-Speicher pro Agent (Qdrant)").style(
                    "font-size: 12px; color: var(--textdim); font-family: var(--mono)"
                )
            with ui.row().classes("gap-2 items-center"):
                ui.html(
                    '<a href="/settings" style="font-size:11px;color:var(--textdim);'
                    'text-decoration:none;padding:4px 10px;border:1px solid var(--b2);'
                    'border-radius:4px;">Qdrant konfigurieren →</a>'
                )

        # Info-Banner
        ui.html("""
            <div style="background: rgba(0,230,118,0.05); border: 1px solid var(--b2);
                        border-radius: 6px; padding: 10px 14px; font-size: 12px;
                        color: var(--textdim); display: flex; align-items: center; gap: 8px;">
                <span class="material-icons" style="font-size:16px;color:var(--green)">info</span>
                Jeder Agent hat eine eigene Qdrant-Collection. Dokumente und Wissenseinträge
                werden als Vektoren gespeichert und beim Chat abgefragt.
            </div>
        """)

        # Memory-Liste (dynamisch via JS)
        ui.html('<div id="memory-list"></div>')

    ui.add_body_html(f"<script>{_MEMORY_JS}</script>")
