# Umsetzung 1–3: TDD- und Betriebsnachweise

Datum: 2026-09-06. Ausgangscode: `da93536`.
Die User Journeys stammen aus dem gemeinsam vereinbarten Umsetzungsplan,
nicht aus einem externen Plan-Skript.

## Ziele und Ergebnis

1. Martin und Alice verwenden lokal das vorhandene `qwen3.5:latest`.
   Direkte Antworten und Delegation wurden mit echten Modellaufrufen geprüft.
   Frühere lokale Arbeiten bleiben auf Backup-Branch und im Stash erhalten;
   die Auswahl gegenüber neuen Upstream-Änderungen ist in der README beschrieben.
2. Monotone Aktionsmessung trennt Vorbereitung, Handler, Modellaufruf, Toolaufruf
   und Delegationswartezeit. Pro Agent begrenzte Messreihen werden bei Änderungen
   von Modell/Backend/Konfiguration verworfen. Die CDC-Protokollrate verwendet
   ebenfalls monotone Zeitabstände.
3. Der abschaltbare Latenzblock liefert Martin begrenzte, explizite Beobachtungen
   und speichert, welche Werte vorlagen und welches Ziel gewählt wurde.
   Ausdrückliche Zielvorgaben umgehen den Planner weiterhin.

## RED/GREEN-Verlauf

Die Checkpoint-Commits liegen auf `feature/measured-latency-routing` und bleiben
für den Review erhalten. Bei einem Squash diesen Nachweis mit übernehmen.

| Verhalten | RED-Beleg | GREEN-Beleg |
| --- | --- | --- |
| Neue Messkomponente | `3907863`: Importfehler wegen fehlendem `backend.core.timing`; beabsichtigtes fehlendes Modul. | `f7250c8`: 7 Messvertrags-Tests bestanden. |
| Dispatcher, monotone CDC und Planner | `1bc2bf3`: 7 Fehler, 1 bestanden; Timing-Felder und Schalter fehlten, Wall-Time-Sprung änderte die Rate. | `60e0517`: 88 fokussierte Tests bestanden; bestehende Zeit-Mocks auf monotone Uhr umgestellt. |
| Signierte Kindfehler, Missiondauer, Konfigurationswechsel | `6fef0ae`: 4 Fehler, 16 bestanden. | `7a6caa9`: 20 bestanden, inklusive echter Hash-Ketten-Verifikation der Testmission. |
| Strukturierter Planner und Fehlerstatus | `119cc7c`: 3 Fehler, 8 bestanden; JSON/Thinking-Optionen fehlten und leeres Planning zählte als Erfolg. | `82afd26`: 46 fokussierte Tests bestanden. |
| Vorhandene Evidenz im Zeichenbudget | `d605693`: 1 Fehler, 22 bestanden; unbekannte Agenten verdrängten vorhandene Messwerte. | `2f1cd2e`: vollständige Suite 279 bestanden, Timing-Abdeckung 98 %. |
| Fehlerstatus im Browser-SSE | `e8c590f`: 1 Fehler, 12 bestanden; Mission trotz Planfehler als completed geführt. | `fc0c255`: vollständige Suite nach Korrektur: 280 bestanden, Timing-Abdeckung 98 %. |

## Garantien der Tests

| Garantie | Testbeleg |
| --- | --- |
| Leerlauf zählt nicht zur nächsten Aktionsdauer | `test_duration_segments_and_no_idle_pollution` |
| Modell-/Endpoint-Wechsel invalidiert alte Messwerte | `test_model_and_endpoint_changes_reset_evidence_and_hide_credentials` |
| Zu wenige, alte oder fehlende Messwerte werden nicht als schnell behandelt | `test_stale_insufficient_failures_and_bounded_window` |
| Fehlerantwort, fehlgeschlagenes QC, Timeout und Abbruch ergeben keine Erfolgsprobe | `test_error_responses_and_qc_failure_are_not_success_samples`, `test_dispatch_timeout_records_failed_attempt_without_fast_sample`, `test_cancellation_is_recorded_and_context_is_restored` |
| Gleichzeitige Aufrufe teilen keine Modellspanne | `test_simultaneous_calls_keep_separate_model_spans` |
| Geschachtelte Warteintervalle werden nicht doppelt gezählt | `test_nested_calls_isolate_model_time_and_union_parallel_waits` |
| Wall-Time-Sprünge verändern die Protokollrate nicht | `test_cdc_rate_ignores_wall_clock_jumps` |
| Der echte Planner-Code erhält den Block nur bei eingeschaltetem Feature | `test_planner_receives_optional_context_and_audits_decision` mit beiden Schalterwerten und simuliertem HTTP-Provider |
| Explizite Zielwahl umgeht den Planner | `test_explicit_target_bypasses_planner` |
| Signierte Kindnachricht bleibt unverändert | `test_signed_child_error_is_not_mutated_when_returned_by_parent` |
| API zeigt unbekannte Latenz als null | `test_agent_api_exposes_latency_with_unknown_not_zero` |
| Browser-Stream speichert fehlgeschlagene Mission als failed | `test_browser_stream_records_failed_planning_as_failed_mission` |

