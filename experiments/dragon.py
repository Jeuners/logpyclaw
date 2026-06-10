"""
experiments/dragon.py — Drachen-Experiment: Zeitgefühl unter realer Deadline.

Hypothese (Paper, §Bauchgefühl): Ein Agent, der seine eigene und fremde
Eigenzeit-Rate kennt, trifft unter Zeitdruck bessere Delegations-
entscheidungen als derselbe Agent ohne diesen Kontext.

Szenario pro Trial:
  Ein Drache erreicht den Spieler in T Sekunden (echte Wanduhr).
  Der RITTER (lokales Ollama-Modell, langsam) entscheidet:
    A) selbst hinlaufen und angreifen      -> 2 echte Ritter-LLM-Calls
    B) dem MAGIER (Groq, schnell) zurufen  -> 1 Ritter-Call + 1 Magier-Call
  Die gewählte Option wird REAL ausgeführt (echte Latenzen) und gegen T
  gemessen. Überlebt = Ausführungszeit <= T.

Bedingungen (randomisiert verschränkt):
  cdc=True  -> Entscheidungs-Prompt enthält die GEMESSENEN Raten beider Agenten
  cdc=False -> identischer Prompt ohne die Raten-Zeile

Ergebnisse werden nach jedem Trial nach experiments/dragon-results.json
geschrieben (inkrementell, damit der Fortschritt beobachtbar ist).
"""
from __future__ import annotations

import json
import random
import statistics
import time
from pathlib import Path

import httpx

BASE = "http://localhost:6060"
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "dragon-results.json"
N_TRIALS = 16  # 8 mit / 8 ohne CDC-Kontext
KNIGHT = "agent:ritter"
MAGE = "agent:alice"  # Groq — schnell

random.seed(42)

state: dict = {"status": "starting", "trials": [], "started_at": time.time()}


def save() -> None:
    RESULTS.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def chat(agent_id: str, message: str, timeout: float = 240.0) -> tuple[str, float]:
    t0 = time.time()
    r = httpx.post(
        f"{BASE}/api/chat",
        json={"agent_id": agent_id, "message": message},
        timeout=timeout,
    )
    dt = time.time() - t0
    j = r.json()
    return str(((j.get("result") or {}).get("result")) or ""), dt


def ensure_knight() -> None:
    try:
        httpx.post(
            f"{BASE}/api/agents/spawn",
            json={
                "name": "Ritter",
                "model": "gemma4:e4b",
                "provider": "ollama",
                "soul": (
                    "Du bist ein schwer gepanzerter Ritter in einem Echtzeit-Gefecht. "
                    "Du antwortest extrem knapp."
                ),
                "faction": "makers",
            },
            timeout=30,
        )
    except Exception:
        pass  # existiert vermutlich schon


def measure_rate(agent_id: str, n: int = 2) -> float:
    times = []
    for _ in range(n):
        _, dt = chat(agent_id, "Antworte mit genau einem Wort: bereit")
        times.append(dt)
    return 1.0 / max(statistics.median(times), 0.05)


def decide(t_deadline: float, with_cdc: bool, knight_rate: float, mage_rate: float) -> tuple[str, str, float]:
    ctx = ""
    if with_cdc:
        ctx = (
            f"Dein Zeitgefühl (gemessen): DU schaffst {knight_rate:.2f} Aktionen pro Sekunde, "
            f"der Magier schafft {mage_rate:.2f} Aktionen pro Sekunde.\n"
        )
    prompt = (
        "Echtzeit-Gefecht. Von rechts naht ein Drache und erreicht den Spieler "
        f"in {t_deadline:.0f} Sekunden.\n"
        "Option A: Du läufst selbst hin und greifst an — kostet DICH 2 Aktionen.\n"
        "Option B: Du rufst dem Magier zu, er stunnt den Drachen — kostet DICH 1 Aktion "
        "(Zuruf) und IHN 1 Aktion (Stun).\n"
        + ctx
        + "Was rettet den Spieler? Antworte mit GENAU einem Buchstaben: A oder B."
    )
    text, dt = chat(KNIGHT, prompt)
    up = text.upper()
    # erstes alleinstehendes A/B nehmen
    choice = "A"
    for ch in up:
        if ch in ("A", "B"):
            choice = ch
            break
    return choice, text[:200], dt


