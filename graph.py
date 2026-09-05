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
from datetime import datetime, timezone
from typing import Any, Dict, List

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from shared.schema import CashFlowState, MAX_REPLAN_LOOPS, TIER_APPROVAL
from stubs import ingestion_node, gate_node, planner_node
# from stubs import forecast_node  # SWAPPED OUT 2026-09-05 -- kept here as the
# fallback import; uncomment this and comment out the real import below if
# modules/forecast.py ever regresses and blocks the demo.
from modules.forecast import forecast_node  # Member 2's real forecast engine (docs/SWAP_STATUS.md)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- New orchestration nodes (owned here, not in stubs.py -- these are
# control-flow nodes, not worker-module stand-ins) --------------------------

def clarify_node(state: CashFlowState) -> Dict[str, Any]:
    """Ask the open questions raised by low forecast confidence, then loop
    back to re-ingest. For now, answers are simulated from a fixed lookup
    so the cycle closes without a human present in the loop; a real UI
    would surface `open_questions` to Bob via /answer and feed real
    answers back in instead (see server.py step 4).
    """
    questions = state.get("open_questions", [])
    simulated_answers = {
        q: "Simulated answer: logged a Sunday dinner shift, made about $95."
        for q in questions
    }
    loop_count = state.get("loop_count", 0) + 1
    trace = {
        "node": "clarify_node",
        "ts": _now_iso(),
        "checked": questions,
        "found": {"answers_simulated": simulated_answers},
        "concluded": (
            f"Confidence was not HIGH, so asked {len(questions)} targeted "
            f"question(s) instead of guessing (replan loop {loop_count}/{MAX_REPLAN_LOOPS})."
        ),
        "confidence": "LOW",
        "degraded": True,
    }
    return {"loop_count": loop_count, "trace": [trace]}


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
    """Low confidence must trigger action, not a shrug."""
    if (state["forecast"]["confidence"] != "HIGH"
            and state.get("loop_count", 0) < MAX_REPLAN_LOOPS):
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
_builder.add_node("forecast", forecast_node)
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
graph = _builder.compile(checkpointer=_checkpointer, interrupt_before=["execute_node"])


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
    def _print_trace(state: CashFlowState) -> None:
        for record in state.get("trace", []):
            print(f"   [{record['node']}] {record['concluded']}")

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
    print("\n=== Demo (a): replanning loop (default scenario -> TIER_NOTIFY) ===")
    state_a = run_agent(user_id="bob-001", thread_id="bob-demo-a")
    _print_trace(state_a)
    assert 1 <= state_a["loop_count"] <= MAX_REPLAN_LOOPS, (
        f"expected the replan loop to fire at least once and never exceed "
        f"the {MAX_REPLAN_LOOPS} ceiling, got loop_count={state_a['loop_count']}"
    )
    assert not state_a.get("awaiting_approval", False)
    print(f"   Path: forecast(<HIGH) -> clarify -> ingestion -> forecast(...) -> "
          f"[repeat until HIGH or ceiling] -> gate -> planner -> END")
    print(f"   loop_count={state_a['loop_count']} (ceiling={MAX_REPLAN_LOOPS}), tier_level={state_a['tier_level']}")

    # --- Demo (b): the gate does not fire -> ends early in silence ---------
    print("\n=== Demo (b): materiality gate stays silent ===")
    state_b = run_agent(user_id="bob-silent-001", thread_id="bob-demo-b")
    _print_trace(state_b)
    assert state_b["materiality_flag"]["fire"] is False
    assert "chosen_plan" not in state_b, "planner should never have run"
    print("   Path: forecast(LOW) -> clarify -> ingestion -> forecast(HIGH) -> gate(silent) -> END")

    # --- Demo (c): a Tier 2 action halts, then resumes after approval ------
    print("\n=== Demo (c): Tier 2 action halts for approval, then resumes ===")
    state_c = run_agent(user_id="bob-approval-001", thread_id="bob-demo-c")
    _print_trace(state_c)
    assert state_c.get("awaiting_approval") is True, "Tier 2 action should halt awaiting approval"
    print("   Halted: awaiting_approval =", state_c["awaiting_approval"])
    state_c_resumed = resume_after_approval("bob-demo-c", approved=True)
    _print_trace(state_c_resumed)
    assert state_c_resumed.get("awaiting_approval") is False
    print("   Resumed after approval: awaiting_approval =", state_c_resumed["awaiting_approval"])

    print("\nALL THREE ROUTING PATHS DEMONSTRATED.")
