#!/bin/bash
# ─────────────────────────────────────────────
#  AgentClaw — Start / Stop / Status / Restart
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/venv/bin/activate"
APP="$SCRIPT_DIR/app.py"
PID_FILE="$SCRIPT_DIR/.agentclaw.pid"
LOG_FILE="$SCRIPT_DIR/.agentclaw.log"
PORT=5050

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        kill -0 "$pid" 2>/dev/null && return 0
    fi
    return 1
}

cmd_start() {
    if is_running; then
        echo -e "${YELLOW}⚠️  AgentClaw läuft bereits (PID $(cat $PID_FILE))${NC}"
        echo "   → http://localhost:$PORT"
        return
    fi
    echo -e "${GREEN}🚀 Starte AgentClaw...${NC}"
    source "$VENV"
    nohup python "$APP" > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 2
    if is_running; then
        echo -e "${GREEN}✅ AgentClaw läuft (PID $(cat $PID_FILE))${NC}"
        echo -e "   → http://localhost:$PORT"
    else
        echo -e "${RED}❌ Start fehlgeschlagen — Log:${NC}"
        tail -20 "$LOG_FILE"
    fi
}

cmd_stop() {
    if ! is_running; then
        echo -e "${YELLOW}⚠️  AgentClaw läuft nicht${NC}"
        return
    fi
    local pid=$(cat "$PID_FILE")
    echo -e "${RED}🛑 Stoppe AgentClaw (PID $pid)...${NC}"
    kill "$pid" 2>/dev/null
    sleep 1
    rm -f "$PID_FILE"
    echo -e "${GREEN}✅ Gestoppt${NC}"
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

cmd_status() {
    if is_running; then
        local pid=$(cat "$PID_FILE")
        echo -e "${GREEN}✅ AgentClaw läuft${NC} (PID $pid)"
        echo "   → http://localhost:$PORT"
        echo ""
        echo "--- Letzte Log-Zeilen ---"
        tail -10 "$LOG_FILE" 2>/dev/null
    else
        echo -e "${RED}⛔ AgentClaw ist gestoppt${NC}"
    fi
}

cmd_log() {
    echo "--- AgentClaw Log ($LOG_FILE) ---"
    tail -f "$LOG_FILE"
}

case "${1:-help}" in
    start)   cmd_start   ;;
    stop)    cmd_stop    ;;
    restart) cmd_restart ;;
    status)  cmd_status  ;;
    log)     cmd_log     ;;
    *)
        echo ""
        echo "  AgentClaw Server Control"
        echo ""
        echo "  Verwendung: ./agentclaw.sh [Befehl]"
        echo ""
        echo "  Befehle:"
        echo "    start    — Server starten"
        echo "    stop     — Server stoppen"
        echo "    restart  — Server neu starten"
        echo "    status   — Status & letzte Logs anzeigen"
        echo "    log      — Live-Log verfolgen (Ctrl+C zum Beenden)"
        echo ""
        ;;
esac
