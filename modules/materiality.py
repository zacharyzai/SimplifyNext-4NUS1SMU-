"""modules/materiality.py -- Member 4: Decision Layer, part 1.

Decides WHETHER the agent should speak at all. Most assistants fail by
talking too much; this module is engineered to stay silent unless silence
would be worse than interrupting Bob. It also owns the permission-tiering
system that decides what the agent may do alone versus what it must ask
about first.

Rules for this whole file:
- Standard library only. Zero LLM calls, no boto3.
- All money is integer cents, never floats.
- Every threshold is a named constant with a comment -- no magic numbers.
- Nothing in this file may ever raise.
"""

import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.money import format_cents
from shared.schema import TIER_AUTONOMOUS, TIER_NOTIFY, TIER_APPROVAL

# --- Materiality scoring constants -------------------------------------------
MATERIALITY_THRESHOLD = 55       # score >= this fires an alert; below stays silent
COOLDOWN_DAYS = 7                # suppress a repeat of the same signature within a week
SEVERITY_WEIGHT = 40             # how big the shortfall is vs. a typical day
URGENCY_WEIGHT = 30              # how soon the shortfall arrives
NOVELTY_WEIGHT = 15              # whether this is a new situation or a repeat
ACTIONABILITY_WEIGHT = 15        # whether there's still time to do anything about it
ACTIONABILITY_MIN_DAYS = 2       # below this, there's no realistic time to react
CONFIDENCE_MULTIPLIER = {"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.4}
URGENCY_HORIZON_DAYS = 14        # beyond this, urgency contributes ~0

# --- Staleness constants ------------------------------------------------------
STALE_WARN_DAYS = 9              # roughly one missed weekly statement cycle
STALE_URGENT_DAYS = 14

# --- Permission tiering constants ---------------------------------------------
AUTONOMY_CEILING_CENTS = 5000    # $50 -- reversible actions above this still need a human look
READ_ONLY_ACTIONS = ["ask_for_data", "move_to_buffer"]
# ^ "read-only" is the handbook's name for this allow-list, but it really
# means "pre-cleared for TIER_AUTONOMOUS once rules 1-4 haven't already
# disqualified it": ask_for_data has zero real-world effect; move_to_buffer
# moves Bob's own money into his own account, which is why it still has to
# clear the irreversibility/third-party/ceiling checks above it first.


def score_materiality(forecast, profile, last_alert, today):
    """Score a projected shortfall 0-100 and decide whether to interrupt Bob.

    Five weighted signals, discounted by forecast confidence. Silence is a
    reasoned output, not an absence of one: reasons_against is populated
    even when the alert fires, and reasons_for even when it stays silent,
    so the trace panel can show the decision was deliberate either way.
    """
    shortfall_date = forecast.get("shortfall_date")
    shortfall_amount_cents = forecast.get("shortfall_amount_cents", 0)
    confidence = forecast.get("confidence", "LOW")
    median_daily_cents = profile.get("median_daily_cents", 0)

    if not shortfall_date:
        return {
            "fire": False,
            "score": 0,
            "signature": "no_shortfall",
            "alert_type": "shortfall",
            "reasons_for": [],
            "reasons_against": ["No shortfall is currently projected."],
            "suppressed_by": None,
        }

    days_until = (
        datetime.strptime(shortfall_date, "%Y-%m-%d")
        - datetime.strptime(today, "%Y-%m-%d")
    ).days
    signature = f"shortfall|{shortfall_date}"

    reasons_for = []
    reasons_against = []

    # Severity: how big is this relative to a typical day's earnings?
    severity_ratio = (shortfall_amount_cents / median_daily_cents) if median_daily_cents > 0 else 4.0
    severity_score = min(SEVERITY_WEIGHT, severity_ratio * (SEVERITY_WEIGHT / 4.0))
    if severity_ratio >= 2.0:
        reasons_for.append(
            f"Shortfall of {format_cents(shortfall_amount_cents)} is {severity_ratio:.1f}x a typical day's earnings."
        )
    else:
        reasons_against.append(
            f"Shortfall of {format_cents(shortfall_amount_cents)} is only {severity_ratio:.1f}x a typical day's "
            f"earnings -- inside normal variation."
        )

    # Urgency: closer dates matter more, capped at the horizon.
    urgency_score = URGENCY_WEIGHT * max(0.0, min(1.0, (URGENCY_HORIZON_DAYS - days_until) / URGENCY_HORIZON_DAYS))
    if days_until <= 7:
        reasons_for.append(f"Shortfall arrives in {days_until} day(s) -- close enough to matter now.")
    else:
        reasons_against.append(f"Shortfall is {days_until} days away -- still time before it's urgent.")

    # Novelty: has this exact situation already been alerted recently?
    suppressed_by = None
    if last_alert and last_alert.get("signature") == signature:
        days_since_alert = (
            datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last_alert["date"], "%Y-%m-%d")
        ).days
        if days_since_alert < COOLDOWN_DAYS:
            novelty_score = 0
            suppressed_by = "cooldown"
            reasons_against.append(
                f"Already alerted for this exact shortfall {days_since_alert} day(s) ago -- "
                f"within the {COOLDOWN_DAYS}-day cooldown."
            )
        else:
            novelty_score = NOVELTY_WEIGHT
            reasons_for.append("This shortfall was alerted before, but the cooldown period has passed.")
    else:
        novelty_score = NOVELTY_WEIGHT
        reasons_for.append("This is a new situation -- not previously alerted.")

    # Actionability: is there still time to change the outcome?
    if days_until >= ACTIONABILITY_MIN_DAYS:
        actionability_score = ACTIONABILITY_WEIGHT
        reasons_for.append(f"There are still {days_until} day(s) to act before the shortfall hits.")
    else:
        actionability_score = 0
        reasons_against.append("Too little time remains to change the outcome.")

    raw_score = severity_score + urgency_score + novelty_score + actionability_score
    multiplier = CONFIDENCE_MULTIPLIER.get(confidence, 0.4)
    if multiplier < 1.0:
        reasons_against.append(f"Forecast confidence is {confidence}, which discounts the score.")
    else:
        reasons_for.append("Forecast confidence is HIGH -- the number can be trusted.")

    final_score = round(raw_score * multiplier)
    fire = final_score >= MATERIALITY_THRESHOLD

    return {
        "fire": fire,
        "score": final_score,
        "signature": signature,
        "alert_type": "shortfall",
        "reasons_for": reasons_for,
        "reasons_against": reasons_against,
        "suppressed_by": suppressed_by,
        "date": today,
        # ^ lets this exact dict be handed back in as next run's `last_alert`
        # (gate_node does this via the checkpointed materiality_flag field)
        # without needing a separate date to track alongside it.
    }

