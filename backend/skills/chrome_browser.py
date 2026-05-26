"""
backend/skills/chrome_browser.py — Chrome Extension Bridge Skill.

Steuert Chrome via WebSocket-Bridge (siehe backend/api/chrome_ws.py +
integrations/chrome-extension/).

Syntax:
  chrome screenshot
  chrome navigate https://example.com
  chrome click "button.submit"
  chrome fill "#search" "mein text"
  chrome get_content
  chrome evaluate_js "document.title"

JSON-Form:
  chrome: {"command": "navigate", "url": "https://example.com"}
"""

from __future__ import annotations

import json
import re

import httpx

from backend.skills import Skill

_API_BASE  = "http://localhost:6060/api/chrome"
_TIMEOUT   = 35.0  # leicht über Backend-WS-Timeout (30s)

_VALID = {"screenshot", "navigate", "click", "fill_form", "get_content", "evaluate_js"}

_ALIASES = {
    "nav": "navigate", "go": "navigate", "open": "navigate", "goto": "navigate",
    "shot": "screenshot", "snap": "screenshot", "capture": "screenshot",
    "fill": "fill_form", "input": "fill_form", "type": "fill_form",
    "js": "evaluate_js", "eval": "evaluate_js", "execute": "evaluate_js",
    "content": "get_content", "text": "get_content", "read": "get_content",
    "getcontent": "get_content",
}


class ChromeBrowserSkill(Skill):
    skill_id    = "chrome_browser"
    description = "Steuert Chrome via Extension: screenshot, navigate, click, fill_form, get_content, evaluate_js"

    async def execute(self, query: str) -> str:
        try:
            cmd, params = self._parse(query)
            if not cmd:
                return self._usage()

            # Verbindungs-Check
            async with httpx.AsyncClient(timeout=5.0) as client:
                s = await client.get(f"{_API_BASE}/status")
                if not s.json().get("connected"):
                    return "[Chrome] Extension nicht verbunden. Lade sie in chrome://extensions/ und öffne den Popup um zu verbinden."

                r = await client.post(
                    f"{_API_BASE}/command",
                    json={"command": cmd, "params": params},
                    timeout=_TIMEOUT,
                )
                r.raise_for_status()
                result = r.json()

            return self._format(cmd, result)

        except httpx.TimeoutException:
            return f"[Chrome] Timeout bei Befehl '{cmd}'"
        except Exception as e:
            return f"[Chrome] Fehler: {e}"

    # ── Parser ────────────────────────────────────────────────────────────────

    def _parse(self, query: str) -> tuple[str | None, dict]:
        s = query.strip()

        # JSON-Form: chrome_browser: {...} oder chrome: {...}
        m = re.search(r"(?:chrome_browser|chrome)\s*:\s*(\{.+\})", s, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                cmd = self._normalize(data.pop("command", ""))
                return (cmd, data) if cmd in _VALID else (None, {})
            except json.JSONDecodeError:
                pass

        # "chrome <cmd> [args]" — flexibler Parser
        tokens = s.split(None, 2)
        if not tokens:
            return None, {}
        if tokens[0].lower() in ("chrome", "browser"):
            tokens = tokens[1:]
        if not tokens:
            return None, {}

        raw_cmd = tokens[0].lower().rstrip(":,.")
        cmd = self._normalize(raw_cmd)
        if cmd not in _VALID:
            return None, {}

        rest = tokens[1] if len(tokens) > 1 else ""
        params: dict = {}

        if cmd == "navigate":
            url_m = re.search(r"https?://\S+", s)
            if url_m:
                params["url"] = url_m.group(0)
        elif cmd == "click":
            params["selector"] = self._unquote(rest)
        elif cmd == "fill_form":
            # fill <selector> <text>  oder  fill "<selector>" "<text>"
            quoted = re.findall(r'"([^"]+)"', rest)
            if len(quoted) >= 2:
                params["selector"], params["text"] = quoted[0], quoted[1]
            else:
                bits = rest.split(None, 1)
                if len(bits) == 2:
                    params["selector"], params["text"] = bits[0], bits[1]
        elif cmd == "evaluate_js":
            params["code"] = self._unquote(rest)
        # screenshot + get_content: keine Params nötig

        return cmd, params

    @staticmethod
    def _normalize(cmd: str) -> str:
        c = cmd.lower().replace("-", "_")
        return _ALIASES.get(c, c)

    @staticmethod
    def _unquote(s: str) -> str:
        return s.strip().strip('"').strip("'")

    # ── Formatter ─────────────────────────────────────────────────────────────

    def _format(self, cmd: str, result: dict) -> str:
        if err := result.get("error"):
            return f"[Chrome] Fehler bei {cmd}: {err}"

        if cmd == "screenshot":
            path = result.get("path") or result.get("file") or "?"
            url  = result.get("url") or ""
            return f"[Chrome] 📸 Screenshot: {path}" + (f"\n{url}" if url else "")
        if cmd == "navigate":
            return f"[Chrome] 🌐 Navigiert: {result.get('url','?')} — {result.get('title','')}"
        if cmd == "click":
            return f"[Chrome] 🖱️ Geklickt: {result.get('selector','?')}"
        if cmd == "fill_form":
            return f"[Chrome] ✍️ Ausgefüllt: {result.get('selector','?')}"
        if cmd == "get_content":
            text = result.get("text", "")[:1500]
            title = result.get("title", "?")
            return f"[Chrome] 📄 {title}\n{text}"
        if cmd == "evaluate_js":
            return f"[Chrome] 🧮 JS-Result: {result.get('result','')}"
        return f"[Chrome] {cmd}: {json.dumps(result)[:300]}"

    @staticmethod
    def _usage() -> str:
        return (
            "[Chrome] Befehle:\n"
            "- chrome screenshot\n"
            "- chrome navigate https://...\n"
            "- chrome click \"<selector>\"\n"
            "- chrome fill \"<selector>\" \"<text>\"\n"
            "- chrome get_content\n"
            "- chrome evaluate_js \"document.title\"\n"
            "Voraussetzung: Extension geladen + verbunden (chrome://extensions/)"
        )
