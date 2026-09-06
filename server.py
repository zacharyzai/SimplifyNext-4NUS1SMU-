"""server.py -- FastAPI backend exposing the LangGraph agent to a web front
end. OWNED BY MEMBER 1.

The graph already exists in graph.py with run_agent(), resume_after_approval(),
save_constraint() and load_constraints(). This file is a thin HTTP seam over
that: it does not contain any orchestration logic of its own.

RULE FOR THE WHOLE FILE: no endpoint may ever return a 500. The demo must
never show a stack trace to a judge, so every handler is wrapped to return
a 200 with {"ok": false, "error": str} on any failure instead.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from shared.schema import AWS_PROFILE, AWS_REGION
import graph as agent_graph

app = FastAPI(title="Agentic Cash-Flow Copilot for Bob")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request bodies ----------------------------------------------------------

class RunRequest(BaseModel):
    user_id: str
    thread_id: str
    inputs: Optional[Dict[str, Any]] = None


class ApproveRequest(BaseModel):
    thread_id: str
    approved: bool


class AnswerRequest(BaseModel):
    thread_id: str
    answers: Dict[str, str]


class ResetRequest(BaseModel):
    thread_id: str


# --- Shared response shaping --------------------------------------------------

def _state_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    """A curated, front-end-sized view of CashFlowState -- the full state
    dict is large and mostly internal bookkeeping; the UI only needs these
    fields to render the left-hand conversation column.
    """
    return {
        "user_id": state.get("user_id"),
        "forecast": state.get("forecast"),
        "materiality_flag": state.get("materiality_flag"),
        "tier_level": state.get("tier_level"),
        "chosen_plan": state.get("chosen_plan"),
        "rejected_plans": state.get("rejected_plans"),
        "explanation": state.get("explanation"),
        "user_constraints": state.get("user_constraints", []),
        "loop_count": state.get("loop_count", 0),
    }


def _run_response(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": True,
        "state_summary": _state_summary(state),
        "trace": state.get("trace", []),
        "awaiting_approval": bool(state.get("awaiting_approval", False)),
        "open_questions": state.get("open_questions", []),
    }


def _error_response(exc: Exception) -> Dict[str, Any]:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# --- Endpoints ----------------------------------------------------------------

@app.post("/run")
def run(req: RunRequest) -> Dict[str, Any]:
    try:
        state = agent_graph.run_agent(
            user_id=req.user_id, thread_id=req.thread_id, inputs=req.inputs,
        )
        return _run_response(state)
    except Exception as exc:  # noqa: BLE001 -- the demo must never show a 500
        return _error_response(exc)


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/run/{run_id}/stream")
def run_stream(run_id: str, user_id: str = "bob-001", raw_text: Optional[str] = None) -> StreamingResponse:
    """SSE version of /run: one 'trace' event per node as the graph
    actually executes it, then a final 'done' event in the same shape
    /run returns (so the front end's existing renderConversation() works
    unchanged for it).

    GET, not POST -- EventSource can only issue GET with no body, so
    run_id doubles as the thread_id and any raw input rides in the query
    string instead of a JSON payload.
    """
    def event_stream():
        config = {"configurable": {"thread_id": run_id}}
        inputs: Dict[str, Any] = {"user_id": user_id, "loop_count": 0}
        if raw_text:
            inputs["raw_text"] = raw_text
        try:
            for update in agent_graph.graph.stream(inputs, config=config, stream_mode="updates"):
                for node_update in update.values():
                    for record in node_update.get("trace", []) or []:
                        yield _sse("trace", record)
            state = agent_graph.graph.get_state(config).values
            yield _sse("done", _run_response(state))
        except Exception as exc:  # noqa: BLE001 -- a dropped stream must not 500, just stop
            yield _sse("error", _error_response(exc))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/approve")
def approve(req: ApproveRequest) -> Dict[str, Any]:
    try:
        state = agent_graph.resume_after_approval(req.thread_id, req.approved)
        return _run_response(state)
    except Exception as exc:  # noqa: BLE001
        return _error_response(exc)


@app.post("/answer")
def answer(req: AnswerRequest) -> Dict[str, Any]:
    """Feed Bob's real answer back into the replanning loop.

    clarify_node is not in the graph's interrupt_before list, so a thread
    is never actually left paused waiting on this endpoint -- the loop
    already resolves (up to MAX_REPLAN_LOOPS) inside a single run_agent()
    call, simulating its own answers. So there is nothing to "resume"
    here; what Bob's real answer needs is a NEW run_agent() call on this
    thread, with the answer fed in as raw_text -- the same field the
    trace-panel UI's "earnings message" box uses. START always leads to
    ingestion, so this naturally re-enters at ingestion_node (the
    clarify_node -> ingestion_node edge), giving Bob's answer a real
    chance to change the forecast/plan instead of just being logged.
    """
    try:
        config = {"configurable": {"thread_id": req.thread_id}}
        snapshot = agent_graph.graph.get_state(config)

        trace_record = {
            "node": "answer_endpoint",
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checked": list(req.answers.keys()),
            "found": {"answers": req.answers},
            "concluded": f"Received Bob's answer(s) to {len(req.answers)} open question(s); re-running with it as new ingestion input.",
            "confidence": "HIGH",
            "degraded": False,
        }
        agent_graph.graph.update_state(config, {"trace": [trace_record]})

        if snapshot.next:
            # Genuinely paused -- that's a Tier 2 approval halt, which
            # /approve resumes, not /answer. Nothing more to do than log
            # the answer above; return the (still-paused) state as-is.
            state = agent_graph.graph.get_state(config).values
        else:
            combined_answer = " ".join(v for v in req.answers.values() if v).strip()
            state = agent_graph.run_agent(
                user_id=snapshot.values.get("user_id", ""),
                thread_id=req.thread_id,
                inputs={"raw_text": combined_answer} if combined_answer else None,
            )
        return _run_response(state)
    except Exception as exc:  # noqa: BLE001
        return _error_response(exc)


@app.get("/trace/{thread_id}")
def get_trace(thread_id: str) -> Dict[str, Any]:
    try:
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = agent_graph.graph.get_state(config)
        return {"ok": True, "trace": snapshot.values.get("trace", [])}
    except Exception as exc:  # noqa: BLE001
        return _error_response(exc)


@app.get("/health")
def health() -> Dict[str, Any]:
    """Reports whether AWS Bedrock credentials resolve, WITHOUT crashing if
    they do not -- every module in this project must run to completion
    with zero AWS credentials present (Rules Sheet #7), and this endpoint
    is the one place that actually checks whether they happen to be there.
    """
    bedrock_ok = False
    try:
        import boto3  # imported lazily so the whole server still starts without boto3-adjacent env quirks
        session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        creds = session.get_credentials()
        bedrock_ok = creds is not None and creds.get_frozen_credentials().access_key is not None
    except Exception:  # noqa: BLE001 -- absence of credentials must never crash /health
        bedrock_ok = False
    return {"ok": True, "bedrock": bedrock_ok}


@app.post("/demo/reset")
def demo_reset(req: ResetRequest) -> Dict[str, Any]:
    """Clear a thread's checkpointed state so the two-run persistence demo
    (and any other demo) can be re-run live on stage without restarting
    the server.
    """
    try:
        agent_graph._checkpointer.delete_thread(req.thread_id)
        return {"ok": True, "cleared_thread_id": req.thread_id}
    except Exception as exc:  # noqa: BLE001
        return _error_response(exc)


# --- Static front end ---------------------------------------------------------
# Mounted last so it does not shadow the API routes above. static/index.html
# (Step 5) is served from / once it exists; until then this mount is a no-op
# 404 for anything under /static, which is fine -- the API routes still work.
app.mount("/", StaticFiles(directory="static", html=True), name="static")


# --- Module swap notes ---------------------------------------------------------
# When a teammate's real module is ready, swap it into graph.py's imports --
# server.py never changes, because it only ever talks to graph.run_agent()
# and friends, never to a worker module directly.
#
#   Member 2 (forecast):    graph.py: `from modules.forecast import forecast_node`
#                            replaces `from stubs import ... forecast_node`
#   Member 3 (ingestion):   graph.py: `from modules.ingestion import ingestion_node`
#                            (ingestion.py will need a thin `ingestion_node(state)`
#                            wrapper around its real functions -- same shape stubs.py uses)
#   Member 4 (materiality): graph.py: `from modules.materiality import gate_node`
#   Member 4 (planner):     graph.py: `from modules.planner import planner_node`
#
# Keep the stub imports commented alongside the real ones as a fallback: if a
# teammate's module is late or broken, reverting one import line keeps the
# demo alive on stubs rather than blocking on their work.

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
