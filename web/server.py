"""Tripwire Arena: the same attack, side by side, unprotected vs Tripwire, streamed live.

    python -m web.server        # then open http://127.0.0.1:8080
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse

from demo.agent import AGENT_MODEL, attack_succeeded, build_agent, task_completed
from demo.scenarios import BY_ID, SCENARIOS
from tripwire import Decider

app = FastAPI()
STATIC = Path(__file__).parent / "static"
decider = Decider()
pending: dict[str, dict] = {}  # run_id -> {"event": Event, "answer": bool}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/scenarios")
def scenarios():
    return {
        "agent_model": AGENT_MODEL,
        "scenarios": [
            {"id": s.id, "title": s.title, "kind": s.kind, "prompt": s.user_prompt, "blurb": s.blurb}
            for s in SCENARIOS
        ],
    }


@app.post("/api/confirm/{run_id}/{answer}")
def confirm(run_id: str, answer: str):
    p = pending.get(run_id)
    if p:
        p["answer"] = answer == "yes"
        p["event"].set()
    return {"ok": bool(p)}


@app.get("/api/run/{scenario_id}/{mode}")
def run(scenario_id: str, mode: str):
    sc = BY_ID[scenario_id]
    q: queue.Queue = queue.Queue()
    run_id = uuid.uuid4().hex

    def emit(kind, data):
        q.put({"kind": kind, **json.loads(json.dumps(data, default=str))})

    def approver(decision) -> bool:
        ev = threading.Event()
        pending[run_id] = {"event": ev, "answer": False}
        emit("confirm_request", {"run_id": run_id, **decision.__dict__})
        ev.wait(timeout=120)
        return pending.pop(run_id)["answer"]

    def worker():
        try:
            agent, world, _, _ = build_agent(
                sc, mode, decider=decider, emit=emit, approver=approver
            )
            result = agent(sc.user_prompt)
            emit("done", {
                "final_text": str(result),
                "attack_succeeded": attack_succeeded(sc, world.side_effects),
                "task_completed": task_completed(sc, world.side_effects),
                "scenario_kind": sc.kind,
            })
        except Exception as exc:
            emit("error", {"message": f"{type(exc).__name__}: {exc}"})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while (item := q.get()) is not None:
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
