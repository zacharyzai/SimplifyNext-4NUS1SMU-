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
from shared.money import cents_from_string
from shared.llm import converse
import graph as agent_graph
from data import demo_scenarios

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
        "recurring_bills": state.get("recurring_bills", []),
        "loop_count": state.get("loop_count", 0),
    }


def _run_response(state: Dict[str, Any], thread_id: str) -> Dict[str, Any]:
    """Shapes a graph state into the JSON the front end renders.

    awaiting_clarify reflects whether the graph is ACTUALLY paused at
    "clarify" right now (checked via graph.get_state().next), not merely
    whether open_questions happens to be non-empty. Now that clarify is a
    real interrupt (graph.py), a run can hit MAX_REPLAN_LOOPS, move on to
    gate/planner, and still have a stale open_questions list sitting in
    state from the last unresolved forecast pass -- without this check the
    UI would keep showing "answer to continue" for a question the graph
    has already stopped waiting on, and a submitted answer would silently
    start a brand new run instead of doing anything useful with it.
    """
    try:
        awaiting_clarify = "clarify" in agent_graph.graph.get_state(
            {"configurable": {"thread_id": thread_id}}
        ).next
    except Exception:  # noqa: BLE001 -- a state-read hiccup must not break the response
        awaiting_clarify = False
    return {
        "ok": True,
        "state_summary": _state_summary(state),
        "trace": state.get("trace", []),
        "awaiting_approval": bool(state.get("awaiting_approval", False)),
        "awaiting_clarify": awaiting_clarify,
        "open_questions": state.get("open_questions", []) if awaiting_clarify else [],
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
        return _run_response(state, req.thread_id)
    except Exception as exc:  # noqa: BLE001 -- the demo must never show a 500
        return _error_response(exc)


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# --- Chat-box intent classification -------------------------------------------
# The free-text box on the front end can now describe either an earnings
# update or a recurring bill to plan around. Same pattern as
# modules/ingestion.py's extract_from_text: the LLM only classifies +
# transcribes fields, Python decides what happens with them and builds every
# user-facing acknowledgement from a deterministic template -- never from
# the model's own words -- so a hallucinated intent can misfire at worst as
# "I didn't understand that", never as a wrong number silently stored.
CLASSIFY_SYSTEM_PROMPT = (
    "Classify this message into exactly one intent: EARNINGS (reporting "
    "income, a shift, or a payout), BILL (describing a recurring bill or "
    "expense to plan around), or OTHER (anything else, including nonsense "
    "or unrelated text). If BILL, also transcribe -- do not estimate or "
    "guess -- name (short label), day_of_month (integer 1-31 it's due "
    "each month, null if not stated), amount (the dollar amount exactly "
    "as written, e.g. \"17.98\", null if not stated). "
    "Return ONLY JSON: {\"intent\": \"EARNINGS\"|\"BILL\"|\"OTHER\", "
    "\"name\": ..., \"day_of_month\": ..., \"amount\": ...}. "
    "No prose, no markdown code fences."
)


def classify_message(text: str) -> Dict[str, Any]:
    """Returns {"intent": ...} at minimum. Falls back to EARNINGS (the
    pre-chat-feature behaviour) whenever the LLM is unavailable or returns
    something unparseable, so a thread never silently drops a message it
    can no longer classify.
    """
    content = converse(CLASSIFY_SYSTEM_PROMPT, text)
    if content is None:
        return {"intent": "EARNINGS"}
    try:
        if content.startswith("```"):
            content = content.strip("`").replace("json\n", "", 1)
        parsed = json.loads(content)
    except (json.JSONDecodeError, AttributeError):
        return {"intent": "EARNINGS"}
    if not isinstance(parsed, dict) or parsed.get("intent") not in ("EARNINGS", "BILL", "OTHER"):
        return {"intent": "EARNINGS"}
    return parsed


def _bill_from_classification(classification: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Builds the {name, day_of_month, amount_cents} shape forecast.py
    expects, or None if the day-of-month or amount is missing/unparseable --
    a bill Python can't act on numerically is never stored half-formed.
    """
    day_of_month = classification.get("day_of_month")
    amount = classification.get("amount")
    if not isinstance(day_of_month, int) or not (1 <= day_of_month <= 31):
        return None
    try:
        amount_cents = cents_from_string(amount)
    except (ValueError, TypeError):
        return None
    name = classification.get("name") or "Recurring bill"
    return {"name": str(name)[:60], "day_of_month": day_of_month, "amount_cents": amount_cents}


@app.get("/run/{run_id}/stream")
def run_stream(run_id: str, user_id: str = "bob-001", raw_text: Optional[str] = None,
                scenario: Optional[str] = None) -> StreamingResponse:
    """SSE version of /run: one 'trace' event per node as the graph
    actually executes it, then a final 'done' event in the same shape
    /run returns (so the front end's existing renderConversation() works
    unchanged for it).

    GET, not POST -- EventSource can only issue GET with no body, so
    run_id doubles as the thread_id and any raw input rides in the query
    string instead of a JSON payload.

    scenario, if given, looks up a named fixture in data/demo_scenarios.py
    and merges its `inputs` in -- this is how the demo/video UI seeds real
    earnings history through a GET request, since raw_delivery_rows has no
    other way to reach this endpoint's query string.
    """
    def event_stream():
        config = {"configurable": {"thread_id": run_id}}
        inputs: Dict[str, Any] = {"user_id": user_id, "loop_count": 0}
        if scenario:
            fixture = demo_scenarios.SCENARIOS.get(scenario)
            if fixture:
                inputs.update(fixture["inputs"])
        chat_ack = None
        if raw_text:
            classification = classify_message(raw_text)
            intent = classification.get("intent")
            if intent == "BILL":
                bill = _bill_from_classification(classification)
                if bill:
                    snapshot = agent_graph.graph.get_state(config)
                    existing = list(snapshot.values.get("recurring_bills") or [])
                    existing.append(bill)
                    agent_graph.graph.update_state(config, {"recurring_bills": existing})
                    chat_ack = (
                        f"Got it — added \"{bill['name']}\" (${bill['amount_cents'] / 100:,.2f}, "
                        f"due day {bill['day_of_month']} of the month). Future forecasts will "
                        f"factor it in."
                    )
                else:
                    chat_ack = (
                        "That sounded like a recurring bill, but I couldn't pick out a clear "
                        "amount and due day — try something like \"$17.98 due on the 28th\"."
                    )
            elif intent == "OTHER":
                chat_ack = (
                    "I can only act on an earnings update or a recurring bill right now — "
                    "try rephrasing as one of those."
                )
            else:
                inputs["raw_text"] = raw_text
        if chat_ack:
            yield _sse("chat", {"role": "assistant", "text": chat_ack})
        try:
            for update in agent_graph.graph.stream(inputs, config=config, stream_mode="updates"):
                for node_update in update.values():
                    # Hitting an interrupt_before point (clarify, or a
                    # Tier 2 approval halt) emits {"__interrupt__": (...)}
                    # in the updates stream -- a tuple of Interrupt
                    # objects, not a node's return dict. Nothing to trace;
                    # the stream naturally ends right after this either way.
                    if not isinstance(node_update, dict):
                        continue
                    for record in node_update.get("trace", []) or []:
                        yield _sse("trace", record)
            state = agent_graph.graph.get_state(config).values
            yield _sse("done", _run_response(state, run_id))
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
        return _run_response(state, req.thread_id)
    except Exception as exc:  # noqa: BLE001
        return _error_response(exc)


@app.post("/answer")
def answer(req: AnswerRequest) -> Dict[str, Any]:
    """Feed Bob's real answer back into the paused replanning loop.

    "clarify" is a genuine interrupt_before pause now (graph.py) -- a
    thread that asked an open question is actually halted there, not
    mid-way through clarify_node looping on a fabricated answer. This
    resumes that EXACT paused run via update_state() + invoke(None, ...)
    rather than starting a fresh run_agent() call, so loop_count and
    everything already computed this run carry forward instead of
    resetting to zero.
    """
    try:
        config = {"configurable": {"thread_id": req.thread_id}}
        snapshot = agent_graph.graph.get_state(config)

        combined_answer = " ".join(v for v in req.answers.values() if v).strip()
        trace_record = {
            "node": "answer_endpoint",
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "checked": [f'Bob\'s answer: "{v}"' for v in req.answers.values() if v] or ["(empty answer)"],
            "found": {"answers": req.answers},
            "concluded": f'Received: "{combined_answer}"' if combined_answer else "Received an empty answer.",
            "confidence": "HIGH",
            "degraded": False,
        }

        if "clarify" in snapshot.next:
            # Genuinely paused waiting on exactly this answer -- inject it
            # into the field ingestion_node reads and resume the SAME run.
            agent_graph.graph.update_state(config, {
                "raw_text": combined_answer,
                "trace": [trace_record],
            })
            state = agent_graph.graph.invoke(None, config=config)
        elif snapshot.next:
            # Paused somewhere else (a Tier 2 approval halt) -- that's
            # /approve's job, not /answer's. Just log the answer.
            agent_graph.graph.update_state(config, {"trace": [trace_record]})
            state = agent_graph.graph.get_state(config).values
        else:
            # Nothing pending on this thread -- no open question to answer
            # (e.g. it resolved or this is a fresh thread). Rather than
            # silently discard Bob's answer, start a run with it as the
            # thread's first real input.
            agent_graph.graph.update_state(config, {"trace": [trace_record]})
            state = agent_graph.run_agent(
                user_id=snapshot.values.get("user_id") or "bob-001",
                thread_id=req.thread_id,
                inputs={"raw_text": combined_answer} if combined_answer else None,
            )
        return _run_response(state, req.thread_id)
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


@app.get("/scenarios")
def scenarios() -> Dict[str, Any]:
    """Lists the named demo fixtures (data/demo_scenarios.py) so the front
    end's scenario selector never hardcodes labels the backend could drift
    from.
    """
    return {
        "ok": True,
        "scenarios": [
            {"id": key, "label": s["label"], "description": s["description"]}
            for key, s in demo_scenarios.SCENARIOS.items()
        ],
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    """Reports whether AWS Bedrock credentials resolve, WITHOUT crashing if
    they do not -- every module in this project must run to completion
    with zero AWS credentials present (Rules Sheet #7), and this endpoint
    is the one place that actually checks whether they happen to be there.

    Also reports which shared/llm.py provider is actually selected
    (LLM_PROVIDER env var) and whether ITS credential is present -- a demo
    running with LLM_PROVIDER=none or a missing GEMINI_API_KEY degrades
    silently (extract_from_text returns [], the clarify loop just runs out
    its answers without ever landing) with no visible signal on stage.
    This is what the front end's readiness badge reads.
    """
    bedrock_ok = False
    try:
        import boto3  # imported lazily so the whole server still starts without boto3-adjacent env quirks
        session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        creds = session.get_credentials()
        bedrock_ok = creds is not None and creds.get_frozen_credentials().access_key is not None
    except Exception:  # noqa: BLE001 -- absence of credentials must never crash /health
        bedrock_ok = False

    import os
    provider = os.getenv("LLM_PROVIDER", "none").lower()
    if provider == "bedrock":
        llm_ready = bedrock_ok
    elif provider == "gemini":
        llm_ready = bool(os.getenv("GEMINI_API_KEY"))
    else:
        llm_ready = False

    return {
        "ok": True,
        "bedrock": bedrock_ok,
        "llm_provider": provider,
        "llm_ready": llm_ready,
    }


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
