"""
lab/api/dilation_demo_router.py — Time Dilation Demo (Paper §3–§4).

Dispatcht denselben Prompt gleichzeitig an zwei Agenten mit verschiedenen
γ-Faktoren. Streamt Token-Events + CDC-Clock-Vergleich via SSE.

Endpoint: POST /api/lab/dilation/run
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Body
from fastapi.responses import HTMLResponse, StreamingResponse

from core.causal_dilation_clock import CausalDilationClock
from core.llm_stream import stream_ollama

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/lab/dilation", tags=["dilation-demo"])

OLLAMA_URL = "http://localhost:11434"

# Zwei Agenten: unterschiedliche Modellgröße → unterschiedliche γ-Faktoren.
# γ entspricht dem heuristischen estimate_dilation() aus time_provider.py.
AGENT_A = {
    "id": "A",
    "name": "SCHNELL",
    "model": "gemma3:latest",
    "max_tokens": 80,
    "gamma": 1.0,       # Referenzrahmen — kleines, schnelles Modell
}
AGENT_B = {
    "id": "B",
    "name": "TIEF",
    "model": "gemma4:e4b",
    "max_tokens": 350,
    "gamma": 3.5,       # tiefes Reasoning → mehr Eigenzeit pro Token
}


async def _run_agent(
    agent: dict,
    messages: list[dict],
    clock: CausalDilationClock,
    queue: asyncio.Queue,
) -> None:
    """Streamt Tokens, tickt die CDC-Clock, schiebt Events in die Queue."""
    aid = agent["id"]
    t0 = time.time()
    token_count = 0
    try:
        async for token in stream_ollama(
            messages, agent["model"], OLLAMA_URL, agent["max_tokens"]
        ):
            token_count += 1
            # Eigenzeit-Tick: jeder Token kostet γ Eigenzeit-Einheiten.
            clock.tick(aid, agent["gamma"])
            await queue.put({
                "type": "tok",
                "agent": aid,
                "tok": token,
                "tau": round(clock.dilation.get(aid, 0.0), 2),
                "ms": int((time.time() - t0) * 1000),
            })
    except Exception as exc:
        logger.warning("dilation_demo: Agent %s Fehler: %s", aid, exc)
        await queue.put({"type": "err", "agent": aid, "msg": str(exc)})

    await queue.put({
        "type": "done",
        "agent": aid,
        "tau": round(clock.dilation.get(aid, 0.0), 2),
        "tokens": token_count,
        "ms": int((time.time() - t0) * 1000),
        "clock": clock.to_dict(),
    })


@router.post("/run")
async def run_demo(body: dict = Body(...)):
    """SSE-Stream: zwei parallele Agenten + CDC-Vergleich."""
    prompt = (body.get("prompt") or "Was bedeutet Eigenzeit in einem KI-Agentensystem?").strip()
    messages = [{"role": "user", "content": prompt}]

    async def generate():
        queue: asyncio.Queue = asyncio.Queue()
        clock_a = CausalDilationClock()
        clock_b = CausalDilationClock()
        wall_start = time.time()

        task_a = asyncio.create_task(_run_agent(AGENT_A, messages, clock_a, queue))
        task_b = asyncio.create_task(_run_agent(AGENT_B, messages, clock_b, queue))

        done_clocks: dict[str, dict] = {}
        done_count = 0

        while done_count < 2:
            event = await queue.get()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event["type"] == "done":
                done_clocks[event["agent"]] = event.get("clock", {})
                done_count += 1

        await asyncio.gather(task_a, task_b, return_exceptions=True)

        # CDC-Relation-Klassifikation (§3.4).
        # Beide Clocks sind nebenläufig (kein kausaler Pfad A→B oder B→A).
        relation = clock_a.relate(clock_b).value

        # Frame-Transformation (§3.3): τ_B aus A's Frame.
        tau_a = clock_a.dilation.get("A", 0.0)
        tau_b = clock_b.dilation.get("B", 0.0)
        gamma_ratio = AGENT_A["gamma"] / AGENT_B["gamma"]
        tau_b_in_a = CausalDilationClock.transform(
            tau_b, "B", "A",
            {("B", "A"): gamma_ratio},
        )

        yield f"data: {json.dumps({
            'type': 'cdc',
            'relation': relation,
            'tau_a': round(tau_a, 2),
            'tau_b': round(tau_b, 2),
            'tau_b_in_a': round(tau_b_in_a, 2),
            'gamma_a': AGENT_A['gamma'],
            'gamma_b': AGENT_B['gamma'],
            'gamma_ratio': round(gamma_ratio, 4),
            'delta_tau': round(abs(tau_a - tau_b_in_a), 2),
            'wall_ms': int((time.time() - wall_start) * 1000),
            'clock_a': done_clocks.get('A', {}),
            'clock_b': done_clocks.get('B', {}),
        }, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/config")
def get_config():
    """Liefert die Agent-Konfiguration für die UI."""
    return {"agent_a": AGENT_A, "agent_b": AGENT_B}


_PAGE_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Time Dilation Demo — AgentClaw</title>
<style>
:root{--green:#00e676;--green-dim:#3a5a3a;--blue:#3b82f6;--amber:#fbbf24;--bg:#050a06;--bg2:#070d08;--bg3:#0a150b;--border:#0f2010;--text:#e2e8f0;--muted:#6b7280}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:monospace;font-size:13px;min-height:100vh}
a{color:var(--green-dim);text-decoration:none}a:hover{color:var(--green)}

/* NAV */
nav{height:44px;background:#070d08;border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 20px;gap:20px;position:sticky;top:0;z-index:100}
.nav-logo{color:var(--green);font-weight:700;font-size:13px;letter-spacing:.05em}
.nav-back{font-size:11px;color:var(--green-dim);letter-spacing:.04em}
.nav-back:hover{color:var(--green)}

/* WRAP */
.wrap{padding:18px 20px;max-width:1400px;margin:0 auto}
.page-title{font-size:17px;font-weight:700;color:#b8d4b8;margin-bottom:3px}
.page-sub{font-size:10px;color:var(--green-dim);line-height:1.8;margin-bottom:18px}

/* PROMPT */
.prompt-row{display:flex;gap:10px;margin-bottom:16px;align-items:flex-end}
.prompt-inp{flex:1;background:var(--bg2);border:1px solid var(--border);border-radius:8px;color:var(--text);padding:9px 14px;font-size:13px;font-family:monospace;outline:none}
.prompt-inp:focus{border-color:var(--green)}
.run-btn{background:#14532d;color:var(--green);border:1px solid #166534;border-radius:8px;padding:9px 22px;font-size:13px;font-family:monospace;cursor:pointer;white-space:nowrap;transition:background .15s}
.run-btn:hover{background:#166534}
.run-btn:disabled{opacity:.4;cursor:not-allowed}

/* TWO COL GRID */
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}

/* AGENT CARD */
.acard{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px;display:flex;flex-direction:column;gap:10px}
.acard-a{border-color:rgba(0,230,118,.25)}
.acard-b{border-color:rgba(59,130,246,.25)}

.acard-hdr{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px}
.acard-name{font-size:12px;font-weight:700;letter-spacing:.06em}
.acard-meta{font-size:10px;color:var(--muted)}

/* EZ row */
.ez-row{display:flex;gap:10px}
.ez-box{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:6px 10px;flex:1}
.ez-val{font-size:22px;font-weight:700}
.ez-lbl{font-size:8px;color:var(--green-dim);text-transform:uppercase;letter-spacing:.5px}

/* bar */
.bar-lbl{font-size:8px;color:var(--green-dim);margin-bottom:3px}
.bar-track{background:var(--bg3);border:1px solid var(--border);border-radius:3px;height:7px;overflow:hidden}
.bar-fill{height:100%;width:0%;transition:width .2s;border-radius:3px}
.fill-a{background:var(--green)}
.fill-b{background:var(--blue)}

/* response */
.resp{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:10px 12px;font-size:11px;color:#b8d4b8;line-height:1.75;white-space:pre-wrap;overflow-y:auto;min-height:120px;max-height:220px}
.resp-ph{color:var(--green-dim);font-style:italic}
.tok-lbl{font-size:9px;color:var(--green-dim)}

/* CDC */
.cdc-panel{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:16px;display:none}
.cdc-panel.show{display:block}
.cdc-title{font-size:11px;font-weight:700;color:#b8d4b8;letter-spacing:.06em;text-transform:uppercase;margin-bottom:14px}
.cdc-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:14px}
.cdc-card{background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:12px}
.cdc-ct{font-size:8px;color:var(--green-dim);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}
.badge{display:inline-block;padding:4px 12px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.04em}
.b-ordered{background:rgba(0,200,83,.15);color:#00c853;border:1px solid #00c85333}
.b-causal{background:rgba(255,193,7,.12);color:#ffc107;border:1px solid #ffc10733}
.b-concurrent{background:rgba(255,152,0,.12);color:#ff9800;border:1px solid #ff980033}
.b-inconsistent{background:rgba(244,67,54,.15);color:#f44336;border:1px solid #f4433633}
.tau-vis-lbl{font-size:8px;color:var(--green-dim);margin-bottom:3px}
.tau-vis-row{display:flex;gap:8px;align-items:center;margin-bottom:5px}
.tau-vis-name{font-size:8px;color:var(--muted);width:50px}
.tau-vis-track{flex:1;background:var(--bg3);height:12px;border-radius:3px;overflow:hidden}
.formula{font-size:10px;color:var(--muted);background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:10px;line-height:1.8}
.formula .fh{color:var(--amber)}
.formula .fg{color:var(--green)}
.formula .fb{color:var(--blue)}
</style>
</head>
<body>

<nav>
  <a href="/" class="nav-logo">⚙ AGENT CLAW</a>
  <a href="/temporal" class="nav-back">← Eigenzeit & Drift</a>
  <span style="margin-left:auto;font-size:10px;color:var(--green-dim)">TIME DILATION DEMO · §3–§4</span>
</nav>

<div class="wrap">
  <div class="page-title">TIME DILATION DEMO</div>
  <div class="page-sub">
    §3 Agent Proper Time (Eigenzeit) · §3.3 Frame-Transformation · §3.4 Causal-Dilation Clock<br>
    Gleicher Prompt → zwei Agenten → verschiedene γ-Faktoren → sichtbare Eigenzeit-Divergenz
  </div>

  <div class="prompt-row">
    <input type="text" id="prompt" class="prompt-inp"
      value="Was bedeutet Eigenzeit in einem KI-Agentensystem, und warum entstehen Synchronisationsprobleme?"
      placeholder="Prompt…">
    <button id="run-btn" class="run-btn" onclick="runDemo()">▸ STARTEN</button>
  </div>

  <div class="grid2">
    <!-- A -->
    <div class="acard acard-a">
      <div class="acard-hdr">
        <span class="acard-name" style="color:var(--green)">▸ AGENT A — SCHNELL</span>
        <span class="acard-meta">gemma3:latest · γ = 1.0 · max 80 Token</span>
      </div>
      <div class="ez-row">
        <div class="ez-box"><div class="ez-val" id="tau-a" style="color:var(--green)">0.0</div><div class="ez-lbl">Eigenzeit τ_A</div></div>
        <div class="ez-box"><div class="ez-val" id="ms-a" style="color:var(--green-dim);font-size:16px">0ms</div><div class="ez-lbl">Wall Clock</div></div>
      </div>
      <div><div class="bar-lbl">Eigenzeit-Akkumulation</div><div class="bar-track"><div class="bar-fill fill-a" id="bar-a"></div></div></div>
      <div class="resp resp-ph" id="resp-a">Wartet auf Start…</div>
      <div class="tok-lbl" id="tok-a">0 Token · 0ms</div>
    </div>

    <!-- B -->
    <div class="acard acard-b">
      <div class="acard-hdr">
        <span class="acard-name" style="color:var(--blue)">▸ AGENT B — TIEF</span>
        <span class="acard-meta">gemma4:e4b · γ = 3.5 · max 350 Token</span>
      </div>
      <div class="ez-row">
        <div class="ez-box"><div class="ez-val" id="tau-b" style="color:var(--blue)">0.0</div><div class="ez-lbl">Eigenzeit τ_B</div></div>
        <div class="ez-box"><div class="ez-val" id="ms-b" style="color:var(--muted);font-size:16px">0ms</div><div class="ez-lbl">Wall Clock</div></div>
      </div>
      <div><div class="bar-lbl">Eigenzeit-Akkumulation</div><div class="bar-track"><div class="bar-fill fill-b" id="bar-b"></div></div></div>
      <div class="resp resp-ph" id="resp-b">Wartet auf Start…</div>
      <div class="tok-lbl" id="tok-b">0 Token · 0ms</div>
    </div>
  </div>

  <!-- CDC -->
  <div class="cdc-panel" id="cdc-panel">
    <div class="cdc-title">CAUSAL-DILATION CLOCK — §3.4 <span id="cdc-badge" style="margin-left:10px"></span></div>
    <div class="cdc-grid">
      <div class="cdc-card" id="cdc-a">—</div>
      <div class="cdc-card" id="cdc-b">—</div>
      <div class="cdc-card" id="cdc-t">—</div>
    </div>
    <div id="cdc-vis" style="margin-bottom:12px"></div>
    <div class="formula" id="cdc-formula">—</div>
  </div>
</div>

<script>
const API = '/api/lab/dilation/run';
const TAU_MAX = 500;
let running = false;
const S = {tokA:0,tokB:0,tauA:0,tauB:0,msA:0,msB:0,rA:'',rB:''};

function reset(){
  Object.assign(S,{tokA:0,tokB:0,tauA:0,tauB:0,msA:0,msB:0,rA:'',rB:''});
  ['a','b'].forEach(x=>{
    document.getElementById('resp-'+x).textContent='Wartet auf Start…';
    document.getElementById('resp-'+x).className='resp resp-ph';
    document.getElementById('tau-'+x).textContent='0.0';
    document.getElementById('ms-'+x).textContent='0ms';
    document.getElementById('tok-'+x).textContent='0 Token · 0ms';
    document.getElementById('bar-'+x).style.width='0%';
  });
  document.getElementById('cdc-panel').classList.remove('show');
}

function onTok(e){
  const a=e.agent==='A';
  if(a){S.tokA++;S.tauA=e.tau;S.msA=e.ms;S.rA+=e.tok}
  else {S.tokB++;S.tauB=e.tau;S.msB=e.ms;S.rB+=e.tok}
  const x=a?'a':'b';
  const r=document.getElementById('resp-'+x);
  if(r.classList.contains('resp-ph')){r.className='resp';r.textContent=''}
  r.textContent=(a?S.rA:S.rB);r.scrollTop=9999;
  document.getElementById('tau-'+x).textContent=e.tau.toFixed(1);
  document.getElementById('ms-'+x).textContent=e.ms+'ms';
  document.getElementById('tok-'+x).textContent=(a?S.tokA:S.tokB)+' Token · '+e.ms+'ms';
  document.getElementById('bar-'+x).style.width=Math.min(100,e.tau/TAU_MAX*100)+'%';
}

function relInfo(r){
  const m={
    'causally_and_temporally_ordered':{cls:'b-ordered',lbl:'ORDERED'},
    'causally_ordered_temporally_divergent':{cls:'b-causal',lbl:'CAUSAL DRIFT'},
    'concurrent_with_divergence':{cls:'b-concurrent',lbl:'CONCURRENT DRIFT'},
    'inconsistent':{cls:'b-inconsistent',lbl:'INCONSISTENT'},
  };
  return m[r]||{cls:'b-concurrent',lbl:r};
}

function onCDC(e){
  const ri=relInfo(e.relation);
  const mx=Math.max(e.tau_a,e.tau_b,1);
  document.getElementById('cdc-panel').classList.add('show');
  document.getElementById('cdc-badge').innerHTML=`<span class="badge ${ri.cls}">${ri.lbl}</span>`;
  document.getElementById('cdc-a').innerHTML=`<div class="cdc-ct">Agent A · γ=${e.gamma_a}</div><div style="font-size:20px;font-weight:700;color:var(--green)">${e.tau_a.toFixed(1)}</div><div style="font-size:8px;color:var(--green-dim)">Eigenzeit τ_A · ${S.tokA} Tok · ${S.msA}ms Wall</div>`;
  document.getElementById('cdc-b').innerHTML=`<div class="cdc-ct">Agent B · γ=${e.gamma_b}</div><div style="font-size:20px;font-weight:700;color:var(--blue)">${e.tau_b.toFixed(1)}</div><div style="font-size:8px;color:var(--green-dim)">Eigenzeit τ_B · ${S.tokB} Tok · ${S.msB}ms Wall</div>`;
  document.getElementById('cdc-t').innerHTML=`<div class="cdc-ct">Frame-Transformation B→A</div><div style="font-size:18px;font-weight:700;color:var(--amber)">${e.tau_b_in_a.toFixed(1)} τ</div><div style="font-size:8px;color:var(--green-dim)">γ_ratio = ${e.gamma_ratio.toFixed(3)}<br>Δτ = ${e.delta_tau.toFixed(1)}</div>`;
  document.getElementById('cdc-vis').innerHTML=`
    <div class="tau-vis-lbl">Eigenzeit-Vergleich (normiert auf max)</div>
    ${['A','B','B→A'].map((lbl,i)=>{
      const v=i===0?e.tau_a:i===1?e.tau_b:e.tau_b_in_a;
      const col=i===0?'var(--green)':i===1?'var(--blue)':'var(--amber)';
      return `<div class="tau-vis-row"><span class="tau-vis-name" style="color:${col}">${lbl}</span><div class="tau-vis-track"><div style="width:${(v/mx*100).toFixed(1)}%;background:${col};height:100%"></div></div><span style="font-size:9px;color:var(--green-dim);margin-left:6px">${v.toFixed(1)}</span></div>`;
    }).join('')}`;
  document.getElementById('cdc-formula').innerHTML=
    `<span class="fh">Φ(τ_B, B→A)</span> = τ_B × (γ_A / γ_B) = <span class="fb">${e.tau_b.toFixed(1)}</span> × <span class="fh">${e.gamma_ratio.toFixed(3)}</span> = <span class="fh">${e.tau_b_in_a.toFixed(1)}</span><br>`+
    `CDC Relation: <span class="fh">${ri.lbl}</span> — `+
    (e.delta_tau>5
      ?`Δτ = ${e.delta_tau.toFixed(1)} überschreitet Toleranz → <span style="color:#ff9800">Koordinationsfehler möglich (§4.3)</span>`
      :`Δτ = ${e.delta_tau.toFixed(1)} innerhalb Toleranz → <span style="color:var(--green)">kohärente Koordination</span>`);
}

async function runDemo(){
  if(running)return;
  const p=document.getElementById('prompt').value.trim();
  if(!p)return;
  running=true; reset();
  const btn=document.getElementById('run-btn');
  btn.disabled=true; btn.textContent='▸ LÄUFT…';
  try{
    const res=await fetch(API,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:p})});
    if(!res.ok){alert('Fehler '+res.status);return}
    const reader=res.body.getReader();const dec=new TextDecoder();let buf='';
    while(true){
      const{done,value}=await reader.read();if(done)break;
      buf+=dec.decode(value,{stream:true});
      const lines=buf.split('\n');buf=lines.pop();
      for(const l of lines){
        if(!l.startsWith('data: '))continue;
        try{const ev=JSON.parse(l.slice(6));
          if(ev.type==='tok')onTok(ev);
          else if(ev.type==='cdc')onCDC(ev);
          else if(ev.type==='err')console.warn('Agent',ev.agent,ev.msg);
        }catch(e){}
      }
    }
  }catch(e){console.error(e);alert('Fehler: '+e.message)}
  finally{running=false;btn.disabled=false;btn.textContent='▸ STARTEN'}
}

document.getElementById('prompt').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();runDemo()}
});
</script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse)
async def dilation_demo_page():
    return HTMLResponse(_PAGE_HTML)
