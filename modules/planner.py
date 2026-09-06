"""modules/planner.py -- Member 4: Decision Layer, part 2.

Decides WHAT to propose, once materiality.py has decided the agent should
speak. Python decides; the model only narrates an already-made decision
in render_explanation. Options are generated and scored deterministically
here -- the model is never asked to brainstorm plays, because that would
be unbounded, unverifiable, and different every run.

Play IDs deliberately match stubs.py's placeholder plans
(defer_phone_bill, pause_streaming_subscription, work_sunday_dinner) so
the already-verified persistence demo keeps working unchanged once this
real module is swapped in.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.money import format_cents
from shared.resilience import resilient_call
from shared.schema import TIER_APPROVAL, TIER_NOTIFY
from modules.materiality import classify_action

_CONSTRAINT_STOPWORDS = {
    "never", "touch", "the", "a", "an", "do", "not", "don't", "dont",
    "touching", "please", "always", "avoid", "no", "my", "again",
}


def _constraint_excludes_play(constraint, play_name):
    """Same whole-word overlap approach as stubs.py and materiality.py's
    _conflicts -- kept duplicated rather than cross-imported so each
    module stays a fully standalone, independently-runnable file.
    """
    subject_words = {w for w in constraint.lower().replace("'", " ").split() if w not in _CONSTRAINT_STOPWORDS and len(w) > 2}
    play_words = set(play_name.lower().replace(",", " ").split())
    return bool(subject_words & play_words)


def _cell(cells, platform, weekday, time_block):
    return cells.get((platform, weekday, time_block), {"status": "UNKNOWN", "median_net_per_hour_cents": None, "observations": 0})


def _has_shortfall(forecast):
    return bool(forecast.get("shortfall_date"))


def _has_thin_cell(cells):
    return any(c.get("status") == "UNKNOWN" for c in cells.values())


def _shift_impact(forecast, profile, cells, user_constraints):
    cell = _cell(cells, "Grab", 5, "dinner")
    return (cell.get("median_net_per_hour_cents") or 0) * 4


def _sunday_impact(forecast, profile, cells, user_constraints):
    cell = _cell(cells, "Grab", 6, "dinner")
    return (cell.get("median_net_per_hour_cents") or 0) * 3


def _extra_trips_impact(forecast, profile, cells, user_constraints):
    median_daily = profile.get("median_daily_cents", 0)
    avg_trip_value = median_daily // 6 if median_daily else 0
    return avg_trip_value * 5


def _buffer_impact(forecast, profile, cells, user_constraints):
    return 2000


def _defer_bill_impact(forecast, profile, cells, user_constraints):
    return 4500


def _pause_subscription_impact(forecast, profile, cells, user_constraints):
    return 1800


def _defer_servicing_impact(forecast, profile, cells, user_constraints):
    return 3000


def _log_shift_impact(forecast, profile, cells, user_constraints):
    return 0  # informational -- its value is unlocking a future recommendation


PLAY_LIBRARY = [
    {
        "id": "shift_to_saturday_dinner",
        "name": "Shift 4 hours into Saturday dinner on Grab",
        "applies_when": lambda forecast, profile, cells, user_constraints: _has_shortfall(forecast),
        "estimate_impact_cents": _shift_impact,
        "reversible": True, "affects_third_party": False, "effort": 2,
        "action_type": "shift_schedule", "requires_cells": [("Grab", 5, "dinner")],
    },
    {
        "id": "work_sunday_dinner",
        "name": "Work a Sunday dinner shift on Grab",
        "applies_when": lambda forecast, profile, cells, user_constraints: _has_shortfall(forecast),
        "estimate_impact_cents": _sunday_impact,
        "reversible": True, "affects_third_party": False, "effort": 2,
        "action_type": "shift_schedule", "requires_cells": [("Grab", 6, "dinner")],
    },
    {
        "id": "extra_trips_before_shortfall",
        "name": "Take 5 extra trips before the shortfall date",
        "applies_when": lambda forecast, profile, cells, user_constraints: _has_shortfall(forecast),
        "estimate_impact_cents": _extra_trips_impact,
        "reversible": True, "affects_third_party": False, "effort": 2,
        "action_type": "extra_trips", "requires_cells": [],
    },
    {
        "id": "move_to_buffer",
        "name": "Move a small buffer amount into a separate account now",
        "applies_when": lambda forecast, profile, cells, user_constraints: _has_shortfall(forecast),
        "estimate_impact_cents": _buffer_impact,
        "reversible": True, "affects_third_party": False, "effort": 1,
        "action_type": "move_to_buffer", "requires_cells": [],
    },
    {
        "id": "defer_phone_bill",
        "name": "Defer the phone bill by 3 days",
        "applies_when": lambda forecast, profile, cells, user_constraints: _has_shortfall(forecast),
        "estimate_impact_cents": _defer_bill_impact,
        "reversible": False, "affects_third_party": True, "effort": 1,
        "action_type": "request_deferral", "requires_cells": [],
    },
    {
        "id": "pause_streaming_subscription",
        "name": "Pause the streaming subscription for one cycle",
        "applies_when": lambda forecast, profile, cells, user_constraints: _has_shortfall(forecast),
        "estimate_impact_cents": _pause_subscription_impact,
        "reversible": True, "affects_third_party": False, "effort": 1,
        "action_type": "pause_subscription", "requires_cells": [],
    },
    {
        "id": "defer_bike_servicing",
        "name": "Defer non-essential bike servicing by two weeks",
        "applies_when": lambda forecast, profile, cells, user_constraints: _has_shortfall(forecast),
        "estimate_impact_cents": _defer_servicing_impact,
        "reversible": True, "affects_third_party": False, "effort": 1,
        "action_type": "defer_maintenance", "requires_cells": [],
    },
    {
        "id": "log_a_shift",
        "name": "Log tonight's shift to fill in a thin data cell",
        "applies_when": lambda forecast, profile, cells, user_constraints: _has_thin_cell(cells),
        "estimate_impact_cents": _log_shift_impact,
        "reversible": True, "affects_third_party": False, "effort": 1,
        "action_type": "ask_for_data", "requires_cells": [],
    },
]


def _evidence_for(play, cells):
    if not play["requires_cells"]:
        return "Not cell-dependent."
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    parts = []
    for platform, weekday, time_block in play["requires_cells"]:
        cell = cells.get((platform, weekday, time_block), {"status": "UNKNOWN"})
        if cell.get("status") == "KNOWN":
            parts.append(
                f"{weekday_names[weekday]} {time_block} on {platform} has paid a median of "
                f"{format_cents(cell['median_net_per_hour_cents'])}/hr across {cell.get('observations', 0)} shifts."
            )
        else:
            parts.append("insufficient history")
    return " ".join(parts)


def generate_plans(forecast, profile, cells, user_constraints):
    """THE CELL EVIDENCE RULE: if a play requires a cell that's UNKNOWN, it
    is rejected with reason "insufficient_history" and never appears in
    candidate_plans. A play that tells Bob to work Sunday dinner must be
    able to cite what Sunday dinner has actually paid him -- a
    recommendation that can't cite its basis is withheld, not softened.
    """
    # .get(..., 0) only covers a MISSING key -- an UNKNOWN-confidence
    # forecast (modules/forecast.py) sets this key present but None, which
    # would otherwise crash the ">" comparison below.
    shortfall_amount_cents = forecast.get("shortfall_amount_cents") or 0
    candidates = []
    rejected = []

    for play in PLAY_LIBRARY:
        if not play["applies_when"](forecast, profile, cells, user_constraints):
            rejected.append({"id": play["id"], "reason": "not_applicable"})
            continue

        unknown_cell = next(
            (key for key in play["requires_cells"] if cells.get(key, {}).get("status") != "KNOWN"),
            None,
        )
        if unknown_cell is not None:
            rejected.append({"id": play["id"], "reason": "insufficient_history"})
            continue

        conflicting_constraint = next(
            (c for c in user_constraints if _constraint_excludes_play(c, play["name"])),
            None,
        )
        if conflicting_constraint is not None:
            rejected.append({"id": play["id"], "reason": "user_constraint"})
            continue

        impact_cents = play["estimate_impact_cents"](forecast, profile, cells, user_constraints)

        if shortfall_amount_cents > 0:
            score = (impact_cents / shortfall_amount_cents) * 100 - (play["effort"] * 10)
        else:
            score = -(play["effort"] * 10)
        if play["reversible"]:
            score += 15
        score = round(score, 1)

        candidates.append({
            "id": play["id"],
            "name": play["name"],
            "impact_cents": impact_cents,
            "effort": play["effort"],
            "reversible": play["reversible"],
            "affects_third_party": play["affects_third_party"],
            "action_type": play["action_type"],
            "closes_gap": impact_cents >= shortfall_amount_cents > 0,
            "score": score,
            "why": f"Estimated to recover {format_cents(impact_cents)} toward a {format_cents(shortfall_amount_cents)} shortfall.",
            "evidence": _evidence_for(play, cells),
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:3], rejected


def choose_plan(candidate_plans):
    """The ONLY plan-selection logic in the entire system -- lives here in
    Python so it can be tested, never delegated to the model.
    """
    return candidate_plans[0] if candidate_plans else None

from shared.llm import converse

RENDER_SYSTEM_PROMPT = (
    "You are given a decision that has already been made and figures that "
    "have already been calculated. Restate them faithfully in 3 short "
    "sentences at roughly secondary-school reading level. Do not perform "
    "arithmetic. Do not suggest alternatives. Do not change any number you "
    "are given."
)


def _template_explanation(chosen_plan, forecast):
    # .get(key, default) only covers a MISSING key -- an UNKNOWN-confidence
    # forecast (modules/forecast.py) sets these keys present but None (e.g.
    # when the chosen play, like log_a_shift, doesn't itself depend on a
    # shortfall existing), which "or" catches and a bare default wouldn't.
    shortfall_amount_cents = forecast.get("shortfall_amount_cents") or 0
    shortfall_date = forecast.get("shortfall_date") or "an upcoming date"
    return (
        f"You're projected to run short by {format_cents(shortfall_amount_cents)} "
        f"around {shortfall_date}. "
        f"I'd suggest: {chosen_plan['name']}, which could recover about "
        f"{format_cents(chosen_plan['impact_cents'])}. {chosen_plan['why']}"
    )


def _call_llm_for_explanation(chosen_plan, forecast):
    shortfall_amount_cents = forecast.get("shortfall_amount_cents") or 0
    shortfall_date = forecast.get("shortfall_date") or "an upcoming date"
    user_text = (
        f"Chosen plan: {chosen_plan['name']}. "
        f"Estimated impact: {format_cents(chosen_plan['impact_cents'])}. "
        f"Projected shortfall: {format_cents(shortfall_amount_cents)} "
        f"around {shortfall_date}. "
        f"Why: {chosen_plan['why']}"
    )
    text = converse(RENDER_SYSTEM_PROMPT, user_text)
    if text is None:
        raise RuntimeError("no LLM provider available")
    return text


def render_explanation(chosen_plan, forecast, trace_records):
    """Sends the ALREADY-CHOSEN plan and ALREADY-CALCULATED figures to
    the LLM (via shared.llm.converse, whichever provider is configured)
    for a 3-sentence explanation. The system prompt is a REQUEST, not a
    guarantee -- so after the call, every money figure sent in is checked
    against the response verbatim. If even one is missing or altered, the
    model's output is discarded and the deterministic template is used
    instead. This verification step is what turns "we told it not to
    change numbers" into "we know it didn't."
    """
    if chosen_plan is None:
        return "No plan is currently proposed."

    figures_to_check = [format_cents(forecast.get("shortfall_amount_cents") or 0), format_cents(chosen_plan["impact_cents"])]
    template = _template_explanation(chosen_plan, forecast)

    result = resilient_call(
        _call_llm_for_explanation,
        chosen_plan, forecast,
        retries=1, fallback=lambda: template, tool_name="llm_render_explanation",
    )

    if not result["ok"]:
        return template

    candidate_text = result["value"]
    for figure in figures_to_check:
        if figure not in candidate_text:
            print(f"WARNING: figure {figure!r} missing from model output -- discarding, using template fallback")
            return template

    return candidate_text

def build_trace(candidate_plans, rejected, chosen_plan, tool_health=None, user_constraints=None):
    """checked lists EVERY play evaluated, including rejected ones with
    their reason -- rejected plays are some of the best material in the
    trace panel because they show the agent considered and discarded
    alternatives, not that it only knows one trick.
    """
    checked = []
    for play in PLAY_LIBRARY:
        rejection = next((r for r in rejected if r["id"] == play["id"]), None)
        if rejection:
            if rejection["reason"] == "user_constraint" and user_constraints:
                matched = next((c for c in user_constraints if _constraint_excludes_play(c, play["name"])), None)
                if matched:
                    checked.append(f"{play['id']}: rejected (user_constraint: violates '{matched}')")
                else:
                    checked.append(f"{play['id']}: rejected (user_constraint)")
            else:
                checked.append(f"{play['id']}: rejected ({rejection['reason']})")
        elif any(c["id"] == play["id"] for c in candidate_plans):
            checked.append(f"{play['id']}: candidate")
        else:
            checked.append(f"{play['id']}: not evaluated")

    if chosen_plan:
        concluded = f"Chose to propose: {chosen_plan['name']} (score {chosen_plan['score']})."
    elif candidate_plans:
        concluded = "Candidates were generated, but none was chosen."
    else:
        concluded = "No viable plan found -- every play was rejected or inapplicable."

    degraded = bool(tool_health) and not tool_health.get("all_ok", True)

    return {
        "node": "planner",
        "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checked": checked,
        "found": {"candidates": len(candidate_plans), "rejected": len(rejected)},
        "concluded": concluded,
        "confidence": "LOW" if degraded else "HIGH",
        "degraded": degraded,
    }


def planner_node(state: dict):
    """Real planner node for graph.py. Generates and scores plays against
    Member 2's forecast/cells, picks one, then -- because this is the
    first point in the run a specific action actually exists -- runs it
    through materiality.classify_action() to get the real tier_level
    (may upgrade gate_node's provisional TIER_NOTIFY to TIER_APPROVAL, or
    stay put). If every candidate is excluded, chosen_plan is explicitly
    None (not omitted, so a stale plan from a previous run on this thread
    doesn't stay checkpointed) and tier_level downgrades to TIER_NOTIFY --
    there's no plan left to halt for approval on.
    """
    forecast = state.get("forecast") or {}
    cells = state.get("cells") or {}
    user_constraints = state.get("user_constraints", [])
    profile = {"median_daily_cents": forecast.get("daily_income_baseline_cents", 0)}

    candidate_plans, rejected_plans = generate_plans(forecast, profile, cells, user_constraints)
    chosen_plan = choose_plan(candidate_plans)

    tier_result = None
    if chosen_plan is not None:
        action = {
            "type": chosen_plan["action_type"],
            "amount_cents": chosen_plan["impact_cents"],
            "reversible": chosen_plan["reversible"],
            "affects_third_party": chosen_plan["affects_third_party"],
            "description": chosen_plan["name"],
        }
        tier_result = classify_action(action, forecast, user_constraints)
        tier_level = tier_result["tier_level"]
    else:
        tier_level = TIER_NOTIFY

    awaiting_approval = tier_level == TIER_APPROVAL
    explanation = render_explanation(chosen_plan, forecast, [])

    trace = build_trace(candidate_plans, rejected_plans, chosen_plan, None, user_constraints)
    if tier_result is not None:
        trace["checked"].append(f"permission tier: rule '{tier_result['rule_fired']}' -> tier {tier_level}")

    return {
        "candidate_plans": candidate_plans,
        "rejected_plans": rejected_plans,
        "chosen_plan": chosen_plan,
        "explanation": explanation,
        "tier_level": tier_level,
        "awaiting_approval": awaiting_approval,
        "trace": [trace],
    }


if __name__ == "__main__":
    print("=" * 60)
    print("STEP 1: PLAY_LIBRARY + generate_plans() + the cell evidence rule")
    print("=" * 60)

    forecast = {
        "shortfall_date": "2026-09-15", "shortfall_amount_cents": 11200,
        "worst_balance_cents": -11200, "confidence": "HIGH",
    }
    profile = {"median_daily_cents": 5000}
    cells = {
        ("Grab", 5, "dinner"): {"status": "KNOWN", "median_net_per_hour_cents": 1940, "observations": 11},
        ("Grab", 6, "dinner"): {"status": "UNKNOWN", "median_net_per_hour_cents": None, "observations": 2},
    }
    user_constraints = []

    candidates, rejected = generate_plans(forecast, profile, cells, user_constraints)

    print(f"\n{len(candidates)} candidate plans:")
    for c in candidates:
        print(f"  {c['id']:<28} score={c['score']:<7} impact={format_cents(c['impact_cents'])}  {c['evidence']}")

    print(f"\n{len(rejected)} rejected plays:")
    for r in rejected:
        print(f"  {r['id']:<28} reason={r['reason']}")

    sunday_rejection = next((r for r in rejected if r["id"] == "work_sunday_dinner"), None)
    assert sunday_rejection is not None and sunday_rejection["reason"] == "insufficient_history", \
        "a play requiring an UNKNOWN cell must be rejected with insufficient_history"

    chosen = choose_plan(candidates)
    print(f"\nChosen plan: {chosen['id'] if chosen else None}")
    assert chosen is not None

    print("\nSTEP 1 ASSERTS PASSED")

    print("\n" + "=" * 60)
    print("STEP 2: LOG A SHIFT is evaluated only when a cell is actually thin")
    print("=" * 60)

    _, rejected_thin = generate_plans(forecast, profile, cells, user_constraints)
    log_shift_evaluated = not any(r["id"] == "log_a_shift" and r["reason"] == "not_applicable" for r in rejected_thin)
    print(f"\nlog_a_shift considered applicable with a thin cell in play: {log_shift_evaluated}")
    assert log_shift_evaluated, "LOG A SHIFT should be considered applicable because the Sunday dinner cell is UNKNOWN"

    cells_all_known = {("Grab", 5, "dinner"): {"status": "KNOWN", "median_net_per_hour_cents": 1940, "observations": 11}}
    _, rejected_full = generate_plans(forecast, profile, cells_all_known, user_constraints)
    log_shift_not_applicable = any(r["id"] == "log_a_shift" and r["reason"] == "not_applicable" for r in rejected_full)
    print(f"log_a_shift marked not_applicable when nothing is thin: {log_shift_not_applicable}")
    assert log_shift_not_applicable, "LOG A SHIFT should be marked not_applicable when there's nothing thin to fill in"

    print("\nSTEP 2 ASSERTS PASSED")

    print("\n" + "=" * 60)
    print("STEP 3: render_explanation() -- the only LLM call in this file")
    print("=" * 60)

    explanation = render_explanation(chosen, forecast, [])
    print(f"\nExplanation:\n  {explanation}")

    assert format_cents(forecast["shortfall_amount_cents"]) in explanation
    assert format_cents(chosen["impact_cents"]) in explanation

    print("\nSTEP 3 COMPLETED (ran to completion with no LLM provider configured)")

    print("\n" + "=" * 60)
    print("STEP 4: build_trace() + user_constraint rejection + full test suite")
    print("=" * 60)

    constrained = ["never defer the phone bill by 3 days"]
    candidates_constrained, rejected_constrained = generate_plans(forecast, profile, cells, constrained)
    phone_bill_rejection = next((r for r in rejected_constrained if r["id"] == "defer_phone_bill"), None)
    print(f"\nWith constraint 'never defer the phone bill by 3 days': {phone_bill_rejection}")
    assert phone_bill_rejection is not None and phone_bill_rejection["reason"] == "user_constraint"

    trace_constrained = build_trace(candidates_constrained, rejected_constrained, choose_plan(candidates_constrained), None, constrained)
    phone_line = next((line for line in trace_constrained["checked"] if line.startswith("defer_phone_bill")), "")
    print(f"Trace line for the constrained rejection: {phone_line}")
    assert "never defer the phone bill by 3 days" in phone_line, "the trace must name the exact constraint that excluded the play"

    trace = build_trace(candidates, rejected, chosen)
    print("\nTrace record (unconstrained run):")
    for k, v in trace.items():
        print(f"  {k}: {v}")

    no_provider_explanation = render_explanation(chosen, forecast, [])
    assert format_cents(chosen["impact_cents"]) in no_provider_explanation

    assert sunday_rejection["reason"] == "insufficient_history"
    assert choose_plan([]) is None, "no candidates -> None, never an invented plan"

    print("\nALL DECISION LAYER TESTS PASSED (planner.py)")
