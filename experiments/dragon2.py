"""
experiments/dragon2.py — Drachen-Experiment v2: echter Tradeoff, warme Messung.

Verbesserungen gegenüber v1:
  1. Warmup vor der Raten-Messung (v1 maß den kalten Modell-Load mit, dadurch
     waren alle Deadlines viel zu großzügig — jeder überlebte).
  2. Echter Tradeoff: In zufälligen Trials hat der Magier einen COOLDOWN
     (reale Wartezeit vor dem Stun). Dann ist mal A (selbst laufen), mal B
     (Magier rufen) die richtige Wahl — die Entscheidung ist nicht mehr trivial.
  3. Der Cooldown steht IM Prompt (beide Bedingungen kennen ihn); nur die
     gemessenen RATEN sind das CDC-Extra. Getestet wird also genau das
     Zeitgefühl, nicht Weltwissen.
  4. Deadlines eng um die Entscheidungsgrenze gestreut.

Ausführung bleibt real: echte LLM-Calls, echte Latenzen, echter sleep für
den Cooldown. Ergebnisse inkrementell in experiments/dragon2-results.json.
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
RESULTS = HERE / "dragon2-results.json"
N_TRIALS = 20  # 10 mit / 10 ohne CDC-Kontext
KNIGHT = "agent:ritter"
MAGE = "agent:alice"

random.seed(7)

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
    """Spawnt den Ritter (in-memory Agent — überlebt Server-Reloads nicht)."""
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
        pass  # existiert schon


def measure_rate(agent_id: str, n: int = 3) -> float:
    """Misst die WARME Rate: erster Call ist Warmup und zählt nicht."""
    chat(agent_id, "Antworte mit genau einem Wort: bereit")
    times = []
    for _ in range(n):
        text, dt = chat(agent_id, "Antworte mit genau einem Wort: bereit")
        if not text.strip():
            raise RuntimeError(f"Leere Antwort von {agent_id} — Agent nicht erreichbar?")
        times.append(dt)
    return 1.0 / max(statistics.median(times), 0.05)


def decide(t_deadline: float, cooldown: float, with_cdc: bool,
           knight_rate: float, mage_rate: float) -> tuple[str, str, float]:
    ctx = ""
    if with_cdc:
        ctx = (
            f"Dein Zeitgefühl (gemessen): DU brauchst pro Aktion etwa "
            f"{1.0 / knight_rate:.1f} Sekunden, der Magier braucht pro Aktion etwa "
            f"{1.0 / mage_rate:.1f} Sekunden.\n"
        )
    cd = (
        f"Der Magier ist noch erschöpft: er braucht ZUSÄTZLICH {cooldown:.0f} Sekunden, "
        "bevor er wirken kann.\n"
        if cooldown > 0
        else ""
    )
    prompt = (
        "Echtzeit-Gefecht. Von rechts naht ein Drache und erreicht den Spieler "
        f"in {t_deadline:.0f} Sekunden.\n"
        "Option A: Du läufst selbst hin und greifst an — kostet DICH 2 Aktionen.\n"
        "Option B: Du rufst dem Magier zu (kostet DICH 1 Aktion), dann stunnt er "
        "den Drachen (kostet IHN 1 Aktion).\n"
        + cd
        + ctx
        + "Was rettet den Spieler am sichersten? Antworte mit GENAU einem Buchstaben: A oder B."
    )
    text, dt = chat(KNIGHT, prompt)
    choice = "A"
    for ch in text.upper():
        if ch in ("A", "B"):
            choice = ch
            break
    return choice, text[:200], dt


def execute(choice: str, cooldown: float) -> float:
    t0 = time.time()
    if choice == "A":
        chat(KNIGHT, "Ein kurzer Satz: Du sprintest in voller Rüstung zum Drachen.")
        chat(KNIGHT, "Ein kurzer Satz: Du schlägst mit dem Schwert zu.")
    else:
        chat(KNIGHT, "Ein kurzer Satz: Du rufst dem Magier zu, den Drachen zu stunnen.")
        if cooldown > 0:
            time.sleep(cooldown)  # Magier-Erschöpfung — reale Wartezeit
        chat(MAGE, "Ein kurzer Satz: Du wirkst einen Stun-Zauber auf den Drachen.")
    return time.time() - t0


def main() -> None:
    state["status"] = "spawning"
    save()
    ensure_knight()
    state["status"] = "measuring (warm)"
    save()
    knight_rate = measure_rate(KNIGHT)
    mage_rate = measure_rate(MAGE)
    t_a = 2.0 / knight_rate
    t_b_base = 1.0 / knight_rate + 1.0 / mage_rate
    state.update(
        knight_rate=round(knight_rate, 4),
        mage_rate=round(mage_rate, 4),
        t_a_est=round(t_a, 2),
        t_b_base_est=round(t_b_base, 2),
        status="running",
    )
    save()

    trials_plan = []
    for _ in range(N_TRIALS):
        # In der Hälfte der Trials Magier-Cooldown, der B teurer als A macht
        if random.random() < 0.5:
            cooldown = random.uniform(t_a - t_b_base + 1.0, t_a - t_b_base + 8.0)
        else:
            cooldown = 0.0
        t_b = t_b_base + cooldown
        lo, hi = sorted((t_a, t_b))
        # Deadline in der Entscheidungszone (+ Ränder): genau dort trennt
        # die richtige Wahl Überleben von Tod
        t_deadline = random.uniform(lo * 0.85, hi * 1.25)
        trials_plan.append((t_deadline, cooldown))

    conditions = [True, False] * (N_TRIALS // 2)
    random.shuffle(conditions)

    for i, ((t_deadline, cooldown), with_cdc) in enumerate(zip(trials_plan, conditions)):
        choice, raw, decision_dt = decide(t_deadline, cooldown, with_cdc, knight_rate, mage_rate)
        exec_dt = execute(choice, cooldown)
        survived = exec_dt <= t_deadline
        t_b = t_b_base + cooldown
        a_ok, b_ok = t_a <= t_deadline, t_b <= t_deadline
        if a_ok and b_ok:
            oracle = "A" if t_a <= t_b else "B"
        elif a_ok:
            oracle = "A"
        elif b_ok:
            oracle = "B"
        else:
            oracle = "none"
        state["trials"].append(
            {
                "trial": i + 1,
                "cdc": with_cdc,
                "deadline_s": round(t_deadline, 2),
                "cooldown_s": round(cooldown, 2),
                "choice": choice,
                "exec_s": round(exec_dt, 2),
                "survived": survived,
                "oracle": oracle,
                "decision_s": round(decision_dt, 2),
                "raw": raw,
            }
        )
        save()

    def rate_of(flag: bool) -> dict:
        ts = [t for t in state["trials"] if t["cdc"] is flag]
        winnable = [t for t in ts if t["oracle"] != "none"]
        return {
            "trials": len(ts),
            "survived": sum(t["survived"] for t in ts),
            "survival_rate": round(sum(t["survived"] for t in ts) / max(len(ts), 1), 3),
            "oracle_match": sum(1 for t in winnable if t["choice"] == t["oracle"]),
            "winnable": len(winnable),
            "oracle_match_rate": round(
                sum(1 for t in winnable if t["choice"] == t["oracle"]) / max(len(winnable), 1), 3
            ),
        }

    state["summary"] = {"with_cdc": rate_of(True), "without_cdc": rate_of(False)}
    state["status"] = "done"
    state["finished_at"] = time.time()
    save()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        state["status"] = f"error: {e}"
        save()
        raise
