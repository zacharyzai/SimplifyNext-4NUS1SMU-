"""graph.py -- the orchestration layer. OWNED BY MEMBER 1.

Nobody but this file imports LangGraph (Rules Sheet #6). Every worker
module is a pure function: plain dicts in, plain dicts out.

Step 2 (this file, for now): build the whole graph on STUB nodes, wired
with plain static edges, before any real module exists. Persistence is
proven here at hour 6 rather than assembled at hour 40 -- see
stubs.py and shared/schema.py for the contract this is built against.
"""
from typing import Any, Dict, List

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from shared.schema import CashFlowState
from stubs import ingestion_node, forecast_node, gate_node, planner_node

# --- Build the graph -----------------------------------------------------
# Swap seam: to bring in a real module later, replace the imported function
# above with the real one (e.g. `from modules.forecast import forecast_node`)
# -- see the swap_in_real_modules() notes in server.py. The node names and
# state contract never change, so this is a one-line edit per module.

_builder = StateGraph(CashFlowState)
_builder.add_node("ingestion", ingestion_node)
_builder.add_node("forecast", forecast_node)
_builder.add_node("gate", gate_node)
_builder.add_node("planner", planner_node)

_builder.add_edge(START, "ingestion")
_builder.add_edge("ingestion", "forecast")
_builder.add_edge("forecast", "gate")
_builder.add_edge("gate", "planner")
_builder.add_edge("planner", END)

_checkpointer = InMemorySaver()
graph = _builder.compile(checkpointer=_checkpointer)


# --- Public API ------------------------------------------------------------

def run_agent(user_id: str, thread_id: str, inputs: Dict[str, Any] = None) -> CashFlowState:
    """Invoke the graph once on the given thread, returning the final state.

    Reusing the same thread_id across calls resumes from whatever was
    checkpointed last time (e.g. user_constraints saved via
    save_constraint()) rather than starting cold.
    """
    inputs = dict(inputs or {})
    inputs.setdefault("user_id", user_id)
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(inputs, config=config)


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
    THREAD = "bob-001"

    print("=== Run 1 ===")
    state_1 = run_agent(user_id="bob-001", thread_id=THREAD)
    for record in state_1["trace"]:
        print(" ", record["node"], "->", record["concluded"])

    print("\nSaving a constraint after run 1: 'never touch the phone bill'")
    save_constraint(THREAD, "never touch the phone bill")

    print("\n=== Run 2 (same thread_id) ===")
    state_2 = run_agent(user_id="bob-001", thread_id=THREAD)
    print("user_constraints after run 2:", state_2["user_constraints"])

    assert "never touch the phone bill" in state_2["user_constraints"], (
        "State Persistence is broken: the constraint saved after run 1 "
        "did not survive into run 2 on the same thread_id."
    )
    print("\nSTATE PERSISTENCE PROVEN: the constraint survived across two "
          "separate graph invocations on the same thread.")
