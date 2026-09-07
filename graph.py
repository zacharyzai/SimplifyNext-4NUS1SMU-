"""graph.py -- the orchestration layer. OWNED BY MEMBER 1.

Nobody but this file imports LangGraph (Rules Sheet #6). Every worker
module is a pure function: plain dicts in, plain dicts out.

Step 2: build the whole graph on STUB nodes, wired with plain static
edges, before any real module exists -- see stubs.py.

Step 3 (this revision): the static edges become data-dependent routing.
Three routing decisions turn the pipeline into an agent:
  - route_after_forecast: low confidence -> ask + re-ingest, bounded by
    MAX_REPLAN_LOOPS. Deterministic Python, not the model.
  - route_after_gate: the materiality gate not firing ends the run in
    silence -- a first-class outcome, not an error path.
  - the Tier 2 approval halt: an irreversible/material action pauses the
    graph (interrupt_before=["execute_node"]) until Bob approves it.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from shared.schema import CashFlowState, MAX_REPLAN_LOOPS, MAX_TOTAL_CLARIFY_ROUNDS, TIER_APPROVAL
# from stubs import forecast_node  # SWAPPED OUT 2026-09-05 -- kept here as the
# fallback import; uncomment this and comment out the real import below if
# modules/forecast.py ever regresses and blocks the demo.
from modules.forecast import forecast_node  # Member 2's real forecast engine (docs/SWAP_STATUS.md)
# from stubs import ingestion_node  # SWAPPED OUT 2026-09-06 -- kept here as the
# fallback import; uncomment this and comment out the real import below if
# modules/ingestion.py ever regresses and blocks the demo.
from modules.ingestion import ingestion_node  # Member 3's real ingestion engine (docs/SWAP_STATUS.md)
# from stubs import gate_node, planner_node  # SWAPPED OUT 2026-09-06 -- kept
# here as the fallback import; uncomment and comment out the real imports
# below if modules/materiality.py or modules/planner.py ever regress.
from modules.materiality import gate_node  # Member 4's real materiality gate (docs/SWAP_STATUS.md)
from modules.planner import planner_node  # Member 4's real planner (docs/SWAP_STATUS.md)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- New orchestration nodes (owned here, not in stubs.py -- these are
# control-flow nodes, not worker-module stand-ins) --------------------------

def clarify_node(state: CashFlowState) -> Dict[str, Any]:
    """Ask the open questions raised by low forecast confidence, then pause.

    "clarify" is a real interrupt_before point (see graph.compile() below)
    -- a real request genuinely halts the graph here until a human answer
    arrives. server.py's /answer injects that answer into `raw_text` via
    graph.update_state() and resumes with invoke(None, ...); this node
    itself never fabricates or feeds back an answer on Bob's behalf.

    CASHFLOW_DEMO_SIMULATE_ANSWERS=1 is set ONLY by this file's own
    __main__ demo block, purely to annotate the trace with an example of
    what an answer might look like -- even when set, it never writes
    anything back into ingestion. A real server process never sets this.
    """
    questions = state.get("open_questions", [])
    loop_count = state.get("loop_count", 0) + 1
    # total_clarify_rounds (shared/schema.py, flagged extension) is NEVER
    # reset by a fresh run the way loop_count is -- see MAX_TOTAL_CLARIFY_ROUNDS's
    # docstring there. It only increments here, in the one place a question
    # is actually asked, so it accumulates across the thread's entire life
    # regardless of how many separate stream()/run_agent() calls land on it.
    total_clarify_rounds = state.get("total_clarify_rounds", 0) + 1
    found: Dict[str, Any] = {}
    if os.environ.get("CASHFLOW_DEMO_SIMULATE_ANSWERS") == "1":
        found["example_answer_for_demo_only"] = {
            q: "e.g. \"logged a Sunday dinner shift, made about $95\"" for q in questions
        }
    trace = {
        "node": "clarify_node",
        "ts": _now_iso(),
        "checked": questions,
        "found": found,
        "concluded": (
            f"Confidence was not HIGH, so asked {len(questions)} targeted "
            f"question(s) instead of guessing (replan loop {loop_count}/{MAX_REPLAN_LOOPS} "
            f"this run, {total_clarify_rounds}/{MAX_TOTAL_CLARIFY_ROUNDS} total for this thread)."
        ),
        "confidence": "LOW",
        "degraded": True,
    }
    return {"loop_count": loop_count, "total_clarify_rounds": total_clarify_rounds, "trace": [trace]}


def forecast_node_with_replan_trace(state: CashFlowState) -> Dict[str, Any]:
    """Thin wrapper around modules.forecast.forecast_node -- adds exactly
    ONE extra trace entry, owned HERE (not in modules/forecast.py, which
    stays purely about the numbers, never about the replanning decision),
    explaining -- whenever confidence isn't HIGH -- which of the two
    replanning caps (if either) is about to stop route_after_forecast from
    sending the run back to clarify. It reads the exact same fields
    (loop_count, total_clarify_rounds) that route_after_forecast checks
    immediately after this node runs, so the explanation can never
    silently drift out of sync with the actual routing decision.

    Without this, a judge reading the trace after a thread hits the
    thread-level cap would see forecast_engine report non-HIGH confidence
    and then jump straight to materiality_gate with no clarify step at
    all -- indistinguishable from a bug, unless something says why.
    """
    result = forecast_node(state)
    confidence = (result.get("forecast") or {}).get("confidence")
    if confidence and confidence != "HIGH":
        loop_count = state.get("loop_count", 0)
        total_clarify_rounds = state.get("total_clarify_rounds", 0)
        note = None
        if total_clarify_rounds >= MAX_TOTAL_CLARIFY_ROUNDS:
            note = (
                f"Thread-level clarify cap reached ({total_clarify_rounds}/"
                f"{MAX_TOTAL_CLARIFY_ROUNDS} total rounds across this thread's "
                f"whole life) -- proceeding with the best available data instead "
                f"of asking again, even though this run's own loop_count "
                f"({loop_count}) hasn't hit the per-run cap ({MAX_REPLAN_LOOPS})."
            )
        elif loop_count >= MAX_REPLAN_LOOPS:
            note = (
                f"Per-run replan cap reached (loop_count {loop_count}/{MAX_REPLAN_LOOPS} "
                f"this run) -- proceeding with the best available data instead of "
                f"asking again this run (thread total so far: {total_clarify_rounds}/"
                f"{MAX_TOTAL_CLARIFY_ROUNDS})."
            )
        if note:
            result = dict(result)
            result["trace"] = list(result.get("trace", [])) + [{
                "node": "forecast_engine",
                "ts": _now_iso(),
                "checked": ["loop_count vs MAX_REPLAN_LOOPS", "total_clarify_rounds vs MAX_TOTAL_CLARIFY_ROUNDS"],
                "found": {
                    "loop_count": loop_count, "MAX_REPLAN_LOOPS": MAX_REPLAN_LOOPS,
                    "total_clarify_rounds": total_clarify_rounds, "MAX_TOTAL_CLARIFY_ROUNDS": MAX_TOTAL_CLARIFY_ROUNDS,
                },
                "concluded": note,
                "confidence": confidence,
                "degraded": True,
            }]
    return result


def execute_node(state: CashFlowState) -> Dict[str, Any]:
    """Carry out the chosen plan. Only ever reached for a Tier 2 action,
    and only after the interrupt_before pause below has been resumed with
    approval -- see resume_after_approval().
    """
    plan = state.get("chosen_plan", {})
    trace = {
        "node": "execute_node",
        "ts": _now_iso(),
        "checked": ["approval status"],
        "found": {"plan_id": plan.get("id")},
        "concluded": f"Executed the approved action: {plan.get('name', 'unnamed plan')}.",
        "confidence": "HIGH",
        "degraded": False,
    }
    return {"awaiting_approval": False, "trace": [trace]}


# --- Routing functions -- deterministic Python reads state and picks the
# edge. The model never makes any of these decisions. ------------------------

def route_after_forecast(state: CashFlowState) -> str:
    """Low confidence must trigger action, not a shrug.

    Two independent caps gate the "ask again" branch: loop_count (reset to
    0 by every fresh run -- see graph.run_agent()/server.py's
    /run/{id}/stream) bounds one continuous back-and-forth, and
    total_clarify_rounds (never reset by a fresh run) bounds the thread's
    entire life. Either one being exhausted is enough to stop asking and
    fall back to the best available data -- see
    forecast_node_with_replan_trace() for the trace explaining which.
    """
    if (state["forecast"]["confidence"] != "HIGH"
            and state.get("loop_count", 0) < MAX_REPLAN_LOOPS
            and state.get("total_clarify_rounds", 0) < MAX_TOTAL_CLARIFY_ROUNDS):
        return "gather_more"
    return "materiality_gate"


def route_after_gate(state: CashFlowState) -> str:
    """Silence is a first-class outcome, not a failure to produce output."""
    if not state["materiality_flag"]["fire"]:
        return "END"
    return "planner"


def route_after_planner(state: CashFlowState) -> str:
    """Only a Tier 2 (irreversible/material) action needs to halt for
    approval before it can execute. Tier 0/1 actions never touch
    execute_node at all -- there is nothing to approve.
    """
    if state.get("tier_level") == TIER_APPROVAL:
        return "execute"
    return "END"


# --- Build the graph ---------------------------------------------------------
# Swap seam: to bring in a real module later, replace the imported function
# above with the real one (e.g. `from modules.forecast import forecast_node`)
# -- see the swap_in_real_modules() notes in server.py. The node names and
# state contract never change, so this is a one-line edit per module.

_builder = StateGraph(CashFlowState)
_builder.add_node("ingestion", ingestion_node)
_builder.add_node("forecast", forecast_node_with_replan_trace)
_builder.add_node("clarify", clarify_node)
_builder.add_node("gate", gate_node)
_builder.add_node("planner", planner_node)
_builder.add_node("execute_node", execute_node)

_builder.add_edge(START, "ingestion")
_builder.add_edge("ingestion", "forecast")
_builder.add_conditional_edges(
    "forecast", route_after_forecast,
    {"gather_more": "clarify", "materiality_gate": "gate"},
)
_builder.add_edge("clarify", "ingestion")  # closes the replanning cycle
_builder.add_conditional_edges(
    "gate", route_after_gate,
    {"END": END, "planner": "planner"},
)
_builder.add_conditional_edges(
    "planner", route_after_planner,
    {"execute": "execute_node", "END": END},
)
_builder.add_edge("execute_node", END)

_checkpointer = InMemorySaver()
# "clarify" pauses the graph for a real human answer (server.py's /answer
# resumes it via update_state + invoke(None, ...)); "execute_node" pauses
# for Tier 2 approval (resume_after_approval() below).
graph = _builder.compile(checkpointer=_checkpointer, interrupt_before=["clarify", "execute_node"])


# --- Public API ------------------------------------------------------------

def run_agent(user_id: str, thread_id: str, inputs: Dict[str, Any] = None) -> CashFlowState:
    """Invoke the graph once on the given thread, returning the final state
    (or the paused state, if a Tier 2 action halted for approval).

    Reusing the same thread_id across calls resumes from whatever was
    checkpointed last time (e.g. user_constraints saved via
    save_constraint()) rather than starting cold.
    """
    inputs = dict(inputs or {})
    inputs.setdefault("user_id", user_id)
    inputs.setdefault("loop_count", 0)
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(inputs, config=config)


def resume_after_approval(thread_id: str, approved: bool) -> CashFlowState:
    """Resume a graph halted on a Tier 2 action.

    approved=True runs execute_node to completion. approved=False never
    executes anything -- instead the rejected action is appended to
    user_constraints, so the agent will not propose it again (this is the
    same mechanism save_constraint() uses).
    """
    config = {"configurable": {"thread_id": thread_id}}
    if approved:
        return graph.invoke(None, config=config)

    snapshot = graph.get_state(config)
    chosen_plan = snapshot.values.get("chosen_plan", {})
    description = chosen_plan.get("description") or chosen_plan.get("name", "the proposed action")
    rejection = f"never {description}"
    constraints: List[str] = list(snapshot.values.get("user_constraints", []))
    if rejection not in constraints:
        constraints.append(rejection)
    trace = {
        "node": "resume_after_approval",
        "ts": _now_iso(),
        "checked": ["Bob's approval response"],
        "found": {"approved": False},
        "concluded": f"Bob rejected the proposal. Recorded constraint: '{rejection}'.",
        "confidence": "HIGH",
        "degraded": False,
    }
    graph.update_state(config, {
        "user_constraints": constraints,
        "awaiting_approval": False,
        "trace": [trace],
    })
    return graph.get_state(config).values


def save_constraint(thread_id: str, constraint: str) -> None:
    """Append a constraint (e.g. "never touch the phone bill") to the
    checkpointed state for this thread, so it survives into future runs.
    """
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    existing: List[str] = list(snapshot.values.get("user_constraints", []))
    if constraint not in existing:
        existing.append(constraint)
    graph.update_state(config, {"user_constraints": existing})


def load_constraints(thread_id: str) -> List[str]:
    """Read back the constraints persisted for this thread."""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    return list(snapshot.values.get("user_constraints", []))


if __name__ == "__main__":
    # Purely cosmetic (see clarify_node's docstring) -- annotates its trace
    # with an example answer. A real server process never sets this.
    os.environ["CASHFLOW_DEMO_SIMULATE_ANSWERS"] = "1"

    def _print_trace(state: CashFlowState) -> None:
        for record in state.get("trace", []):
            print(f"   [{record['node']}] {record['concluded']}")

    def _drive_through_clarify(thread_id: str, answer_text: str) -> CashFlowState:
        """Resume a thread paused at "clarify" by injecting a real answer
        into raw_text and resuming -- exactly what server.py's /answer
        does -- repeating up to MAX_REPLAN_LOOPS+1 times in case the
        answer doesn't raise confidence enough to clear the loop in one
        step. This replaces the old single run_agent() call now that
        clarify is a genuine interrupt_before pause instead of something
        clarify_node used to loop through internally on faked answers.
        """
        config = {"configurable": {"thread_id": thread_id}}
        for _ in range(MAX_REPLAN_LOOPS + 1):
            if "clarify" not in graph.get_state(config).next:
                break
            graph.update_state(config, {"raw_text": answer_text})
            graph.invoke(None, config=config)
        return graph.get_state(config).values

    # --- Demo 0: the original two-run persistence proof (still valid) ------
    print("=== Demo 0: State Persistence across two runs on one thread ===")
    PERSIST_THREAD = "bob-persistence-demo"
    run_agent(user_id="bob-001", thread_id=PERSIST_THREAD)
    save_constraint(PERSIST_THREAD, "never touch the phone bill")
    state_2 = run_agent(user_id="bob-001", thread_id=PERSIST_THREAD)
    assert "never touch the phone bill" in state_2["user_constraints"]
    print("   Constraint survived across two invocations:", state_2["user_constraints"])

    # --- Demo (a): sub-HIGH confidence triggers the bounded replan cycle ----
    # NOTE: exactly how many loops fire depends on whether forecast_node is
    # the stub (scripted to reach HIGH after exactly 1 loop) or the real
    # modules/forecast.py (which stays at whatever confidence the data
    # actually supports -- with ingestion still a stub that never
    # incorporates clarify_node's answers, that means it correctly runs to
    # the MAX_REPLAN_LOOPS ceiling instead of "resolving" after one pass).
    # Either way the invariant that matters is: it looped at least once,
    # and it never exceeded the ceiling.
    # Real ingestion + real materiality means the three scenarios below can
    # no longer be selected by a magic user_id string (that was the STUB's
    # trick) -- they're driven by what raw_delivery_rows actually says,
    # same as a real thread would be. A thin/short earnings history (a) is
    # what genuinely produces a real shortfall at non-HIGH confidence.
    def _recent_daily_rows(num_days, daily_gross_cents, end_offset_days=1):
        end_date = datetime.now(timezone.utc).date() - timedelta(days=end_offset_days)
        return [
            {
                "date": (end_date - timedelta(days=i)).isoformat(),
                "platform": "Grab", "start_hour": 18,
                "gross_cents": str(daily_gross_cents / 100),
                "tip_cents": "0.00", "platform_fee_cents": "0.00",
                "trips": 4, "hours": 3.0,
            }
            for i in range(num_days)
        ]

    print("\n=== Demo (a): replanning loop (real shortfall -> TIER_NOTIFY) ===")
    thin_history = _recent_daily_rows(num_days=3, daily_gross_cents=5000)
    # A real date is required -- extract_from_text's system prompt (per
    # the "never guess" rule) has the LLM transcribe date: null when none
    # is stated, and normalise_records correctly rejects an undated row.
    # Without a date, this answer would ALWAYS hit the "didn't land" path
    # even with a working LLM, which would demonstrate the failure path
    # only -- including one lets the demo prove the landing path too.
    _answer_date = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()
    ANSWER_TEXT = (
        f"On Grab, I also worked a Sunday dinner shift on {_answer_date}: "
        f"gross $105 minus $10 fee, 4 trips, 3 hours."
    )
    state_a = run_agent(
        user_id="bob-001", thread_id="bob-demo-a",
        inputs={"raw_delivery_rows": thin_history, "raw_source": "partner_statement"},
    )
    print("   [paused] awaiting a real answer at clarify -- this is a genuine")
    print("   interrupt now, not clarify_node looping on a faked one internally.")
    assert "clarify" in graph.get_state({"configurable": {"thread_id": "bob-demo-a"}}).next, (
        "expected the first pass to genuinely pause at clarify, not loop through it"
    )
    state_a = _drive_through_clarify("bob-demo-a", ANSWER_TEXT)
    _print_trace(state_a)
    assert 1 <= state_a["loop_count"] <= MAX_REPLAN_LOOPS, (
        f"expected the replan loop to fire at least once and never exceed "
        f"the {MAX_REPLAN_LOOPS} ceiling, got loop_count={state_a['loop_count']}"
    )
    assert not state_a.get("awaiting_approval", False)
    assert not graph.get_state({"configurable": {"thread_id": "bob-demo-a"}}).next, (
        "expected the run to fully settle (reach gate/planner/END), not stay paused"
    )
    print(f"   Path: forecast(<HIGH) -> [pause at clarify -> real answer -> ingestion -> "
          f"forecast(...)] -> [repeat until HIGH or ceiling] -> gate -> planner -> END")
    print(f"   loop_count={state_a['loop_count']} (ceiling={MAX_REPLAN_LOOPS}), tier_level={state_a['tier_level']}")

    # --- Demo (b): the gate does not fire -> ends early in silence ---------
    # Genuinely healthy, recent, 14-day partner-statement history: enough
    # daily income that the fixed demo bill calendar (forecast.py's
    # DEMO_BILLS) never dips the balance negative -- nothing material and
    # nothing stale, so both real checks stay quiet regardless of exactly
    # which confidence bucket the real forecast lands in. It's driven
    # through clarify the same way (a)/(c) are (harmlessly a no-op if it
    # never needed clarify at all -- the driving loop just breaks
    # immediately) rather than hand-tuning the synthetic data to force
    # HIGH confidence on the first pass, which real deterministic Python
    # is free to not grant.
    print("\n=== Demo (b): materiality gate stays silent ===")
    healthy_history = _recent_daily_rows(num_days=14, daily_gross_cents=12000, end_offset_days=1)
    state_b = run_agent(
        user_id="bob-001", thread_id="bob-demo-b",
        inputs={"raw_delivery_rows": healthy_history, "raw_source": "partner_statement"},
    )
    state_b = _drive_through_clarify("bob-demo-b", ANSWER_TEXT)
    _print_trace(state_b)
    assert state_b["materiality_flag"]["fire"] is False
    # gate_node explicitly sets chosen_plan: None (not omits it) when it
    # stays silent, precisely so a stale plan from an earlier run on this
    # thread can't linger checkpointed -- so the key legitimately exists
    # with value None here; checking for that value, not mere absence.
    assert state_b.get("chosen_plan") is None, "planner should never have run"
    print("   Path: forecast(HIGH, no shortfall) -> gate(silent) -> END")

    # --- Demo (c): a Tier 2 action halts, then resumes after approval ------
    # Reuses Demo (a)'s thin/shortfall history, but pre-seeds constraints
    # excluding every reversible play in the library -- the only survivor
    # is defer_phone_bill (irreversible + third-party), which forces a
    # real TIER_APPROVAL through classify_action rather than a scripted one.
    print("\n=== Demo (c): Tier 2 action halts for approval, then resumes ===")
    for reversible_play_constraint in [
        "never shift into saturday dinner",
        "never work a sunday dinner shift",
        "never take extra trips",
        "never move money to a buffer",
        "never pause the streaming subscription",
        "never touch bike servicing",  # not "never defer..." -- collides with defer_phone_bill's "defer"
        "never log a shift",
    ]:
        save_constraint("bob-demo-c", reversible_play_constraint)
    state_c = run_agent(
        user_id="bob-001", thread_id="bob-demo-c",
        inputs={"raw_delivery_rows": thin_history, "raw_source": "partner_statement"},
    )
    state_c = _drive_through_clarify("bob-demo-c", ANSWER_TEXT)
    _print_trace(state_c)
    assert state_c.get("awaiting_approval") is True, "Tier 2 action should halt awaiting approval"
    print("   Halted: awaiting_approval =", state_c["awaiting_approval"])
    state_c_resumed = resume_after_approval("bob-demo-c", approved=True)
    _print_trace(state_c_resumed)
    assert state_c_resumed.get("awaiting_approval") is False
    print("   Resumed after approval: awaiting_approval =", state_c_resumed["awaiting_approval"])

    # --- Demo (d): thread-level clarify cap stops indefinite re-asking -----
    # Reproduces the exact gap this fix closes: repeated FRESH restarts
    # (run_agent() with real inputs, exactly what a new raw_text/scenario
    # via server.py's /run/{id}/stream does) each reset loop_count back to
    # 0 -- so the per-run cap (MAX_REPLAN_LOOPS=2) never gets a chance to
    # fire, since we only ever resume ONCE per restart before restarting
    # again. total_clarify_rounds is untouched by these resets, so it
    # climbs by exactly 1 per cycle regardless -- proving it's the NEW
    # thread-level cap doing the stopping here, not the pre-existing
    # per-run one (which this test deliberately never lets trigger).
    print("\n=== Demo (d): thread-level clarify cap (repeated fresh restarts) ===")
    CAP_THREAD = "bob-demo-cap"
    cap_config = {"configurable": {"thread_id": CAP_THREAD}}
    cycles = 0
    for cycles in range(1, MAX_TOTAL_CLARIFY_ROUNDS + 3):
        run_agent(
            user_id="bob-001", thread_id=CAP_THREAD,
            inputs={"raw_delivery_rows": thin_history, "raw_source": "partner_statement"},
        )
        if "clarify" not in graph.get_state(cap_config).next:
            break
        graph.invoke(None, config=cap_config)  # resume exactly once, then restart fresh again
    cap_state = graph.get_state(cap_config).values
    _print_trace(cap_state)
    print(f"   Stopped after {cycles} fresh-restart cycles.")
    print(f"   total_clarify_rounds={cap_state.get('total_clarify_rounds')} "
          f"(cap={MAX_TOTAL_CLARIFY_ROUNDS}), loop_count={cap_state.get('loop_count')} "
          f"(per-run cap={MAX_REPLAN_LOOPS})")
    assert cap_state.get("total_clarify_rounds", 0) >= MAX_TOTAL_CLARIFY_ROUNDS, (
        "expected the thread-level cap to actually be reached"
    )
    assert cap_state.get("loop_count", 0) < MAX_REPLAN_LOOPS, (
        "this test's whole point is proving the THREAD cap stopped it while the "
        "per-run cap never got the chance to -- if loop_count reached its own "
        "cap here, this test no longer isolates what it claims to"
    )
    assert not graph.get_state(cap_config).next, (
        "expected the thread to fully settle (reach gate/planner/END) once the "
        "thread-level cap fires, not still be paused waiting on a question"
    )
    print("   Thread-level cap fired while the per-run cap never did -- "
          "fell back to the best available data instead of continuing to ask.")

    print("\nALL THREE ROUTING PATHS DEMONSTRATED.")
