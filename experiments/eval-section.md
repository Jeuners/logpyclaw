## 5. Preliminary Evaluation

The framework is implemented in LogpyClaw v3 (see *Reference Implementation*).
This section reports what happened when the concepts met a running system:
one metric degeneration observed in real traces, one direct measurement of
proper-time divergence, one honest negative result, and a controlled
experiment on deadline-driven delegation — including a replication attempt
that partially failed and taught us more than the pilot did, and a second,
decisive replication that isolated the effect the first two had only hinted
at. All data comes
from the system's signed mission log (ML-DSA-65 hash chain): 464 missions and
1,719 inter-agent messages at the time of analysis, 72% of them signed. Most
of this corpus is development and test traffic; we state that openly and
treat the numbers accordingly. Experiment scripts and raw results are
published alongside the implementation (`experiments/dragon*.py`).

### 5.1 A naive rate metric degenerates in practice

The first implementation approximated each agent's pace as a lifetime average
(operations completed divided by uptime). Across 1,697 legacy messages this
metric collapsed: median recorded rates of 0.001–0.003 ops/s for every agent,
with idle agents drifting asymptotically toward zero. Apparent "dilation
spreads" of five orders of magnitude between agents turned out to be
artifacts of the metric, not properties of the system. This is direct
empirical support for separating the two quantities the framework defines:
cumulative proper time τ (monotonic, merged by max) and instantaneous pace
(an EWMA over recent operations, merged by causal recency). A single number
conflating them measures uptime, not experience.

### 5.2 Proper-time divergence is real and measurable

Once the τ/pace separation went live, ordinary missions immediately exhibited
the phenomenon §1 predicts. Three orchestration missions routing work from a
fast coordinator (Groq-served Llama) to a slow worker (Claude Opus via CLI):

| Mission | Wall time | τ coordinator | τ worker | Ratio |
|---|---|---|---|---|
| `mis_274e87fe` | 384.5 s | 6.0 | 1.0 | 6.0× |
| `mis_d18a03bc` | 144.1 s | 10.0 | 3.0 | 3.3× |
| `mis_4783a34e` | 600.0 s | 4.0 | 2.0 | 2.0× |

Identical wall-clock windows, up to 6× divergence in lived time. Caveat: τ
here counts protocol-level operations (dispatch, handle, delegation ticks),
not LLM reasoning steps; the granularity is coarser than the ideal of §3.2.

### 5.3 An honest negative result

All 849 classifiable request/response pairs in the corpus relate as ORDERED;
no CAUSAL_DRIFT and no INCONSISTENT was observed. This is expected rather
than disconfirming: sequential dispatch produces causal order by
construction. The interesting relations (CONCURRENT_DRIFT and the
faction-aware reclassifications) require genuinely parallel branches, which
the orchestrator only recently gained. The classifier has not yet met the
traffic it was built for. We flag this as the primary gap between
implementation and validation.

### 5.4 Experiment: does temporal self-knowledge change decisions?

To test whether proper-time awareness changes *decisions* rather than just
logs, we built a real-time delegation scenario on the live system. A slow
agent (the "knight", a local Ollama model, ~6–8 s per action) must save a
player from a dragon arriving in T real seconds. It chooses between acting
itself (two of its own actions) or delegating to a fast agent (the "mage",
Groq-served, ~0.4 s per action; one knight action to call, one mage action to
cast, sometimes plus an announced exhaustion cooldown that makes delegation
slower than acting). The chosen option is then *actually executed* against
the wall clock; survival means finishing before T. In half the trials the
decision prompt contains the measured per-action times of both agents
("temporal self-knowledge"); the other half receives an otherwise identical
prompt. The cooldown, when present, is stated in both conditions — only the
*rates* are exclusive to the treatment arm.

**Pilot (n=20).** Survival 5/10 with temporal context vs. 3/10 without.
Against a post-hoc oracle computed from observed true costs, the context arm
decided 7/7 winnable trials correctly, the control arm 3/5. The only two
trials lost *through a wrong choice* both occurred in the control arm. A
methodological by-product: the injected time sense was itself miscalibrated
by ~9× (one-shot measurement with short prompts vs. real action costs) and
still helped — the decision only required the ordinal fact that the mage is
faster. The 9× drift of a static self-estimate is precisely the failure mode
§3 predicts, and motivates continuously updated rates.

