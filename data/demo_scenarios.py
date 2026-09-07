"""data/demo_scenarios.py -- named fixtures for the live demo / video walkthrough.

Each scenario is an `inputs` dict shaped exactly like what POST /run (and
the SSE stream) pass straight through to graph.run_agent() -- realistic
Singapore gig-worker earnings across Grab/foodpanda, tuned so each scenario
reliably reaches its intended outcome. Verified end-to-end (not just via
each module's own self-test) in tools/verify_demo_scenarios.py -- see that
script's output for the exact trace/state each one produces.

Numbers are synthetic and tuned to hit a specific forecast/materiality
outcome deterministically, same category of disclosure as forecast.py's
own DEMO_BILLS -- these are demo fixtures, not claims about a real user.
"""
from datetime import datetime, timedelta, timezone


def _daily_rows(platform, num_days, daily_gross_cents, start_hour=18,
                end_offset_days=1, trips=4, hours=3.0):
    """num_days consecutive days ending end_offset_days ago, one shift/day
    at a fixed platform/time_block so cells actually accumulate enough
    observations (MIN_OBSERVATIONS) for HIGH confidence once num_days>=21
    (3 full weekly cycles -- 21/7=3 observations per weekday cell).
    """
    end_date = datetime.now(timezone.utc).date() - timedelta(days=end_offset_days)
    rows = []
    for i in range(num_days):
        date = (end_date - timedelta(days=i)).isoformat()
        rows.append({
            "date": date, "platform": platform, "start_hour": start_hour,
            "gross_cents": f"{daily_gross_cents / 100:.2f}", "tip_cents": "2.00",
            "platform_fee_cents": "0.00", "trips": trips, "hours": hours,
        })
    return rows


# Excludes every reversible play in modules/planner.py's PLAY_LIBRARY, the
# same trick graph.py's own __main__ demo (c) uses, so the only play left
# standing is defer_phone_bill (irreversible + third-party) -- forcing a
# real TIER_APPROVAL through classify_action rather than hand-tuning the
# scoring constants to make it win on its own. Not "never defer..." for
# bike servicing -- collides with defer_phone_bill's "defer" on whole-word
# overlap (see modules/materiality.py's _conflicts() known limitation).
_EXCLUDE_REVERSIBLE_PLAYS = [
    "never shift into saturday dinner",
    "never work a sunday dinner shift",
    "never take extra trips",
    "never move money to a buffer",
    "never pause the streaming subscription",
    "never touch bike servicing",
    "never log a shift",
]

SCENARIOS = {
    "steady_earner": {
        "label": "Steady earner — stays silent",
        "description": (
            "21 days of solid $120/day Grab dinner shifts. HIGH confidence, "
            "no material shortfall -- the gate reasons about it and reports "
            "genuine silence, not a stall."
        ),
        "inputs": {
            "raw_delivery_rows": _daily_rows("Grab", 21, 12000),
            "raw_source": "partner_statement",
        },
    },
    "shortfall_approval": {
        "label": "Cash crunch — needs your approval",
        "description": (
            "21 days of thin $45/day Grab dinner shifts. HIGH confidence, a "
            "real ~$248 shortfall, and every reversible play pre-excluded by "
            "stated constraints -- forces the planner to propose the one "
            "irreversible play left (defer the phone bill), which halts for "
            "Tier 2 approval."
        ),
        "inputs": {
            "raw_delivery_rows": _daily_rows("Grab", 21, 4500),
            "raw_source": "partner_statement",
            "user_constraints": list(_EXCLUDE_REVERSIBLE_PLAYS),
        },
    },
    "thin_history": {
        "label": "New user — asks a question",
        "description": (
            "Only 3 days logged. MEDIUM confidence triggers the bounded "
            "clarify/replan loop instead of guessing."
        ),
        "inputs": {
            "raw_delivery_rows": _daily_rows("Grab", 3, 5000),
            "raw_source": "partner_statement",
        },
    },
}


if __name__ == "__main__":
    assert set(SCENARIOS) == {"steady_earner", "shortfall_approval", "thin_history"}
    for key, scenario in SCENARIOS.items():
        assert "label" in scenario and "description" in scenario and "inputs" in scenario
        assert "raw_delivery_rows" in scenario["inputs"]
    print("ALL DEMO SCENARIO FIXTURE TESTS PASSED")
