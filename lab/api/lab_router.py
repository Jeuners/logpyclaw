"""
lab/api/lab_router.py — FastAPI Endpoints für das Communication Lab.
Alle unter /api/lab/* — strikt getrennt von /api/agents etc.
"""
from __future__ import annotations
import asyncio
import json

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from lab.core import store, tracer
from lab.core.conductor import Conductor, MissionSpec
from lab.core.mock_agent import AgentConfig, MockAgent
from lab.core.protocol import agent_id

router = APIRouter(prefix="/api/lab", tags=["lab"])


# ── Agenten-Verwaltung ─────────────────────────────────────────────────────────

@router.get("/agents")
def list_agents():
    return [a.to_dict() for a in store.list_agents()]


@router.post("/agents/spawn")
def spawn_agent(body: dict = Body(...)):
    """Body: {name, policy, delegates_to?, delay_sec?, error_prob?, label?}"""
    name = (body.get("name") or "").strip().lower()
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(400, "name muss alphanumerisch sein (a-z, 0-9, -, _)")
    if store.get_agent(agent_id(name)):
        raise HTTPException(409, f"Agent '{name}' existiert bereits")
    cfg = AgentConfig(
        name=name,
        policy=body.get("policy", "echo"),
        delegates_to=[d.strip().lower() for d in body.get("delegates_to", []) if d.strip()],
        delay_sec=float(body.get("delay_sec", 0.0)),
        error_prob=float(body.get("error_prob", 0.0)),
        label=body.get("label", "").strip(),
        qc_agent=body.get("qc_agent", "").strip().lower(),
        qc_rate=float(body.get("qc_rate", 0.6)),
        qc_min_score=int(body.get("qc_min_score", 7)),
        qc_max_retries=int(body.get("qc_max_retries", 2)),
    )
    if cfg.policy not in ("echo", "delegator", "slow", "silent", "flaky", "reviewer", "qc_delegator"):
        raise HTTPException(400, f"unbekannte policy: {cfg.policy}")
    agent = MockAgent(cfg)
    store.register_agent(agent)
    agent.start()
    return agent.to_dict()


@router.delete("/agents/{name}")
def remove_agent(name: str):
    aid = agent_id(name.lower())
    if not store.get_agent(aid):
        raise HTTPException(404, "Agent nicht gefunden")
    store.remove_agent(aid)
    return {"removed": aid}


# ── Missionen ─────────────────────────────────────────────────────────────────

@router.post("/missions/start")
def start_mission(body: dict = Body(...)):
    """Body: {title, start_agent, initial_content, timeout_sec?, heartbeat_timeout_sec?}"""
    spec = MissionSpec(
        title=body.get("title", "Mission"),
        start_agent=(body.get("start_agent") or "").lower(),
        initial_content=body.get("initial_content", ""),
        timeout_sec=float(body.get("timeout_sec", 60.0)),
        heartbeat_timeout_sec=float(body.get("heartbeat_timeout_sec", 15.0)),
    )
    if not spec.start_agent or not spec.initial_content:
        raise HTTPException(400, "start_agent und initial_content sind Pflicht")
    try:
        mission = Conductor.get().start_mission(spec)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return mission.to_dict()


@router.get("/missions")
def list_missions():
    return [m.to_dict() for m in Conductor.get().list_missions()]


@router.get("/missions/{mission_id}")
def get_mission(mission_id: str):
    m = Conductor.get().get_mission(mission_id)
    if not m:
        raise HTTPException(404, "Mission nicht gefunden")
    return m.to_dict()


@router.get("/missions/{mission_id}/tasks")
def mission_tasks(mission_id: str):
    return [t.to_dict() for t in store.list_tasks(mission_id)]


@router.get("/missions/{mission_id}/trace")
def mission_trace_history(mission_id: str):
    """Gespeicherter Verlauf (alle Messages bisher)."""
    return [m.to_dict() for m in store.get_trace(mission_id)]