def score_staleness(profile, upcoming_bills, today, last_alert=None):
    """Staleness must not sit silently going blind, but it also must not
    nag when nothing is at stake -- it only fires early if a real
    obligation falls inside the horizon. One signature covers "the data is
    stale" as an ongoing episode, cooled down the same way a shortfall
    alert is, so the agent doesn't repeat itself every run.
    """
    days_since = profile.get("days_since_last_observation", 0)
    signature = "stale_data"
    reasons_for = []
    reasons_against = []

    nearest_bill = None
    nearest_days = None
    for bill in upcoming_bills:
        due = datetime.strptime(bill["due_date"], "%Y-%m-%d")
        days_until_bill = (due - datetime.strptime(today, "%Y-%m-%d")).days
        if 0 <= days_until_bill <= URGENCY_HORIZON_DAYS and (nearest_days is None or days_until_bill < nearest_days):
            nearest_days, nearest_bill = days_until_bill, bill

    if days_since < STALE_WARN_DAYS:
        reasons_against.append(f"Only {days_since} days since the last observation -- within normal cadence.")
        score = 0
    elif days_since < STALE_URGENT_DAYS:
        if nearest_bill is not None:
            reasons_for.append(
                f"No earnings seen for {days_since} days, and {nearest_bill['name']} is due in {nearest_days} day(s)."
            )
            score = 70
        else:
            reasons_against.append(
                f"{days_since} days stale, but no bills fall within the next {URGENCY_HORIZON_DAYS} days -- "
                f"not worth an interruption yet."
            )
            score = 20
    else:
        if nearest_bill is not None:
            reasons_for.append(
                f"No earnings seen for {days_since} days (past the {STALE_URGENT_DAYS}-day urgent threshold), "
                f"and {nearest_bill['name']} is due in {nearest_days} day(s)."
            )
        else:
            reasons_for.append(
                f"No earnings seen for {days_since} days -- past the {STALE_URGENT_DAYS}-day urgent threshold "
                f"regardless of upcoming bills."
            )
        score = 90

    suppressed_by = None
    if score >= MATERIALITY_THRESHOLD and last_alert and last_alert.get("signature") == signature:
        days_since_alert = (
            datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last_alert["date"], "%Y-%m-%d")
        ).days
        if days_since_alert < COOLDOWN_DAYS:
            reasons_against.append(
                f"Already alerted about stale data {days_since_alert} day(s) ago -- within the {COOLDOWN_DAYS}-day cooldown."
            )
            score = 0
            suppressed_by = "cooldown"

    return {
        "fire": score >= MATERIALITY_THRESHOLD,
        "score": score,
        "signature": signature,
        "alert_type": "stale_data",
        "reasons_for": reasons_for,
        "reasons_against": reasons_against,
        "suppressed_by": suppressed_by,
        "date": today,
    }

