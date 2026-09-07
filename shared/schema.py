"""shared/schema.py -- OWNED BY MEMBER 1. Nobody else edits this file.

The contract everyone codes against. Frozen after T+2 (see
HACKATHON_OBJECTIVES.md). If you need a new field, ask Member 1.
"""
from typing import TypedDict, List, Annotated
import operator

# --- Model / infra constants -------------------------------------------------
# Changed from "global.anthropic.claude-haiku-4-5-20251001-v1:0" (2026-09-07)
# -- the "global." cross-region inference profile resolves to an unregioned
# resource ARN that this hackathon account's org-level Service Control
# Policy explicitly denies bedrock:InvokeModel on, independent of IAM
# permissions. The "us." regional inference profile for the same model
# resolves to a region-qualified ARN the SCP does not block -- verified
# working end to end.
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
# Changed from ap-southeast-1 to us-east-1 (2026-09-07) -- the hackathon
# organisers confirmed Bedrock is only enabled for this account in
# us-east-1 / us-east-2 / us-west-2.
AWS_REGION = "us-east-1"
AWS_PROFILE = "workshop"

# --- Permission tiers ---------------------------------------------------------
# The boundary is drawn at "is this reversible and is the blast radius small?"
# The cautious default branch always lands on the higher tier.
TIER_AUTONOMOUS = 0  # reversible, small blast radius -> agent acts, logs it
TIER_NOTIFY = 1      # informational, no action taken
TIER_APPROVAL = 2    # irreversible or material -> agent halts, asks Bob

# --- Replanning ----------------------------------------------------------------
MAX_REPLAN_LOOPS = 2  # hard ceiling on the low-confidence replanning cycle


class CashFlowState(TypedDict, total=False):
    user_id: str

    # Raw input, supplied by the caller (server.py's /run `inputs`), never
    # written by any node -- only read, by ingestion_node. LangGraph strips
    # any state key not declared here before a node ever sees it, so these
    # have to exist in the schema even though they're pure caller input,
    # not something any module computes. All optional; a fresh thread with
    # none of them present falls back to the data/benchmarks.json prior.
    raw_text: str                 # unstructured text/screenshot content for LLM transcription
    raw_delivery_rows: List[dict]  # structured rows for normalise_records()
    raw_source: str                # source label for raw_delivery_rows, e.g. "partner_statement"
    raw_bank_rows: List[dict]      # rows for parse_bank_statement()
    expenses: List[dict]           # outgoings for add_expenses()

    # FLAGGED EXTENSION (not in the original frozen contract, added the same
    # way raw_text etc. were): Bob-stated recurring bills, e.g.
    # {"name": "Netflix", "day_of_month": 28, "amount_cents": 1798}.
    # Accumulated across sessions (checkpointer), read by forecast.py to add
    # to its bill calendar alongside the synthetic DEMO_BILLS.
    recurring_bills: List[dict]

    delivery_log: List[dict]     # Member 3 (ingestion.py)
    ingest_health: dict          # Member 3 (ingestion.py)

    forecast: dict                # Member 2 (forecast.py)
    cells: dict                   # Member 2 -- (platform, weekday, time_block) profile
    data_gaps: List[dict]         # Member 2
    open_questions: List[str]     # Member 2 -> drives the replan loop

    materiality_flag: dict        # Member 4 (materiality.py)
    tier_level: int               # Member 4 (materiality.py)
    candidate_plans: List[dict]   # Member 4 (planner.py)
    rejected_plans: List[dict]    # Member 4 (planner.py)
    chosen_plan: dict             # Member 4 (planner.py)
    explanation: str              # Member 4 (planner.py)

    # `trace` is the one field every module appends to, and appends never
    # overwrite each other's contributions in the same run -- so it is the
    # only key that needs a reducer. operator.add concatenates each node's
    # trace record onto the running list instead of one node clobbering the
    # last. Every other field is a plain "last write wins" state update,
    # because only one owner (module) ever produces it per run.
    trace: Annotated[List[dict], operator.add]

    tool_health: dict             # Member 1 (shared/resilience.py merge_tool_health output)
    loop_count: int               # Member 1 -- bounded by MAX_REPLAN_LOOPS
    awaiting_approval: bool       # Member 1 -- human-in-the-loop gate
    user_constraints: List[str]   # persisted across sessions via the checkpointer


if __name__ == "__main__":
    # Sanity check: the TypedDict constructs and the constants are sane.
    demo_state: CashFlowState = {
        "user_id": "bob-001",
        "trace": [],
        "loop_count": 0,
        "awaiting_approval": False,
        "user_constraints": [],
    }
    assert TIER_AUTONOMOUS < TIER_NOTIFY < TIER_APPROVAL
    assert MAX_REPLAN_LOOPS >= 1
    print("shared/schema.py OK")
    print("CashFlowState demo:", demo_state)
    print("Tiers:", TIER_AUTONOMOUS, TIER_NOTIFY, TIER_APPROVAL)
    print("MAX_REPLAN_LOOPS:", MAX_REPLAN_LOOPS)
