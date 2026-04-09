#!/bin/bash
# agentclaw.sh — Start / Stop / Restart / Status / Logs
# Verwendung: ./agentclaw.sh [start|stop|restart|status|logs]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
APP="$SCRIPT_DIR/app.py"
PIDFILE="$SCRIPT_DIR/.agentclaw.pid"
LOGFILE="$SCRIPT_DIR/agentclaw.log"
PYTHON="$VENV/bin/python"

# ── Farben ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

_check_venv() {
    if [ ! -f "$PYTHON" ]; then
        echo -e "${RED}✗ Kein venv gefunden unter $VENV${NC}"
        echo -e "${YELLOW}Einmalig ausführen:${NC}"
        echo "  python -m venv .venv"
        echo "  source .venv/bin/activate"
        echo "  pip install -r requirements.txt"
        exit 1
    fi
}

_is_running() {
    if [ -f "$PIDFILE" ]; then
        local pid
        pid=$(cat "$PIDFILE" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

_start() {
    _check_venv

    if _is_running; then
        local pid
        pid=$(cat "$PIDFILE")
        echo -e "${YELLOW}⚠ AgentClaw läuft bereits (PID $pid)${NC}"
        return 1
    fi

    echo -e "${CYAN}▶ Starte AgentClaw...${NC}"
    cd "$SCRIPT_DIR" || exit 1

    nohup "$PYTHON" "$APP" >> "$LOGFILE" 2>&1 &
    local pid=$!
    echo $pid > "$PIDFILE"

    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        echo -e "${GREEN}✓ AgentClaw gestartet (PID $pid)${NC}"
        echo -e "  URL:   http://localhost:5050"
        echo -e "  Logs:  ./agentclaw.sh logs"
    else
        rm -f "$PIDFILE"
        echo -e "${RED}✗ Fehler beim Starten — letzte Log-Zeilen:${NC}"
        tail -20 "$LOGFILE"
        return 1
    fi
}

_stop() {
    if ! _is_running; then
        echo -e "${YELLOW}⚠ AgentClaw läuft nicht${NC}"
        rm -f "$PIDFILE"
        return 0
    fi

    local pid
    pid=$(cat "$PIDFILE")
    echo -e "${CYAN}■ Stoppe AgentClaw (PID $pid)...${NC}"
    kill -TERM "$pid" 2>/dev/null

    local i=0
    while kill -0 "$pid" 2>/dev/null && [ $i -lt 10 ]; do
        sleep 1
        ((i++))
    done

    if kill -0 "$pid" 2>/dev/null; then
        echo -e "${YELLOW}  Erzwinge Stop (SIGKILL)...${NC}"
        kill -KILL "$pid" 2>/dev/null
    fi

    rm -f "$PIDFILE"
    echo -e "${GREEN}✓ AgentClaw gestoppt${NC}"
}

_restart() {
    echo -e "${CYAN}↺ Restart AgentClaw...${NC}"
    _stop
    sleep 1
    _start
}

_status() {
    echo -e "${CYAN}AgentClaw Status${NC}"
    echo "─────────────────────────────"
    if _is_running; then
        local pid
        pid=$(cat "$PIDFILE")
        echo -e "  Status:  ${GREEN}● läuft${NC} (PID $pid)"
        echo -e "  URL:     http://localhost:5050"
        echo -e "  Log:     $LOGFILE"
        local uptime
        uptime=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')
        [ -n "$uptime" ] && echo -e "  Uptime:  $uptime"
    else
        echo -e "  Status:  ${RED}● gestoppt${NC}"
    fi
    echo "─────────────────────────────"
}

_logs() {
    echo -e "${CYAN}Logs — Strg+C zum Beenden${NC}"
    tail -f "$LOGFILE"
}

# ── Main ──────────────────────────────────────────────────────────────────────
case "${1:-}" in
    start)   _start   ;;
    stop)    _stop    ;;
    restart) _restart ;;
    status)  _status  ;;
    logs)    _logs    ;;
    *)
        echo ""
        echo "  AgentClaw Control"
        echo ""
        echo "  ./agentclaw.sh start    — im Hintergrund starten"
        echo "  ./agentclaw.sh stop     — stoppen"
        echo "  ./agentclaw.sh restart  — neu starten"
        echo "  ./agentclaw.sh status   — Status & PID"
        echo "  ./agentclaw.sh logs     — Live-Log"
        echo ""
        ;;
esac
