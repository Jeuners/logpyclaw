# Test-Mission für MARTIN — Bienen-Lernsite

> Optimierter Prompt zum Einkippen in `/chat/MARTIN-ID`.
> Testet: A2A-Delegation an Image-Agent + CodeCraft, QC-Loop, CDC-Frische ohne Wall-Time-Injection.

---

Erstelle eine interaktive Lern-Website **"Das Leben einer Biene"** für Kinder ab 12 Jahren.

## Zielgruppe & Ton
- Alter: 12–15, neugierig aber nicht naiv
- Sprache: Deutsch, einfach aber nicht infantil, keine Disney-Kitsch
- Lernziel: nach dem Besuch können Kinder Lebenszyklus, Stockaufgaben und ökologische Bedeutung in eigenen Worten erklären

## Inhalt (5 Sektionen, scrollbar)
1. **Vom Ei zur Biene** — Lebenszyklus (Ei → Larve → Puppe → Biene, ~21 Tage)
2. **Aufgaben im Stock** — Königin, Drohnen, Arbeiterinnen + Rollenwechsel der Arbeiterin im Leben
3. **Tanz der Sammlerin** — Schwänzeltanz, Kommunikation, Sonnen-Orientierung
4. **Was Bienen für uns tun** — Bestäubung, Honig, Wachs, ökologische Bedeutung
5. **Was wir für Bienen tun können** — konkrete altersgerechte Tipps

## Interaktivität (Pflicht, mindestens 2 davon)
- Hover-Tooltips über Fachbegriffen (Drohne, Brutkammer, Pollenhöschen…)
- Lebenszyklus-Slider mit den 4 Stadien
- Mini-Quiz mit 3 Fragen am Ende, Auswertung clientseitig
- Tabs zwischen den Rollen Königin/Drohne/Arbeiterin

→ Reine Scroll-Seite ohne JS-Interaktion gilt als unfertig.

## Bildmaterial (Image-Agent via ComfyUI)
- **6 Bilder**: 1 Hero (1600×900) + je 1 pro Sektion (1024×768)
- Stil: fotorealistisch, Makro-Ästhetik mit weichem Bokeh, KEINE Cartoon-Bienen
- Jedes Bild thematisch passend zur Sektion (kein generisches „irgendeine Biene")

## Deliverable
- Eine HTML-Datei mit eingebettetem CSS+JS (self-contained)
- Bilder als PNG im selben Ordner relativ referenziert
- Ablage: `data/exports/bienen-website/index.html`

## Qualitäts-Check vor Abgabe
- [ ] Auf 375px Breite (Handy) lesbar
- [ ] Interaktivität ohne Konsolen-Fehler
- [ ] Biologische Fakten korrekt (Lebenszyklus ~21 Tage, Stocktemperatur ~35°C, ein Volk hat 30k–60k Bienen etc.)
- [ ] Mindestens 2 der vier Interaktivitäts-Patterns umgesetzt
- [ ] Alle 6 Bilder geliefert und thematisch passend

---

## Was dieser Test verifiziert

| Komponente | Was geprüft wird |
|---|---|
| A2A-Delegation | Martin verteilt via `@Image-Agent` und `@CodeCraft` |
| Operator-Pattern | Martin orchestriert mehrere parallele Sub-Tasks |
| ComfyUI-Integration | Image-Agent → 192.168.4.15:8000, 6 Renders |
| QC-Loop | Acceptance-Kriterien triggern Re-Iteration |
| CDC ohne Wall-Time | System-Prompt enthält **keinen** `[Aktuelle Zeit: …]` mehr |
| Coding-Skill | CodeCraft baut self-contained HTML mit JS |
