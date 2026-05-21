from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/.well-known/agent.json")
async def agent_card(request: Request):
    from backend.agents.a2a_gateway import A2AGatewayAgent
    base = str(request.base_url).rstrip("/")
    return A2AGatewayAgent.agent_card(base)


@router.post("/a2a/tasks/send")
async def a2a_send(body: dict, request: Request):
    conductor = request.app.state.conductor
    gw = conductor.get_agent("a2a:gateway")
    if gw is None:
        return JSONResponse({"error": "gateway not registered"}, status_code=503)

    cdc_msg = gw.wrap_a2a_task(body)
    response = await conductor.dispatch(cdc_msg)
    task_id = body.get("id", cdc_msg.task_id)
    return gw.unwrap_cdc_response(response, task_id)


@router.get("/a2a/tasks/{task_id}")
async def a2a_get_task(task_id: str, request: Request):
    conductor = request.app.state.conductor
    task = conductor.store.get_task(task_id)
    if not task:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"id": task_id, "status": {"state": task.state.value}}
