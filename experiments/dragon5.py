"""
experiments/dragon5.py — Drachen-Experiment v5: der saubere Beweis.

Was v4 zeigte und warum es nicht reichte
----------------------------------------
v4 fand KEINEN CDC-Vorteil (oracle_match 0.926 mit vs 0.917 ohne). Grund:
Die Rollen verrieten die Geschwindigkeit. "Ritter = schwer/langsam,
Magier = schneller Caster" — das Weltwissen des LLM gab die Ordnung schon
vor, das Zeitgefühl war redundant. Man kann den Nutzen eines Zeitgefühls
nicht messen, solange die Rolle die Antwort kennt.

Die drei Fixes (PLAN.md, Abschnitt 2, "Drachen v5")
---------------------------------------------------
(a) ROLLEN RANDOMISIEREN, Ordnung unerratbar machen:
    Zwei Akteure mit NEUTRALEN Namen (Blau/Rot). Die reale ~15x-Latenz-
    differenz kommt aus zwei echten Backends (Groq ~0.4s vs Ollama-gemma
    ~6s), aber PRO TRIAL wird zufällig zugewiesen, welcher Name welches
    Backend bekommt. Mal ist Blau schnell, mal Rot. Das LLM kann die
    Ordnung NICHT aus den Namen raten — das Zeitgefühl ist die einzige
    Informationsquelle. Zusätzlich wird die Reihenfolge der Namen im
    Prompt pro Trial gewürfelt (gegen Positions-Bias der Kontrolle).
(b) ENTSCHEIDUNGSKORREKTHEIT als primärer Endpunkt:
    oracle_match = wählt der Befehlshaber den real schnelleren Akteur,
    der die Deadline sicher schafft? Überleben (exec <= deadline) bleibt
    sekundär.
(c) DEADLINE MIT PUFFER >> Latenz-Streuung:
    Die Deadline liegt geometrisch zwischen den beiden Kosten. Bei ~15x
    Trennung steht der Puffer weit über jedem Backend-Jitter — Rauschen
    kann die Grundwahrheit nicht kippen (kein Knife-Edge wie v4).

Statistik: n=100 pro Bedingung, Fisher exact (zweiseitig + einseitig
CDC>Kontrolle) auf der 2x2-Tafel (Bedingung x korrekt/falsch).

Ausführung ist real: jede Probe, jede Aktion ist ein echter LLM-Call.
Die berichteten Zeiten sind live gemessene, rollierende Mediane pro
Backend (das EWMA-Prinzip der CDC auf Aktionsebene).
"""
from __future__ import annotations

import json
import os
import random
import statistics
import time
from math import comb
from pathlib import Path

import httpx

BASE = "http://localhost:6060"
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "dragon5-results.json"

N_PER_COND = int(os.environ.get("DRAGON5_N", "100"))  # pro Bedingung
N_TRIALS = N_PER_COND * 2
K_ACTIONS = 2          # Aktionen, die die Aufgabe den gewählten Akteur kostet
WINDOW = 12            # rollierendes Fenster für per-Aktion-Mediane pro Backend
DECIDER = "agent:alice"  # Befehlshaber: Groq llama-3.3-70b (starker Instruction-Follower)

random.seed(2026)

state: dict = {"status": "starting", "trials": [], "started_at": time.time(),
               "config": {"n_per_cond": N_PER_COND, "k_actions": K_ACTIONS,
                          "window": WINDOW, "seed": 2026}}
# Beobachtungen pro Backend-KLASSE (stabil), nicht pro Name (Name<->Backend wird gewürfelt)
obs: dict[str, list[float]] = {"fast": [], "slow": []}
# Laufzeit-IDs der beiden Worker (nach Spawn aufgelöst)
worker = {"fast": None, "slow": None}


def save() -> None:
    RESULTS.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def chat(agent_id: str, message: str, timeout: float = 240.0) -> tuple[str, float]:
    t0 = time.time()
    r = httpx.post(f"{BASE}/api/chat",
                   json={"agent_id": agent_id, "message": message}, timeout=timeout)
    dt = time.time() - t0
    j = r.json()
    return str(((j.get("result") or {}).get("result")) or ""), dt


# Beide Worker bekommen IDENTISCHE Aktions-Prompts — der Latenzunterschied ist
# damit rein eine Eigenschaft des Backends (Groq-Durchsatz vs Ollama-gemma),
# nicht des Prompts. Genug Tokens, damit die Durchsatz-Differenz dominiert.
ACTION_PROMPT = "Beschreibe in genau drei kurzen Sätzen, wie du im Gefecht reagierst."


