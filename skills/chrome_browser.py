"""
skills/chrome_browser.py — Chrome Browser Skill.

Agenten können damit Chrome steuern:
  screenshot, navigate, click, fill_form, get_content, evaluate_js

Syntax für LLM:
  chrome screenshot
  chrome navigate https://example.com
  chrome click "button.submit"
  chrome fill_form "#search" "mein suchbegriff"
  chrome get_content
  chrome evaluate_js "document.title"
  chrome_browser: {"command": "navigate", "url": "https://example.com"}
"""
import json
import logging
import re

import requests

from skills.base import BaseSkill, SkillResult

logger = logging.getLogger(__name__)

_CHROME_API = "http://localhost:5050/api/chrome/command"
_STATUS_API = "http://localhost:5050/api/chrome/status"
_HTTP_TIMEOUT = 35  # etwas über WS-Timeout (30s)

_COMMAND_ALIASES = {
    "nav": "navigate", "go": "navigate", "open": "navigate", "goto": "navigate",
    "shot": "screenshot", "snap": "screenshot", "capture": "screenshot",
    "fill": "fill_form", "input": "fill_form", "type": "fill_form",
    "js": "evaluate_js", "eval": "evaluate_js", "execute": "evaluate_js",
    "content": "get_content", "text": "get_content", "read": "get_content",
    "getcontent": "get_content",
}

VALID_COMMANDS = {
    "screenshot", "navigate", "click", "fill_form", "get_content", "evaluate_js"
}


