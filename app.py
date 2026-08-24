# -*- coding: utf-8 -*-
"""
FFL Autonomous Bug Reporting - web demo.
Serves the demo site and streams live agent progress over SSE.
Run: uvicorn app:app --port 8787
"""
import asyncio, json, uuid
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from auto_pipeline import run_auto

app = FastAPI(title="FFL Agentic Bug Reporting")
RUNS: dict[str, asyncio.Queue] = {}


@app.post("/api/report")
async def report(payload: dict):
    run_id = uuid.uuid4().hex[:8]
    q: asyncio.Queue = asyncio.Queue()
    RUNS[run_id] = q

    async def emit(ev):
        await q.put(ev)

    asyncio.create_task(run_auto(payload, emit))
    return {"runId": run_id}


@app.get("/api/stream/{run_id}")
async def stream(run_id: str):
    q = RUNS.get(run_id)
    if q is None:
        raise HTTPException(404, "unknown run")

    async def gen():
        while True:
            ev = await q.get()
            yield f"data: {json.dumps(ev)}\n\n"
            if ev.get("type") == "done":
                RUNS.pop(run_id, None)
                break

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/register")
async def get_register():
    return json.load(open("register.json", encoding="utf-8"))


app.mount("/", StaticFiles(directory="static", html=True), name="static")