## Abschließende Prüfung

```bash
python -m pytest tests/ -q --cov=backend.core.timing --cov-report=term-missing
```

Ergebnis: **280 passed**, `backend/core/timing.py`: **98 %**, 131 ausführbare
Statements, zwei nicht ausgeführte Guard-Zeilen. Diese Abdeckung gilt für das
neue Kernmodul, nicht pauschal für die gesamte Anwendung. Ruff-Prüfung aller
geänderten Python-Produktionsdateien und der neuen Testdateien: **All checks
passed**. `git diff --check` ohne Fehler.

Die 24 neuen Testfälle sind Netzwerk-unabhängig. Das App-Testfixture verwendet
In-Memory-Missionsspeicherung und ersetzt das semantische Gedächtnis, damit
Integrationstests nicht die produktive Memory-Datenbank öffnen.

## Live-Prüfung auf diesem Mac

Lokales Modell: `qwen3.5:latest`, beobachteter Ollama-Digest-Präfix `6488c96fa5fa`.
Der Windows-Modelldownload wurde nicht vorausgesetzt; Alice verwendet nun den Mac.

| Prüfung | Beobachtung |
| --- | --- |
| Alice direkt | „Bereit“, erfolgreiche Mission `mis_a3fdf6c8`. |
| Martin vor dem Planner-Fix | „No plan found“ trotz Erfolgsstatus; dieser Befund motivierte den zusätzlichen RED/GREEN-Zyklus. |
| Martin nach dem Fix | „Ja, ich bin bereit! Wie kann ich dir heute helfen?“, `mis_b0e7354b`, Routingmodus `self`, Latenzblock vorhanden. |
| Explizite Delegation Martin → Alice | „Bereit“, `mis_f68a3fb6`, Modus `explicit`, ca. 81,9 s einschließlich Delegation/QC. |
| Drei Alice-Proben | `mis_622b9095`, `mis_bf60cba2`, `mis_2ba3a763`: jeweils „Bereit“. Registry: n=3, ready, Median ca. 15,524 s, Spanne ca. 9,583–18,600 s. |

Das sind Funktionstests, keine statistische Evaluation oder behauptete
Geschwindigkeitsverbesserung. Ein Neustart leert die prozesslokale Registry;
die obigen Trace-IDs bleiben als historische Live-Belege erhalten.

## Review und Grenzen

Ein Python-Review-Agent wurde zweimal angefordert, konnte aber wegen eines
internen Thread-Store-Zugriffsfehlers nicht starten. Es gibt daher keinen
unabhängigen Agenten-Review. Die Eigenprüfung führte insbesondere zu den
Signatur-, Fehlerstatus- und Konkurrenztests oben.

Noch nicht enthalten: automatische Modell-Digest-Aktualisierung im laufenden
Prozess, getrennte Stichproben je Aufgabenklasse, inferenzinterne GPU-Zeit,
kalibrierte Deadline-Wahrscheinlichkeiten oder ein neuer kontrollierter
LLM-Versuch. Der [Betriebsvertrag](../MEASURED-LATENCY.md) beschreibt die Grenzen.

Abschließender Browser-Smoke-Test nach dem Neustart: HTTP 200, Oberfläche
„LogpyClaw v3“ mit 29 Agenten; Martin und Alice zeigen `qwen3.5:latest`.
Vorhandene Chatverläufe bleiben erhalten und können historische Planfehler zeigen.