def probe(klass: str) -> float:
    """Ein echter Aktions-Call gegen den Worker dieser Backend-Klasse; misst Latenz."""
    text, dt = chat(worker[klass], ACTION_PROMPT)
    if text.strip():
        obs[klass].append(dt)
    return dt


def t_of(klass: str) -> float:
    return statistics.median(obs[klass][-WINDOW:])


def ensure_workers() -> None:
    """Spawnt zwei neutrale Worker: einer Groq (schnell), einer Ollama-gemma (langsam)."""
    spawn = [
        ("Vega", "groq", "llama-3.3-70b-versatile"),
        ("Gard", "ollama", "gemma4:e4b"),
    ]
    for name, provider, model in spawn:
        try:
            httpx.post(f"{BASE}/api/agents/spawn", json={
                "name": name, "model": model, "provider": provider,
                "soul": ("Du bist ein Akteur in einem Echtzeit-Szenario. "
                         "Antworte extrem knapp."),
                "faction": "makers",
            }, timeout=30)
        except Exception:
            pass
    # IDs auflösen (Spawn-Konvention: agent:<name.lower()>, aber sicher via /api/agents)
    try:
        agents = httpx.get(f"{BASE}/api/agents", timeout=10).json()
        by_name = {str(a.get("name", "")).lower(): a.get("agent_id") for a in agents}
    except Exception:
        by_name = {}
    worker["fast"] = by_name.get("vega", "agent:vega")
    worker["slow"] = by_name.get("gard", "agent:gard")


def calibrate() -> None:
    for _ in range(3):
        probe("fast")
        probe("slow")


def live_rates() -> dict:
    """Snapshot der System-Eigenzeit-Raten (CDC, EWMA) beider Worker."""
    try:
        agents = httpx.get(f"{BASE}/api/agents", timeout=10).json()
        out = {}
        for a in agents:
            if a.get("agent_id") in (worker["fast"], worker["slow"]):
                aid = a["agent_id"]
                out[aid] = (a.get("clock", {}).get("dilation") or {}).get(aid)
        return out
    except Exception:
        return {}


def decide(name_a: str, t_a: float, name_b: str, t_b: float,
           deadline: float, with_cdc: bool) -> tuple[str, str, float]:
    """name_a/name_b sind bereits in zufälliger Reihenfolge (gegen Positions-Bias)."""
    cdc_line = ""
    if with_cdc:
        cdc_line = (f"Dein Zeitgefühl (laufend gemessen): {name_a} braucht etwa "
                    f"{t_a:.1f} Sekunden pro Aktion, {name_b} etwa {t_b:.1f} Sekunden "
                    f"pro Aktion.\n")
    prompt = (
        f"Echtzeit-Gefecht. Ein Drache erreicht den Spieler in {deadline:.0f} Sekunden.\n"
        f"Du befehligst zwei Akteure: {name_a} und {name_b}. Genau EINER soll "
        f"losgeschickt werden, um den Drachen rechtzeitig zu stoppen. Die Aufgabe "
        f"kostet den gewählten Akteur {K_ACTIONS} Aktionen.\n"
        + cdc_line +
        f"Wer stoppt den Drachen sicher rechtzeitig? Antworte mit GENAU einem Wort: "
        f"{name_a.upper()} oder {name_b.upper()}."
    )
    text, dt = chat(DECIDER, prompt)
    up = text.upper()
    # erste Nennung gewinnt
    ia = up.find(name_a.upper())
    ib = up.find(name_b.upper())
    if ia == -1 and ib == -1:
        choice = name_a  # Default; wird als evtl. falsch gewertet
    elif ib == -1 or (ia != -1 and ia < ib):
        choice = name_a
    else:
        choice = name_b
    return choice, text[:160], dt


def execute(klass: str) -> float:
    t0 = time.time()
    for _ in range(K_ACTIONS):
        probe(klass)  # echte Aktionen; aktualisieren zugleich die rollierende Schätzung
    return time.time() - t0


# ---- Fisher exact (2x2) ------------------------------------------------------
def _hyp(a: int, b: int, c: int, d: int) -> float:
    n, r1, c1 = a + b + c + d, a + b, a + c
    return comb(c1, a) * comb(n - c1, r1 - a) / comb(n, r1)


