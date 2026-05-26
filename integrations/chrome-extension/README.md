# LogpyClaw Chrome Extension

Verbindet Chrome mit LogpyClaw via WebSocket — Agenten können dann
**Tabs steuern, Screenshots machen, Inhalte lesen, Formulare ausfüllen**
und beliebiges JavaScript ausführen.

## Installation

1. Chrome → `chrome://extensions/`
2. Oben rechts **"Entwicklermodus"** aktivieren
3. **"Entpackte Erweiterung laden"** → den Ordner
   `integrations/chrome-extension/` aus diesem Repo auswählen
4. Icon erscheint in der Toolbar — Popup zeigt Verbindungsstatus

## Verwendung

Skill ist als `skill:chrome_browser` registriert. Im Chat:

```
chrome screenshot
chrome navigate https://example.com
chrome click "button.submit"
chrome fill "#email" "max@example.com"
chrome get_content
chrome evaluate_js "document.title"
```

JSON-Form:

```
chrome: {"command": "navigate", "url": "https://example.com"}
```

## Architektur

```
Chat-Prompt
   │
   ▼
ChromeBrowserSkill (skills/chrome_browser.py)
   │  HTTP POST
   ▼
/api/chrome/command  (backend/api/chrome_ws.py)
   │  WebSocket-Frame
   ▼
background.js (Service Worker im Browser)
   │  chrome.tabs / chrome.scripting
   ▼
Aktiver Tab
```

- **WebSocket-Verbindung:** `ws://localhost:6060/api/chrome/ws`
- **Reconnect:** automatisch alle 2 s (exponentielles Backoff bis 20 Versuche)
- **Keepalive:** Ping alle 20 s (hält Service Worker am Leben)
- **Command-Timeout:** 30 s (im Skill 35 s)

## Status prüfen

```bash
curl http://localhost:6060/api/chrome/status
# {"connected": true}
```

Im Chat:
```
chrome screenshot
# → wenn nicht verbunden: "Extension nicht verbunden. Lade sie in chrome://extensions/ ..."
```

## Verfügbare Commands

| Command | Params | Beschreibung |
|---|---|---|
| `screenshot` | — | PNG des sichtbaren Bereichs des aktiven Tabs |
| `navigate` | `url` | Aktiven Tab zu URL navigieren, wartet auf Load |
| `click` | `selector` | CSS-Selector klicken |
| `fill_form` | `selector`, `text` | Input-Feld füllen + `input`-Event triggern |
| `get_content` | — | `document.body.innerText` (max 8000 chars) |
| `evaluate_js` | `code` | JavaScript im Tab ausführen, Result zurückgeben |

## Sicherheit

- WebSocket bindet auf `localhost` only → nicht von außen erreichbar
- Extension fragt nur `<all_urls>` + `localhost:6060/*` als `host_permissions`
- Keine Authentication für die WS — schützt sich durch das Localhost-Binding

Für Production-Setups: Auth-Token im WS-Handshake ergänzen.