_CONSTRAINT_STOPWORDS = {
    "never", "touch", "the", "a", "an", "do", "not", "don't", "dont",
    "touching", "please", "always", "avoid", "no", "my", "again",
}
# Reused verbatim from stubs.py's _constraint_excludes_plan so the real
# module and the stub agree on what counts as a conflicting word.


def _conflicts(description, constraint):
    """Exact whole-word overlap after stripping a small stopword list --
    no stemming, no synonym handling. KNOWN LIMITATION (see
    docs/SWAP_STATUS.md): "never touch subscriptions" will NOT catch a
    play described as "pause one subscription" (singular vs plural). This
    is a deliberate hackathon-timeline simplification, not an accidental
    gap -- a production version should match on a structured action_type
    + target entity id instead of free text.
    """
    def _words(text):
        return {w for w in re.findall(r"[a-z']+", text.lower()) if w not in _CONSTRAINT_STOPWORDS and len(w) > 2}

    return bool(_words(description) & _words(constraint))


def classify_action(action, forecast, user_constraints):
    """Six ordered rules, cautious default. Applied IN ORDER, stopping at
    the first match -- ordering matters, because an action could satisfy
    multiple rules and picking the wrong one first would be a safety bug,
    not a style choice. The unhandled case (rule 6) is deliberately the
    LEAST permissive outcome: fail-safe means "we don't recognise this"
    defaults to TIER_NOTIFY, never TIER_AUTONOMOUS.
    """
    description = action.get("description", "")

    for constraint in user_constraints:
        if _conflicts(description, constraint):
            return {
                "tier_level": TIER_APPROVAL,
                "rule_fired": "user_constraint_violation",
                "explanation": f"'{description}' conflicts with your stored constraint: '{constraint}'.",
            }

    if not action.get("reversible", True):
        return {
            "tier_level": TIER_APPROVAL,
            "rule_fired": "irreversible",
            "explanation": f"'{description}' cannot be undone once taken.",
        }

    if action.get("affects_third_party", False):
        return {
            "tier_level": TIER_APPROVAL,
            "rule_fired": "third_party",
            "explanation": f"'{description}' affects someone other than you.",
        }

    if action.get("amount_cents", 0) > AUTONOMY_CEILING_CENTS:
        return {
            "tier_level": TIER_APPROVAL,
            "rule_fired": "above_autonomy_ceiling",
            "explanation": (
                f"'{description}' involves {format_cents(action.get('amount_cents', 0))}, above the "
                f"{format_cents(AUTONOMY_CEILING_CENTS)} autonomy ceiling."
            ),
        }

    if action.get("type") in READ_ONLY_ACTIONS:
        return {
            "tier_level": TIER_AUTONOMOUS,
            "rule_fired": "read_only_allowlist",
            "explanation": f"'{description}' is on the pre-cleared allow-list -- safe to act on without approval.",
        }

    return {
        "tier_level": TIER_NOTIFY,
        "rule_fired": "default_deny_to_notify",
        "explanation": f"'{description}' doesn't match any autonomous allow-list rule -- surfaced for visibility, not acted on.",
    }

