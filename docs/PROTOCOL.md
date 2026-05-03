# AgentClaw — Agent-to-Agent Protocol

AgentClaw implementiert ein asynchrones, skill-basiertes Kommunikationsprotokoll zwischen Agents. Ein Agent kann Tasks an andere Agents delegieren, die diese anhand ihrer Skills verarbeiten und Ergebnisse zurückliefern.

---

## Inhaltsverzeichnis

1. [Überblick](#1-überblick)
2. [Task-Lifecycle](#2-task-lifecycle)
3. [API-Referenz](#3-api-referenz)
4. [Datenstrukturen](#4-datenstrukturen)
5. [Skill-System](#5-skill-system)
6. [Trigger-Muster](#6-trigger-muster)
7. [@-Mention Delegation](#7--mention-delegation)
8. [Event-System](#8-event-system)
9. [Heartbeat-System](#9-heartbeat-system)
10. [Multi-Agent Modi](#10-multi-agent-modi)
11. [Fehlerbehandlung](#11-fehlerbehandlung)
12. [Vollständiges Beispiel](#12-vollständiges-beispiel)

---

## 1. Überblick

```
User        Frontend         Server          ComfyUI / LLM
 │               │               │                  │
 │  @Jane draw   │               │                  │
 ├──────────────►│               │                  │
 │               │  POST /tasks  │                  │
 │               ├──────────────►│                  │
 │               │  task_id      │                  │
 │               │◄──────────────┤                  │
 │               │               │  process_task()  │
 │               │               ├─────────────────►│
 │               │  GET /tasks/… │  base64 image    │
 │               ├──────────────►│◄─────────────────┤
 │               │  status=done  │                  │
 │  Bild + Text  │◄──────────────┤                  │
 │◄──────────────┤               │                  │
```

**Kern-Prinzipien:**
- Tasks sind **asynchron** — Erstellung und Verarbeitung sind getrennt
- Skill-Matching erfolgt **automatisch** anhand von Trigger-Mustern
- Der Sender-Agent wird über das **Ergebnis informiert** (History-Eintrag)
- Tasks werden auf Disk **persistiert** und überleben Server-Restarts

---

## 2. Task-Lifecycle

```
  POST /api/tasks
        │
        ▼
   ┌─────────┐    Hintergrund-Thread    ┌─────────────┐
   │ pending │─────────────────────────►│ processing  │
   └─────────┘                          └──────┬──────┘
                                               │
                              ┌────────────────┼────────────────┐
                              │                │                │
                              ▼                ▼                ▼
                          ┌──────┐        ┌───────┐       ┌───────┐
                          │ done │        │ error │       │timeout│
                          └──────┘        └───────┘       └───────┘
                                                         (nach 180s)
```

### Statusübergänge

| Status | Bedeutung |
|--------|-----------|
| `pending` | Task erstellt, wartet auf Verarbeitung |
| `processing` | Wird gerade verarbeitet |
| `done` | Erfolgreich abgeschlossen |
| `error` | Fehler bei der Verarbeitung |

---

## 3. API-Referenz

### `POST /api/tasks` — Task erstellen

**Request:**
```json
{
  "sender_agent_id":   "uuid",
  "sender_agent_name": "Alex",
  "recipient_agent_id":   "uuid",
  "recipient_agent_name": "Jane",
  "message": "Generiere einen Sonnenuntergang über den Bergen"
}
```

> `recipient_agent_id` ist optional — wenn weggelassen, wird anhand von `recipient_agent_name` aufgelöst. Alternativ kann auch `task_type` (z.B. `"image_gen"`) übergeben werden für Auto-Matching.

**Response `202 Accepted`:**
```json
{
  "id": "a1b2c3d4-...",
  "sender_agent_name": "Alex",
  "recipient_agent_name": "Jane",
  "message": "Generiere einen Sonnenuntergang über den Bergen",
  "status": "pending",
  "skill_used": null,
  "result_text": null,
  "result_image": null,
  "error": null,
  "created_at": "2026-04-01T10:30:00.000000",
  "completed_at": null,
  "timeout_at": "2026-04-01T10:33:00.000000"
}
```

**Fehler:**

| Code | Bedeutung |
|------|-----------|
| `400` | Fehlende Pflichtfelder (`message`) |
| `404` | Recipient-Agent nicht gefunden |

```json
{ "error": "Agent 'Jane' nicht gefunden", "available": ["Alex", "Flo"] }
```

---

### `GET /api/tasks/<task_id>` — Task-Status abfragen

**Response `200 OK`:**
```json
{
  "id": "a1b2c3d4-...",
  "status": "done",
  "skill_used": "image_gen",
  "result_text": null,
  "result_image": "data:image/png;base64,iVBOR...",
  "prompt_used": "Sonnenuntergang über Bergen, golden hour, dramatic sky",
  "error": null,
  "created_at": "2026-04-01T10:30:00.000000",
  "completed_at": "2026-04-01T10:31:45.000000"
}
```

**Polling-Empfehlung:** Exponentieller Backoff, Start 1500 ms, Maximum 4000 ms, Abbruch nach ~180 s.

---

### `POST /api/chat` — Direkter Chat mit Agent

**Request:**
```json
{
  "agent_id":     "uuid",
  "message":      "Was ist der Unterschied zwischen TCP und UDP?",
  "image_data":   "data:image/png;base64,...",
  "system_extra": "Zusätzlicher Kontext vom Frontend"
}
```

`image_data` und `system_extra` sind optional.

**Response:**
```json
{
  "reply": "TCP ist verbindungsorientiert…",
  "image": null,
  "stats": { "tokens": 312, "model": "gemma3:latest" }
}
```

---

### `GET /api/agents` — Alle Agents

```json
[
  {
    "id": "uuid",
    "name": "Jane",
    "role": "Image specialist",
    "model": "gemma3:latest",
    "provider": "ollama",
    "skills": ["image_gen", "telegram"],
    "color": "#8b5cf6",
    "voice": "gb_jane_sarcasm"
  }
]
```

---

### `POST /api/agents` — Neuen Agent erstellen

```json
{
  "name":     "Nova",
  "role":     "Research assistant",
  "soul":     "You are Nova, a sharp…",
  "model":    "gemma3:latest",
  "provider": "ollama",
  "color":    "#3b82f6",
  "voice":    ""
}
```

---

### `PUT /api/agents/<id>/skills` — Skills aktualisieren

```json
{ "skills": ["image_gen", "web_search"] }
```

---

### `PUT /api/agents/<id>/voice` — Stimme aktualisieren

```json
{ "voice": "de_anna_warm" }
```

---

### `GET /api/agents/filter?skill=image_gen` — Agents nach Skill filtern

Gibt alle Agents zurück, die den angegebenen Skill haben.

---

## 4. Datenstrukturen

### Task-Objekt (vollständig)

```typescript
interface Task {
  id:                   string;        // UUID
  sender_agent_id:      string;
  sender_agent_name:    string;
  recipient_agent_id:   string;
  recipient_agent_name: string;
  message:              string;        // Prompt / Aufgabentext
  status:               "pending" | "processing" | "done" | "error";
  skill_used:           string | null; // Welche Skill wurde ausgeführt
  result_text:          string | null;
  result_image:         string | null; // data:image/png;base64,…
  prompt_used:          string | null; // Für image_gen: bereinigter Prompt
  error:                string | null;
  created_at:           string;        // ISO 8601
  completed_at:         string | null;
  timeout_at:           string;        // created_at + 180s
}
```

### Agent-Objekt (vollständig)

```typescript
interface Agent {
  id:         string;
  name:       string;
  role:       string;       // Kurze Rollenbezeichnung
  soul:       string;       // System-Prompt
  model:      string;       // z.B. "gemma3:latest"
  provider:   string;       // "ollama" | "mistral" | "openrouter"
  max_tokens: number | null;
  color:      string;       // Hex-Farbe für UI
  voice:      string;       // TTS-Voice-Slug oder ""
  skills:     string[];     // Aktive Skills
  web_search: boolean;
  heartbeat: {
    active:       boolean;
    prompt:       string;
    interval_min: number;
    next_run:     string | null;
    last_run:     string | null;
  };
  created_at: string;
}
```

---

## 5. Skill-System

### Verfügbare Skills

| Skill-ID | Name | Beschreibung |
|----------|------|--------------|
| `image_gen` | Image Generation | Bildgenerierung via ComfyUI (Flux, Wan…) |
| `image_edit` | Image Editing | Bild bearbeiten via ComfyUI |
| `web_search` | Web Search | Live-Suche via SearXNG |
| `url_fetch` | URL Fetch | Text aus URLs extrahieren |
| `screenshot` | Screenshot | Browser-Screenshots via Playwright |
| `telegram` | Telegram | Nachrichten/Bilder senden |
| `telegram_incoming` | Telegram Incoming | Eingehende Nachrichten empfangen |
| `gmail` | Gmail | E-Mails senden und lesen (IMAP/SMTP) |
| `prompt_optimize` | Prompt Optimizer | Prompts mit Frameworks verfeinern |
| `tagesschau` | Tagesschau | Deutsche Nachrichten |

### Skill-Verarbeitungs-Priorität

Innerhalb von `process_task()` wird in dieser Reihenfolge geprüft:

```
1. image_edit    — Bild vorhanden + Edit-Trigger
2. telegram      — Telegram-Trigger erkannt
3. gmail         — Gmail-Trigger erkannt
4. image_gen     — Image-Trigger erkannt (oder only_image_gen)
5. llm           — Fallback: normaler LLM-Call
```

### Sonderfall: `only_image_gen`

Wenn ein Agent **ausschließlich** den `image_gen`-Skill hat, wird **jede** eingehende Task als Bild-Prompt behandelt — unabhängig vom Text.

---

## 6. Trigger-Muster

### Image Generation

```regex
\b(generier\w*|mal\w*|zeichn\w*|illustrier\w*|generate|draw|paint|illustrate|
   bild|foto|image|picture|photo|wallpaper|artwork|illustration|zeichnung|gemälde)\b
```

**Matcht:** „generiere einen Drachen", „draw me a sunset", „Bild von einer Stadt"
**Matcht nicht:** „erstelle eine Liste", „zeig mir das Ergebnis", „mach das nochmal"

### Telegram

```regex
schick.*(das\s*)?(bild|foto|photo|image).*telegram
schick.*telegram
send.*(the\s*)?(image|picture|photo).*telegram
send.*to\s*telegram
telegram.*(bild|foto|image)
tg\s*send
```

### Gmail — Senden

```regex
schick.*mail | sende.*e-?mail | e-?mail.*an | send.*mail | send.*email | email.*to
```

### Gmail — Lesen

```regex
check.*(my\s*)?mail | letzte.*mail | letzte.*e-?mail | neue.*mail
```

### Image Edit

```regex
bearbeit\w*|editier\w*|änder\w*|verbessere?|entfern\w*|ersetze?|
edit|modify|change|remove|replace|enhance|improve|adjust|fix
```

### Prompt Optimize

```regex
optimier\w*|verbessere?.*prompt|verfeinere?|optimize|improve.*prompt|refine|enhance.*prompt
```

---

## 7. @-Mention Delegation

### Syntax

```
@<AgentName> <Aufgabe>
```

**Beispiele:**
```
@Jane generiere einen Sonnenuntergang
@Alex recherchiere den Bitcoin-Kurs
@Flo schreib eine Zusammenfassung des Gesprächs
```

### Verarbeitung im Frontend

```javascript
// 1. Eingabe-Erkennung in sendMessage()
const match = text.match(/^@(\S+)\s+([\s\S]+)$/);
if (match) {
    const targetName = match[1];
    const taskMessage = match[2];
    const target = agents.find(a =>
        a.name.toLowerCase() === targetName.toLowerCase()
    );
    if (target) await handleAgentMention(target, taskMessage);
}
```

### Auto-Delegation aus Agent-Antworten

Ein Agent kann in seiner Antwort andere Agents erwähnen — das Frontend erkennt dies und delegiert automatisch weiter:

```javascript
// Scan auf @Mentions in Agent-Antworten
const rx = /@([A-Za-zÄÖÜäöüß][\w\s&]*?)(?=[,\s:!?.–—]|$)/g;
// Erste gültige Mention wird als Task delegiert
// Max. 1 Auto-Delegation pro Antwort (Endlosschleife verhindern)
```

### Polling-Logik

```javascript
async function pollTask(taskId, targetAgent) {
    let delay = 1500;
    const MAX_POLLS = 80; // ~3 Minuten

    for (let i = 0; i < MAX_POLLS; i++) {
        await sleep(delay);
        delay = Math.min(delay * 1.25, 4000);

        const task = await api(`/api/tasks/${taskId}`);

        if (task.status === 'done') {
            renderTaskResult(task, targetAgent);
            return;
        }
        if (task.status === 'error') {
            showError(task.error);
            return;
        }
    }
    showError('Timeout');
}
```

---

## 8. Event-System

### `GET /api/events?v=<version>` — Events abrufen

Das System verwendet **Version-basiertes Polling** statt WebSockets.

**Request:**
```
GET /api/events?v=0        # Alle Events
GET /api/events?v=42       # Nur Events nach Version 42
```

**Response:**
```json
[
  {
    "type": "agent_updated",
    "data": { "id": "uuid" },
    "v": 43,
    "ts": "2026-04-01T10:31:00"
  },
  {
    "type": "heartbeat_completed",
    "data": { "id": "uuid", "name": "Jane" },
    "v": 44,
    "ts": "2026-04-01T10:32:00"
  }
]
```

### Event-Typen

| Typ | Daten | Auslöser |
|-----|-------|---------|
| `new_agent` | `{ id, name }` | Agent erstellt |
| `agent_updated` | `{ id }` | Agent-Settings geändert |
| `agent_deleted` | `{ id }` | Agent gelöscht |
| `heartbeat_completed` | `{ id, name }` | Heartbeat ausgeführt |

### Interne Implementierung

```python
_EVENTS: list = []       # Ring-Buffer, max. 100 Einträge
_EVENT_VERSION: int = 0  # Globaler Zähler, monoton steigend

def emit_event(event_type: str, data: dict = None):
    global _EVENT_VERSION
    with _events_lock:
        _EVENT_VERSION += 1
        _EVENTS.append({
            "type": event_type,
            "data": data or {},
            "v":    _EVENT_VERSION,
            "ts":   datetime.now().isoformat()
        })
        if len(_EVENTS) > 100:
            _EVENTS[:] = _EVENTS[-100:]
```

**Frontend-Polling:** alle 3 Sekunden, speichert letzte `v` lokal.

---

## 9. Heartbeat-System

Heartbeats lassen Agents **autonom und periodisch** aktiv werden — unabhängig von User-Input.

### Konfiguration

```json
{
  "active": true,
  "prompt": "Was beschäftigt dich gerade? Teile einen kurzen Gedanken.",
  "interval_min": 30
}
```

### `PUT /api/agents/<id>/heartbeat`

```json
{
  "active": true,
  "prompt": "...",
  "interval_min": 15
}
```

### `POST /api/agents/<id>/heartbeat/run`

Führt den Heartbeat sofort aus (für Tests / manuellen Trigger).

### Ablauf

```
1. Background-Thread prüft alle 60s ob next_run erreicht
2. Heartbeat-Prompt wird als User-Message an den Agent gesendet
3. Antwort wird in die Agent-History gespeichert
4. Wenn Agent image_gen Skill hat: Bild wird generiert
5. emit_event("heartbeat_completed")
6. next_run = now + interval_min
```

### Interaktion mit image_gen

Hat ein Agent gleichzeitig Heartbeat + `image_gen` aktiv, wird bei jedem Heartbeat-Tick ein Bild generiert — der Heartbeat-Prompt dient als Bild-Prompt.

---

## 10. Multi-Agent Modi

### Auto-Dialog (zwei Agents)

Zwei Agents führen einen automatisierten Dialog mit konfigurierbarer Rundenanzahl.

**Ablauf:**
```
Opening-Prompt → Agent A → Agent B → Agent A → Agent B → …
```

```javascript
// Frontend-Logik (vereinfacht)
let message = openingPrompt;
for (let turn = 0; turn < maxTurns; turn++) {
    const replyA = await api('/api/chat', { agent_id: idA, message });
    renderMessage(agentA, replyA.reply);

    const replyB = await api('/api/chat', { agent_id: idB, message: replyA.reply });
    renderMessage(agentB, replyB.reply);

    message = replyB.reply;
}
```

### Broadcast (mehrere Agents)

Dieselbe Nachricht wird **parallel** an alle selektierten Agents gesendet. Jeder Agent antwortet unabhängig.

```javascript
const results = await Promise.all(
    selectedAgents.map(id =>
        api('/api/chat', { agent_id: id, message: broadcastText })
    )
);
// Ergebnisse werden in Card-Grid angezeigt
```

---

## 11. Fehlerbehandlung

### Task-Timeout

Tasks die nach 180 Sekunden noch nicht `done` oder `error` sind, werden automatisch auf `error` gesetzt:

```json
{ "status": "error", "error": "Timeout" }
```

### ComfyUI nicht erreichbar

```json
{ "status": "error", "error": "ComfyUI nicht erreichbar: Connection refused" }
```

### Agent nicht gefunden

```json
{ "error": "Agent 'Nova' nicht gefunden", "available": ["Alex", "Jane", "Flo"] }
```

### LLM-Fehler (Fallback)

Wenn der LLM-Call innerhalb von `process_task()` fehlschlägt, wird `result_text` auf die Fehlermeldung gesetzt und `status` auf `error`.

---

## 12. Vollständiges Beispiel

### Szenario: Alex → Jane (Bild generieren + Telegram senden)

**Schritt 1 — User-Input:**
```
@Jane generiere ein Bild von einem Neondrachen und schick es zu Telegram
```

**Schritt 2 — Frontend erkennt @Jane:**
```javascript
POST /api/tasks {
  sender_agent_id:   "alex-uuid",
  sender_agent_name: "Alex",
  recipient_agent_name: "Jane",
  message: "generiere ein Bild von einem Neondrachen und schick es zu Telegram"
}
// → { id: "task-abc", status: "pending", … }
```

**Schritt 3 — Server verarbeitet Task (Hintergrund):**
```
skills(Jane) = {"image_gen", "telegram"}

1. TG_TRIGGERS matched ("schick es zu Telegram")
   → Telegram-Pfad wird gewählt
2. IMG_TRIGGERS matched ("generiere ein Bild")
   → _run_comfyui_sync("Neondrachen")
   → result_image = "data:image/png;base64,…"
3. _run_telegram(message, result_image)
   → "✅ Bild an Telegram gesendet"
4. status = "done", skill_used = "telegram"
5. History-Eintrag bei Jane + bei Alex
```

**Schritt 4 — Frontend pollt:**
```javascript
// Alle 1500ms → 1875ms → 2344ms → …
GET /api/tasks/task-abc
// → { status: "done", result_image: "…", result_text: "✅ Bild an Telegram…" }
```

**Schritt 5 — Anzeige in Alex's Chat:**
```
📬 @Jane: Neondrachen generiert und an Telegram gesendet.
[Bild-Thumbnail]
```

---

## Persistierung

| Datei | Inhalt |
|-------|--------|
| `agents.json` | Alle Agents mit Settings & Heartbeat |
| `history.json` | Konversationsverläufe pro Agent |
| `tasks.json` | Alle Tasks (pending, done, error) |
| `providers.json` | API-Keys, Endpoint-URLs |
| `watchdogs.json` | Watchdog-Konfigurationen |

Alle Dateien werden atomar geschrieben (Write → `.tmp` → Rename) um Datenverlust bei Abstürzen zu verhindern.

---

*AgentClaw v0.7 — Protokoll-Version 1*
