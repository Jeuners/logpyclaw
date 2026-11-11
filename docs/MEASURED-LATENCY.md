# Gemessene Aktionslatenzen im Betrieb

Die Implementierung ergänzt den [historischen Paper-Abgleich](TIME-DILATION.md).
Sie misst beobachtbare Laufzeiten und gibt Martin optional einen begrenzten
Kontextblock. Die historischen Drachen-Experimente werden dadurch weder
überschrieben noch automatisch repliziert.

## Betrieb

Martin und Alice sind in `agents.yaml` auf `qwen3.5:latest` am lokalen Ollama
eingestellt. Das Modell muss dort vorhanden sein. Beide teilen sich den Server;
gleichzeitige Anfragen können sich verzögern. Der Stand verwendet weiterhin
Martins vorhandenen QC-Loop.

Die Messung ist immer aktiv. Nur ihre Verwendung im Planner ist ein Opt-in:

```dotenv
MARTIN_LATENCY_CONTEXT_ENABLED=true
MARTIN_LATENCY_CONTEXT_MAX_CHARS=2400
LATENCY_MAX_AGE_S=1800
LATENCY_MIN_SAMPLES=3
```

Nach Änderungen an diesen Werten den Server neu starten. `false` entfernt den
Latenzblock aus dem Planner, behält aber die Laufzeitbeobachtung. Im Repository
ist die Funktion standardmäßig ausgeschaltet; für den lokalen Praxistest wurde
sie in der nicht eingecheckten `.env` aktiviert.