def build_trace(materiality, staleness, tier_result=None):
    """One trace record summarising what the materiality gate checked --
    including the staleness check even when it found nothing, so the
    trace panel can render that as proof of work, not a gap.
    """
    checked = [
        f"materiality score ({materiality['alert_type']}): {materiality['score']}/100, fire={materiality['fire']}",
        f"staleness score ({staleness['alert_type']}): {staleness['score']}/100, fire={staleness['fire']}",
    ]
    if tier_result is not None:
        checked.append(f"permission tier: rule '{tier_result['rule_fired']}' -> tier {tier_result['tier_level']}")

    fired_flag = materiality["fire"] or staleness["fire"]
    if fired_flag:
        source = materiality if materiality["fire"] else staleness
        concluded = source["reasons_for"][0] if source["reasons_for"] else f"{source['alert_type']} fired."
    else:
        against = materiality["reasons_against"] + staleness["reasons_against"]
        concluded = f"Staying silent: {against[0]}" if against else "Staying silent: nothing material to report."

    return {
        "node": "materiality_gate",
        "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checked": checked,
        "found": {"materiality_score": materiality["score"], "staleness_score": staleness["score"]},
        "concluded": concluded,
        "confidence": "HIGH",
        "degraded": False,
    }


def gate_node(state: dict):
    """Real materiality gate node for graph.py. Reads Member 2's forecast
    and Member 3's ingest_health off state, decides whether to fire, and
    sets a provisional tier_level (TIER_NOTIFY) -- planner_node is what
    upgrades this to TIER_APPROVAL once it has a specific chosen_plan to
    run through classify_action(), since no action exists yet here.

    Cross-run cooldown/novelty memory: CashFlowState has no DEDICATED field
    for "the last alert that fired," but it doesn't need one -- this node's
    own previous output, `materiality_flag`, is already checkpointed
    (last-write-wins) and already carries everything score_materiality/
    score_staleness need as a `last_alert` (signature, date -- see the
    "date" key added to both functions' return dicts). Reading it back in
    BEFORE this run overwrites it is the whole fix: no new state key, no
    schema change, just actually using what was already being stored.
    Previously this was hardcoded to `None` on every call, which silently
    meant cooldown suppression could never fire in the real running graph
    even though the standalone tests (which construct a fake last_alert by
    hand) proved the underlying math worked.
    """
    forecast = state.get("forecast") or {}
    ingest_health = state.get("ingest_health") or {}
    today = datetime.utcnow().strftime("%Y-%m-%d")
    previous_alert = state.get("materiality_flag")

    profile = {"median_daily_cents": forecast.get("daily_income_baseline_cents", 0)}
    staleness_profile = {"days_since_last_observation": ingest_health.get("days_since_last_observation", 0)}

    upcoming_bills = []
    gen_from = forecast.get("generated_from_date")
    if gen_from:
        try:
            base_date = datetime.strptime(gen_from, "%Y-%m-%d")
            for bill in forecast.get("bills_considered") or []:
                due = base_date + timedelta(days=bill.get("day_offset", 0))
                upcoming_bills.append({
                    "name": bill.get("name", "bill"),
                    "due_date": due.strftime("%Y-%m-%d"),
                    "amount_cents": bill.get("amount_cents", 0),
                })
        except ValueError:
            pass

    # previous_alert is handed to BOTH checks -- each only actually uses it
    # if its own signature matches (a shortfall alert's signature never
    # equals a staleness alert's "stale_data", so passing the same dict to
    # both is safe: whichever type it actually was is the only one it can
    # suppress).
    materiality = score_materiality(forecast, profile, previous_alert, today)
    staleness = score_staleness(staleness_profile, upcoming_bills, today, previous_alert)

    fire = materiality["fire"] or staleness["fire"]
    materiality_flag = materiality if materiality["fire"] else staleness if staleness["fire"] else materiality

    result = {
        "materiality_flag": materiality_flag,
        "tier_level": TIER_NOTIFY,
        "trace": [build_trace(materiality, staleness)],
    }

    if not fire:
        # route_after_gate sends a silent run straight to END -- planner_node
        # never runs, so it never gets the chance to clear its own fields.
        # Without this, a chosen_plan/explanation from a PREVIOUS run on
        # this thread (back when something WAS material) stays checkpointed
        # forever and renders as if it were this run's answer -- the same
        # stale-plan failure mode planner_node's own "no candidates" path
        # already guards against, just one step earlier in the graph.
        result.update({
            "candidate_plans": [],
            "rejected_plans": [],
            "chosen_plan": None,
            "explanation": None,
            "awaiting_approval": False,
        })

    return result


