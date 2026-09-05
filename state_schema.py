"""state_schema.py -- typed state definition for agent run tracking.

This is deliberately separate from `shared/schema.py::CashFlowState`, which
is the frozen domain-state contract (forecasts, plans, etc.) owned by
Member 1. This file defines the cross-cutting *governance* state every
agentic run should carry -- who is running it, what permission level
applies, what was decided, what was checked, and what went wrong -- so it
can be logged, replayed, and audited independently of domain logic.

TODO: if/when this project wants a single unified state object, merge the
fields below into `CashFlowState` under Member 1's sign-off rather than
importing both separately.
"""
from typing import TypedDict, List, Literal
from datetime import datetime

# Reuse the project's existing permission tiers rather than inventing new ones.
# TODO: import these for real once this module lives alongside shared/schema.py
# from shared.schema import TIER_AUTONOMOUS, TIER_NOTIFY, TIER_APPROVAL
PermissionLevel = Literal["autonomous", "notify", "approval"]


class DataSourceCheck(TypedDict):
    """One record of a data source the agent consulted during a run."""
    source_name: str          # TODO: e.g. "delivery_log", "benchmarks.json", "bedrock_llm"
    truth_level: Literal["authoritative", "inferred"]
    succeeded: bool
    latency_ms: int
    fallback_used: bool       # True if the primary source failed and a fallback path was taken


class ErrorRecord(TypedDict):
    """One record of a failure the agent hit and how it responded."""
    tool_name: str            # TODO: e.g. "fetch_data", "compute", "approve"
    error_type: str           # e.g. "timeout", "auth_error", "malformed_response"
    message: str
    recovered: bool           # True if a fallback/retry resolved it without halting the run


class AgentRunState(TypedDict):
    """Governance state for a single agent run/session.

    Tracks identity, permission scope, the decision reached, and everything
    consulted or that went wrong along the way -- independent of the
    domain-specific forecast/plan state in CashFlowState.
    """

    # Identifies this specific run, for correlating logs/traces/replays.
    # TODO: generate as uuid4() at run start in the orchestrator.
    session_id: str

    # Identifies whose data this run is operating on (maps to CashFlowState.user_id).
    user_id: str

    # The permission tier this run is currently operating under.
    # Drawn at "is this reversible and is the blast radius small?" -- see
    # docs/SYSTEM_DESIGN.md. Defaults to the most restrictive tier until a
    # materiality/tiering decision says otherwise.
    permission_level: PermissionLevel

    # The most recent decision the agent reached (e.g. "notify_shortfall",
    # "stay_silent", "propose_plan:defer_bill_X"). Human-readable, not a code.
    last_decision: str

    # Every data source consulted this run, in order, with outcome.
    # Used to answer "what did the agent actually look at before deciding?"
    data_sources_checked: List[DataSourceCheck]

    # Every error hit this run, whether or not it was recovered from.
    # An empty list is a meaningful signal (clean run), not just an omission.
    errors_encountered: List[ErrorRecord]

    # When this state snapshot was produced -- needed for replay ordering.
    timestamp: str  # ISO 8601, e.g. datetime.utcnow().isoformat()


def new_run_state(session_id: str, user_id: str) -> AgentRunState:
    """Construct a fresh AgentRunState at the most restrictive permission
    tier, with empty tracking lists. Callers narrow permission_level and
    fill in last_decision as the run proceeds.
    """
    return {
        "session_id": session_id,
        "user_id": user_id,
        "permission_level": "approval",  # cautious default -- see docs/SYSTEM_DESIGN.md
        "last_decision": "",
        "data_sources_checked": [],
        "errors_encountered": [],
        "timestamp": datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    demo = new_run_state(session_id="demo-session-001", user_id="bob-001")
    demo["data_sources_checked"].append({
        "source_name": "delivery_log",
        "truth_level": "authoritative",
        "succeeded": True,
        "latency_ms": 42,
        "fallback_used": False,
    })
    demo["last_decision"] = "stay_silent"
    demo["permission_level"] = "notify"
    assert demo["session_id"] == "demo-session-001"
    assert len(demo["data_sources_checked"]) == 1
    print("state_schema.py OK")
    print(demo)
