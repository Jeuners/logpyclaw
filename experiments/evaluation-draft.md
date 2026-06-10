# Draft: §5 Preliminary Evaluation (für das Time-Dilation-Paper)

*Entwurf, wird nach Abschluss von Lauf v4 finalisiert. Sprache: Englisch fürs Paper.*

---

## 5. Preliminary Evaluation

The framework was implemented in LogpyClaw v3 (see Reference Implementation
above). We report four findings from operating the system: one metric
degeneration observed in production traces, one direct measurement of proper-
time divergence, one honest negative result, and a controlled pilot experiment
on deadline-driven delegation. All data comes from the system's signed mission
log (PQC hash chain, ML-DSA-65), which preserved 464 missions and 1,719
inter-agent messages at the time of analysis. Most missions were development
and test traffic; we state this openly and treat the corpus accordingly.

### 5.1 A naive rate metric degenerates in practice

The first implementation approximated each agent's pace as a lifetime average
(operations completed divided by agent uptime). Across 1,697 legacy messages
this metric collapsed: median recorded rates of 0.001–0.003 ops/s for all
agents, with idle agents drifting asymptotically toward zero. Apparent
"dilation spreads" of five orders of magnitude between agents turned out to be
artifacts of the metric, not properties of the system. This is direct
empirical support for the paper's separation of concerns: cumulative proper
time τ (monotonic, merge by max) and instantaneous pace (EWMA over recent
operations, merge by causal recency) must be tracked as distinct quantities.
A single number conflating them measures uptime, not experience.

### 5.2 Proper-time divergence is real and measurable

After the τ/rate separation went live, real missions immediately exhibited the
phenomenon the paper predicts. Three orchestration missions routing work from
a coordinator (Groq-served Llama) to a slow worker (Claude Opus via CLI):

| Mission | Wall time | τ coordinator | τ worker | Ratio |
|---|---|---|---|---|
| mis_274e87fe | 384.5 s | 6.0 | 1.0 | 6.0× |
| mis_d18a03bc | 144.1 s | 10.0 | 3.0 | 3.3× |
| mis_4783a34e | 600.0 s | 4.0 | 2.0 | 2.0× |

Identical wall-clock windows, divergence of lived time up to 6×. Caveat:
τ here counts protocol-level operations (dispatch, handle, delegation ticks),
not LLM reasoning steps; the granularity is coarser than the ideal of §3.2.

### 5.3 An honest negative result

All 849 classifiable request/response pairs in the corpus relate as ORDERED;
no CAUSAL_DRIFT or INCONSISTENT was observed. This is expected, not
disconfirming: sequential dispatch produces causal order by construction. The
interesting relations (CONCURRENT_DRIFT, and the faction-aware EXPECTED_DRIFT
/ FACTION_RACE reclassifications) require genuinely parallel branches, which
the orchestrator only recently gained. The classifier, in other words, has
not yet met the traffic it was built for. We flag this as the primary gap
between implementation and validation.

### 5.4 Pilot: temporal self-knowledge improves deadline decisions

To test whether proper-time awareness changes *decisions* (not just logs), we
built a real-time delegation scenario on the live system. A slow agent (the
"knight", a local Ollama model) must save a player from a dragon arriving in
T real seconds, choosing between acting itself (two of its own actions) or
delegating to a fast agent (the "mage", Groq-served; one knight action to
call, one mage action to cast, sometimes plus an announced exhaustion
cooldown). The chosen option is then actually executed against the wall
clock; survival means finishing before T. Half the trials inject the measured
per-action times of both agents into the decision prompt ("temporal
self-knowledge"); the other half receive an otherwise identical prompt.

Pilot results (v3, n=20, real latencies):

- Survival: 5/10 with temporal context vs. 3/10 without.
- Decision quality against a post-hoc oracle (computed from observed true
  costs): 7/7 winnable trials decided correctly with context, 3/5 without.
- The only two trials lost *through a wrong choice* both occurred in the
  no-context arm: the knight ran itself (true cost 13.7 s) although the
  delegation path (≈1 s + cooldown) would have survived.
- Without context the knight chose ~50/50 (guessing); with context it
  computed.

Two methodological findings from the pilot matter beyond the numbers. First,
our injected "time sense" was itself miscalibrated by a factor of ~9 (one-shot
measurement with short prompts vs. real action costs) — and still helped,
because the decision requires only the *ordinal* fact that the mage is faster.
Second, the 9× drift of a static self-estimate is precisely the failure mode
§3 predicts for any one-shot calibration, and motivates continuously updated
proper-time rates (EWMA), which the production clock implements.

### 5.5 Scaled run

[PLATZHALTER — v4-Ergebnisse: n=60, rollierende per-Aktion-Mediane als
Zeitgefühl, Deadlines aus beobachteten Kosten, Live-CDC-Raten pro Trial
mitgeloggt. Tabelle + Signifikanz (Fisher exact) hier einfügen.]

### 5.6 Threats to validity

Single machine, single operator, mostly test traffic; the game scenario is
synthetic even though latencies are real; per-action τ granularity is coarse;
n remains small for strong claims. We consider the evaluation preliminary by
design: its purpose is to demonstrate that the framework's claims are
*testable on a running system*, and to report the first such tests honestly.
