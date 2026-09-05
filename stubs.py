"""stubs.py -- hardcoded stand-ins for the four worker modules.

OWNED BY MEMBER 1. These let graph.py run end to end before Members 2, 3
and 4 have written a single real line. Swapping a stub for the real thing
later is a one-line import change in graph.py -- see the
swap_in_real_modules() notes in server.py.

Every stub returns plausible hardcoded values plus exactly one trace
record in the shape frozen in shared/schema.py section 6.2 (the shape
every real module must also produce):

    {"node": str, "ts": ISO8601 UTC, "checked": list[str], "found": dict,
     "concluded": str, "confidence": "HIGH"|"MEDIUM"|"LOW", "degraded": bool}
"""
from datetime import datetime, timezone
from typing import Any, Dict

from shared.schema import CashFlowState, TIER_APPROVAL, TIER_NOTIFY


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trace(node: str, checked, found: dict, concluded: str,
           confidence: str, degraded: bool = False) -> dict:
    return {
        "node": node,
        "ts": _now_iso(),
        "checked": checked,
        "found": found,
        "concluded": concluded,
        "confidence": confidence,
        "degraded": degraded,
    }


def ingestion_node(state: CashFlowState) -> Dict[str, Any]:
    """STUB for Member 3's modules/ingestion.py. Writes delivery_log, ingest_health."""
    delivery_log = [
        {"date": "2026-08-29", "platform": "Grab", "time_block": "dinner",
         "gross_cents": 8200, "tip_cents": 500, "platform_fee_cents": 700,
         "net_cents": 8000, "trips": 6, "hours": 4.0,
         "source": "partner_statement", "precision": 1.0},
        {"date": "2026-08-30", "platform": "foodpanda", "time_block": "lunch",
         "gross_cents": 6100, "tip_cents": 200, "platform_fee_cents": 500,
         "net_cents": 5800, "trips": 5, "hours": 3.0,
         "source": "partner_statement", "precision": 1.0},
    ]
    ingest_health = {
        "ok": True, "rows_in": 2, "rows_accepted": 2, "rows_rejected": 0,
        "rejection_reasons": [], "date_range": ["2026-08-29", "2026-08-30"],
        "sources_seen": ["partner_statement"], "weighted_precision": 1.0,
        "days_since_last_observation": 6, "degraded": False,
    }
    trace = _trace(
        node="ingestion_node (STUB)",
        checked=["stub Weekly Partner Statement (2 rows)"],
        found={"rows_accepted": 2, "rows_rejected": 0},
        concluded="Loaded 2 stub earnings records across Grab and foodpanda.",
        confidence="HIGH",
        degraded=False,
    )
    return {
        "delivery_log": delivery_log,
        "ingest_health": ingest_health,
        "trace": [trace],
    }


def forecast_node(state: CashFlowState) -> Dict[str, Any]:
    """STUB for Member 2's modules/forecast.py. Writes forecast, cells, data_gaps, open_questions.

    Confidence is driven by loop_count so the replanning cycle has something
    real to demonstrate: the first pass through is LOW (not enough evidence
    yet), and once clarify_node has asked its questions and looped back,
    the second pass reports HIGH. A real forecast module derives this from
    actual data quality (see modules/forecast.py step 4); the stub just
    needs to exercise the same routing decision.
    """
    loop_count = state.get("loop_count", 0)
    if loop_count == 0:
        confidence = "LOW"
        confidence_reason = "STUB: only 2 days of stub history on the first pass."
        open_questions = ["STUB: You worked Sunday dinner once. Roughly what did you make on that shift?"]
        cells = {
            "Grab|5|dinner": {"median_net_per_hour_cents": 1940, "observations": 11, "status": "KNOWN"},
            "Grab|6|dinner": {"median_net_per_hour_cents": None, "observations": 1, "status": "UNKNOWN"},
        }
        data_gaps = [
            {"platform": "Grab", "weekday": 6, "time_block": "dinner",
             "observations": 1, "why_it_matters": "STUB: falls inside the next 7 days."},
        ]
    else:
        confidence = "HIGH"
        confidence_reason = "STUB: confidence raised after the replanning loop answered the outstanding question."
        open_questions = []
        cells = {
            "Grab|5|dinner": {"median_net_per_hour_cents": 1940, "observations": 11, "status": "KNOWN"},
            "Grab|6|dinner": {"median_net_per_hour_cents": 2100, "observations": 3, "status": "KNOWN"},
        }
        data_gaps = []

    forecast = {
        "shortfall_date": "2026-09-13",
        "shortfall_amount_cents": 11200,
        "worst_balance_cents": -11200,
        "confidence": confidence,
        "confidence_reason": confidence_reason,
    }
    trace = _trace(
        node="forecast_node (STUB)",
        checked=["stub earnings history", "stub bill calendar"],
        found={"median_daily_cents": 6900, "unknown_cells": len(data_gaps)},
        concluded=f"Projected a stub shortfall of $112.00 on 2026-09-13 ({confidence} confidence).",
        confidence=confidence,
        degraded=(confidence != "HIGH"),
    )
    return {
        "forecast": forecast,
        "cells": cells,
        "data_gaps": data_gaps,
        "open_questions": open_questions,
        "trace": [trace],
    }


