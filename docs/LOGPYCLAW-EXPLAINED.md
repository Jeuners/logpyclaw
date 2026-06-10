# LogpyClaw v3, Explained Simply

*A plain-language introduction for non-developers. June 2026.*

---

## What is this?

LogpyClaw v3 is a small piece of software that runs a **team of AI assistants** on a single computer. Instead of one AI doing everything, there are many specialists: one writes code, one searches the web, one checks quality, one fetches videos, and so on. A coordinator named **Martin** reads incoming requests and decides which specialist should handle them.

That part is not unusual. Many systems today coordinate teams of AI agents. What makes LogpyClaw different is *how the team members talk to each other*. Three ideas set it apart.

---

## Idea 1: Every message carries a clock

Imagine a group of coworkers in different time zones, some working fast, some slow, some asleep. If they only exchanged notes with no timestamps, you could never reconstruct who knew what, and when.

LogpyClaw solves this by attaching a special clock to **every single message** between agents. The clock is not optional. It records three things:

- **Order**: what each agent had already seen and done when it sent the message. This makes it possible to tell whether one event truly happened "before" another, or whether two things happened independently at the same time.
- **Experienced time (τ, "tau")**: how much work an agent has actually performed, its subjective working time. A fast agent "experiences" more time per real minute than a slow one.
- **Pace**: how fast the agent is working right now, in operations per second.

With these clocks, the system can ask questions that most AI frameworks cannot ask at all, such as: *Is this answer based on up-to-date knowledge, or did the agent drift behind?* The system sorts every pair of messages into four categories, from "perfectly in order" to "inconsistent, something is corrupted." Drift between naturally fast and naturally slow agents is recognized as expected and does not raise an alarm.

The name for this mechanism is the **Causal-Dilation Clock (CDC)**. The word "dilation" is a playful nod to Einstein: like travelers moving at different speeds, agents experience time differently, and the system accounts for it.

## Idea 2: The team has a social structure, not just a job chart

The agents belong to **factions**, persistent groups with their own identity and code of conduct:

- **Operators** route and translate work (Martin lives here).
- **Makers** build things: code, images, text.
- **Auditors** check quality and give scores. They refuse to rush.
- **Gatherers** fetch raw information without interpreting it.
- **Guardians** enforce safety rules.
- **Scribes** keep records.

Crucially, the relationships between factions are **directed and learned**. Auditors are deliberately skeptical of Makers, while Makers cooperate with Auditors. After every interaction, the system updates a trust score based on whether the result was useful, the way a person slowly builds (or loses) confidence in a colleague. If two factions are configured as *adversarial*, they may not talk directly at all: their messages must pass through an Operator bridge that reformulates them. If no bridge is available, the message is refused rather than delivered, a safety choice known as "fail-closed."

There is also a built-in **quality loop**: after a specialist finishes a task, Martin can show the task and the result to an Auditor, who scores it from 1 to 10. If the score is too low, the work is sent back with feedback and redone, up to two times.

## Idea 3: A tamper-evident logbook, secured for the quantum age

Every message is **digitally signed and chained** to the previous one, like pages in a bound logbook where each page references the one before. If anyone alters a single message after the fact, the chain visibly breaks and verification fails.

The signature method (ML-DSA-65, a U.S. federal standard finalized in 2024) is designed to stay secure even against future **quantum computers**. In plain terms: the project keeps a court-grade diary of everything its AI agents say to each other, and that diary is built to remain trustworthy for decades. A logbook with no signatures at all is treated as invalid, never as silently acceptable.

---

## How a request flows through the system

1. You type a request in the web interface (or an external program sends one through a standard "agent-to-agent" gateway, which hides all the internal machinery).
2. **Martin** reads it. If you addressed a specialist explicitly, your exact words go straight to that specialist, untouched. Otherwise Martin's planner breaks the request into steps and assigns them; independent steps run in parallel, with sensible limits so the machine is never overwhelmed.
3. The chosen specialists do the work. Some are local AI models running on the computer itself, some are cloud models, some are simple tools (web search, file access, deployment).
4. An Auditor may score the result, possibly triggering a retry.
5. Everything that happened, every message, clock reading, and score, is signed, chained, and stored.

A real example: in one session, Martin was asked to have the in-house Claude agent build two small retro arcade games. The games were built, tested, fixed, and published to a public web server, with the entire conversation preserved in the signed log.

---

## Why does this matter?

Most AI agent platforms today focus on *scale and convenience*: more tools, more integrations, bigger orchestras. LogpyClaw is a research project pointed at a different question:

> What if causality, trust, and accountability were properties of the **protocol itself**, instead of features bolted on top?

When AI teams act with real-world consequences, three questions become urgent: *Who knew what, and when? Who should be trusted with which task? Can we prove afterwards what actually happened?* LogpyClaw's answer is to bake all three into the messages themselves: the clock answers the first, learned faction trust the second, and the signed chain the third.

The whole system is intentionally small, roughly three thousand lines of core code with over two hundred automated tests, readable in an afternoon. It is one person's working prototype of an idea, not a product: a glimpse of what AI teamwork could look like when time, trust, and truth are built in from the first line.

---

*LogpyClaw v3 runs locally (default: Ollama models), supports Anthropic, OpenAI, Groq and OpenRouter as cloud back-ends, speaks the standard A2A protocol to the outside world, and ships with a single-file web interface that needs no build step.*
