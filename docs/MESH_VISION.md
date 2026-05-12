<!--
SPIEGEL-DOKU. Quelle: ~/Desktop/Server AI/dillenberg.net/content/drafts/logpy-vision.md (gepushtes Public-Paper für dillenberg.net).

AgentClaw ist die Orchestrator-Engine innerhalb LogPy — siehe Layer-Sektion. AgentClaw bleibt standalone wertvoll für alltägliche Multi-Agent-Anwendungen am eigenen Mac, unabhängig von der Mesh-Vision.

Bei inhaltlichen Updates: dillenberg.net-Repo ist source-of-truth, hier nur spiegeln. Phase-6-Bezüge → siehe docs/TODO_PHASE6_TEMPORAL.md im selben Verzeichnis.
-->

---
title: "Wenn keiner kommt."
subtitle: "Eine Forschungs-Hypothese: kann eine Bottom-up-Koordinationsschicht das tragen, was Top-down-Hilfe heute nicht mehr abdeckt?"
project: LogPy
author: "H.G.O. Dillenberg"
date: 2026-05-09
status: draft
type: research-hypothesis
tags: [logpy, agentclaw, mesh, antifragility, multi-agent, a2a, resilience, lora]
language: de
abstract: >
  LogPy ist eine Forschungs-Initiative für eine zweite Linie der
  gesellschaftlichen Koordination — Bottom-up, am Körper, augmentiert
  durch Multi-Agent-Systeme. Als Orchestrator-Substrat nutzt LogPy
  AgentClaw, ein lokales Multi-Agent-Framework. Hier skizziere ich eine
  Hypothese, die mich seit Monaten beschäftigt: ob das Substrat eine
  Schicht tragen könnte, die einspringt, wenn die erste Linie reißt.
  Drei-Tier-Architektur (Wearable, Mesh-Peers, Resilience Centers),
  Antifragilität als Möglichkeit, klare Risiko-Achse. Krise ist
  Lackmustest, nicht Selbstzweck. Ich weiß nicht, ob das geht. Ich will
  herausfinden, ob es geht.
---

# Wenn keiner kommt.

## Wo die erste Linie reißt

2021 ist im Ahrtal die Linie gerissen. Telefon weg, Funk überlastet,
Behörden überfordert — Menschen wussten nicht, wer noch lebt, wer wo war,
wer Hilfe brauchte. Hilfe kam. Aber spät. Manche zu spät.

Das wird wieder passieren. Häufiger sogar — Klimakrisen werden nicht
weniger, Cyber-Angriffe auf Strom und Wasser sind keine Theorie mehr,
Hilfsorganisationen verlieren Personal während die Lagen wachsen. Eine
Gesellschaft, deren Koordination an *einer* Linie hängt — top-down,
hierarchisch, zentral — wird das nicht aushalten.

Ich frage mich seit Monaten: was wäre, wenn es eine zweite Linie gäbe?
Eine, die einspringt, wenn die erste nicht reicht. Bottom-up, von Mensch
zu Mensch, augmentiert durch Maschinen, die wir am Körper tragen.

Ich weiß nicht, ob das geht. Aber ich will herausfinden, ob es geht. Die
Initiative dahinter heißt — bei mir — **LogPy**.

## Was schon steht

Eine kurze Begriffsklärung, weil die Layer wichtig sind: **LogPy** ist
die Initiative — das gesamte System mit Brand, Front, Identity-Stack,
Mesh-Schicht. **AgentClaw** ist die Orchestrator-Engine darin, ein
lokales Multi-Agent-Framework, das in mehreren Phasen entstanden ist.
AgentClaw hat Wert auch außerhalb von LogPy (alltägliche
Multi-Agent-Anwendungen am eigenen Mac). LogPy nutzt AgentClaw als
Kernel und baut Mesh, Tier-3-Architektur und Public-Front darum herum.

Was AgentClaw als Substrat heute trägt:

- **Frame-Architektur** — jeder Agent hat eigene Reasoning-Frames,
  versionierte Reads, kausale Ordnung.
- **Time-Provider mit Eigenzeit** — schnelle Agents (kleine Modelle) und
  langsame Agents (Frontier-Modelle) haben unterschiedliche „Eigenzeiten"
  (γ), gegen die Drift gemessen werden kann.
- **Causal Dilation Clock (CDC)** — eine Datenstruktur, die Cross-Agent-
  Merges in vier Relationen klassifiziert: ordered, causal-drift,
  concurrent-drift, inconsistent.
- **Temporal-Policy** — Side-Effect-Skills (Mail, Telegram, LinkedIn)
  werden bei zu großem Drift abgelehnt, statt blind ausgeführt zu werden.

Parallel auf [dillenberg.net](https://www.dillenberg.net): unter
`rent-a-human.dillenberg.net` läuft seit 2026-05-04 ein A2A-spec-konformer
Agent-Endpoint (JSON-RPC 2.0, AgentCard, SendMessage/GetTask). Das ist der
erste produktive Agent-zu-Agent-Test, den ich im offenen Netz fahre.

Das Substrat trägt heute Single-Agent-Use-Cases. Die Frage in diesem Paper
ist: könnte es auch ein gesellschaftliches Mesh tragen?

## Drei Tiers, drei Verfügbarkeiten

Die naive Frage „lokal oder Cloud?" hat eine naive Antwort: weder noch.
Die Architektur, die ich zur Diskussion stelle, hat drei Schichten — jede
mit eigener Verfügbarkeit und eigenem Kompromiss zwischen Resilienz und
Leistung.

### Tier 1 — Personal Device (am Körper)

Ein Wearable oder Smartphone mit einem kleinen LLM (3–7B Parameter,
NPU-beschleunigt). Verbindet sich über LoRa, Bluetooth oder Mobilfunk.
Verfügbarkeit: immer.
Stärke: persönlich, privat, jederzeit ansprechbar.
Schwäche: kleines Modell, begrenzter Kontext.

### Tier 2 — Mesh-Peers (~10 km Radius)

Andere LogPy-Träger in lokaler Funkreichweite. LoRa-Mesh, Bluetooth-Mesh
oder lokales WLAN. Compute wird gepoolt, Skills werden geteilt,
Lagebild entsteht aus den Sensoren mehrerer Träger.
Verfügbarkeit: opportunistisch.
Stärke: emergente Intelligenz, keine zentrale Abhängigkeit.
Schwäche: braucht Träger-Dichte, niedrige Bandbreite, Latenz.

### Tier 3 — Resilience Centers

Gehärtete Rechenzentren mit garantierter Mindest-Laufzeit (X Stunden via
USV / Diesel / Solar). Frontier-LLMs, Storage, Cross-Region-Routing.
Erreichbar primär über Internet, sekundär über LoRa-Gateway.
Verfügbarkeit: garantiert in Zeitfenstern, planbar.
Stärke: massive Capability, persistente Logs, große Modelle.
Schwäche: nicht überall, nicht immer, Governance-Frage offen.

**Tier 3 ist optional, aber wenn erreichbar, ein Capability-Uplift.** Der
Agent priorisiert: reicht Tier 1 für die Aufgabe → solo. Tier 2 erreichbar
und nützlich → kollaborieren. Tier 3 erreichbar und Aufgabe groß → eskalieren.

Das System funktioniert auf jedem einzelnen Tier. Es wird *besser* mit
jedem zusätzlichen Tier. Kein Tier ist Single Point of Failure. Das ist
**graceful degradation by design** — das Standard-Pattern aus verteilten
Systemen, übertragen auf gesellschaftliche Infrastruktur.

Das Vorbild für Tier 3 sind Tor Directory Authorities und IPFS Pinning
Services: spezialisierte, opportunistisch nutzbare Knoten, die das Netz
nicht *brauchen*, aber stärker machen, wenn sie da sind.

## Was zwischen den Tiers passiert

Drei Substrat-Layer müssten zusammenkommen, damit das Mesh überhaupt
möglich wird.

**Identity.** Jeder Agent hat eine kryptografische Identität, mit der er
sich gegenüber anderen Agents authentifiziert. Web-of-Trust, ohne
Certificate Authority, ohne Big-Tech-Anchor. Reputation wird lokal
geführt: dein Agent vertraut den Agents, mit denen er gute Erfahrungen
gemacht hat — und übernimmt vorsichtig Vertrauen aus deinem unmittelbaren
Umfeld.

**Time und Order.** In einem Mesh ohne synchronen Wall-Clock (kein NTP
über LoRa, RTC-Drift auf billiger Hardware, lange Offline-Phasen) braucht
es Hybrid Logical Clocks für kausale Ordnung. Die γ-Achse aus AgentClaw
liefert zusätzlich Aufwand-Information: wer hat seit dem letzten Sync
*wieviel* nachgedacht? Das ist beim Merge zweier Agents die Differenz
zwischen „du bist hinterher" und „du bist auf einer anderen Spur".

**Routing und Capability-Discovery.** Ein Agent, der Hilfe sucht, broadcastet
sein Bedürfnis lokal. Andere Agents prüfen, ob sie passen — Skill, Distanz,
Verfügbarkeit. Die A2A-Spec gibt das technische Vokabular vor; das
Skill-Matching darüber wäre eigener Beitrag.

## Vier Phasen — der Adoption-Pfad

Das Mesh entsteht nicht durch Verkündigung, sondern durch Adoption. Damit
Adoption funktioniert, muss jede Phase auch alleine wertvoll sein. Die
Phase darunter darf nicht auf die Phase darüber warten müssen.

| Phase | Was es leistet | Wert pro Träger | Mindest-Adoption |
|-------|----------------|------------------|-------------------|
| **A** | Solo-Agent: persönliche Produktivität | sofort | keiner |
| **B** | Lokales Skill-Matching, Mutual Aid in der Nachbarschaft | mittel | ~3 % lokal |
| **C** | Krisenkoordination, Schwarm-Triage | hoch (selten) | ~10 % regional |
| **D** | Gesellschaftliche Resilienz-Schicht | systemisch | ~20 % national |

**Phase A muss eigenständig tragen.** Das ist die kritische Bedingung. Wer
einen LogPy-Träger nicht überzeugt, dass das Gerät am ersten Tag
nützlich ist, wird die Phasen B–D nie erreichen. Das ist die Lehre aus
allen vergleichbaren Mesh-Networks der letzten 15 Jahre — Meshtastic,
Briar, Helium, Bridgefy. Alle technisch solide, alle bei Cold-Start
gestrandet.

## Krise als Lackmustest

Wir reden über Krisen, weil sie zeigen, was sonst unsichtbar bleibt. Aber
das Mesh ist nicht *für* Krisen gebaut, sondern *getestet* an ihnen.

Das kanonische Szenario: Vulkanausbruch im Umkreis von 10 km. Mobilfunk
überlastet, Strom unzuverlässig. LogPy-Träger im Gebiet vernetzen sich
über LoRa und lokales WLAN. Lagebild entsteht aus den Sensoren der Träger.
Skills werden lokal gematcht: drei Personen mit Medikamentenbedarf, zwei
mit Erste-Hilfe-Ausbildung, einer mit Geländewagen — die Agents schlagen
ein Treffen vor, Menschen entscheiden. Wenn ein Resilience Center
erreichbar ist, läuft das Routing über dort; wenn nicht, lokal.

Ich bin in dieser Beschreibung ehrlich vorsichtig. **Real ist Krise messy.**
Das Kind weint, der Erste-Helfer hat eine Panikattacke, der Patient stirbt
unterwegs. Algorithmen scheitern dort, wo Improvisation gebraucht wird.
Was das Mesh kann, ist: Vorschläge machen, Friction zwischen Helfenden
reduzieren, Lagebild teilen. Was es nicht kann: die Krise „lösen".

Ein zweites Beispiel, das mir wichtig ist, weil es zeigt wo die Schicht
gefährlich werden könnte: ein Agent kann *Familien zusammenführen* —
„dein Bruder ist im Sektor 3, hier der kürzeste Weg" — er kann es aber
auch *nicht* tun, wenn die Risikoabschätzung etwas anderes sagt
(Trümmer-Gebiet, Gas-Leck, vermutete Gewaltsituation). In beiden Fällen
gilt eine Regel, die ich für nicht-verhandelbar halte: **die Empfehlung
trägt immer das Entscheidungs-Level mit.** Auf welcher Information beruht
sie? Mit welcher Konfidenz? Aus welcher Quelle? Wann wurde diese
Information zuletzt gemessen, und wie schnell veraltet sie? Und am
wichtigsten: **ist die Zeit darin berechnet oder erfahren?** Ein Agent,
der „du hast 20 Minuten Zeit" sagt, sagt etwas mathematisches — keine
erfahrene menschliche Zeit, die unter Stress dehnt und unter Müdigkeit
staucht. Das muss er als „berechnet, nicht gefühlt" markieren, damit der
Mensch mit eigenem Bauch korrigiert. Ein Agent, der „geh nicht dort hin"
sagt, ohne offenzulegen warum, ist ein Agent, der heimlich Macht ausübt.
Genau das wollen wir nicht.

Der ehrliche Anspruch ist also: Sekunden gewinnen, Doppelarbeit
verhindern, Wissen finden, das sonst verloren geht — und Entscheidungen
*sichtbar* machen, statt sie wegzuautomatisieren. Nicht: die
Katastrophenschutzbehörde ersetzen.

## Die Risiken, die wir mitdenken müssen

Eine Vision ohne ehrliche Risikoanalyse ist Marketing. Hier sind unsere.

**1. Vertrauen ist das eigentliche Kernfeature, nicht Skill-Matching.**
Wenn dein Agent dem Agent eines Fremden glaubt „ich bin Sanitäter", was
passiert, wenn das eine Lüge ist? In Krisen explodiert Vertrauen — gefakte
Skills, Panik-Manipulation von außen, Bot-Schwärme die das Netz fluten.
Die Identity- und Reputation-Layer sind 80 % der Komplexität. Skill-Matching
ist 5 %. Wer das Trust-Problem nicht löst, baut nichts Tragfähiges.

**2. Hardware existiert noch nicht im Mass-Market.** Lokales Inferenz-fähiges
Wearable, das einen ganzen Tag durchhält und ein 7B-Modell lokal fährt:
2026 noch nicht in der Form, die das Mesh braucht. Realistisch ist
Smartphone-im-Pocket-Modus für die ersten Jahre. Das ist ok — es heißt
nur, dass wir mit Form-Faktoren leben, die existieren, statt auf den
perfekten Form-Faktor zu warten.

**3. Cold-Start ist das Killer-Problem.** Adoption ist nicht eine Frage von
Tech-Eleganz, sondern von Wert-am-Tag-Eins. Wenn der Wert eines LogPy-
Trägers erst nach 3 % lokaler Adoption beginnt, kommen die 3 % nie
zusammen. Phase A (Solo) muss tragen, sonst bleibt es bei der Demo.

**4. Tier-3-Governance — der heikle Punkt.** Wer betreibt Resilience Centers?
Staat → Single Point of Control. Privat → Single Point of Failure.
Genossenschaftlich → noch nie im Maßstab erprobt. Das Tier-3-Modell ist
der Punkt, an dem Top-Down zurückkriecht. Die Frage „wer betreibt"
entscheidet, ob das Mesh Resilienz bleibt oder Parallelstruktur wird.

**5. Dual-Use ist real.** Mesh + Identity + Skill-Matching ist neutrale
Infrastruktur. Sie verstärkt, was reingeht. Mutual-Aid-Netzwerke werden
damit besser. Selbstjustiz-Schwärme, gewaltbereite Gruppen mit Skill-
Matching auch. „Wer nicht im Netz ist, gehört nicht dazu" ist eine
schreckliche Schwelle, die von selbst entsteht, wenn das Netz nützlich
genug wird. Self-Limiting-Mechanismen müssen früh rein, sonst sind sie
später nicht nachrüstbar.

**6. LLMs haben mathematische Zeit, keine erfahrene.** Ein LLM kann
„240 Sekunden" rechnen, aber es kann nicht *fühlen* wie sich 240
Sekunden in einer Krise mit weinenden Kindern anfühlen. Es kennt keine
Stress-Dehnung, keine Müdigkeits-Stauchung, keine Schwankungs-Intuition
für Sensor-Daten. Empfehlungen mit Zeit-Bezug („in 20 Minuten",
„dringend", „bald") müssen explizit als *berechnet* markiert sein, damit
der Mensch mit eigenem Bauch korrigiert. Schwankungs-Erkennung in
Sensorströmen ist nicht Sache des LLM — die liefert Statistik (Z-Score,
Rate-of-Change), das LLM kontextualisiert sie. Trennung: Zahlen werden
gerechnet, Sprache vom LLM. Niemals umgekehrt.

**7. Die Verifikations-Welle kommt — die Frage ist nur wer sie baut.**
EU DSA + eIDAS 2.0, UK Online Safety Act, Australia U16-Ban,
französisches SREN-Gesetz — Identity-Pflicht im Web wird Realität, ob
wir es wollen oder nicht. Dazu kommt die Bot- und KI-Agent-Schwemme im
offenen Netz, auch durch unsere eigenen Agents. Die ehrliche Frage ist
nicht „Pflicht-Verifikation ja oder nein", sondern „zentrale Top-down-
Lösung (Apple/Google/Staat sammelt) oder selbstsouveräne Bottom-up-
Variante (Träger trägt seine Identity, niemand sammelt sie zentral)".
LogPy positioniert sich als Bottom-up-Antwort, nicht als Verzicht aufs
Thema. Wer „etwas Freiheit aufgeben für Sicherheit" mit zentraler
Datensammlung verwechselt, hat nur eine der beiden Varianten gesehen.
Selektive Offenlegung + Web-of-Trust + Strafverfolgung-mit-Anordnung-
aber-ohne-Default-Zugriff ist Achse-A-konform — verdrängen wäre
Realitätsverweigerung.

## Die Achsen-Entscheidung

Es gibt zwei Visionen, die technisch identisch aussehen, politisch aber
Welten trennen.

**Achse A — Resilienz-Schicht.** Das Mesh hilft, wenn Institutionen
überlastet oder ausgefallen sind. Es kooperiert mit Behörden, wo möglich.
Es ist sonst unsichtbar. Es hat Self-Limiting-Mechanismen, die es daran
hindern, Aufgaben zu übernehmen, die Institutionen besser leisten. Es ist
anschlussfähig an eine offene Gesellschaft.

**Achse B — Parallelstruktur.** Das Mesh tritt als Alternative zu
Institutionen an. Es verweigert Kooperation. Es ist „autonom im starken
Sinn". Es ist anschlussfähig an Akzelerationismus, Krypto-Anarchie,
sezessionistische Strömungen.

Tech ist identisch. Wirkung ist gegensätzlich.

**Mein Bauchgefühl ist klar: ich glaube an Achse A.** Eine Schicht, die
*mit* Behörden kann, nicht gegen sie. Die Strafverfolgung nicht unmöglich
macht. Die Pseudonymität nicht erzwingt. Die Self-Limiting-Mechanismen im
Code trägt, statt sie nur zu versprechen. Konkret heißt das mindestens:

- keine Eskalations-Aufrufe an Träger
- keine Verschlüsselung, die Strafverfolgung bei realen Verbrechen unmöglich macht — aber kein Default-Zugriff von Behörden, nur per richterlicher Anordnung gegen einen einzelnen Träger
- Identity ist Bottom-up und selbstsouverän: selektive Offenlegung pro Anwendung („ich bin >18", „ich bin Sanitäter mit verifizierter Ausbildung") ohne den vollen Namen freizugeben. Keine zentrale Datensammlung. Kein erzwungenes Anchoring an Staat oder Big-Tech. Web-of-Trust ohne CA.
- jede Agent-Empfehlung trägt das Entscheidungs-Level mit — Konfidenz, Quelle, Begründung, **Alter der Datengrundlage**, **Halbwertszeit der Aussage**, **Zeit-Typ (berechnet vs. erfahren)** — keine heimliche Macht durch Black-Box-Vorschläge

Mein Verstand weiß: das muss ich prüfen, nicht behaupten. Diese
Forschungsphase wird die Bedingungen aufschreiben, unter denen Achse A
überhaupt erhalten bleiben kann. Wenn sich die als unhaltbar erweisen,
kommt das Paper offen zurück.

Was sicher ist: die Achsen-Entscheidung ist im späteren Verlauf
irreversibel. Wer einmal auf B baut, kommt nicht zurück nach A. Wer ohne
Self-Limiting startet, rüstet es nicht mehr nach. Deshalb gehört sie
*jetzt* in die Diskussion, nicht später.

## Was die Forschungsphase prüfen muss

Dieses Paper ist eine Hypothese, kein Produkt. Der nächste Schritt ist
nicht ein Manifest, sondern ein Validator.

**2-Device-Demo (4–6 Wochen, ~150 € Hardware).** Ein Mac (vorhandene
Infrastruktur) als „starker Agent", ein Raspberry Pi 5 mit Hailo-NPU als
„wearable proxy". Bluetooth oder LoRa-Pair für die nicht-internet
Strecke. Ein einziger E2E-Use-Case: ich diktiere dem Pi eine Aufgabe,
der reicht sie an den Mac, der erledigt sie und syncs zurück. Drift-
Reject einmal mit Wall-Clock, einmal γ-aware. Differenz messen.

Was die Demo beantworten soll:

- Funktioniert die Auth-Story zwischen zwei LogPy-Trägern (AgentClaw-zu-AgentClaw)?
- Wo bricht Wall-Clock-Sync, wenn LoRa-Latenz reinkommt?
- Was ist der Demo-Wert für einen einzelnen Träger, *ohne* Netz?
- Sind Token-Kosten und Latenzen so, wie die γ-Heuristik vermutet?
- Lässt sich Decision-Transparency (Konfidenz + Quelle + Begründung pro Empfehlung) so umsetzen, dass sie für den Träger lesbar bleibt — nicht im Maschinen-Log versteckt?

Wenn die Demo trägt, ist die Tech-Hypothese validiert. Wenn nicht, war's
ein billiger Validator für das Eingeständnis, dass eine Lücke da ist, die
mit dem aktuellen Substrat nicht zu schließen ist.

Parallel zu prüfen:

- HLC (Hybrid Logical Clocks) als Standard-Ordnung evaluieren, statt AgentClaws Phase-6-Migration blind zu starten
- Tier-3-Governance-Modelle vergleichen — Genossenschaft, Stiftung, Stadtwerke-Tier — welches trägt die Achse-A-Entscheidung?
- Self-Limiting-Mechanismen als Code skizzieren, nicht als Versprechen — inklusive der Decision-Level-Pflicht für jede Empfehlung
- Offen: ob ein eigenes LLM-OS (Karpathy 2024) für den Stack lohnt — gleicher Kernel von Wearable bis Resilience Center, statt generisches Modell mit verlustbehafteten Tier-Übergängen. Nur mit Uni- oder Industrie-Partner sinnvoll, der Modelltrainings-Kompetenz mitbringt.
- Offen: zwei-achsige Eigenzeit als Substrat — `γ_machine` (AgentClaws CDC-Achse, Reasoning-Aufwand pro Wand-Sekunde) und `γ_human` (Stress-/Müdigkeits-bedingte Wahrnehmungs-Dehnung). Wenn ein Mesh beide Achsen kennt, kann es zwischen ihnen vermitteln — z.B. einer überlasteten Maschine sagen „liefere langsamere Sätze, dieser Mensch ist gerade in Stress-Dehnung". Das ist nicht Phase-6-Kosmetik, das ist Forschungspfad mit echtem Use-Case.

## Schluss

Ich weiß heute nicht, ob LogPy in zwei Jahren 100 oder 100.000 oder
1.000.000 Träger hat. Ich weiß nicht, ob Tier 3 von einer Genossenschaft
oder vom THW oder von Hetzner betrieben werden wird. Ich weiß nicht, wie
die Krise aussieht, in der das Netz das erste Mal wirklich gebraucht würde.

Was ich weiß: dass die erste Linie unter Lasten steht, denen sie nicht
gewachsen ist. Dass eine zweite gerade nicht existiert. Dass die Bauteile
für die zweite — kleine Modelle, Mesh-Funk, Agent-Protokolle,
Identity-Stacks — alle bereits da sind und nur noch ehrlich
zusammengesetzt werden müssten.

Dieses Paper ist ein Aufruf zur Mit-Forschung. Stimmen aus
Katastrophenschutz, Distributed-Systems-Forschung, Mutual-Aid-Praxis,
Krypto-Engineering, Politik-Theorie und Hardware-Engineering sind
willkommen. Wo ich mich verrenne, sagt es mir. Wo ihr eigene Daten habt,
die meine Hypothese prüfen oder widerlegen, sagt es mir auch.

Der nächste Beleg, den ich erbringen will, ist die 2-Device-Demo. Das ist
das nächste Update, das hier erscheinen wird.

— H.G.O. Dillenberg
2026-05-09, [dillenberg.net](https://www.dillenberg.net)

---

## Anhang: Status der Komponenten

| Komponente | Status | Repo |
|------------|--------|------|
| AgentClaw Substrat (Phase 1–5) | gebaut, produktiv | `~/Desktop/agentclaw/` |
| AgentClaw Phase 6 (τ-basierte Eigenzeit) | offen, zurückgestellt | `docs/TODO_PHASE6_TEMPORAL.md` |
| A2A-Endpoint (Single-Agent, JSON-RPC 2.0) | live | `rent-a-human.dillenberg.net` |
| ALICE Single-Agent UI | live | `dillenberg.net/alice/` |
| Identity / Web-of-Trust | konzeptionell | – |
| HLC-Spike | nicht begonnen | – |
| 2-Device-Demo | nicht begonnen | – |
| Tier-3-Governance-Modell | offen | – |

## Anhang: Beziehung zu existierenden Bewegungen

Wir bauen auf Schultern, nicht auf der grünen Wiese. Anschluss zu:

- **Meshtastic** — LoRa-Mesh-Stack, ~100 k Devices weltweit. Wir lernen
  vom Form-Faktor und vom Cold-Start-Verlauf.
- **Briar** — Bluetooth/Tor-P2P-Messaging, Identity-Layer ohne Server.
  Wir lernen vom Trust-Modell.
- **Freifunk** — WLAN-Mesh in DE, ~50 k Knoten, 20 Jahre Erfahrung mit
  Genossenschafts-Governance.
- **Solid (Tim Berners-Lee)** — Personal Data Pods. Wir lernen vom
  Daten-Souveränitäts-Konzept.
- **A2A-Spec** — Industrie-Standard für Agent-zu-Agent-Kommunikation,
  Basis unserer Tier-übergreifenden Protokoll-Schicht.

Wer von diesen Bewegungen kommt und in das Mesh-Bild reinpasst, ist
willkommen. Was wir nicht versprechen: dass alles aus diesen Welten
nahtlos kombinierbar ist. Was wir versprechen: dass wir es ehrlich
versuchen werden.