def gate_node(state: CashFlowState) -> Dict[str, Any]:
    """STUB for Member 4's modules/materiality.py (gate half). Writes materiality_flag, tier_level.

    Derives a demo scenario from user_id rather than adding an undeclared
    field to the frozen CashFlowState contract (LangGraph strips any key
    not declared in the schema before a node ever sees it). This lets
    graph.py's __main__ drive three different routing paths through the
    same stub: "notify" (default), "silent", and "approval".
    """
    user_id = state.get("user_id", "")
    if "silent" in user_id:
        scenario = "silent"
    elif "approval" in user_id:
        scenario = "approval"
    else:
        scenario = "notify"

    if scenario == "silent":
        materiality_flag = {
            "fire": False, "score": 22, "signature": "shortfall|2026-09-13|stub-silent",
            "alert_type": "shortfall",
            "reasons_for": ["STUB: shortfall is technically nonzero"],
            "reasons_against": ["STUB: $18 dip is inside normal weekly variation and resolves by Friday"],
            "suppressed_by": None,
        }
        tier_level = TIER_NOTIFY
        concluded = "Staying silent: the stub shortfall is inside normal variation, below the materiality threshold."
    elif scenario == "approval":
        materiality_flag = {
            "fire": True, "score": 91, "signature": "shortfall|2026-09-13|stub-approval",
            "alert_type": "shortfall",
            "reasons_for": ["STUB: large shortfall with an irreversible third-party action proposed"],
            "reasons_against": [],
            "suppressed_by": None,
        }
        tier_level = TIER_APPROVAL
        concluded = "Materiality gate fired and the proposed action requires Bob's approval before it can proceed."
    else:  # "notify"
        materiality_flag = {
            "fire": True, "score": 68, "signature": "shortfall|2026-09-13|stub-notify",
            "alert_type": "shortfall",
            "reasons_for": ["STUB: shortfall exceeds one median daily earning"],
            "reasons_against": ["STUB: still 8 days of runway before the due date"],
            "suppressed_by": None,
        }
        tier_level = TIER_NOTIFY
        concluded = "Materiality gate fired: stub shortfall of $112.00 is worth flagging."

    trace = _trace(
        node="gate_node (STUB)",
        checked=["stub materiality score", "stub cooldown check"],
        found={"score": materiality_flag["score"], "threshold": 55},
        concluded=concluded,
        confidence="HIGH",
        degraded=False,
    )
    return {
        "materiality_flag": materiality_flag,
        "tier_level": tier_level,
        "trace": [trace],
    }


def planner_node(state: CashFlowState) -> Dict[str, Any]:
    """STUB for Member 4's modules/planner.py. Writes candidate_plans, rejected_plans, chosen_plan, explanation.

    Also sets awaiting_approval from tier_level: this is the flag graph.py's
    interrupt_before pause on execute_node surfaces to a caller, so a
    Tier 2 action halts visibly rather than silently.
    """
    candidate_plans = [
        {"id": "defer_phone_bill", "name": "Defer the phone bill by 3 days",
         "description": "defer the phone bill by 3 days",
         "impact_cents": 4500, "effort": 1, "reversible": False,
         "affects_third_party": True, "action_type": "request_deferral",
         "closes_gap": False, "score": 42.0,
         "why": "STUB placeholder plan.", "evidence": "STUB evidence."},
    ]
    rejected_plans = [
        {"id": "work_sunday_dinner", "reason": "insufficient_history"},
    ]
    chosen_plan = candidate_plans[0]
    explanation = (
        "STUB: Based on a placeholder forecast, a $112.00 shortfall is expected "
        "around 2026-09-13. Deferring the phone bill by 3 days would close it."
    )
    awaiting_approval = state.get("tier_level") == TIER_APPROVAL
    trace = _trace(
        node="planner_node (STUB)",
        checked=["stub play library (2 plays evaluated)"],
        found={"candidates": 1, "rejected": 1},
        concluded="Chose to propose deferring the phone bill (STUB)."
        + (" Halting for approval before acting." if awaiting_approval else ""),
        confidence="MEDIUM",
        degraded=False,
    )
    return {
        "candidate_plans": candidate_plans,
        "rejected_plans": rejected_plans,
        "chosen_plan": chosen_plan,
        "explanation": explanation,
        "awaiting_approval": awaiting_approval,
        "trace": [trace],
    }


if __name__ == "__main__":
    demo_state: CashFlowState = {"user_id": "bob-001", "trace": [], "loop_count": 0}
    for fn in (ingestion_node, forecast_node, gate_node, planner_node):
        update = fn(demo_state)
        demo_state.update({k: v for k, v in update.items() if k != "trace"})
        demo_state["trace"] = demo_state.get("trace", []) + update["trace"]
        print(f"{fn.__name__} -> {list(update.keys())}")
    print()
    print("Full trace:")
    for record in demo_state["trace"]:
        print(" ", record)
    print("ALL STUB TESTS PASSED")
