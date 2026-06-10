# PLAN — LogpyClaw v3

Stand: 2026-06-10. Lebendes Dokument; erledigte Punkte wandern nach unten.

## Nordstern

Der Maschine ein **Bauchgefühl für Zeit** geben: Agenten, die wissen, was sie
in der verbleibenden Zeit schaffen — und auf dieser Basis delegieren,
eskalieren oder selbst handeln. Der Weg dahin führt vom heutigen zentral
orchestrierten System (siehe README, "Einordnung") zu echtem Peer-Verkehr,
bei dem Zeit, Vertrauen und Kausalität im Protokoll stecken.

---

## 1. System — der Weg zum echten Multi-Agent-System

- [ ] **Peer-Dispatch**: Agenten dürfen Missionen initiieren und einander
      direkt (via Conductor, aber ohne Martin-Umweg) beauftragen.
      Damit entsteht erstmals der Verkehr, für den der CDC-Klassifikator
      gebaut ist.
- [ ] **Agenten-Initiative**: Heartbeat-/Dream-artige Eigenzeit-Loops für
      reguläre Agenten — ein Agent, der von sich aus etwas anstößt.
- [ ] **Distributionales Zeitgefühl**: Eigenzeit-Raten als Verteilung
      (Median + Streuung) statt Skalar. Lektion 3 des Drachen-Experiments:
      ein Median ist kein Bauchgefühl. `llm_summary()` soll Unsicherheit
      mitliefern ("meistens 12s, selten 30s").
- [ ] **Trust-Semantik schärfen**: `success` an den QC-Ausgang koppeln
      (Score ≥ min) statt an den Message-Typ — QC-Fails zählen aktuell
      als Erfolg.
- [ ] **Stance-Matrix**: bleibt bewusst Policy (nicht gelernt) — im README
      kurz begründen, damit es nicht wie eine Lücke aussieht.
- [ ] Kleinkram: Boot lädt komplette DB in RAM (Lazy-Trace-Load),
      `result`/`error`-Persistenz auf Secrets prüfen, `timeout: 900` für
      agent:claude in agents.yaml, EWMA-Lock falls je Multi-Threading.

## 2. Experiment — der fehlende Beweis

- [ ] **Drachen v5**: (a) Rollen randomisieren (mal ist der Ritter schneller,
      mal der Magier — die Ordnungsrelation darf nicht erratbar sein),
      (b) Entscheidungskorrektheit als primärer Endpunkt, Überleben sekundär,
      (c) Deadlines mit Puffer ≥ Latenz-Streuung statt Knife-Edge.
      Skripte: `experiments/dragon4.py` als Basis.
- [ ] Stichprobe groß genug für Signifikanz (n ≥ 100, Fisher exact).
- [ ] CONCURRENT_DRIFT real erzeugen: Mission mit parallelen Plan-Wellen
      auf ungleich schnelle Agenten, Klassifikator-Output auswerten.

## 3. Paper (Time_Dilation_in_LLM_Agent_Systems)

- [ ] **§6 Implications** und **§7 Conclusion** schreiben (Autorenstimme).
- [ ] §4 Stack-Beschreibung aktualisieren: beschreibt noch den alten
      AgentClaw-Stack (NiceGUI, Qdrant); LogpyClaw v3 als Nachfolger
      präzisieren oder §4 explizit als historische Fallstudie rahmen.
- [ ] v5-Ergebnisse in §5 nachtragen, sobald gelaufen.
- [ ] Offen: `index.html` im Paper-Repo — behalten oder entfernen?

## 4. Repo & Außenwirkung

- [ ] Branch `cdc-evaluation` auf GitHub löschen (identisch mit main)
      oder als Marker dokumentieren.
- [ ] `legacy-agentclaw`-Branch: Einzeiler in dessen README, dass main
      jetzt LogpyClaw v3 ist (Orientierung für alte Links).
- [ ] Optionale Migration der Laufzeitnamen: LaunchAgent
      `com.agentclaw.wacli-sync`, `~/Downloads/AgentClaw`, `~/.agentclaw`
      (drei Code-Stellen + drei macOS-Befehle; bis dahin bleiben sie bewusst).
- [ ] Replay-Viewer deployen (`deploy dragon-replay`) und im Paper/X-Thread
      verlinken.
- [ ] Pete/OpenClaw-Kontakt: Paper + Repo + ehrliche Frage (Entwurf liegt
      im Chat-Verlauf; Kern: "working draft, running code, was würde dich
      überzeugen?").

---

## Erledigt (Juni 2026)

- CDC tau/rate-Split, EWMA-Raten, Clock-Vererbung; 218 Tests grün
- Fraktionssystem im Dispatch verdrahtet (Envelope, Trust-Learning,
  Adversarial-Bridge fail-closed), EXPECTED_DRIFT/FACTION_RACE aktiv
- Trust-Verjährung (Evidenz-Halbwertszeit 7d) + Mathematik-Doku im README
- Martin: explizite Adressierung schlägt Planner; QC sieht Task+Ergebnis;
  parallele Plan-Wellen mit Caps
- Storage: PQC-Chain-Race gefixt, verify_chain fail-closed, WAL,
  async Offload, Heartbeat via Token-Stream
- Security-Review (4 MEDIUMs gefixt), Public-Release-Hygiene, MIT-Lizenz
- Spiele Liftwerk + Sky Vanguard (gebaut via Martin→Claude, deployed)
- Drachen-Experiment v1–v4 inkl. Replay-Viewer; Missions-DB-Auswertung
- Paper: Laien-Intro, Reference Implementation, §5 Preliminary Evaluation,
  Topologie-Diagnose in §5.3, Renummerierung §6/§7
- Repo public: main = LogpyClaw v3, alter Stand in `legacy-agentclaw`
