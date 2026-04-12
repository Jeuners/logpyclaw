"""
ui/pages/backup.py — Backup & Restore UI.
Nutzt die existierenden /api/backup Endpoints via client-seitiges JS.
"""
import logging
from nicegui import ui
from ui.layout import create_layout
from ui.theme import apply_theme

logger = logging.getLogger(__name__)

_BACKUP_JS = r"""
(function() {

  async function loadBackups() {
    const list = document.getElementById('backup-list');
    if (!list) return;
    list.innerHTML = '<div class="bk-loading">Lade Backups...</div>';

    try {
      const r = await fetch('/api/backup/list');
      const backups = await r.json();

      if (!backups.length) {
        list.innerHTML = '<div class="bk-empty">Noch keine Backups vorhanden.</div>';
        return;
      }

      list.innerHTML = '';
      backups.forEach(b => {
        const sizeKb = (b.size / 1024).toFixed(1);
        const date = new Date(b.modified).toLocaleString('de-DE');

        const row = document.createElement('div');
        row.className = 'bk-row';
        row.innerHTML = `
          <div class="bk-row-icon">
            <span class="material-icons" style="font-size:20px;color:var(--green)">archive</span>
          </div>
          <div class="bk-row-info">
            <div class="bk-row-name">${b.name}</div>
            <div class="bk-row-meta">${date} &nbsp;·&nbsp; ${sizeKb} KB</div>
          </div>
          <div class="bk-row-actions">
            <button class="bk-btn bk-btn-restore" data-name="${b.name}">Wiederherstellen</button>
            <a class="bk-btn bk-btn-dl" href="/api/backup/download/${b.name}" download="${b.name}">
              <span class="material-icons" style="font-size:14px;vertical-align:middle">download</span>
            </a>
          </div>
        `;

        row.querySelector('.bk-btn-restore').addEventListener('click', () => {
          restoreBackup(b.name);
        });

        list.appendChild(row);
      });
    } catch(e) {
      list.innerHTML = '<div class="bk-empty" style="color:#ef4444">Fehler: ' + e.message + '</div>';
    }
  }

  async function createBackup() {
    const btn = document.getElementById('btn-create-backup');
    const status = document.getElementById('backup-status');
    if (!btn) return;

    btn.disabled = true;
    btn.textContent = 'Erstelle Backup...';
    status.textContent = '';
    status.style.color = 'var(--textdim)';

    try {
      const r = await fetch('/api/backup', { method: 'POST' });
      const data = await r.json();

      if (data.ok) {
        status.textContent = '✓ Backup erstellt: ' + data.backup_file;
        status.style.color = 'var(--green)';
        await loadBackups();
      } else {
        status.textContent = '✗ Fehler beim Erstellen';
        status.style.color = '#ef4444';
      }
    } catch(e) {
      status.textContent = '✗ Fehler: ' + e.message;
      status.style.color = '#ef4444';
    } finally {
      btn.disabled = false;
      btn.textContent = 'Backup erstellen';
    }
  }

  async function restoreBackup(name) {
    if (!confirm('Backup "' + name + '" wirklich wiederherstellen?\\n\\nAchtung: Aktuelle Daten werden überschrieben!')) {
      return;
    }

    const status = document.getElementById('backup-status');
    status.textContent = 'Stelle wieder her...';
    status.style.color = 'var(--textdim)';

    try {
      const r = await fetch('/api/backup/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ backup_name: name }),
      });
      const data = await r.json();

      if (data.ok) {
        status.textContent = '✓ ' + (data.message || 'Wiederhergestellt. Bitte neu starten.');
        status.style.color = 'var(--green)';
      } else {
        status.textContent = '✗ Fehler bei der Wiederherstellung';
        status.style.color = '#ef4444';
      }
    } catch(e) {
      status.textContent = '✗ Fehler: ' + e.message;
      status.style.color = '#ef4444';
    }
  }

  function init() {
    const createBtn = document.getElementById('btn-create-backup');
    if (createBtn) {
      createBtn.addEventListener('click', createBackup);
    }
    loadBackups();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    setTimeout(init, 100);
  }
})();
"""

