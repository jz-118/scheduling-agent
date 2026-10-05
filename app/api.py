from __future__ import annotations

from dataclasses import asdict
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .agents import SchedulingOrchestrator, clean_query
from .feishu import FeishuBitableClient, FeishuSettings, extract_aily_query, extract_event_query, make_card, result_text, verify_bearer
from .models import ScheduleState
from .rules import serialize_solution
from .store import ScheduleStore
from .ui import PAGE

app = FastAPI(title="Scheduling Agent", version="1.0.0")
orchestrator = SchedulingOrchestrator()
store = ScheduleStore()
feishu_settings = FeishuSettings()
bitable_client = FeishuBitableClient(feishu_settings)


class ScheduleRequest(BaseModel):
    schedule_id: str = "SCH-DEMO-W01"
    week_start: str = "2026-09-28"
    query: str = Field(min_length=1)
    force: bool = False


def _run(request: ScheduleRequest) -> dict:
    state = store.load(request.schedule_id, request.week_start)
    query = clean_query(request.query)
    force = request.force or any(token in query for token in ("强制重排", "强制重新", "重新生成", "重新排班"))
    if not force:
        replay = store.replay(request.schedule_id, query, state.version)
        if replay:
            return replay
    result = orchestrator.run(query, state)
    output = {k: v for k, v in result.items() if k not in {"plan", "solutions"}}
    if "solutions" in result:
        output["solutions"] = [serialize_solution(s) for s in result["solutions"]]
    if result.get("status") == "approved":
        state.version += 1
        state.assignments = result["solutions"][0].assignments
        plan = result.get("plan")
        if plan:
            state.hard_constraints = list(plan.hard_constraints)
            state.soft_constraints = list(plan.soft_constraints)
            output["run_id"] = plan.run_id
        output.update({"version": state.version, "replayed": False})
        store.save(state)
        store.cache(request.schedule_id, query, state.version, output)
    return output


@app.get("/", response_class=HTMLResponse)
def home():
    return PAGE


@app.get("/health")
def health():
    return {"status": "ok", "agents": ["planner", "executor", "checker"], "planner": orchestrator.planner.status()}


@app.post("/schedule/run")
def run_schedule(request: ScheduleRequest):
    return _run(request)


@app.get("/schedule/{schedule_id}")
def get_schedule(schedule_id: str):
    if not store.exists(schedule_id):
        raise HTTPException(404, "schedule not found")
    state = store.load(schedule_id, "")
    return asdict(state)


@app.post("/feishu/aily/webhook")
async def aily_webhook(request: Request, authorization: str | None = Header(default=None)):
    """Synchronous Aily custom-trigger endpoint."""
    if not verify_bearer(authorization, feishu_settings.aily_bearer_token):
        raise HTTPException(401, "invalid bearer token")
    payload = await request.json()
    query = extract_aily_query(payload)
    schedule_id = str(payload.get("schedule_id") or payload.get("data", {}).get("schedule_id") or "FEISHU-DEMO-W01")
    week_start = str(payload.get("week_start") or payload.get("data", {}).get("week_start") or "2026-09-28")
    result = _run(ScheduleRequest(schedule_id=schedule_id, week_start=week_start, query=query, force=bool(payload.get("force", False))))
    return {"status_code": "0", "data": result_text(result), "result": result}


@app.post("/feishu/events")
async def feishu_events(request: Request, background_tasks: BackgroundTasks):
    """Feishu event subscription endpoint, including URL verification and message events."""
    payload = await request.json()
    if payload.get("type") == "url_verification":
        token = payload.get("token")
        if feishu_settings.event_verification_token and token != feishu_settings.event_verification_token:
            raise HTTPException(401, "invalid verification token")
        return {"challenge": payload.get("challenge")}
    received_token = payload.get("header", {}).get("token")
    if feishu_settings.event_verification_token and received_token != feishu_settings.event_verification_token:
        raise HTTPException(401, "invalid verification token")
    event_type = payload.get("header", {}).get("event_type")
    if event_type == "im.message.receive_v1":
        query, message_id = extract_event_query(payload)
        chat_id = str(payload.get("event", {}).get("message", {}).get("chat_id") or "DEMO")
        if not query:
            return {"status": "ignored", "reason": "empty message"}
        background_tasks.add_task(_process_feishu_message, query, message_id, chat_id)
        return {"status": "accepted", "message_id": message_id}
    return {"status": "ignored", "event_type": event_type}


async def _process_feishu_message(query: str, message_id: str | None, chat_id: str) -> None:
    result = _run(ScheduleRequest(schedule_id=f"FEISHU-{chat_id}", week_start="2026-09-28", query=query))
    if message_id and feishu_settings.app_id and feishu_settings.app_secret:
        await bitable_client.reply_message(message_id, make_card(result))


@app.post("/feishu/card/callback")
async def feishu_card_callback(request: Request):
    payload = await request.json()
    action = payload.get("action", {})
    value = action.get("value", {}) if isinstance(action, dict) else {}
    if value.get("action") == "health":
        return {"toast": {"type": "info", "content": "排班服务正常"}}
    return {"toast": {"type": "info", "content": "已收到操作"}}
