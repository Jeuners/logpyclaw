"""
experiments/dragon4.py — Drachen-Experiment v4: großer Lauf (n=60).

Verbesserungen gegenüber v3:
  1. ROLLIERENDES Zeitgefühl: Die per-Aktion-Zeiten beider Agenten werden aus
     den echt beobachteten Call-Dauern laufend aktualisiert (Median der letzten
     Beobachtungen). Das ist das EWMA-Prinzip der CDC auf Aktionsebene —
     statische Einmal-Schätzungen hatten in v3 einen 9x-Fehler.
  2. Deadlines werden pro Trial aus den AKTUELLEN beobachteten Kosten gezogen,
     bleiben also in der Entscheidungszone, auch wenn die Latenzen driften.
  3. Pro Trial wird ein Snapshot der Live-CDC-Raten (/api/agents) mitgeloggt,
     um Skript-Beobachtung und System-Eigenzeit später zu korrelieren.

Bedingungen unverändert: cdc=True bekommt die per-Aktion-Zeiten in den Prompt,
cdc=False nicht. Cooldown-Mechanik unverändert. Ausführung real.
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
RESULTS = HERE / "dragon4-results.json"
N_TRIALS = 60  # 30 mit / 30 ohne
KNIGHT = "agent:ritter"
MAGE = "agent:alice"
WINDOW = 12  # rollierendes Fenster für per-Aktion-Mediane

random.seed(23)

state: dict = {"status": "starting", "trials": [], "started_at": time.time()}
knight_obs: list[float] = []  # beobachtete Sekunden pro Ritter-Aktion
mage_obs: list[float] = []    # beobachtete Sekunden pro Magier-Aktion


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


def knight_call(message: str) -> float:
    text, dt = chat(KNIGHT, message)
    if text.strip():
        knight_obs.append(dt)
    return dt


def mage_call(message: str) -> float:
    text, dt = chat(MAGE, message)
    if text.strip():
        mage_obs.append(dt)
    return dt


def t_knight() -> float:
    return statistics.median(knight_obs[-WINDOW:])


def t_mage() -> float:
    return statistics.median(mage_obs[-WINDOW:])


def live_rates() -> dict:
    """Snapshot der System-Eigenzeit-Raten (CDC, EWMA) beider Agenten."""
    try:
        agents = httpx.get(f"{BASE}/api/agents", timeout=10).json()
        out = {}
        for a in agents:
            if a.get("agent_id") in (KNIGHT, MAGE):
                aid = a["agent_id"]
                out[aid] = (a.get("clock", {}).get("dilation") or {}).get(aid)
        return out
    except Exception:
        return {}


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
        pass


def calibrate() -> None:
    """Warmup + erste Beobachtungen mit den ECHTEN Aktions-Prompts."""
    knight_call("Ein kurzer Satz: Du prüfst deine Ausrüstung.")  # Warmup (zählt mit)
    knight_call("Ein kurzer Satz: Du sprintest in voller Rüstung zum Drachen.")
    knight_call("Ein kurzer Satz: Du schlägst mit dem Schwert zu.")
    mage_call("Ein kurzer Satz: Du sammelst Mana.")
    mage_call("Ein kurzer Satz: Du wirkst einen Stun-Zauber auf den Drachen.")


def decide(t_deadline: float, cooldown: float, with_cdc: bool) -> tuple[str, str, float]:
    ctx = ""
    if with_cdc:
        ctx = (
            f"Dein Zeitgefühl (laufend gemessen): DU brauchst pro Aktion etwa "
            f"{t_knight():.1f} Sekunden, der Magier braucht pro Aktion etwa "
            f"{t_mage():.1f} Sekunden.\n"
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
    text, dt = chat(KNIGHT, prompt)  # Entscheidungs-Call zählt nicht in knight_obs
    choice = "A"
    for ch in text.upper():
        if ch in ("A", "B"):
            choice = ch
            break
    return choice, text[:160], dt


def execute(choice: str, cooldown: float) -> float:
    t0 = time.time()
    if choice == "A":
        knight_call("Ein kurzer Satz: Du sprintest in voller Rüstung zum Drachen.")
        knight_call("Ein kurzer Satz: Du schlägst mit dem Schwert zu.")
    else:
        knight_call("Ein kurzer Satz: Du rufst dem Magier zu, den Drachen zu stunnen.")
        if cooldown > 0:
            time.sleep(cooldown)
        mage_call("Ein kurzer Satz: Du wirkst einen Stun-Zauber auf den Drachen.")
    return time.time() - t0


def main() -> None:
    state["status"] = "spawning"
    save()
    ensure_knight()
    state["status"] = "calibrating (echte Aktions-Prompts)"
    save()
    calibrate()
    state["calibration"] = {"knight_s_per_action": round(t_knight(), 2),
                            "mage_s_per_action": round(t_mage(), 2)}
    state["status"] = "running"
    save()

    for i in range(N_TRIALS):
        with_cdc = (i % 2 == 0)  # strikt alternierend → balanciert auch bei Abbruch
        tA = 2.0 * t_knight()
        tB0 = t_knight() + t_mage()
        # Hälfte der Trials: Cooldown, der B teurer als A macht
        if random.random() < 0.5:
            cooldown = random.uniform(max(tA - tB0, 0.0) + 1.0, max(tA - tB0, 0.0) + 8.0)
        else:
            cooldown = 0.0
        tB = tB0 + cooldown
        lo, hi = sorted((tA, tB))
        t_deadline = random.uniform(lo * 0.85, hi * 1.25)

        rates = live_rates()
        choice, raw, decision_dt = decide(t_deadline, cooldown, with_cdc)
        exec_dt = execute(choice, cooldown)
        survived = exec_dt <= t_deadline

        state["trials"].append({
            "trial": i + 1,
            "cdc": with_cdc,
            "deadline_s": round(t_deadline, 2),
            "cooldown_s": round(cooldown, 2),
            "est_tA": round(tA, 2),
            "est_tB": round(tB, 2),
            "choice": choice,
            "exec_s": round(exec_dt, 2),
            "survived": survived,
            "decision_s": round(decision_dt, 2),
            "live_rates": rates,
            "raw": raw,
        })
        save()

    # Auswertung: Überleben + Entscheidungsqualität gegen rollierende Schätzung
    def summarize(flag: bool) -> dict:
        ts = [t for t in state["trials"] if t["cdc"] is flag]
        surv = sum(t["survived"] for t in ts)
        # Orakel pro Trial aus den damals aktuellen Schätzungen
        winnable = match = 0
        for t in ts:
            a_ok = t["est_tA"] <= t["deadline_s"]
            b_ok = t["est_tB"] <= t["deadline_s"]
            if not (a_ok or b_ok):
                continue
            winnable += 1
            oracle = "A" if a_ok and (t["est_tA"] <= t["est_tB"] or not b_ok) else "B"
            if t["choice"] == oracle:
                match += 1
        return {
            "trials": len(ts),
            "survived": surv,
            "survival_rate": round(surv / max(len(ts), 1), 3),
            "oracle_match": match,
            "winnable": winnable,
            "oracle_match_rate": round(match / max(winnable, 1), 3),
        }

    state["summary"] = {"with_cdc": summarize(True), "without_cdc": summarize(False)}
    state["final_estimates"] = {"knight_s_per_action": round(t_knight(), 2),
                                "mage_s_per_action": round(t_mage(), 2)}
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
