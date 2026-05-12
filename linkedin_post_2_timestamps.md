**Stop putting timestamps in your LLM prompts.**

I just removed every `[Current time: 2026-05-12T14:23:17Z]` line from my agent system. The result was cleaner thinking and shorter prompts. Here's why.

---

**The dirty secret about timestamps in prompts:**

LLMs cannot compute time.

They have no wall clock. They cannot calculate "how long ago was 14:23 if it's now 15:08." When you put a timestamp into the context, the model does not perform temporal reasoning — it pattern-matches against training data and *makes up* an interpretation.

What you actually get from `created_at: 2025-11-30T02:14:00Z`:

- The model invents narratives ("the user waited a long time before replying")
- It guesses staleness from training-data priors ("November 2025 sounds recent")
- It compares ISO strings as sequences ("this is before that") — sometimes right, often for wrong reasons
- It allocates attention to digits and `T` and `Z` characters that carry no signal

You pay ~25 tokens per timestamp. Multiply by every message in a long conversation. That's pure cost — and pure noise.

---

**What actually matters in multi-agent systems is not wall time. It is freshness relative to the receiver.**

If agent A finishes thinking at t=0 and agent B receives the answer at t=20, the relevant question is not "what is the ISO timestamp." The relevant question is:

> How much has the world moved while this message was in flight, relative to my own perception of time?

That is what a Causal-Dilation Clock gives you. Two values per agent:

- **V** — vector clock (causal order)
- **D** — per-agent ops-rate (subjective time speed)

The receiver sees a single phrase like `analyst:dilated(ez=44,rate=0.1)` and instantly knows: this answer comes from a slow frame, my live monitor is 50x faster, I should refresh before acting.

A categorical label. Not a number to interpret. LLMs are good at labels. They are bad at clocks.

---

**The swap:**

| Old: timestamps in prompts | New: CDC summary in prompts |
|---|---|
| `created_at: 2026-05-12T14:23:17Z` | `monitor:fast(ez=42,rate=5.0)` |
| 25 tokens of noise | 30 tokens of decision-relevant signal |
| Frame of the logger | Frame of the receiver |
| LLM invents temporal narratives | LLM reads a label |
| No causality | True causal ordering |

You still keep wall time at **system boundaries**: when you log for humans, when you call an external API that demands RFC 3339, when you audit. But inside the agent-to-agent path, where the consumer is a language model, wall time is pure overhead.

---

**The result in my system:**

I removed the `[Current time: ...]` injection from every worker agent's system prompt. Only agents that explicitly opt in (because they actually schedule calendar events) still see it.

Multi-agent freshness now flows through the CDC clock attached to every message. Agents categorize their own data as fresh or dilated and act accordingly. No hallucinated time math. No wasted tokens.

The pitch in one sentence:
> We swapped 25-token strings the model overinterprets for 60-character labels the model categorically understands. Fewer tokens, fewer hallucinations, more reliable multi-agent decisions.

Paper: github.com/Jeuners/Time_Dilation_in_LLM_Agent_Systems

#LLM #MultiAgent #PromptEngineering #AIArchitecture