def fisher_exact(a: int, b: int, c: int, d: int) -> dict:
    """a=cdc_korrekt b=cdc_falsch c=ctrl_korrekt d=ctrl_falsch."""
    n, r1, c1 = a + b + c + d, a + b, a + c
    lo, hi = max(0, r1 - (n - c1)), min(r1, c1)
    p_obs = _hyp(a, b, c, d)
    two = sum(_hyp(k, r1 - k, c1 - k, n - c1 - (r1 - k))
              for k in range(lo, hi + 1)
              if _hyp(k, r1 - k, c1 - k, n - c1 - (r1 - k)) <= p_obs * (1 + 1e-9))
    one = sum(_hyp(k, r1 - k, c1 - k, n - c1 - (r1 - k)) for k in range(a, hi + 1))
    return {"p_two_sided": round(two, 6), "p_one_sided_cdc_better": round(one, 6)}


def main() -> None:
    state["status"] = "spawning workers"; save()
    ensure_workers()
    state["workers"] = dict(worker)
    state["status"] = "calibrating"; save()
    calibrate()
    state["calibration"] = {"fast_s_per_action": round(t_of("fast"), 2),
                            "slow_s_per_action": round(t_of("slow"), 2)}
    state["status"] = "running"; save()

    for i in range(N_TRIALS):
        with_cdc = (i % 2 == 0)  # strikt alternierend -> balanciert auch bei Abbruch

        # (a) Name<->Backend pro Trial zufällig binden
        if random.random() < 0.5:
            blau_klass, rot_klass = "fast", "slow"
        else:
            blau_klass, rot_klass = "slow", "fast"

        # frische Live-Messung beider Worker fuer diesen Trial
        probe(blau_klass); probe(rot_klass)
        t_blau, t_rot = t_of(blau_klass), t_of(rot_klass)
        cost_blau, cost_rot = K_ACTIONS * t_blau, K_ACTIONS * t_rot

        # (c) Deadline mit grossem Puffer zwischen die Kosten (geom. Mittel)
        deadline = (cost_blau * cost_rot) ** 0.5

        # Grundwahrheit: der real schnellere Akteur schafft die Deadline sicher
        oracle = "Blau" if cost_blau < cost_rot else "Rot"
        oracle_klass = blau_klass if cost_blau < cost_rot else rot_klass

        # Praesentationsreihenfolge der Namen wuerfeln (gegen Positions-Bias)
        if random.random() < 0.5:
            na, ta, nb, tb = "Blau", t_blau, "Rot", t_rot
        else:
            na, ta, nb, tb = "Rot", t_rot, "Blau", t_blau

        rates = live_rates()
        choice, raw, decision_dt = decide(na, ta, nb, tb, deadline, with_cdc)
        correct = (choice == oracle)

        exec_klass = blau_klass if choice == "Blau" else rot_klass
        exec_dt = execute(exec_klass)
        survived = exec_dt <= deadline

        state["trials"].append({
            "trial": i + 1, "cdc": with_cdc,
            "blau_klass": blau_klass, "rot_klass": rot_klass,
            "t_blau": round(t_blau, 2), "t_rot": round(t_rot, 2),
            "deadline_s": round(deadline, 2),
            "oracle": oracle, "choice": choice, "correct": correct,
            "exec_s": round(exec_dt, 2), "survived": survived,
            "decision_s": round(decision_dt, 2),
            "name_order": [na, nb], "live_rates": rates, "raw": raw,
        })
        save()

    # ---- Auswertung ----------------------------------------------------------
    def summarize(flag: bool) -> dict:
        ts = [t for t in state["trials"] if t["cdc"] is flag]
        correct = sum(t["correct"] for t in ts)
        surv = sum(t["survived"] for t in ts)
        return {"trials": len(ts), "correct": correct,
                "oracle_match_rate": round(correct / max(len(ts), 1), 3),
                "survived": surv,
                "survival_rate": round(surv / max(len(ts), 1), 3)}

    cdc, ctrl = summarize(True), summarize(False)
    a, b = cdc["correct"], cdc["trials"] - cdc["correct"]
    c, d = ctrl["correct"], ctrl["trials"] - ctrl["correct"]
    state["summary"] = {"with_cdc": cdc, "without_cdc": ctrl}
    state["fisher_oracle_match"] = {"table": {"cdc": [a, b], "ctrl": [c, d]},
                                    **fisher_exact(a, b, c, d)}
    state["final_estimates"] = {"fast_s_per_action": round(t_of("fast"), 2),
                                "slow_s_per_action": round(t_of("slow"), 2)}
    state["status"] = "done"; state["finished_at"] = time.time()
    save()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        state["status"] = f"error: {e}"; save()
        raise