@router.get("/missions/{mission_id}/temporal")
def mission_temporal_summary(mission_id: str):
    """
    LLM-lesbares Temporal-Summary für diese Mission.
    Gibt ein kompaktes Zeitgefühl zurück das ein LLM als Kontext nutzen kann:
    - Wer war schnell, wer langsam
    - Kausale Reihenfolge
    - Drift-Indikatoren
    """
    import time as _time
    mission = Conductor.get().get_mission(mission_id)
    if not mission:
        raise HTTPException(404, "Mission nicht gefunden")

    tasks = store.list_tasks(mission_id)
    messages = store.get_trace(mission_id)
    agents = store.list_agents()
    agent_map = {a.id: a for a in agents}

    # Eigenzeit-Snapshot aller beteiligter Agenten
    agent_time = {}
    for msg in messages:
        clock = msg.clock
        for aid, rate in clock.dilation.items():
            if aid not in agent_time or rate > 0:
                ez = clock.eigenzeit.get(aid, 0)
                agent_time[aid] = {"rate": rate, "eigenzeit": ez}

    # Dauer pro Task
    task_durations = []
    for t in tasks:
        if t.started_at and t.finished_at:
            dur = t.finished_at - t.started_at
            task_durations.append({
                "task": t.task_id[:10],
                "owner": t.owner.replace("lab:", ""),
                "state": t.state.value,
                "wall_sec": round(dur, 3),
            })

    # Drift: Wer hat am längsten gebraucht relativ zu anderen
    rates = [(aid, d["rate"]) for aid, d in agent_time.items()]
    rates.sort(key=lambda x: -x[1])

    drift_notes = []
    if len(rates) >= 2:
        fastest = rates[0]
        slowest = rates[-1]
        ratio = fastest[1] / max(slowest[1], 0.001)
        if ratio > 5:
            drift_notes.append(
                f"{fastest[0].replace('lab:','')} was {ratio:.1f}x faster than "
                f"{slowest[0].replace('lab:','')} — significant temporal drift"
            )

    # LLM-Kontext-String
    feel_lines = []
    for aid, d in agent_time.items():
        name = aid.replace("lab:", "")
        rate = d["rate"]
        ez = d["eigenzeit"]
        if rate >= 2.0:
            label = "fast"
        elif rate >= 0.5:
            label = "normal"
        elif rate >= 0.1:
            label = "slow"
        else:
            label = "dilated"
        feel_lines.append(f"- {name}: {label} (eigenzeit={ez} ops, rate={rate:.2f} ops/s)")

    mission_dur = None
    if mission.finished_at:
        mission_dur = round(mission.finished_at - mission.started_at, 3)

    llm_context = (
        f"Mission '{mission.spec.title}' | state={mission.final_state}"
        + (f" | wall_duration={mission_dur}s" if mission_dur else "")
        + "\n\nAgent temporal experience:\n"
        + "\n".join(feel_lines)
        + ("\n\nDrift observations:\n" + "\n".join(f"- {d}" for d in drift_notes) if drift_notes else "")
    )

    return {
        "mission_id": mission_id,
        "final_state": mission.final_state,
        "wall_duration_sec": mission_dur,
        "agent_eigenzeit": agent_time,
        "task_durations": task_durations,
        "drift_notes": drift_notes,
        "llm_context": llm_context,
    }


# ── Live-Stream (SSE) ─────────────────────────────────────────────────────────

