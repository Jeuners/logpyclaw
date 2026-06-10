# WhatsApp Sync-Daemon

LogpyClaw nutzt das CLI-Tool [`wacli`](https://github.com/tulir/whatsmeow) für
WhatsApp-Integration. Der `wacli sync --follow`-Daemon hält die Verbindung zu
WhatsApp persistent offen und synchronisiert eingehende Nachrichten in eine
lokale SQLite-Datenbank unter `~/.wacli/`.

## Status prüfen

```bash
launchctl list | grep wacli
# 14700  0  com.agentclaw.wacli-sync   ← läuft

/Users/jeuner/bin/wacli doctor
# STORE          /Users/jeuner/.wacli
# LOCKED         true            ← Daemon hält den DB-Lock
# AUTHENTICATED  true            ← QR-Code bereits gescannt
# CONNECTED      true|false      ← aktuelle WhatsApp-Verbindung
```

## Aktivieren / Neustarten

Der Daemon ist als macOS LaunchAgent unter
`~/Library/LaunchAgents/com.agentclaw.wacli-sync.plist` registriert und startet
automatisch beim Login (`RunAtLoad=true`, `KeepAlive=true`).

Manuell laden / entladen:

```bash
launchctl load   ~/Library/LaunchAgents/com.agentclaw.wacli-sync.plist
launchctl unload ~/Library/LaunchAgents/com.agentclaw.wacli-sync.plist
```

## Lock-Konflikt

`wacli send` und `wacli sync --follow` schließen sich gegenseitig aus —
beide brauchen exklusiven Schreibzugriff auf die `messages.db`. Der
`WhatsAppSkill` löst das automatisch:

```python
# skills/whatsapp.py:_run()
stopped = await self._launchctl("stop", sync_label)
if stopped:
    await asyncio.sleep(1.0)         # Lock freigeben lassen
try:
    proc = await asyncio.create_subprocess_exec(*cmd, ...)
    out, err = await proc.communicate()
finally:
    if stopped:
        await self._launchctl("start", sync_label)
```

Während eines Sends:
1. Daemon wird per `launchctl unload` gestoppt
2. 1 s gewartet bis der Lock freigegeben ist
3. `wacli send …` ausgeführt
4. Daemon per `launchctl load` wieder gestartet

## Erst-Setup

Falls `AUTHENTICATED=false`:

```bash
launchctl unload ~/Library/LaunchAgents/com.agentclaw.wacli-sync.plist
/Users/jeuner/bin/wacli auth
# → QR-Code im Terminal scannen mit WhatsApp → Einstellungen → Verknüpfte Geräte
launchctl load ~/Library/LaunchAgents/com.agentclaw.wacli-sync.plist
```

## Default-Gruppe

Aktuelle Gruppe für `agents.yaml` und Default-Send-Ziel:

```yaml
- type: skill
  skill_id: whatsapp
  config:
    default_group: "<GRUPPEN-JID>@g.us"   # oder via WHATSAPP_DEFAULT_GROUP in .env
```

Wird verwendet wenn keine explizite JID/Empfänger im Prompt steht oder
Gruppen-Keywords ("gruppe", "unsere gruppe", "h.g.o.d.") matchen.