**Scaled run (n=60, improved calibration).** With rolling per-action medians
(the EWMA principle at action granularity) and deadlines drawn from observed
costs, the survival effect did **not** replicate: 18/30 with context vs.
21/30 without. Decomposing the trials explains why, and the decomposition is
more instructive than the pilot:

- *Trials without cooldown* (delegation obviously optimal): both arms chose
  delegation in 33/33 trials. The ordinal fact "the mage is faster" was
  inferable from the scenario framing alone; the treatment information was
  never exclusive, so it could not produce a difference.
- *Trials with cooldown* (the arithmetic flips): the context arm switched
  correctly to acting itself in 12/14 trials, the control arm in 8/13 —
  directionally consistent with the pilot, exactly where the information was
  exclusive. (Small samples; we do not claim significance.)
- *Why survival still favored the control arm*: 9 deaths in the context arm
  occurred despite an estimate-correct choice, versus 5 in the control arm.
  The knight's latency is heavy-tailed; deadlines drawn near the decision
  boundary turn correctly chosen self-action into a coin flip on latency
  spikes. The arm that more often correctly chose the expensive option was
  punished more often by execution variance. Survival, as an endpoint,
  measured the latency lottery rather than the decision.

### 5.5 What the experiment taught us

Three design lessons, each of which feeds back into the framework:

1. **Exclusivity.** A time-sense can only show value where temporal facts are
   not inferable from static framing. Future runs must randomize *who* is
   faster, so that one memorized bit cannot substitute for measurement.
2. **Endpoint choice.** Decision correctness, not survival, is the primary
   endpoint a time-sense controls; outcome metrics are confounded by
   execution variance.
3. **Point estimates are not a time sense.** A median is not a Bauchgefühl.
   The variance-driven deaths show that useful temporal self-knowledge must
   carry dispersion, not just central tendency — an agent should know that it
   *usually* makes it in 12 seconds, and how wide "usually" is. This extends
   the framework: the dilation component of the Causal-Dilation Clock should
   eventually track distributional summaries of proper-time rates, not
   scalars.

### 5.6 Decisive replication with randomized roles (n=200)

The two lessons above specify an experiment, and we ran it. Identities are
neutral ("Blue" and "Red"); each trial randomly binds one name to a fast
backend (Groq-served Llama, ~0.5 s per action) and the other to a slow one
(local Ollama gemma, ~3–15 s per action), with both actors given *identical*
action prompts so the latency gap is purely a property of the backend, not the
task. Which actor is faster therefore flips unpredictably between trials and
cannot be guessed from role priors — the exclusivity condition of §5.5(1) made
concrete. A commander (Groq Llama) must dispatch exactly one actor to stop a
dragon arriving in T seconds. The treatment arm's prompt states the measured
per-action time of each actor; the control arm sees only the neutral names,
otherwise identical. The deadline is set to the geometric mean of the two
option costs — far from either boundary — so that execution variance cannot
flip the ground truth (§5.5(2), §5.5(3)). The primary endpoint is decision
correctness against a per-trial oracle (did the commander pick the actor that
actually meets the deadline?); survival is secondary. 100 trials per arm,
strictly alternating; per-action times are live rolling medians.

| Arm | Decision correct | Survival |
|---|---|---|
| Temporal self-knowledge | **100 / 100 (100%)** | 95 / 100 |
| Control (neutral roles) | 55 / 100 (55%) | 57 / 100 |

With the measured time-sense the commander identified the deadline-meeting
actor in every trial; without it, 55/100 — indistinguishable from the 50% a
no-information chooser achieves once the faster actor is randomized (Fisher
exact, two-sided *p* ≈ 9 × 10⁻¹⁷). Survival followed the decisions this time —
95% vs. 57% — because the buffered deadlines removed the latency lottery that
had confounded the n=60 survival endpoint. The contrast with that
non-replicating run is itself the result: the effect appears exactly when, and
only when, the temporal information is *exclusive*. Where "who is faster"
cannot be read off the framing, a continuously measured proper-time rate is
the difference between perfect and chance-level delegation. This is the
clearest evidence we have that the framework's central claim — that a machine
sense of time changes decisions, not just logs — holds on a running system.

### 5.7 Threats to validity

Single machine, single operator, mostly test traffic; the game scenario is
synthetic even though all latencies are real; τ granularity is protocol-level;
sample sizes are small. The evaluation is preliminary by design: its purpose
is to demonstrate that the framework's claims are *testable on a running
system*, and to report the first such tests — including the parts that did
not work — honestly.