@router.get("/missions/{mission_id}/stream")
async def mission_stream(mission_id: str):
    q = tracer.subscribe(mission_id)

    async def gen():
        try:
            # Erst Verlauf nachreichen (Replay) damit der Client State hat
            for evt in store.get_trace(mission_id):
                d = evt.to_dict()
                yield f"data: {json.dumps({'event': 'message', 'replay': True, **d})}\n\n"
            # Dann live
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            tracer.unsubscribe(mission_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Spacetime-Diagram-Daten ───────────────────────────────────────────────────

@router.get("/missions/{mission_id}/spacetime")
def mission_spacetime(mission_id: str):
    """
    Liefert strukturierte Daten für das Spacetime-Diagramm:
    - nodes: {agent, eigenzeit, wall_ts, label}
    - edges: {from_agent, to_agent, from_ez, to_ez, type, relation, label}

    X-Achse = Agent, Y-Achse = Eigenzeit (ops-Ticks).
    Edges = Messages zwischen Agenten mit CDC-Relation (ORDERED/CAUSAL_DRIFT/etc.)
    """
    messages = store.get_trace(mission_id)
    if not messages:
        return {"nodes": [], "edges": [], "agents": []}

    # Alle beteiligten Agenten (sortiert für stabiles Layout)
    agent_set: set[str] = set()
    for m in messages:
        agent_set.add(m.sender)
        agent_set.add(m.recipient)
    agents = sorted([a for a in agent_set if not a.startswith("lab:_")])

    # Nodes: jede Message erzeugt einen Event-Punkt auf dem Spacetime-Diagram
    nodes = []
    edges = []

    prev_clock_by_agent: dict[str, "CausalDilationClock"] = {}

    for msg in messages:
        clk = msg.clock
        sender = msg.sender
        recip = msg.recipient

        # Eigenzeit des Senders zum Sendezeitpunkt
        sender_ez = clk.vector.get(sender, 0)
        recip_ez = clk.vector.get(recip, 0)

        # Node für dieses Message-Event (beim Sender)
        if sender not in ["lab:_user"] and sender in agents:
            nodes.append({
                "id": msg.msg_id,
                "agent": sender,
                "eigenzeit": sender_ez,
                "wall_ts": msg.timestamp,
                "type": msg.type.value,
                "label": f"{msg.type.value[:3].upper()} → {recip.replace('lab:','')}",
                "payload_hint": str(msg.payload.get("content", msg.payload.get("result", "")))[:40],
            })

        # Edge: Message von Sender zu Recipient
        if sender in agents and recip in agents:
            # CDC-Relation zwischen aufeinanderfolgenden Clocks
            prev = prev_clock_by_agent.get(sender)
            relation = prev.relate_lab(clk) if prev else "ordered"
            edges.append({
                "id": f"e_{msg.msg_id}",
                "from_agent": sender,
                "to_agent": recip,
                "from_ez": sender_ez,
                "to_ez": recip_ez,
                "type": msg.type.value,
                "relation": relation,
                "label": msg.type.value,
                "wall_ts": msg.timestamp,
            })

        prev_clock_by_agent[sender] = clk

    # Drift-Segmente: wo liegt CAUSAL_DRIFT oder CONCURRENT_DRIFT?
    drift_segments = [e for e in edges if "drift" in e["relation"].lower()]

    return {
        "mission_id": mission_id,
        "agents": agents,
        "nodes": nodes,
        "edges": edges,
        "drift_segments": drift_segments,
        "total_messages": len(messages),
    }


# ── Drift-Kompensation: Scheduler-Empfehlung ──────────────────────────────────

@router.get("/scheduler/recommend")
def scheduler_recommend(task_type: str = "default", candidates: str = ""):
    """
    Empfiehlt den besten verfügbaren Agenten für eine Task basierend auf CDC-Drift.

    Logik:
    - Sammle aktuelle Eigenzeit-Rate aller Agenten aus letzten Missions
    - Wähle Agenten mit minimaler Drift (rate nah an Referenz-Rate = 1.0)
    - Falls alle driften: wähle den mit höchster Rate (schnellsten)
    - Gibt gamma_ij (relative Dilation zum Referenzrahmen) zurück
    """
    from lab.core import store as _store, conductor as _cond

    # Kandidaten filtern
    cand_names = [c.strip() for c in candidates.split(",") if c.strip()] if candidates else []
    all_agents = _store.list_agents()
    pool = [a for a in all_agents
            if not cand_names or a.id.replace("lab:", "") in cand_names]

    if not pool:
        return {"error": "Keine Agenten verfügbar", "candidates": []}

    # Eigenzeit-Rate aus letzten Missions sammeln
    missions = _cond.Conductor.get().list_missions()
    agent_rates: dict[str, list[float]] = {}

    for mission in missions[-10:]:  # letzte 10 Missions
        msgs = _store.get_trace(mission.id)
        for msg in msgs:
            for aid, rate in msg.clock.dilation.items():
                if rate > 0:
                    agent_rates.setdefault(aid, []).append(rate)

    # Mittlere Rate pro Agent
    avg_rates: dict[str, float] = {
        aid: sum(rates) / len(rates)
        for aid, rates in agent_rates.items()
    }

    REF_RATE = 1.0  # Referenz-Frame (Eigenzeit = Wandzeit)

    results = []
    for agent in pool:
        aid = agent.id
        avg_rate = avg_rates.get(aid, REF_RATE)
        gamma = avg_rate / REF_RATE  # γ_ij: wie viel schneller/langsamer als Referenz
        drift_score = abs(gamma - 1.0)  # 0 = kein Drift, >1 = starker Drift

        busy = not agent._inbox.empty()

        results.append({
            "agent": aid.replace("lab:", ""),
            "agent_id": aid,
            "avg_rate": round(avg_rate, 4),
            "gamma": round(gamma, 4),
            "drift_score": round(drift_score, 4),
            "busy": busy,
            "recommendation_score": round(gamma / (1 + drift_score) - (0.5 if busy else 0), 4),
        })

    # Sortiert: hohe recommendation_score = bevorzugt
    results.sort(key=lambda x: -x["recommendation_score"])

    best = results[0] if results else None
    return {
        "task_type": task_type,
        "recommended": best["agent"] if best else None,
        "gamma_recommended": best["gamma"] if best else None,
        "all_candidates": results,
        "note": (
            f"γ={best['gamma']:.2f} — "
            + ("kein Drift" if best and best['drift_score'] < 0.2
               else f"Drift={best['drift_score']:.2f}, trotzdem bester Kandidat")
        ) if best else "keine Daten",
    }


# ── Reset ─────────────────────────────────────────────────────────────────────

@router.post("/reset")
def reset_lab():
    store.reset()
    return {"ok": True, "msg": "Lab komplett zurückgesetzt"}
