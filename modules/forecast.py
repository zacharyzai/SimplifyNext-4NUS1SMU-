"""modules/forecast.py -- OWNED BY MEMBER 2. Deterministic forecast engine.

Zero LLM calls, zero AWS SDK imports, zero AWS dependency of any kind
(README non-negotiable rule #5 -- this file must run to completion with no
AWS credentials present). Every number in this file comes from Bob's own
delivery_log via plain arithmetic and Python's `statistics` module -- the LLM may
transcribe and narrate elsewhere in this project, it never calculates,
ranks, decides or estimates (rule #2), and this module is where that rule
matters most.

Public entry point for graph.py's swap seam (see docs/SWAP_STATUS.md):

    forecast_node(state: CashFlowState) -> dict

which writes exactly the keys stubs.forecast_node writes: forecast, cells,
data_gaps, open_questions, trace. Never raises (rule #4) -- any internal
failure degrades to an UNKNOWN-confidence result rather than propagating.

Design note on bills/starting balance: CashFlowState (shared/schema.py) is
frozen and owned by Member 1, and no upstream module supplies a wallet
balance or bill calendar. Rather than silently fabricating a number under
some other name, this module uses a small, clearly-labelled synthetic
scenario (DEMO_STARTING_BALANCE_CENTS / DEMO_BILLS below) to drive the
14-day cash-flow table. This is exactly the kind of assumption the
deliverables checklist requires us to disclose as synthetic, not real Bob
data (HACKATHON_OBJECTIVES.md §9).
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# --- Named thresholds (README rule #9: every threshold is a named constant
# at the top of the file with a comment explaining it) -----------------------

MIN_OBSERVATIONS = 3
# A (platform, weekday, time_block) cell needs at least this many logged
# shifts before we trust a median for it. Below this, the cell is UNKNOWN
# and NEVER filled in with an overall average (data_source_registry.md:
# "never fabricate a number"; HACKATHON_OBJECTIVES.md rule #4).

LOOKBACK_DAYS = 28
# How much delivery_log history the earnings profile (step 1) considers.
# Matches the Weekly Partner Statement cadence Member 3's ingestion targets.

PROJECTION_DAYS = 14
# How far forward the cash-flow table (step 3) projects. Two weeks is long
# enough to catch the next rent/bill cycle without compounding forecast
# error across a whole month of assumed-constant daily income.

GAP_LOOKAHEAD_DAYS = 7
# A thin/UNKNOWN cell only becomes a reported data_gap (and drives a
# replan-loop open_question) if it falls within this many days of "today" --
# an UNKNOWN cell three weeks out isn't worth interrupting Bob about yet.

HIGH_CONFIDENCE_PRECISION = 0.85
MEDIUM_CONFIDENCE_PRECISION = 0.5
# Weighted-average provenance precision (shared/... data capture ladder:
# partner statement 1.0, bank export 0.9, screenshot 0.7, self-reported
# 0.4, benchmark prior 0.2) needed to call the forecast HIGH / MEDIUM
# confidence. Below MEDIUM_CONFIDENCE_PRECISION the forecast is LOW.
# Chosen so a purely self-reported (0.4) history can never read HIGH, per
# HACKATHON_OBJECTIVES.md §7.2 step 4's acceptance test.

HIGH_CONFIDENCE_MIN_DAYS = 14
# HIGH confidence additionally requires at least this many distinct
# observed days -- high-precision sources over too short a window are
# still a thin sample, not a confident forecast.

PESSIMISTIC_HAIRCUT = 0.6
# The "stress case" projection assumes Bob only earns this fraction of his
# median daily income each day -- a deliberately conservative second read
# alongside the baseline projection (HACKATHON_OBJECTIVES.md §1.2: "the
# pessimistic projection").

DEMO_STARTING_BALANCE_CENTS = 2_000
DEMO_BILLS: List[Dict[str, Any]] = [
    {"name": "Phone bill", "day_offset": 3, "amount_cents": 3_800},
    {"name": "Rent contribution", "day_offset": 9, "amount_cents": 70_000},
]
# SYNTHETIC. See module docstring: no state field carries Bob's actual
# wallet balance or bill calendar today, so the standalone demo and the
# graph both fall back to this fixed, cited scenario rather than guessing.


# --- Trace record (shared/schema.py section 6.2 shape, same as stubs.py) ---

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trace(node: str, checked: List[str], found: dict, concluded: str,
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


# --- Step 1: daily totals + earnings profile --------------------------------

def _recompute_net_cents(record: dict) -> int:
    """Never trust a `net_cents` the caller supplied -- recompute it from
    the components every time (HACKATHON_OBJECTIVES.md §3.2)."""
    if record.get("source") == "benchmark_prior":
        # A benchmark row is a single cited rate, not a gross/tip/fee
        # breakdown -- recomputing from parts would silently zero it out.
        return int(record.get("net_cents", 0) or 0)
    gross = int(record.get("gross_cents", 0) or 0)
    tip = int(record.get("tip_cents", 0) or 0)
    fee = int(record.get("platform_fee_cents", 0) or 0)
    return gross + tip - fee


def compute_daily_totals(delivery_log: List[dict]) -> Dict[str, int]:
    """{date: total recomputed net_cents across all records that day}."""
    totals: Dict[str, int] = {}
    for record in delivery_log:
        date = record.get("date")
        if not date:
            continue
        totals[date] = totals.get(date, 0) + _recompute_net_cents(record)
    return totals


def earnings_profile(daily_totals: Dict[str, int]) -> dict:
    """Summary stats over the observed daily totals. UNKNOWN (all-None)
    when there is no history at all -- never a fabricated 0.
    """
    values = list(daily_totals.values())
    days_observed = len(values)
    if days_observed == 0:
        return {
            "days_observed": 0,
            "mean_daily_cents": None,
            "median_daily_cents": None,
            "stdev_daily_cents": None,
            "volatility_ratio": None,
            "status": "UNKNOWN",
        }

    mean_daily = statistics.mean(values)
    median_daily = statistics.median(values)
    stdev_daily = statistics.pstdev(values) if days_observed >= 2 else 0.0
    volatility_ratio = (stdev_daily / mean_daily) if mean_daily else None

    return {
        "days_observed": days_observed,
        "mean_daily_cents": round(mean_daily),
        "median_daily_cents": round(median_daily),
        "stdev_daily_cents": round(stdev_daily),
        "volatility_ratio": round(volatility_ratio, 3) if volatility_ratio is not None else None,
        "status": "KNOWN",
    }


# --- Step 2: cell profile + UNKNOWN rule ------------------------------------

def _cell_key(platform: str, weekday: int, time_block: str) -> str:
    return f"{platform}|{weekday}|{time_block}"


def build_cell_profiles(delivery_log: List[dict]) -> Dict[str, dict]:
    """Group records into (platform, weekday, time_block) cells and take
    the median net-per-hour rate for each. A cell with fewer than
    MIN_OBSERVATIONS rows returns status UNKNOWN and median None -- it is
    NEVER backfilled with the overall average (rule #4).
    """
    groups: Dict[str, List[dict]] = {}
    for record in delivery_log:
        platform = record.get("platform")
        time_block = record.get("time_block")
        if not platform or not time_block:
            continue
        # Benchmark rows carry weekday directly (they have no real date);
        # everything else derives it from the observed date.
        weekday = record.get("weekday")
        if weekday is None:
            date = record.get("date")
            if not date:
                continue
            try:
                weekday = datetime.strptime(date, "%Y-%m-%d").weekday()
            except ValueError:
                continue
        key = _cell_key(platform, weekday, time_block)
        groups.setdefault(key, []).append(record)

    cells: Dict[str, dict] = {}
    for key, records in groups.items():
        observations = len(records)
        rates = [
            _recompute_net_cents(r) / r["hours"]
            for r in records
            if r.get("hours", 0) and r["hours"] > 0
        ]
        if observations < MIN_OBSERVATIONS or not rates:
            cells[key] = {
                "median_net_per_hour_cents": None,
                "observations": observations,
                "status": "UNKNOWN",
            }
        else:
            cells[key] = {
                "median_net_per_hour_cents": round(statistics.median(rates)),
                "observations": observations,
                "status": "KNOWN",
            }
    return cells


_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def find_data_gaps(cells: Dict[str, dict], start_date: "datetime.date") -> List[dict]:
    """UNKNOWN cells whose weekday falls within the next GAP_LOOKAHEAD_DAYS
    of `start_date` -- these are the gaps worth interrupting Bob about,
    not every thin cell that ever existed.

    Sorted soonest-first (then fewest observations as a tiebreaker): a gap
    that occurs tomorrow blocks this run's confidence more urgently than
    one nine days out, and a cell with zero logged shifts is a bigger hole
    than one with a couple. This ordering is what lets a caller ask about
    only the single most-blocking gap at a time instead of dumping the
    whole list on Bob at once.
    """
    weekday_offsets: Dict[int, int] = {}
    for offset in range(GAP_LOOKAHEAD_DAYS):
        weekday = (start_date + timedelta(days=offset)).weekday()
        weekday_offsets.setdefault(weekday, offset)  # first (soonest) occurrence wins

    gaps = []
    for key, cell in cells.items():
        if cell["status"] != "UNKNOWN":
            continue
        platform, weekday_str, time_block = key.split("|")
        weekday = int(weekday_str)
        if weekday in weekday_offsets:
            gaps.append({
                "platform": platform,
                "weekday": weekday,
                "time_block": time_block,
                "observations": cell["observations"],
                "days_until": weekday_offsets[weekday],
                "why_it_matters": (
                    f"Falls inside the next {GAP_LOOKAHEAD_DAYS} days and only has "
                    f"{cell['observations']} logged shift(s) -- below MIN_OBSERVATIONS={MIN_OBSERVATIONS}."
                ),
            })

    gaps.sort(key=lambda g: (g["days_until"], g["observations"]))
    return gaps


def build_open_questions(data_gaps: List[dict]) -> List[str]:
    questions = []
    for gap in data_gaps:
        weekday_name = _WEEKDAY_NAMES[gap["weekday"]]
        questions.append(
            f"You've only logged {gap['observations']} {gap['platform']} {gap['time_block']} "
            f"shift(s) on a {weekday_name} -- roughly what do you make on one of those?"
        )
    return questions


# --- Step 3: 14-day projection + pessimistic stress case --------------------

def _project(daily_income_cents: int, start_date, bills: List[dict],
             starting_balance_cents: int) -> dict:
    """Deterministic day-by-day walk: add one day's income, subtract any
    bill due that day, track the running balance. Pure function of its
    arguments -- calling it twice with the same inputs must always return
    the same output (no wall-clock or randomness inside the loop).
    """
    bills_by_offset: Dict[int, int] = {}
    for bill in bills:
        bills_by_offset[bill["day_offset"]] = bills_by_offset.get(bill["day_offset"], 0) + bill["amount_cents"]

    balance = starting_balance_cents
    table = []
    shortfall_date = None
    shortfall_amount_cents = 0
    worst_balance_cents = balance

    for offset in range(PROJECTION_DAYS):
        date = (start_date + timedelta(days=offset)).isoformat()
        balance += daily_income_cents
        due_today = bills_by_offset.get(offset, 0)
        balance -= due_today
        table.append({"date": date, "balance_cents": balance, "bill_due_cents": due_today})

        if balance < worst_balance_cents:
            worst_balance_cents = balance
        if balance < 0 and shortfall_date is None:
            shortfall_date = date
            shortfall_amount_cents = -balance

    return {
        "table": table,
        "shortfall_date": shortfall_date,
        "shortfall_amount_cents": shortfall_amount_cents,
        "worst_balance_cents": worst_balance_cents,
    }


# --- Step 4: provenance-weighted confidence ---------------------------------

def weighted_precision(delivery_log: List[dict]) -> Optional[float]:
    """Mean of each record's own `precision` field (data_source_registry.md
    capture ladder: partner statement 1.0 ... benchmark prior 0.2).
    Confidence is weighted by provenance, not just row count (rule #3).
    """
    precisions = [r["precision"] for r in delivery_log if isinstance(r.get("precision"), (int, float))]
    if not precisions:
        return None
    return sum(precisions) / len(precisions)


def determine_confidence(days_observed: int, avg_precision: Optional[float],
                          data_gaps: List[dict]) -> tuple:
    """Returns (confidence, reason). Never HIGH on thin or low-provenance
    history, regardless of how clean the arithmetic looks.
    """
    if days_observed == 0 or avg_precision is None:
        return "UNKNOWN", "No earnings history to forecast from."

    if avg_precision < MEDIUM_CONFIDENCE_PRECISION:
        return "LOW", (
            f"Weighted precision {avg_precision:.2f} is below the "
            f"{MEDIUM_CONFIDENCE_PRECISION:.2f} floor for MEDIUM confidence "
            f"(source data is mostly low-provenance, e.g. self-reported shift logs)."
        )

    if (avg_precision >= HIGH_CONFIDENCE_PRECISION
            and days_observed >= HIGH_CONFIDENCE_MIN_DAYS
            and not data_gaps):
        return "HIGH", (
            f"Weighted precision {avg_precision:.2f} over {days_observed} observed days, "
            f"with no unresolved gaps in the next {GAP_LOOKAHEAD_DAYS} days."
        )

    if avg_precision >= HIGH_CONFIDENCE_PRECISION:
        missing_bits = []
        if days_observed < HIGH_CONFIDENCE_MIN_DAYS:
            missing_bits.append(f"only {days_observed} observed days (need {HIGH_CONFIDENCE_MIN_DAYS})")
        if data_gaps:
            missing_bits.append(f"{len(data_gaps)} unresolved near-term data gap(s)")
        return "MEDIUM", (
            f"Weighted precision {avg_precision:.2f} qualifies for HIGH, but "
            + " and ".join(missing_bits) + " caps it at MEDIUM."
        )

    return "MEDIUM", (
        f"Weighted precision {avg_precision:.2f} is between the MEDIUM floor "
        f"({MEDIUM_CONFIDENCE_PRECISION:.2f}) and the HIGH floor ({HIGH_CONFIDENCE_PRECISION:.2f})."
    )


# --- Step 5: orchestrating function + trace ---------------------------------

def run_forecast(delivery_log: List[dict]) -> dict:
    """Pure function: delivery_log in, {forecast, cells, data_gaps,
    open_questions, trace} out. Never raises -- any unexpected shape in
    delivery_log degrades to an UNKNOWN-confidence result instead.
    """
    try:
        daily_totals = compute_daily_totals(delivery_log)
        profile = earnings_profile(daily_totals)
        cells = build_cell_profiles(delivery_log)

        if daily_totals:
            start_date = max(
                datetime.strptime(d, "%Y-%m-%d").date() for d in daily_totals
            ) + timedelta(days=1)
        else:
            start_date = datetime.now(timezone.utc).date()

        data_gaps = find_data_gaps(cells, start_date)
        open_questions = build_open_questions(data_gaps)
        avg_precision = weighted_precision(delivery_log)
        confidence, confidence_reason = determine_confidence(
            profile["days_observed"], avg_precision, data_gaps
        )

        if profile["median_daily_cents"] is None:
            forecast = {
                "generated_from_date": start_date.isoformat(),
                "horizon_days": PROJECTION_DAYS,
                "shortfall_date": None,
                "shortfall_amount_cents": None,
                "worst_balance_cents": None,
                "confidence": "UNKNOWN",
                "confidence_reason": "No earnings history at all -- cannot project.",
                "pessimistic": None,
                "starting_balance_cents": DEMO_STARTING_BALANCE_CENTS,
                "bills_considered": DEMO_BILLS,
            }
            confidence = "UNKNOWN"
            confidence_reason = forecast["confidence_reason"]
        else:
            baseline_daily = profile["median_daily_cents"]
            pessimistic_daily = round(baseline_daily * PESSIMISTIC_HAIRCUT)

            baseline = _project(baseline_daily, start_date, DEMO_BILLS, DEMO_STARTING_BALANCE_CENTS)
            pessimistic = _project(pessimistic_daily, start_date, DEMO_BILLS, DEMO_STARTING_BALANCE_CENTS)

            forecast = {
                "generated_from_date": start_date.isoformat(),
                "horizon_days": PROJECTION_DAYS,
                "daily_income_baseline_cents": baseline_daily,
                "shortfall_date": baseline["shortfall_date"],
                "shortfall_amount_cents": baseline["shortfall_amount_cents"],
                "worst_balance_cents": baseline["worst_balance_cents"],
                "table": baseline["table"],
                "confidence": confidence,
                "confidence_reason": confidence_reason,
                "pessimistic": {
                    "daily_income_cents": pessimistic_daily,
                    "shortfall_date": pessimistic["shortfall_date"],
                    "shortfall_amount_cents": pessimistic["shortfall_amount_cents"],
                    "worst_balance_cents": pessimistic["worst_balance_cents"],
                },
                "starting_balance_cents": DEMO_STARTING_BALANCE_CENTS,
                "bills_considered": DEMO_BILLS,
            }

        if forecast["shortfall_date"]:
            concluded = (
                f"Projected shortfall of ${forecast['shortfall_amount_cents'] / 100:,.2f} "
                f"on {forecast['shortfall_date']} ({confidence} confidence)."
            )
        else:
            concluded = f"No shortfall projected within {PROJECTION_DAYS} days ({confidence} confidence)."

        trace = _trace(
            node="forecast_engine",
            checked=[
                f"{profile['days_observed']}d earnings history",
                "synthetic demo bill calendar",
                f"{len(cells)} platform-time cells",
            ],
            found={
                "median_daily_cents": profile["median_daily_cents"],
                "unknown_cells": sum(1 for c in cells.values() if c["status"] == "UNKNOWN"),
                "weighted_precision": round(avg_precision, 3) if avg_precision is not None else None,
            },
            concluded=concluded,
            confidence=confidence,
            degraded=(confidence != "HIGH"),
        )

        return {
            "forecast": forecast,
            "cells": cells,
            "data_gaps": data_gaps,
            "open_questions": open_questions,
            "trace": trace,
        }

    except Exception as e:  # noqa: BLE001 -- rule #4: no module ever raises
        trace = _trace(
            node="forecast_engine",
            checked=["delivery_log"],
            found={"error": str(e)},
            concluded="Forecast failed unexpectedly; degrading to UNKNOWN rather than crashing.",
            confidence="UNKNOWN",
            degraded=True,
        )
        return {
            "forecast": {
                "shortfall_date": None,
                "shortfall_amount_cents": None,
                "worst_balance_cents": None,
                "confidence": "UNKNOWN",
                "confidence_reason": f"Internal error: {e}",
                "pessimistic": None,
            },
            "cells": {},
            "data_gaps": [],
            "open_questions": [],
            "trace": trace,
        }


def forecast_node(state: dict) -> Dict[str, Any]:
    """The swap-in for stubs.forecast_node (see docs/SWAP_STATUS.md). Same
    input (CashFlowState) and same output keys, real math instead of
    hardcoded values.
    """
    delivery_log = state.get("delivery_log") or []
    result = run_forecast(delivery_log)
    return {
        "forecast": result["forecast"],
        "cells": result["cells"],
        "data_gaps": result["data_gaps"],
        "open_questions": result["open_questions"],
        "trace": [result["trace"]],
    }


# --- Standalone assert suite -------------------------------------------------

def _synthetic_delivery_log(num_days: int, source: str = "partner_statement",
                             precision: float = 1.0, start: str = "2026-08-01") -> List[dict]:
    """Deterministic synthetic history: one Grab dinner shift/day, income
    oscillating in a fixed pattern so the profile has real (but
    reproducible) volatility instead of a flat line.
    """
    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    log = []
    for i in range(num_days):
        date = (start_date + timedelta(days=i)).isoformat()
        gross = 6000 + (i % 5) * 400
        log.append({
            "date": date, "platform": "Grab", "time_block": "dinner",
            "gross_cents": gross, "tip_cents": 300, "platform_fee_cents": 500,
            "trips": 5, "hours": 4.0, "source": source, "precision": precision,
        })
    return log


if __name__ == "__main__":
    # --- Step 1: daily totals + earnings profile ----------------------------
    log_28 = _synthetic_delivery_log(LOOKBACK_DAYS)
    totals = compute_daily_totals(log_28)
    assert len(totals) == LOOKBACK_DAYS
    profile = earnings_profile(totals)
    assert profile["status"] == "KNOWN"
    assert 0 < profile["volatility_ratio"] < 1, f"implausible volatility ratio: {profile['volatility_ratio']}"
    print(f"Step 1 -- {len(totals)} days of totals, profile: {profile}")

    # --- Step 2: cell profile + UNKNOWN rule ---------------------------------
    # Two Grab-dinner shifts exactly 7 days apart share a weekday, so they
    # land in the same (platform, weekday, time_block) cell -- 2 rows, one
    # cell, below MIN_OBSERVATIONS.
    two_obs_log = [
        {"date": "2026-08-01", "platform": "Grab", "time_block": "dinner",
         "gross_cents": 6000, "tip_cents": 300, "platform_fee_cents": 500,
         "trips": 5, "hours": 4.0, "source": "partner_statement", "precision": 1.0},
        {"date": "2026-08-08", "platform": "Grab", "time_block": "dinner",
         "gross_cents": 6200, "tip_cents": 200, "platform_fee_cents": 500,
         "trips": 5, "hours": 4.0, "source": "partner_statement", "precision": 1.0},
    ]
    thin_cells = build_cell_profiles(two_obs_log)
    assert len(thin_cells) == 1
    only_key = next(iter(thin_cells))
    # only 2 rows share this cell, so it must be UNKNOWN with median None --
    # never backfilled with an average.
    assert thin_cells[only_key]["status"] == "UNKNOWN"
    assert thin_cells[only_key]["median_net_per_hour_cents"] is None
    assert thin_cells[only_key]["observations"] == 2
    print(f"Step 2 -- 2-observation cell correctly UNKNOWN: {thin_cells[only_key]}")

    # --- Step 3: projection + pessimistic stress case + determinism --------
    result_1 = run_forecast(log_28)
    result_2 = run_forecast(log_28)
    assert result_1["forecast"] == result_2["forecast"], "run_forecast must be deterministic"
    forecast = result_1["forecast"]
    assert len(forecast["table"]) == PROJECTION_DAYS
    assert forecast["shortfall_date"] is not None, "expected the demo bill calendar to force a shortfall"
    assert forecast["pessimistic"]["shortfall_amount_cents"] >= forecast["shortfall_amount_cents"]
    print(f"Step 3 -- 14-day table computed twice, identical. Shortfall on {forecast['shortfall_date']} "
          f"of {forecast['shortfall_amount_cents']} cents (baseline); "
          f"pessimistic shortfall {forecast['pessimistic']['shortfall_amount_cents']} cents.")

    # --- Step 4: provenance-weighted confidence + gap detection -------------
    self_reported_log = _synthetic_delivery_log(24, source="self_reported_shift_log", precision=0.4)
    self_reported_result = run_forecast(self_reported_log)
    assert self_reported_result["forecast"]["confidence"] != "HIGH", (
        "24 days of entirely self-reported data must not read HIGH confidence"
    )
    print(f"Step 4 -- self-reported-only forecast confidence: "
          f"{self_reported_result['forecast']['confidence']} (correctly not HIGH)")

    high_precision_result = run_forecast(_synthetic_delivery_log(LOOKBACK_DAYS))
    # 28 days of pure Grab-dinner shifts leaves every OTHER weekday/time_block
    # cell UNKNOWN; if any of those fall in the next 7 days that's a real,
    # correctly-reported gap, so we only assert on the confidence label, not
    # on data_gaps being empty.
    assert high_precision_result["forecast"]["confidence"] in ("HIGH", "MEDIUM")
    print(f"Step 4 -- high-precision forecast confidence: {high_precision_result['forecast']['confidence']}, "
          f"data_gaps found: {len(high_precision_result['data_gaps'])}")

    # --- Step 5: trace shape + empty-log edge case ---------------------------
    empty_result = run_forecast([])
    assert empty_result["forecast"]["confidence"] == "UNKNOWN"
    assert empty_result["forecast"]["shortfall_date"] is None
    trace = result_1["trace"]
    for field in ("node", "ts", "checked", "found", "concluded", "confidence", "degraded"):
        assert field in trace, f"trace missing required field {field!r}"
    print(f"Step 5 -- trace record OK: {trace}")

    node_update = forecast_node({"delivery_log": log_28})
    assert set(node_update.keys()) == {"forecast", "cells", "data_gaps", "open_questions", "trace"}
    assert isinstance(node_update["trace"], list) and len(node_update["trace"]) == 1

    print("\nALL FORECAST TESTS PASSED")
