#!/bin/bash
# scripts/cleanup.sh — AgentClaw Wartungsscript
# Löscht: LTX-Batch-Jobs, alte Logs, DB-History, Claude-Transcripts
# Aufruf: bash scripts/cleanup.sh [--dry-run]

set -euo pipefail
DRY=${1:-}
BASE="$(cd "$(dirname "$0")/.." && pwd)"

echo "═══════════════════════════════════════"
echo "  AgentClaw Cleanup"
echo "  Basis: $BASE"
[[ "$DRY" == "--dry-run" ]] && echo "  ⚠ DRY-RUN — nichts wird gelöscht" || echo "  ✓ Echtes Löschen"
echo "═══════════════════════════════════════"

remove() {
  local target="$1"
  local desc="$2"
  if [ -e "$target" ]; then
    local size
    size=$(du -sh "$target" 2>/dev/null | cut -f1)
    if [[ "$DRY" == "--dry-run" ]]; then
      echo "  [DRY] $desc: $target ($size)"
    else
      rm -rf "$target"
      echo "  ✓ $desc gelöscht ($size)"
    fi
  fi
}

# ── 1. LTX Batch Jobs ─────────────────────────────────────────────────
echo ""
echo "▶ LTX Batch Jobs ($BASE/data/ltx_batch/)"
LTX_DIR="$BASE/data/ltx_batch"
if [ -d "$LTX_DIR" ]; then
  COUNT=$(ls "$LTX_DIR" | wc -l | tr -d ' ')
  TOTAL=$(du -sh "$LTX_DIR" 2>/dev/null | cut -f1)
  echo "  $COUNT Jobs gefunden, gesamt $TOTAL"
  for job in "$LTX_DIR"/*/; do
    [ -d "$job" ] || continue
    AGE_DAYS=$(( ( $(date +%s) - $(stat -f %m "$job" 2>/dev/null || stat -c %Y "$job" 2>/dev/null) ) / 86400 ))
    SIZE=$(du -sh "$job" 2>/dev/null | cut -f1)
    ID=$(basename "$job")
    if [[ "$DRY" == "--dry-run" ]]; then
      echo "  [DRY] Job $ID (${AGE_DAYS}d alt, $SIZE)"
    else
      rm -rf "$job"
      echo "  ✓ Job $ID gelöscht (${AGE_DAYS}d alt, $SIZE)"
    fi
  done
else
  echo "  (kein ltx_batch Verzeichnis)"
fi

# ── 2. App Log rotieren ────────────────────────────────────────────────
echo ""
echo "▶ App Log ($BASE/agentclaw.log)"
LOG="$BASE/agentclaw.log"
if [ -f "$LOG" ]; then
  SIZE=$(du -sh "$LOG" | cut -f1)
  if [[ "$DRY" == "--dry-run" ]]; then
    echo "  [DRY] Log kürzen auf letzte 500 Zeilen ($SIZE)"
  else
    tail -500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    echo "  ✓ Log auf letzte 500 Zeilen gekürzt (war $SIZE)"
  fi
fi

# ── 3. SQLite DB — alte Tasks + Messages ──────────────────────────────
echo ""
echo "▶ SQLite DB ($BASE/agentclaw.db)"
DB="$BASE/agentclaw.db"
if [ -f "$DB" ]; then
  COUNT_TSK=$(sqlite3 "$DB" "SELECT count(*) FROM tasks;" 2>/dev/null || echo "?")
  COUNT_MSG=$(sqlite3 "$DB" "SELECT count(*) FROM messages;" 2>/dev/null || echo "?")
  if [[ "$DRY" == "--dry-run" ]]; then
    OLD_TSK=$(sqlite3 "$DB" "SELECT count(*) FROM tasks WHERE status IN ('completed','failed','canceled') AND datetime(COALESCE(completed_at, created_at)) < datetime('now', '-3 days');" 2>/dev/null || echo "?")
    OLD_MSG=$(sqlite3 "$DB" "SELECT count(*) FROM messages WHERE datetime(ts) < datetime('now', '-14 days');" 2>/dev/null || echo "?")
    echo "  [DRY] Tasks: $COUNT_TSK (davon $OLD_TSK > 3d abgeschlossen) | Messages: $COUNT_MSG (davon $OLD_MSG > 14d)"
  else
    sqlite3 "$DB" "
      DELETE FROM tasks
        WHERE status IN ('completed','failed','canceled')
        AND datetime(COALESCE(completed_at, created_at)) < datetime('now', '-3 days');
      DELETE FROM messages
        WHERE datetime(ts) < datetime('now', '-14 days');
      VACUUM;
    " 2>/dev/null && echo "  ✓ DB bereinigt + VACUUM (Tasks: ${COUNT_TSK} → $(sqlite3 "$DB" "SELECT count(*) FROM tasks;"), Messages: ${COUNT_MSG} → $(sqlite3 "$DB" "SELECT count(*) FROM messages;"))" \
                  || echo "  ⚠ DB-Cleanup fehlgeschlagen"
  fi
fi

# ── 4. __pycache__ + .pyc ─────────────────────────────────────────────
echo ""
echo "▶ Python Cache"
CACHE_COUNT=$(find "$BASE" -name "__pycache__" -not -path "*/.venv/*" 2>/dev/null | wc -l | tr -d ' ')
if [[ "$DRY" == "--dry-run" ]]; then
  echo "  [DRY] $CACHE_COUNT __pycache__ Verzeichnisse"
else
  find "$BASE" -name "__pycache__" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null || true
  find "$BASE" -name "*.pyc" -not -path "*/.venv/*" -delete 2>/dev/null || true
  echo "  ✓ $CACHE_COUNT __pycache__ Verzeichnisse gelöscht"
fi

# ── 5. Zusammenfassung ─────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
if [[ "$DRY" == "--dry-run" ]]; then
  echo "  DRY-RUN abgeschlossen."
  echo "  Zum echten Löschen: bash scripts/cleanup.sh"
else
  echo "  Cleanup abgeschlossen!"
  echo "  Aktueller Speicherverbrauch:"
  du -sh "$BASE/data/" 2>/dev/null && true
fi
echo "═══════════════════════════════════════"
