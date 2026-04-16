#!/usr/bin/env bash
# scripts/test_precommit.sh — Schnelle Tests vor jedem git commit.
#
# Aufgerufen von .git/hooks/pre-commit.
# Läuft typisch in < 3 Sekunden. Bricht ab bei Test-Fehlern.
#
# Bypass (nur in Ausnahmen): git commit --no-verify
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Farben (nur wenn TTY)
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BOLD=''; RESET=''
fi

printf "${BOLD}▸ pre-commit: Schnelle Tests laufen...${RESET}\n"

# venv aktivieren falls vorhanden
if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Pytest mit Timeout (max 30s hart, sollte meist < 3s sein)
START=$(date +%s)
if pytest tests/ --timeout=30 -q 2>&1 | tail -40; then
    DUR=$(( $(date +%s) - START ))
    printf "${GREEN}✓ Tests OK (${DUR}s)${RESET}\n"
    exit 0
else
    DUR=$(( $(date +%s) - START ))
    printf "\n${RED}${BOLD}✗ Tests fehlgeschlagen nach ${DUR}s${RESET}\n"
    printf "${YELLOW}→ Details: ${RESET}pytest tests/ -v\n"
    printf "${YELLOW}→ Nur einen Test: ${RESET}pytest tests/test_smoke.py::test_name -v\n"
    printf "${YELLOW}→ Bypass (nur Notfall): ${RESET}git commit --no-verify\n"
    exit 1
fi