_BACKUP_CSS = """
<style>
#backup-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.bk-row {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--bg2);
  border: 1px solid var(--b1);
  border-radius: 8px;
  padding: 12px 16px;
  transition: border-color .15s;
}
.bk-row:hover {
  border-color: var(--b2);
}
.bk-row-icon {
  flex-shrink: 0;
}
.bk-row-info {
  flex: 1;
  min-width: 0;
}
.bk-row-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--textbr);
  font-family: var(--mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.bk-row-meta {
  font-size: 11px;
  color: var(--textdim);
  margin-top: 2px;
}
.bk-row-actions {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-shrink: 0;
}
.bk-btn {
  font-size: 11px;
  padding: 4px 10px;
  border-radius: 4px;
  border: 1px solid var(--b2);
  background: transparent;
  color: var(--textdim);
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  transition: border-color .15s, color .15s;
}
.bk-btn:hover {
  border-color: var(--green);
  color: var(--green);
}
.bk-btn-restore:hover {
  border-color: var(--orange);
  color: var(--orange);
}
.bk-loading, .bk-empty {
  color: var(--textdim);
  font-size: 13px;
  padding: 32px;
  text-align: center;
}
#btn-create-backup {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 18px;
  border-radius: 6px;
  border: 1px solid var(--green);
  background: rgba(0,230,118,.08);
  color: var(--green);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background .15s, box-shadow .15s;
}
#btn-create-backup:hover:not([disabled]) {
  background: rgba(0,230,118,.16);
  box-shadow: 0 0 12px rgba(0,230,118,.2);
}
#btn-create-backup[disabled] {
  opacity: 0.5;
  cursor: default;
}
#backup-status {
  font-size: 12px;
  font-family: var(--mono);
  color: var(--textdim);
  min-height: 18px;
}
</style>
"""


@ui.page("/backup")
def backup_page():
    apply_theme()
    create_layout("backup")

    ui.add_head_html(_BACKUP_CSS)

    with ui.column().classes("w-full max-w-4xl mx-auto p-6 gap-6"):
        # Header
        with ui.row().classes("items-center justify-between w-full"):
            with ui.column().classes("gap-1"):
                ui.label("Backup & Restore").classes("text-2xl font-bold").style("color: var(--textbr)")
                ui.label("Agenten, Provider und Watchdog-Konfigurationen sichern").style(
                    "font-size: 12px; color: var(--textdim); font-family: var(--mono)"
                )

        # Aktionen
        with ui.element("div").style(
            "background: var(--bg2); border: 1px solid var(--b1); border-radius: 8px; padding: 20px;"
        ):
            ui.html('<div style="font-size:12px;font-weight:700;color:var(--textdim);'
                    'text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;">Neues Backup</div>')

            ui.html("""
                <div style="font-size:12px;color:var(--textdim);margin-bottom:14px;">
                    Sichert alle Agenten (<code style="font-family:var(--mono)">agents.json</code>),
                    Provider-Konfigurationen (<code style="font-family:var(--mono)">providers.json</code>)
                    und Watchdogs als ZIP-Archiv.
                    Chat-History wird aus Platzgründen nicht gesichert.
                </div>
            """)

            ui.html("""
                <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;">
                    <button id="btn-create-backup">
                        <span class="material-icons" style="font-size:16px">backup</span>
                        Backup erstellen
                    </button>
                    <span id="backup-status"></span>
                </div>
            """)

        # Backup-Liste
        with ui.element("div").style(
            "background: var(--bg2); border: 1px solid var(--b1); border-radius: 8px; padding: 20px;"
        ):
            ui.html('<div style="font-size:12px;font-weight:700;color:var(--textdim);'
                    'text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;">Gespeicherte Backups</div>')
            ui.html('<div id="backup-list"></div>')

        # Hinweis
        ui.html("""
            <div style="background: rgba(255,107,53,.06); border: 1px solid rgba(255,107,53,.2);
                        border-radius: 6px; padding: 10px 14px; font-size: 12px; color: var(--textdim);
                        display: flex; gap: 8px; align-items: flex-start;">
                <span class="material-icons" style="font-size:16px;color:var(--orange);flex-shrink:0;margin-top:1px">warning</span>
                <span>Nach einer Wiederherstellung muss AgentClaw neu gestartet werden,
                damit die Änderungen wirksam werden.</span>
            </div>
        """)

    ui.add_body_html(f"<script>{_BACKUP_JS}</script>")