Martins Ollama-Planner verlangt JSON und deaktiviert den separaten Thinking-Modus
für diese Routinganfrage. Das vermeidet unstrukturierten Text statt eines Plans.
Ein fehlender gültiger Plan ist jetzt ein Fehler, keine erfolgreiche Antwort.
Die Optionen folgen der [Ollama-Chat-API](https://docs.ollama.com/api/chat).

## Messgrenzen

Alle Dauern stammen von `time.monotonic()`. Wall-Time bleibt für Ereigniszeitpunkte
und bestehende Speicherfelder erhalten. Auch die CDC-Protokollrate verwendet
jetzt monotone Zeitabstände; τ bleibt eine Summe von Protokoll-Ticks.

| Feld | Messstrecke |
| --- | --- |
| `duration_s` in der Antwort von `start_mission` | Vom Missionsstart über den Dispatcher einschließlich Antwortspeicherung bis vor der abschließenden Aktualisierung der Missionsmetadaten. Nicht die HTTP-Client-Gesamtdauer. |
| `_timing.total_s` | Eintritt in den Dispatcher bis Ende des Handlers. Die anschließende Antwortsignierung und -speicherung gehört nicht dazu. |
| `_timing.prepare_s` | Vorbereitung im Dispatcher bis zum Handler: unter anderem Request-Signierung und Task-Speicherung. Keine reine Scheduler-Queue-Messung. |
| `_timing.handle_s` | Gesamter Handler-Aufruf, einschließlich darin abgewarteter Unteraufgaben. |
| `_timing.model_request_s` | Beobachtete Modellaufrufe, einschließlich Netzwerk, Serverqueue, Laden und Generierung. Keine isolierte GPU-/CPU-Zeit. |
| `_timing.delegation_wait_s` | Zeit, in der untergeordnete Dispatches laufen; überlappende Intervalle zählen nur einmal. |
| `_timing.tool_request_s` | Beobachtete Skill-Ausführung, einschließlich ihrer internen Wartezeiten. |
| `_timing.other_handle_s` | Handlerzeit außerhalb der erfassten Modell-, Tool- und Delegationsintervalle; auch hier können uninstrumentierte Wartezeiten enthalten sein. |

Überlappende Kategorien sind nicht pauschal addierbar. Ein Kind-Aufruf besitzt
einen eigenen ContextVar-Kontext: Seine Modellzeit ist nicht zugleich Modellzeit
des Elternagenten. Die HTTP-SSE-Oberfläche erhält `_timing` in den aufgezeichneten
Nachrichten; das zusätzliche `duration_s` gehört zum `start_mission`-Pfad
(`/api/chat`, `/api/missions`), nicht zum separaten SSE-Missionsstarter.

## Messreihen und Unsicherheit

- Pro Agent maximal 64 abgeschlossene Versuche im Arbeitsspeicher. Ein Neustart
  leert diese Statistik; aufgezeichnete Nachrichten bleiben in Missions-Traces.
- Modell, Provider, Backendadresse und relevante Modell-/Skill-Konfiguration
  bilden eine Konfigurationsepoche. Änderungen verwerfen die bisherigen
  Messproben. Backendadresse und Konfiguration werden nur als Hash-IDs ausgegeben.
- Ein optionales `model_revision`-Attribut fließt in die Identität ein. Es gibt
  noch keine automatische Digest-Abfrage bei Ollama: Wird ein Tag wie `latest`
  auf demselben Server im laufenden Prozess ersetzt, den Agenten neu registrieren
  oder den Server neu starten. Ein unveränderter Tag ist kein Modell-Digest.
- Nur Antworten mit RESPONSE und ohne gescheitertes QC zählen als erfolgreiche
  Proben. Exceptions, Timeouts und Abbrüche werden als Fehlversuche erfasst.
  Skills, die einen Fehler nur in einem normalen Ergebnistext ausdrücken, bleiben
  durch diesen allgemeinen Mechanismus nicht als Fehler erkennbar.
- Der Default verlangt drei frische Erfolge innerhalb von 30 Minuten. Vorher
  gilt die Schätzung als unbekannt oder unzureichend; fehlende Werte sind `null`.
- Median, empirisches p90 und Min/Max beschreiben die gespeicherten
  Dispatch-Dauern. Sie sind keine Konfidenzintervalle oder Deadline-Garantien.
  Verschiedene Aufgaben, Promptgrößen, Warm-/Kaltstarts und konkurrierende Last
  sind noch nicht in getrennte Klassen aufgeteilt.

## Routing und Nachvollziehbarkeit

Der Latenzblock enthält zunächst Agenten mit ausreichend frischen Messwerten,
danach unbekannte, soweit das Zeichenbudget reicht. Er sortiert die Agenten
nicht nach Geschwindigkeit. Fachliche Eignung, Routing-Regeln und ausdrückliche
Zielvorgaben gehen vor. `@agent:alice` umgeht den Planner weiterhin vollständig.

Die neue JSON-Antwortinformation ist an vorhandenen Endpunkten verfügbar:

- `GET /api/agents` und `GET /api/agents/{agent_id}`: Feld `latency` mit
  Status, Identität, Stichprobenzahl, Messalter, Fehlerzahl, Median, p90 und Spanne.
- `GET /api/missions/{mission_id}/trace`: `payload._timing` je normal
  aufgezeichneter Handler-Antwort. Timeout-/Exception-Frühpfade erscheinen in den
  Fehlversuchszählern, haben aber nicht alle eine eigene gespeicherte Antwort.
- `payload._timing.routing`: Modus `explicit`, `planner`, `self` oder
  `planner_error`; tatsächlich bereitgestellte Kandidaten und ausgewählte IDs.
  `model_rationale` ist eine optionale, gekürzte Begründung des Modells.

`latency_context_supplied=true` beweist, dass der Block vorlag. Es beweist nicht,
dass er die Entscheidung verursacht hat. Dafür bleibt der kontrollierte
Kontextform-Vergleich des Papers erforderlich.

Die neuen Timing-Daten werden im Antwort-Payload vor dessen Signierung ergänzt.
Weitergereichte signierte Kind-Antworten werden für den Eltern-Task neu
eingehüllt; die ursprüngliche Nachricht wird nicht verändert. Die historischen
CDC-Signaturfelder bleiben unverändert, insbesondere wird τ weiterhin nicht
direkt signiert.

## Verifikation

Siehe [TDD- und Betriebsnachweise](testing/measured-latency.tdd.md). Die
Unit-/Integrationstests verwenden keine echten Modell- oder Skill-Netzwerkaufrufe.
Live-Tests gegen den lokalen Server sind separat dokumentiert.
