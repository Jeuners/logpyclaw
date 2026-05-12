# Phase 6 — Echte Eigenzeit-Semantik aktivieren

**Status:** offen, geplant für Q3 2026.
**Bezug:** Dillenberg, *Time Dilation in LLM Agent Systems*, §3.2 + §3.4 + §4.3.

## Kernproblem

Phase 1–5 hat das **Substrat** gebaut, aber den **kritischen Switch nicht angeworfen**:
`WallClockProvider.now()` liefert `datetime.now()`. `reference_now == wall_now` — immer.
Damit ist die ganze Eigenzeit-Schicht aktuell semantisch isomorph zur Wall-Clock.

Mechanismen mit echter Execution-Semantics-Wirkung jetzt:
- ✅ Drift-Reject vor Side-Effect-Skill (`core/temporal_policy.py`)
- ✅ JIT-Refinement im LTX-Batch (`api/ltx_batch.py:_refine_prompt_for_segment`)
- ✅ Auto-Switch `image_mode` bei kaputtem Last-Frame

Aber: alle drei wären auch ohne Eigenzeit-Vokabular baubar (Wall-Clock-TTL + Pipeline-Stage).
Solange `now() == wall_now()`, ist γ kosmetisch.

## Drei konkrete Schritte für Fall B (echte Time-Dilation-Semantics)

### Schritt 1 — `now()` τ-basiert machen

**Datei:** `core/time_provider.py`
**Was:** `WallClockProvider.now()` muss aus τ ableiten, nicht aus `datetime.now()`.

Ansatz:
- Bei `__init__`: `self._epoch = datetime.now()` als Frame-Anker
- `now() = self._epoch + timedelta(seconds=self._tau / γ_reference_rate)`
- `γ_reference_rate` = Operations-pro-Sekunde im Orchestrator-Frame
- Damit: zwei Agents mit unterschiedlichem γ, gleicher Wall-Zeit → unterschiedliche `reference_now`

**Bricht:** jede Stelle die `reference_now` mit `datetime.now()` vergleicht. Migration nötig
(Audit hat ~50 Aufrufstellen — siehe Phase 0 Audit aus initialer Planung).

### Schritt 2 — CDC im Reasoning-Pfad ticken

**Dateien:** `core/llm.py`, `core/llm_stream.py`, `core/skills_registry.py`,
`services/chat_service.py`

**Was:** Bei jedem realen Reasoning-Schritt `cdc.tick(agent_id, weight)` aufrufen.

Tick-Punkte:
- Pro LLM-Call: `weight = output_tokens × cost_factor(model)`
- Pro Tool-Call: `weight = tool_cost_estimate`
- Pro Memory-Lookup: `weight = embedding_cost`

Aktuell existiert `CausalDilationClock` als Datenstruktur in `core/causal_dilation_clock.py`,
wird aber von keinem Code-Pfad getickt. Singleton im ServiceContainer plus Tick-Hooks.

### Schritt 3 — Drift gegen Frame-Transformation messen

**Datei:** `core/temporal_policy.py:evaluate_drift`

**Was:** Aktuell:
```python
d = abs(wall_clock - reference_now)
```
Soll werden:
```python
# γ_target = γ des Empfänger-Frames
# γ_source = γ des Quell-Frames (im Task gespeichert)
projected_target_now = transform(reference_now, γ_source, γ_target)
d = abs(target_now - projected_target_now)
```

Bricht den Test `test_evaluate_drift_above_threshold_rejects_for_side_effect` —
muss neu kalibriert werden, weil γ jetzt skaliert statt nur als Tag mitläuft.

## Risiko-Notiz

Schritt 1 ist die Bombe — **jeder** Code-Pfad der `datetime.now()` als Reference nimmt
muss revidiert werden. Backups/Logging/UI-Format sind unkritisch (bleiben Wall-Clock).
Aber Heartbeat-Scheduling, Task-Timeouts, Watchdog-Intervalle — alles wo „in 5 Minuten"
wirklich Wall-Clock heißen muss — braucht eine bewusste Trennung zwischen `wall_now()`
(neu für solche Stellen Pflicht) und `now()` (Eigenzeit, neu).

## Veralten?

Die drei Schritte sind orthogonal zur Phase-1–5-Architektur. Wenn AgentClaw bis dahin
weiter wächst, bleiben die Schritte gleich — nur die Migrations-Audit-Liste wird länger.
Das Paper bleibt gültig. Was zu altern droht: die γ-Heuristik in `_DILATION_BY_PROVIDER`
(Modelle ändern sich, Provider auch). Sollte sowieso aus Telemetrie gelernt werden statt
hardcoded — das ist Phase 7.

## Akzeptanzkriterium für Phase 6 Done

Ein einziger Test, der Fall B von Fall A unterscheidet:

```python
def test_eigenzeit_actually_dilates():
    fast = WallClockProvider(agent_id="local", dilation_factor=0.5)
    slow = WallClockProvider(agent_id="frontier", dilation_factor=4.0)
    # Beide ticken 10 Reasoning-Schritte
    for _ in range(10):
        fast.tick()
        slow.tick()
    # Eigenzeit muss auseinanderdriften, obwohl Wall-Zeit identisch
    assert fast.now() != slow.now()
    # γ muss in der Differenz erkennbar sein
    delta = (slow.now() - fast.now()).total_seconds()
    assert abs(delta) > 0.0  # in Phase 5 wäre delta == 0
```

In Phase 5 schlägt dieser Test fehl. In Phase 6 muss er grün sein. Das ist der Fall-A→B-Test.