if __name__ == "__main__":
    print("=" * 60)
    print("STEP 1: score_materiality() -- the 0-100 weighted score")
    print("=" * 60)

    profile = {"median_daily_cents": 5000}

    trivial_forecast = {
        "shortfall_date": "2026-09-18", "shortfall_amount_cents": 1800,
        "worst_balance_cents": -1800, "confidence": "HIGH",
    }
    real_forecast = {
        "shortfall_date": "2026-09-15", "shortfall_amount_cents": 11200,
        "worst_balance_cents": -11200, "confidence": "HIGH",
    }
    today_day0 = "2026-09-06"
    today_day1 = "2026-09-07"

    trivial_result = score_materiality(trivial_forecast, profile, None, today_day0)
    real_result = score_materiality(real_forecast, profile, None, today_day0)

    last_alert = {"date": today_day0, "signature": real_result["signature"]}
    suppressed_result = score_materiality(real_forecast, profile, last_alert, today_day1)

    print(f"\nTrivial $18 dip:   fire={trivial_result['fire']!s:<5} score={trivial_result['score']}")
    print(f"  reasons_for:     {trivial_result['reasons_for']}")
    print(f"  reasons_against: {trivial_result['reasons_against']}")

    print(f"\n$112 shortfall:    fire={real_result['fire']!s:<5} score={real_result['score']}")
    print(f"  reasons_for:     {real_result['reasons_for']}")
    print(f"  reasons_against: {real_result['reasons_against']}")

    print(f"\nSame shortfall, 1 day later (cooldown): fire={suppressed_result['fire']!s:<5} score={suppressed_result['score']} suppressed_by={suppressed_result['suppressed_by']}")
    print(f"  reasons_for:     {suppressed_result['reasons_for']}")
    print(f"  reasons_against: {suppressed_result['reasons_against']}")

    assert trivial_result["fire"] is False, "an $18 dip must not fire"
    assert real_result["fire"] is True, "a $112 shortfall must fire"
    assert suppressed_result["fire"] is False, "the same shortfall a day later must be suppressed"
    assert suppressed_result["suppressed_by"] == "cooldown"
    assert len(real_result["reasons_against"]) > 0, "reasons_against must be populated even when firing"
    assert len(trivial_result["reasons_for"]) > 0, "reasons_for must be populated even when silent"

    print("\nSTEP 1 ASSERTS PASSED")

    print("\n" + "=" * 60)
    print("STEP 2: score_staleness() -- staleness as a material event")
    print("=" * 60)

    today = "2026-09-06"
    stale_profile = {"days_since_last_observation": 11}
    bills_with_rent = [{"name": "rent", "due_date": "2026-09-08", "amount_cents": 90000, "essential": True}]
    bills_far_off = [{"name": "bike insurance", "due_date": "2026-10-15", "amount_cents": 6000, "essential": False}]

    stale_with_rent = score_staleness(stale_profile, bills_with_rent, today)
    stale_no_bills = score_staleness(stale_profile, bills_far_off, today)

    print(f"\n11 days stale, rent due Friday: fire={stale_with_rent['fire']} score={stale_with_rent['score']}")
    print(f"  reasons_for: {stale_with_rent['reasons_for']}")

    print(f"\n11 days stale, no bills for a month: fire={stale_no_bills['fire']} score={stale_no_bills['score']}")
    print(f"  reasons_against: {stale_no_bills['reasons_against']}")

    assert stale_with_rent["fire"] is True, "staleness with a bill inside the horizon must fire"
    assert stale_no_bills["fire"] is False, "staleness with no bills at risk must stay silent"

    print("\nSTEP 2 ASSERTS PASSED")

    print("\n" + "=" * 60)
    print("STEP 3: classify_action() -- permission tiering")
    print("=" * 60)

    buffer_transfer = {
        "type": "move_to_buffer", "amount_cents": 2000, "reversible": True,
        "affects_third_party": False, "description": "move $20 into a separate savings buffer",
    }
    loan_application = {
        "type": "apply_for_loan", "amount_cents": 30000, "reversible": False,
        "affects_third_party": True, "description": "apply for a $300 short-term loan",
    }
    ask_for_data = {
        "type": "ask_for_data", "amount_cents": 0, "reversible": True,
        "affects_third_party": False, "description": "ask Bob to log tonight's shift",
    }
    defer_phone_bill = {
        "type": "request_deferral", "amount_cents": 4500, "reversible": True,
        "affects_third_party": True, "description": "defer the phone bill by 3 days",
    }
    constraints = ["never touch the phone bill"]

    r1 = classify_action(buffer_transfer, real_forecast, [])
    r2 = classify_action(loan_application, real_forecast, [])
    r3 = classify_action(ask_for_data, real_forecast, [])
    r4 = classify_action(defer_phone_bill, real_forecast, constraints)

    print(f"\n$20 reversible transfer:  tier={r1['tier_level']} rule={r1['rule_fired']}")
    print(f"$300 irreversible loan:   tier={r2['tier_level']} rule={r2['rule_fired']}")
    print(f"ask_for_data:             tier={r3['tier_level']} rule={r3['rule_fired']}")
    print(f"defer phone bill (constrained): tier={r4['tier_level']} rule={r4['rule_fired']}")

    assert r1["tier_level"] == TIER_AUTONOMOUS
    assert r2["tier_level"] == TIER_APPROVAL
    assert r3["tier_level"] == TIER_AUTONOMOUS
    assert r4["tier_level"] == TIER_APPROVAL and r4["rule_fired"] == "user_constraint_violation"

    print("\nSTEP 3 ASSERTS PASSED")

    print("\n" + "=" * 60)
    print("STEP 3b: trying to defeat the six rules with a $500 action")
    print("=" * 60)

    irreversible_third_party_500 = {
        "type": "ask_for_data", "amount_cents": 50000, "reversible": False,
        "affects_third_party": True, "description": "a suspicious $500 action",
    }
    still_third_party_500 = {**irreversible_third_party_500, "reversible": True}
    only_over_ceiling_500 = {**still_third_party_500, "affects_third_party": False}
    unrecognised_type_small = {**only_over_ceiling_500, "amount_cents": 100, "type": "totally_new_action_type"}

    v1 = classify_action(irreversible_third_party_500, real_forecast, [])
    v2 = classify_action(still_third_party_500, real_forecast, [])
    v3 = classify_action(only_over_ceiling_500, real_forecast, [])
    v4 = classify_action(unrecognised_type_small, real_forecast, [])

    print(f"\nVariant 1 (irreversible, 3rd-party, $500, 'ask_for_data' type): tier={v1['tier_level']} rule={v1['rule_fired']}")
    print(f"Variant 2 (reversible, still 3rd-party, $500):                  tier={v2['tier_level']} rule={v2['rule_fired']}")
    print(f"Variant 3 (reversible, not 3rd-party, still $500):              tier={v3['tier_level']} rule={v3['rule_fired']}")
    print(f"Variant 4 (small $, unrecognised type):                        tier={v4['tier_level']} rule={v4['rule_fired']}")

    assert v1["tier_level"] == TIER_APPROVAL and v1["rule_fired"] == "irreversible"
    assert v2["tier_level"] == TIER_APPROVAL and v2["rule_fired"] == "third_party"
    assert v3["tier_level"] == TIER_APPROVAL and v3["rule_fired"] == "above_autonomy_ceiling"
    assert v4["tier_level"] == TIER_NOTIFY and v4["rule_fired"] == "default_deny_to_notify"

    print("\nEven though Variant 1's type is on the READ_ONLY_ACTIONS allow-list, rule 2")
    print("(irreversible) is checked BEFORE rule 5 (the allow-list) and catches it first.")
    print("This is why ordering matters: a later, more permissive rule never gets a chance")
    print("to fire once an earlier, stricter rule has already matched.")

    print("\nSTEP 3b ASSERTS PASSED")

    print("\n" + "=" * 60)
    print("STEP 4: build_trace() + confidence-penalty test + full suite")
    print("=" * 60)

    trace = build_trace(real_result, stale_no_bills, r1)
    print("\nTrace record:")
    for k, v in trace.items():
        print(f"  {k}: {v}")

    low_confidence_forecast = {**real_forecast, "confidence": "LOW"}
    low_result = score_materiality(low_confidence_forecast, profile, None, today_day0)
    print(f"\nSame $112 shortfall at LOW confidence: score={low_result['score']} (vs HIGH: {real_result['score']})")
    assert low_result["score"] < real_result["score"], "LOW confidence must score lower than HIGH for the same shortfall"
    assert low_result["fire"] is False, "the confidence penalty should be enough to keep this below threshold"

    print("\n" + "=" * 60)
    print("STEP 5: gate_node() cooldown ACTUALLY persists across two calls")
    print("=" * 60)
    print("Previously gate_node hardcoded last_alert=None on every call, so this")
    print("exact scenario (the standalone functions tested cooldown, but gate_node")
    print("itself never could) silently never fired in the real running graph.")

    gate_state_run1 = {
        "forecast": {**real_forecast, "daily_income_baseline_cents": 5000},
        "ingest_health": {"days_since_last_observation": 0},
    }
    gate_result_1 = gate_node(gate_state_run1)
    assert gate_result_1["materiality_flag"]["fire"] is True, "first run should fire on a real shortfall"

    # Simulate what the checkpointer does between two graph.invoke() calls on
    # the same thread: gate_node's own previous output (materiality_flag) is
    # exactly what a second call would see in `state`.
    gate_state_run2 = {**gate_state_run1, "materiality_flag": gate_result_1["materiality_flag"]}
    gate_result_2 = gate_node(gate_state_run2)

    assert gate_result_2["materiality_flag"]["fire"] is False, (
        "the SAME shortfall signature, same day, must now be suppressed by cooldown "
        "on the second gate_node() call -- this is the exact behavior that was broken"
    )
    assert gate_result_2["materiality_flag"]["suppressed_by"] == "cooldown"
    print(f"Run 1: fire={gate_result_1['materiality_flag']['fire']}")
    print(f"Run 2 (same signature, same day): fire={gate_result_2['materiality_flag']['fire']}, "
          f"suppressed_by={gate_result_2['materiality_flag']['suppressed_by']}")
    print("\nSTEP 5 ASSERTS PASSED -- cooldown now genuinely persists across gate_node calls")

    print("\nALL DECISION LAYER TESTS PASSED (materiality.py)")
