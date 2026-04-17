# Dream Agent - Memory Optimization

## Ziel des Träumens

Der Dream Agent räumt täglich das Memory aller Agenten auf:
1. **Memory aufräumen** - relevante Einträge behalten, irrelevante löschen
2. **Widersprüche auflösen** - widersprüchliche Informationen identifizieren und bereinigen
3. **Veraltete Daten löschen** - alte, nicht mehr relevante Erinnerungen entfernen

## Trigger

- **Automatisch**: 1x täglich (konfigurierbar via Heartbeat)
- **Manuell**: "Träume", "optimiere memory", "räume auf"

## Ablauf

### Phase 1: Scan
- Alle Memory-Einträge aller Agenten abrufen
- Zeitstempel analysieren (veraltet = >30 Tage)
- Duplikate erkennen

### Phase 2: Analyse
- Widersprüche finden (gleiche Themen, unterschiedliche Aussagen)
- Relevanz-Score berechnen (basierend auf Recency + Häufigkeit)
- Kategorisieren: wichtig, neutral, veraltet

### Phase 3: Bereinigung
- Veraltete Einträge löschen (>30 Tage, keine relevanten Keywords)
- Widersprüche markieren und optional löschen (neueste behalten)
- Zusammenfassung erstellen

## Konfiguration

```json
{
  "dream": {
    "active": true,
    "time": "03:00",  // 3 Uhr nachts
    "retention_days": 30,
    "keep_duplicates": false,
    "resolve_contradictions": true
  }
}
```

## Agent Flag

Jeder Agent kann ein `dream`-Flag setzen:
```json
{
  "dream": {
    "active": true,
    "optimize": true
  }
}
```

Nur Agenten mit `dream.active = true` werden optimiert.

## Output

Nach dem Träumen:
- Zusammenfassung was gelöscht wurde
- Liste der optimierten Agenten
- Speicherplatz gespart (geschätzt)

## Beispiel-Output

```
🌙 Träume abgeschlossen für 5 Agenten
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• LISA: 12 Einträge → 8 (4 veraltet, 0 Widersprüche)
• MARTIN: 45 Einträge → 38 (5 veraltet, 2 Widersprüche gelöst)
• Flo: 8 Einträge → 8 (keine Änderungen)
• Jan: 23 Einträge → 18 (3 veraltet, 2 Widersprüche)
• Picasso: 5 Einträge → 5 (keine Änderungen)

📊 Gesamt: 93 → 77 Einträge (17% Reduction)
⏱️ Dauer: 45s
```