def execute(choice: str) -> float:
    """Führt die gewählte Option mit ECHTEN LLM-Calls aus, gibt Dauer zurück."""
    t0 = time.time()
    if choice == "A":
        chat(KNIGHT, "Ein Satz: Du sprintest in voller Rüstung zum Drachen.")
        chat(KNIGHT, "Ein Satz: Du schlägst mit dem Schwert zu.")
    else:
        chat(KNIGHT, "Ein Satz: Du rufst dem Magier zu, den Drachen zu stunnen.")
        chat(MAGE, "Ein Satz: Du wirkst einen Stun-Zauber auf den Drachen.")
    return time.time() - t0


def main() -> None:
    state["status"] = "spawning"
    save()
    ensure_knight()

    state["status"] = "measuring rates"
    save()
    knight_rate = measure_rate(KNIGHT)
    mage_rate = measure_rate(MAGE)
    t_a_est = 2.0 / knight_rate
    t_b_est = 1.0 / knight_rate + 1.0 / mage_rate
    state.update(
        knight_rate=round(knight_rate, 4),
        mage_rate=round(mage_rate, 4),
        t_a_est=round(t_a_est, 2),
        t_b_est=round(t_b_est, 2),
        status="running",
    )
    save()

    # Deadlines so streuen, dass die Entscheidung den Unterschied macht:
    # unterhalb min(tA,tB) ist alles verloren, oberhalb max(tA,tB) alles gewonnen —
    # die interessante Zone liegt dazwischen, plus Ränder für Realismus.
    lo, hi = sorted((t_a_est, t_b_est))
    deadlines = [random.uniform(0.7 * lo, 1.5 * hi) for _ in range(N_TRIALS)]
    conditions = [True, False] * (N_TRIALS // 2)
    random.shuffle(conditions)

    for i, (t_deadline, with_cdc) in enumerate(zip(deadlines, conditions)):
        choice, raw, decision_dt = decide(t_deadline, with_cdc, knight_rate, mage_rate)
        exec_dt = execute(choice)
        survived = exec_dt <= t_deadline
        # Orakel auf Basis der gemessenen Raten: welche Option(en) hätten gereicht?
        a_ok = t_a_est <= t_deadline
        b_ok = t_b_est <= t_deadline
        oracle = "A" if a_ok and (t_a_est <= t_b_est or not b_ok) else ("B" if b_ok else "none")
        state["trials"].append(
            {
                "trial": i + 1,
                "cdc": with_cdc,
                "deadline_s": round(t_deadline, 2),
                "choice": choice,
                "exec_s": round(exec_dt, 2),
                "survived": survived,
                "oracle": oracle,
                "decision_s": round(decision_dt, 2),
                "raw": raw,
            }
        )
        save()

    # Zusammenfassung
    def rate_of(flag: bool) -> dict:
        ts = [t for t in state["trials"] if t["cdc"] is flag]
        winnable = [t for t in ts if t["oracle"] != "none"]
        return {
            "trials": len(ts),
            "survived": sum(t["survived"] for t in ts),
            "survival_rate": round(sum(t["survived"] for t in ts) / max(len(ts), 1), 3),
            "oracle_match": sum(1 for t in winnable if t["choice"] == t["oracle"]),
            "winnable": len(winnable),
        }

    state["summary"] = {"with_cdc": rate_of(True), "without_cdc": rate_of(False)}
    state["status"] = "done"
    state["finished_at"] = time.time()
    save()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # Fehler sichtbar machen statt still sterben
        state["status"] = f"error: {e}"
        save()
        raise
