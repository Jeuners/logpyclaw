# BUG v1.89 — NiceGUI core.loop AssertionError

## Datum
2026-04-09

## Umgebung
- NiceGUI 3.10.0
- Python 3.14.0a6
- macOS
- socketio/async_server

## Symptom
JEDER Versuch, aus einem NiceGUI Event-Handler (on_click, on_keydown, 
js_handler emit) den Client zu aktualisieren, schlägt fehl:

```
AssertionError: assert core.loop is not None
  File ".../nicegui/background_tasks.py", line 41, in create
```

## Betroffene Operationen (aus Event-Handlern)
- `ui.run_javascript("...")`         → AssertionError
- `element.value = "new_value"`       → Update kommt nie beim Client an
- `with container: ui.label("...")`   → Element erscheint nicht
- `ui.timer(0.1, callback)`           → Timer wird erstellt, feuert aber nie
- `ui.notify("...")`                  → Notification erscheint nicht
- `reply_md.content = "..."`          → Markdown-Update kommt nicht an

## Root Cause
NiceGUI nutzt `background_tasks.create()` um UI-Updates asynchron an den 
Client zu senden. Diese Funktion prüft `assert core.loop is not None`. 

In Python 3.14 + NiceGUI 3.10.0 ist `core.loop` im SocketIO Event-Handler-
Context `None`, weil der asyncio Event-Loop nicht korrekt im Thread-lokalen 
Speicher verfügbar ist.

## Auswirkung
- Chat-Senden: Handler feuert, Server verarbeitet, aber UI zeigt nichts
- Textarea wird nicht geleert
- Neue Nachrichten-Bubbles erscheinen nicht
- Streaming-Antworten werden nicht angezeigt
- `_state["sending"]` bleibt auf True hängen (Timer für Reset feuert nicht)

## Workaround: JavaScript-only Chat

### Prinzip
NiceGUI wird NUR für das initiale Page-Rendering verwendet.
Alle interaktiven Features (Send, Streaming, UI-Updates) laufen 
komplett client-seitig in JavaScript.

### Implementation (ui/pages/chat.py)

1. **Input-Bereich als reines HTML:**
```python
ui.html('''
    <textarea id="ac-input" ...></textarea>
    <button id="ac-send-btn">send</button>
''')
```

2. **Event-Listener via addEventListener (NICHT inline onclick!):**
```javascript
// Vue/NiceGUI sanitisiert inline onclick="..." Attribute!
document.getElementById('ac-send-btn')
    .addEventListener('click', () => window._ac.send());
document.getElementById('ac-input')
    .addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            window._ac.send();
        }
    });
```

3. **Streaming via fetch() + ReadableStream:**
```javascript
fetch('/api/chat/stream?agent_id=...&message=...')
    .then(response => {
        const reader = response.body.getReader();
        // ... read chunks, parse SSE, update DOM
    });
```

4. **DOM-Updates via insertAdjacentHTML:**
```javascript
document.getElementById('ac-messages')
    .insertAdjacentHTML('beforeend', '<div>...</div>');
```

### Wichtige Details
- `ui.add_head_html(f"<script>...</script>")` für JS-Injection
- NICHT `ui.add_javascript()` (existiert nicht in 3.10.0)
- History wird beim Page-Render als HTML-String generiert (Python-seitig)
- Markdown-Rendering: Python (`_simple_md()`) für History, JS (`renderMd()`) für Streaming

## Prüfung bei NiceGUI-Updates
Wenn NiceGUI aktualisiert wird, testen ob der Bug gefixt ist:
```python
@ui.page("/test")
def test():
    def handler():
        ui.notify("Test")  # Erscheint das?
    ui.button("Click", on_click=handler)
```
Falls die Notification erscheint → Bug ist gefixt, Chat kann zurück auf 
NiceGUI-native Lösung migriert werden.
