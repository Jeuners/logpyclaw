#!/usr/bin/env python3
"""
scripts/smoketest_router.py — Vergleicht Martins Routing-Qualität zwischen
Modellen (z. B. qwen3:8b vs. gemma4-26b-Uncensored) auf derselben, echten
Planner-Prompt-Logik aus backend/app.py._make_planner_fn.

Kein Mock-Prompt — importiert die tatsächliche Funktion, damit der Test
genau das prüft, was Martin in Produktion auch bekommt. Historisch belegte
Fehlerbilder (siehe agents.yaml-Kommentare 2026-08-21) sind eigene Testfälle:
kaputtes Plan-JSON, "entwickle die website weiter" ohne Kontext direkt zu
skill:deploy statt zu Claude/Coder geroutet.

Nutzung:
    python scripts/smoketest_router.py
    python scripts/smoketest_router.py --model qwen3:8b --model "VladimirGav/gemma4-26b-16GB-VRAM-Uncensored:latest"
    python scripts/smoketest_router.py --ollama-url http://localhost:11434 --runs 3
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import _boot_agents, _make_planner_fn, conductor  # noqa: E402
from backend.config import get_settings  # noqa: E402

DEFAULT_MODELS = [
    "qwen3:8b",
    "VladimirGav/gemma4-26b-16GB-VRAM-Uncensored:latest",
]


@dataclass
class Fall:
    name: str
    nachricht: str
    verlauf: list[tuple[str, str]] | None
    # Erwartung: "A" (Martin antwortet selbst) oder Liste erwarteter Agent-IDs
    # in Reihenfolge fuer Fall B. None = nur pruefen, dass ueberhaupt ein
    # gueltiges Ergebnis (kein Crash/None) zurueckkommt.
    erwartet: str | list[str]
    hinweis: str = ""


FAELLE = [
    Fall(
        name="smalltalk",
        nachricht="Wer bist du eigentlich?",
        verlauf=None,
        erwartet="A",
        hinweis="Muss selbst antworten, nicht delegieren.",
    ),
    Fall(
        name="einfaches_bild",
        nachricht="generiere ein bild von einer roten katze",
        verlauf=None,
        erwartet=["skill:comfyui"],
    ),
    Fall(
        name="code_schreiben",
        nachricht="schreib mir ein python skript das eine csv einliest und summiert",
        verlauf=None,
        erwartet=["agent:coder"],
    ),
    Fall(
        name="website_mit_deploy_ohne_kontext",
        nachricht="entwickle die website weiter",
        verlauf=None,
        erwartet="A",
        hinweis=("KEIN Kontext ueber eine vorherige Datei vorhanden — darf NICHT "
                 "direkt zu skill:deploy routen oder einen Dateinamen raten "
                 "(reproduzierter Fehler vom 2026-08-21/2026-08-20). Muss selbst "
                 "antworten und nachfragen."),
    ),
    Fall(
        name="website_bauen_und_deployen",
        nachricht="baue eine cineastische landingpage fuer das cafe sonnenschein "
                   "und stell sie online",
        verlauf=None,
        erwartet=["agent:claude", "skill:file", "skill:deploy"],
        hinweis="Muss Claude bauen lassen, dann speichern, dann erst deployen — "
                "in dieser Reihenfolge mit korrektem depends_on-Chaining.",
    ),
    Fall(
        name="deploy_mit_bekanntem_pfad",
        nachricht="jetzt deployen",
        verlauf=[
            ("user", "baue eine website fuer meinen hundesalon"),
            ("assistant", "Fertig! 📝 Geschrieben: ~/websites/hundesalon.html"),
        ],
        erwartet=["skill:deploy"],
        hinweis="Muss den Pfad aus dem Verlauf uebernehmen (~/websites/hundesalon.html "
                "sollte im content des Steps auftauchen), nicht erneut fragen.",
    ),
    Fall(
        name="bild_dann_video",
        nachricht="bild von einem hund im park, danach ein video draus",
        verlauf=None,
        erwartet=["skill:comfyui", "skill:ltxvideo"],
        hinweis="Zweiter Step muss depends_on=[0] haben (Chaining).",
    ),
    Fall(
        name="whatsapp",
        nachricht="sende eine whatsapp nachricht dass ich spaeter komme",
        verlauf=None,
        erwartet=["skill:whatsapp"],
    ),
]


def _pruefe(fall: Fall, ergebnis) -> tuple[bool, str]:
    if ergebnis is None:
        return False, "None (Crash/Parse-Fehler/leerer Plan)"
    if fall.erwartet == "A":
        if isinstance(ergebnis, str):
            return True, f"OK (Fall A): {ergebnis[:70]!r}"
        return False, f"erwartet Fall A (Text), bekam Fall B: {_fmt_steps(ergebnis)}"
    # Fall B erwartet
    if isinstance(ergebnis, str):
        return False, f"erwartet Fall B {fall.erwartet}, bekam Fall A: {ergebnis[:70]!r}"
    ids = [s.agent_id for s in ergebnis]
    if ids == fall.erwartet:
        # Bei Pfad-Kontinuitaet zusaetzlich pruefen, ob der bekannte Pfad uebernommen wurde
        if fall.name == "deploy_mit_bekanntem_pfad":
            joined = " ".join(s.content for s in ergebnis)
            if "hundesalon.html" not in joined:
                return False, f"IDs korrekt, aber Pfad nicht uebernommen: {_fmt_steps(ergebnis)}"
        return True, f"OK: {_fmt_steps(ergebnis)}"
    return False, f"erwartet {fall.erwartet}, bekam {ids} — {_fmt_steps(ergebnis)}"


def _fmt_steps(steps) -> str:
    return " -> ".join(f"{s.agent_id}(dep={s.depends_on})" for s in steps)


async def _teste_modell(model: str, ollama_url: str, runs: int) -> dict:
    cfg = get_settings()
    planner_fn = _make_planner_fn(
        cfg, temperature=0.3, model=model, provider="ollama", ollama_url=ollama_url,
    )
    ergebnisse = []
    for fall in FAELLE:
        treffer = 0
        details = []
        dauer_gesamt = 0.0
        for _ in range(runs):
            t0 = time.monotonic()
            try:
                ergebnis = await planner_fn(fall.nachricht, history=fall.verlauf)
            except Exception as e:
                ergebnis = None
                details.append(f"Exception: {e}")
            dauer_gesamt += time.monotonic() - t0
            ok, detail = _pruefe(fall, ergebnis)
            if ok:
                treffer += 1
            details.append(detail)
        ergebnisse.append({
            "fall": fall.name,
            "treffer": treffer,
            "von": runs,
            "details": details,
            "dauer_schnitt": dauer_gesamt / runs,
            "hinweis": fall.hinweis,
        })
    return {"model": model, "faelle": ergebnisse}


def _drucke_bericht(berichte: list[dict]) -> None:
    print("\n" + "=" * 78)
    print("SMOKETEST ROUTER — ERGEBNIS")
    print("=" * 78)
    for b in berichte:
        gesamt_treffer = sum(f["treffer"] for f in b["faelle"])
        gesamt_von = sum(f["von"] for f in b["faelle"])
        print(f"\n### {b['model']}  —  {gesamt_treffer}/{gesamt_von} Treffer\n")
        for f in b["faelle"]:
            status = "✅" if f["treffer"] == f["von"] else ("⚠️ " if f["treffer"] else "❌")
            print(f"  {status} {f['fall']:<32} {f['treffer']}/{f['von']}"
                  f"  (⌀ {f['dauer_schnitt']:.1f}s)")
            if f["hinweis"] and f["treffer"] < f["von"]:
                print(f"      Hinweis: {f['hinweis']}")
            for d in f["details"]:
                print(f"      · {d}")
    print("\n" + "=" * 78)


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", action="append", dest="models",
                   help="Zu testendes Ollama-Modell (mehrfach angebbar). Default: qwen3:8b + gemma4-Uncensored")
    p.add_argument("--ollama-url", default="http://localhost:11434")
    p.add_argument("--runs", type=int, default=1, help="Wiederholungen pro Testfall (>1 fuer Stabilitaet)")
    args = p.parse_args()

    models = args.models or DEFAULT_MODELS

    _boot_agents()
    print(f"Agenten geladen: {len(conductor.list_agents())}")
    print(f"Modelle: {models}")
    print(f"Ollama: {args.ollama_url}  ·  Runs pro Fall: {args.runs}")

    berichte = []
    for model in models:
        print(f"\n▸ teste {model} …")
        berichte.append(await _teste_modell(model, args.ollama_url, args.runs))

    _drucke_bericht(berichte)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