class ChromeBrowserSkill(BaseSkill):
    id = "chrome_browser"
    name = "Chrome Browser"
    icon = "open_in_browser"
    description = (
        "Steuert Chrome: screenshot, navigate, click, fill_form, get_content, evaluate_js. "
        "Syntax: 'chrome <command> [args]' oder 'chrome_browser: {\"command\": \"...\", ...}'"
    )
    triggers = [
        # Nur noch explizite chrome-Befehle — kein URL-Catch-all mehr
        r"\bchrome\s+(?:navigate|click|fill|get[_\s]?content|eval|js|open|read|text)\b",
        r"\bchrome_browser\s*:",
        r"\bNutze\s+`?chrome_browser`?\b",
    ]

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        cmd, params = self._parse_command(message)
        if not cmd:
            return SkillResult(
                error=(
                    "Chrome Befehl konnte nicht geparst werden. "
                    "Nutze: 'chrome screenshot', 'chrome navigate https://...', "
                    "'chrome click \"selector\"', 'chrome get_content', etc."
                ),
                skill_used=self.id,
            )

        # Spezialfall: read_url = navigate + get_content in einem Schritt
        if cmd == "read_url":
            url = params.get("url")
            if not url:
                return SkillResult(error="Keine URL angegeben", skill_used=self.id)
            # Verbindungs-Check einmal
            try:
                s = requests.get(_STATUS_API, timeout=3)
                if not s.ok or not s.json().get("connected"):
                    return SkillResult(error="Chrome Extension nicht verbunden.", skill_used=self.id)
            except Exception as e:
                return SkillResult(error=f"Chrome Status-Check fehlgeschlagen: {e}", skill_used=self.id)
            # 1. Navigieren
            try:
                r = requests.post(_CHROME_API, json={"command": "navigate", "params": {"url": url}}, timeout=_HTTP_TIMEOUT)
                if not r.ok:
                    return SkillResult(error=f"Navigation fehlgeschlagen ({r.status_code}): {r.text[:200]}", skill_used=self.id)
            except Exception as e:
                return SkillResult(error=f"Navigation Fehler: {e}", skill_used=self.id)
            # 2. Inhalt lesen
            try:
                r2 = requests.post(_CHROME_API, json={"command": "get_content", "params": {"maxChars": 8000}}, timeout=_HTTP_TIMEOUT)
                if not r2.ok:
                    return SkillResult(error=f"get_content fehlgeschlagen ({r2.status_code})", skill_used=self.id)
                data = r2.json()
            except Exception as e:
                return SkillResult(error=f"get_content Fehler: {e}", skill_used=self.id)
            text = data.get("text", "")
            page_url = data.get("url", url)
            title = data.get("title", "")
            return SkillResult(
                text=f"**{title}**\n\nURL: {page_url}\n\n{text}",
                skill_used=self.id,
                metadata={"command": "read_url", "url": page_url},
            )

        # Verbindungs-Check
        try:
            status_resp = requests.get(_STATUS_API, timeout=3)
            if not status_resp.ok or not status_resp.json().get("connected"):
                return SkillResult(
                    error="Chrome Extension nicht verbunden. Öffne das Extension-Popup und klicke 'Connect'.",
                    skill_used=self.id,
                )
        except requests.exceptions.ConnectionError:
            return SkillResult(error="AgentClaw Server nicht erreichbar", skill_used=self.id)
        except Exception as e:
            return SkillResult(error=f"Status-Check fehlgeschlagen: {e}", skill_used=self.id)

        # Befehl senden
        try:
            resp = requests.post(
                _CHROME_API,
                json={"command": cmd, "params": params},
                timeout=_HTTP_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            return SkillResult(error=f"Chrome Command '{cmd}' Timeout", skill_used=self.id)
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)

        if resp.status_code == 503:
            return SkillResult(error="Chrome Extension nicht verbunden", skill_used=self.id)
        if resp.status_code == 504:
            return SkillResult(error=f"Chrome Command Timeout: {cmd}", skill_used=self.id)
        if not resp.ok:
            return SkillResult(
                error=f"Chrome Command fehlgeschlagen ({resp.status_code}): {resp.text[:300]}",
                skill_used=self.id,
            )

        try:
            data = resp.json()
        except Exception:
            return SkillResult(error="Ungültige Antwort von Chrome Extension", skill_used=self.id)

        if data.get("error"):
            return SkillResult(error=data["error"], skill_used=self.id)

        image = data.get("screenshot")  # base64 data URI (nur bei screenshot)
        text = (
            data.get("text")
            or data.get("result")
            or f"Chrome Command '{cmd}' erfolgreich ausgeführt"
        )
        url = data.get("url", "")
        if url:
            text = f"{text}\n\nURL: {url}"

        return SkillResult(
            text=text,
            image=image,
            skill_used=self.id,
            metadata={"command": cmd, "url": url},
        )

    def _parse_command(self, message: str) -> tuple[str | None, dict]:
        """Parst den Chrome-Befehl aus der Agenten-Nachricht.

        Unterstützte Formate:
          1. JSON:   chrome_browser: {"command": "navigate", "url": "https://..."}
          2. Text:   chrome navigate https://...
                     chrome screenshot
                     chrome click "#btn-submit"
                     chrome fill_form "#search" "suchbegriff"
                     chrome evaluate_js "document.title"
        """
        # Bei A2A-Tasks nur den Teil nach dem Separator verwenden
        task_sep = re.search(r"---\s*\nDeine Aufgabe:\s*(.+)", message, re.DOTALL)
        message = task_sep.group(1).strip() if task_sep else message

        # Format 1: JSON-Block — verschachtelte {} korrekt parsen
        json_start = re.search(r'chrome(?:_browser)?\s*:\s*(\{)', message, re.IGNORECASE)
        if json_start:
            start_idx = json_start.start(1)
            depth = 0
            end_idx = None
            for i, ch in enumerate(message[start_idx:], start_idx):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        end_idx = i + 1
                        break
            if end_idx:
                try:
                    data = json.loads(message[start_idx:end_idx])
                    cmd = data.pop("command", None)
                    # "args" als flache params auspacken falls vorhanden
                    if "args" in data and isinstance(data["args"], dict):
                        params = data.pop("args")
                        data.update(params)
                    if cmd in VALID_COMMANDS or cmd in _COMMAND_ALIASES:
                        cmd = _COMMAND_ALIASES.get(cmd, cmd)
                        return cmd, data
                except json.JSONDecodeError:
                    pass

        # Format 2: Natürliche Sprache mit chrome/browser Prefix
        m = re.search(
            r'\b(?:chrome|browser)\s+([a-zA-Z_]+)\b(.*)',
            message, re.IGNORECASE | re.DOTALL
        )
        if not m:
            # Format 3: URL ohne chrome-Prefix — auto read_url
            url_m = re.search(r'https?://\S+', message)
            if url_m:
                return "read_url", {"url": url_m.group(0).rstrip(".,;\"')}]")}
            return None, {}

        cmd_raw = m.group(1).lower().replace("_", "").replace(" ", "")
        rest = m.group(2).strip()

        cmd = _COMMAND_ALIASES.get(cmd_raw, cmd_raw)
        # Versuche auch mit Underscore-Varianten
        if cmd not in VALID_COMMANDS:
            cmd_with_us = re.sub(r'([a-z])([A-Z])', r'\1_\2', m.group(1)).lower()
            cmd = _COMMAND_ALIASES.get(cmd_with_us, cmd_with_us)
        if cmd not in VALID_COMMANDS:
            return None, {}

        params: dict = {}

        if cmd == "navigate":
            url_m = re.search(r'https?://\S+', rest)
            if url_m:
                params["url"] = url_m.group(0).rstrip(".,;)")
            else:
                # Evtl. nur Domain angegeben
                domain_m = re.search(r'[\w.-]+\.\w{2,}', rest)
                if domain_m:
                    params["url"] = "https://" + domain_m.group(0)

        elif cmd == "click":
            # Selector in Anführungszeichen oder direkt
            sel_m = re.search(r'["\'](.+?)["\']', rest) or re.search(r'(\S+)', rest)
            if sel_m:
                params["selector"] = sel_m.group(1)

        elif cmd == "fill_form":
            # fill_form "selector" "value" oder fill_form selector value
            parts = re.findall(r'["\'](.+?)["\']|(\S+)', rest)
            flat = [a or b for a, b in parts]
            if flat:
                params["selector"] = flat[0]
            if len(flat) > 1:
                params["value"] = " ".join(flat[1:])

        elif cmd == "evaluate_js":
            # JS-Code in Anführungszeichen oder direkt
            code_m = re.search(r'["\'](.+?)["\']', rest, re.DOTALL)
            params["code"] = code_m.group(1) if code_m else rest.strip()

        return cmd, params
