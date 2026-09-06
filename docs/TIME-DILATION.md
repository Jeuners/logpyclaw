# Time Is Not Metadata — Implementierungsabgleich

**Historischer Ausgangsstand:** Der folgende Abgleich beschreibt den unten
gepinnten Commit vor der Umsetzung. Die inzwischen ergänzte monotone
Laufzeitmessung und der optionale Planner-Kontext sind in
[MEASURED-LATENCY.md](MEASURED-LATENCY.md) dokumentiert. Die dort beschriebenen
Erweiterungen ändern die historische Auswertung nicht.

Stand: 2026-09-06. Geprüfte Codebasis: `da935366521a00c25ae940ea0581ac9800064782`.
Zugehöriges [Paper](https://github.com/Jeuners/Time_Dilation_in_LLM_Agent_Systems),
geprüfte Ausgangsfassung: `ca45b8946ff0d6933afba8bb34c0b49610c333ed`.
Dieser Abgleich trennt den vorhandenen Code von historischen Messungen und
Forschungsvorschlägen. Er ergänzt keine neuen LLM-Versuche.

## Was die Implementierung misst

| Größe | Feld / Quelle | Bedeutung und Grenze |
| --- | --- | --- |
| Kausalordnung V | `clock.vector` | Logische Ticks je Agent; kein Tokenzähler. |
| Eigenzeit τ | `clock.tau` | Summe der Tick-Gewichte; der normale Agentenpfad verwendet Gewicht 1. Ein wartender Agent ohne neue Ticks sammelt keine Eigenzeit. |
| Rate π | `clock.dilation` | EWMA von `1 / dt` zwischen Aufrufen von `advance_clock()`, mit α = 0,3. Enthält gegebenenfalls Leerlauf und ist keine isolierte Inferenzgeschwindigkeit. |
| Streuung | `rate_stats` | EWMA der absoluten Abweichung von der aktualisierten Rate; `cv = dev / rate`. Keine Standardabweichung, kein Konfidenzintervall und kein Latenzquantil. |
| Beobachtungszeit | `clock.wall_ts` | Wird beim Serialisieren neu erzeugt; kein stabiler Zeitpunkt des ursprünglichen Ereignisses. |
| Ereigniszeit | `Message.timestamp` | Separates Nachrichtenfeld; Teil des Signatur-Payloads. |

Quellen: [CDC](../backend/core/cdc.py), [Agentenbasis](../backend/agents/base.py),
[Nachrichten](../backend/core/protocol.py).

`LLMAgent.handle()` tickt vor dem Modellaufruf. τ misst damit im aktuellen
Instrument Protokollereignisse, nicht abgeschlossene interne Denkschritte,
Antwortqualität oder Bewusstsein. Die Rate verwendet derzeit `time.time()`;
ein injizierter oder monotoner Zeitgeber ist nicht implementiert.

## Merge, Klassifikation und Signaturen

V und τ werden komponentenweise per Maximum zusammengeführt. Für π gewinnt
die Beobachtung mit dem höheren V-Eintrag desselben Agenten; bei gleichem V
entscheidet das Maximum der Rate. Der Vergleich erfolgt nicht über `wall_ts`.

`relate()` vergleicht die τ-Einträge **desselben** Agenten in zwei Clocks.
Sein Parameter `gamma` bleibt aus Kompatibilität erhalten, wird aber ignoriert.
Die implementierten Fälle sind:

| V-Verhältnis | τ-Verhältnis, innerhalb der Toleranz | Rückgabe |
| --- | --- | --- |
| Strikt geordnet, in beliebiger Richtung | Gleiche Richtung | `ORDERED` |
| Strikt geordnet | Widerspricht der V-Richtung | `CAUSAL_DRIFT` |
| Gleich | Gleich | `ORDERED` |
| Gleich | Verschieden | `INCONSISTENT` |
| Nebenläufig | Gleich | `ORDERED` |
| Nebenläufig | Verschieden | `CONCURRENT_DRIFT` |

Damit ist das Label `ORDERED` nicht in jedem Fall ein Beweis für happened-before.
Unterschiedliche Raten allein lösen keinen dieser Drift-Fälle aus. Die
Fraktionsschicht reklassifiziert Drift anhand eines separat gelernten
Ratenverhältnisses `π_source / π_target`. Dieses γ ist nicht die im Paper
skizzierte Umrechnung `cost_source / cost_target` von Operationszahlen.

Die Signatur bindet aus der Clock nur `vector` und `dilation`, nicht `tau`
oder `wall_ts`. Eine gültige Hash-Kette authentifiziert daher nicht direkt die
gespeicherten τ-Werte. Eine Erweiterung braucht ein versioniertes Format mit
Verifikation alter Nachrichten; ein zusätzliches Feld im bestehenden Payload
würde historische Signaturen brechen.

## Implementierungsstatus

| Bestandteil | Beleg / Status |
| --- | --- |
| CDC auf Nachrichten, τ, EWMA, Streuungszusammenfassung | Implementiert; `test_cdc.py`, `test_agents.py`. |
| Zentraler Dispatcher, parallele Plan-Wellen | `conductor.py`, `martin.py`; keine dezentrale Netzwerktopologie. |
| Agenten-initiierte Missionen | `Conductor.initiate()` und `InitiativeService`; konfigurierter Verkehr durch denselben Conductor. |
| Externer A2A-Eingang | JSON unter `/a2a/tasks/send`; kein XML-Tasklisten-Protokoll. |
| Semantisches Gedächtnis | `SemanticMemory`, SQLite / sqlite-vec; kein Qdrant in diesem Stand. |
| Periodische Dienste | Initiative-Schleifen, RSS und tägliche Traum-Bildgenerierung. Traumdienst konsolidiert keine Erinnerungen. |
| `TimeProvider`, `reference_now`, `parent_reference_now` | In `backend/` und `tests/` nicht vorhanden. |
| Einheitliches Logging-Tupel aus Wall-Time und τ | Nicht vorhanden; strukturierte Missionsnachrichten und Text-Logging unterscheiden sich. |
| Re-Synchronisation nach Drift je Aktionstyp | Nicht als allgemeine Policy implementiert. Fraktions-Bridges sind kein Ersatz dafür. |
| Rate / Streuung automatisch im Routing-Prompt | Hilfsmethoden vorhanden; kein allgemeiner Anschluss dieser Daten an Martins Planner. |
| Explizites Budget für temporalen Promptinhalt | Forschungsvorschlag; kein globaler Filter oder Budgetmechanismus. |

Der normale LLM-Aufruf erhält Nachrichteninhalt und Persona, nicht automatisch
die komplette CDC. Martins Memory-Recall fügt Treffertexte ein, nicht pauschal
alle Zeitfelder. Im Text enthaltene Datumsangaben können dennoch in den Prompt
gelangen. Missionsspeicher und Vektorgedächtnis sind unterschiedliche Komponenten;
eine automatische nächtliche Übernahme von CDC-Logs ins Gedächtnis ist hier nicht
belegt. Das im Paper diskutierte Interpretationsrisiko ist eine offene Hypothese,
kein in dieser Prüfung nachgewiesener Fehler des laufenden Systems.

## Experiment und Reproduktion

Siehe [Experimentübersicht](../experiments/README.md). `dragon5.py` gibt dem
Entscheider rollierende Mediane der gemessenen Aktionslatenz. Die mitgespeicherten
CDC-Raten sind Beobachtungen; sie erzeugen nicht diese Promptwerte. Das Ergebnis
prüft den Nutzen exklusiver Latenzinformation, nicht isoliert den Nutzen von τ,
dem CDC-Wire-Format oder automatischem CDC-basiertem Routing.

Die aktuelle lokale Modellkonfiguration ist keine Rekonstruktion der historischen
Versuchsbedingungen. Insbesondere darf ein Wechsel von Alice auf ein anderes
Modell nicht stillschweigend als Replikation mit demselben Entscheider gelten.

## Nächste klar abgrenzbare Ergänzungen

1. Für Latenzmessungen einen monotonen Zeitgeber und eine explizite
   Messstrecke vorsehen. Protokollrate, Modelllaufzeit, Wartezeit und gesamte
   Aufgabendauer getrennt ausweisen; Leerlauf nicht als reine Modellgeschwindigkeit
   interpretieren.
2. Einen optionalen, begrenzten Block mit gemessenen Aktionslatenzen für den
   Planner entwerfen. Stichprobenzahl, Modell/Backend, Messalter und Unsicherheit
   angeben. Fehlende Messungen als unbekannt behandeln.
3. Das Kontextform-Experiment aus dem Paper durchführen: keine Zeitinformation,
   ein strukturierter Block und verstreute gleichwertige Angaben. Inhalt,
   Aufgaben und Backend-Zuordnung kontrollieren; Entscheidungsgüte,
   Entscheidungsdauer und gesamte Dauer getrennt messen. Dichte und Tokenlänge
   als mögliche Störfaktoren dokumentieren.
4. Für τ-Authentizität ein versioniertes Signaturschema planen, bevor τ als
   kryptographisch gesicherte Provenienz beworben wird.

Diese Punkte sind Arbeitsvorschläge, keine bereits implementierten Funktionen
oder bestätigten experimentellen Ergebnisse.

## Prüfung am 2026-09-06

`python -m pytest tests/test_cdc.py tests/test_agents.py tests/test_faction_protocol.py tests/test_protocol.py -q`

Ergebnis: **124 bestanden**. Der Lauf prüft die vorhandene Implementierung;
er ist keine Replikation der LLM-Experimente und validiert keine nicht
implementierten Paper-Komponenten.
