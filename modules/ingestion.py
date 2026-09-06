"""modules/ingestion.py -- Member 3: Ingestion & Grounding Layer.

Reads Bob's earnings data from whatever source is available (weekly
partner statements, bank exports, screenshots, self-reports, or a
cold-start benchmark prior), normalises it into a common record shape,
and tags every record with a provenance/precision score so downstream
modules know how much to trust it.

Rules for this whole file:
- No pandas/numpy. Standard library + boto3 only.
- All money is integer cents, never floats.
- Nothing in this file may ever raise. Every function returns data or a
  status dict; failures are reported, not thrown.
- Active Singapore platforms: Grab, foodpanda, Lalamove. Deliveroo ceased
  Singapore operations on 4 March 2026 and must not appear anywhere.
"""

import json
import os
import re
import sys
from datetime import datetime
from typing import List

# Makes "from shared.money import ..." work no matter what folder you run
# this file from -- it points Python at the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.money import cents_from_string, format_cents

SOURCE_PRECISION = {
    "partner_statement": 1.0,   # weekly platform statement, per-trip, authoritative
    "bank_statement": 0.9,      # payout credits by descriptor, weekly resolution
    "screenshot_ocr": 0.7,      # model-transcribed, Python-validated
    "self_reported": 0.4,       # user recall
    "benchmark_prior": 0.2,     # public survey data, cold start only
}

TIME_BLOCK_RANGES = [
    (6, 11, "morning"),
    (11, 14, "lunch"),
    (14, 17, "afternoon"),
    (17, 21, "dinner"),
]


def _parse_hour(raw):
    """Accepts an int hour, or a string like '18', '18:00', '6pm'. Returns
    an int 0-23, or None if nothing usable is found -- callers treat that
    as "unknown time block", not a reason to reject the whole row, since
    an LLM transcribing free text may reasonably return either shape."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        hour = int(raw)
        return hour if 0 <= hour <= 23 else None
    match = re.match(r"^\s*(\d{1,2})", str(raw))
    if not match:
        return None
    hour = int(match.group(1))
    return hour if 0 <= hour <= 23 else None


def _derive_time_block(hour):
    hour = _parse_hour(hour)
    if hour is None:
        return "unknown"
    for start, end, label in TIME_BLOCK_RANGES:
        if start <= hour < end:
            return label
    if hour >= 21 or hour < 2:
        return "late"
    return "unknown"


def _parse_date(raw):
    """Accepts 'YYYY-MM-DD', 'DD/MM/YYYY', or 'D MMM YYYY'. Returns
    'YYYY-MM-DD' or None if nothing matches."""
    if not raw:
        return None
    raw = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def normalise_records(raw_rows, source):
    """Turn raw row-dicts into validated, provenance-tagged records.

    Returns (records, health). Never raises -- a bad row is dropped and
    the reason is recorded. Silently accepting a bad row would let a
    corrupted number flow straight into Bob's forecast; rejecting and
    reporting keeps the failure visible instead of hiding it.
    """
    precision = SOURCE_PRECISION.get(source, 0.2)
    accepted = []
    rejection_reasons = []
    seen_keys = set()

    for i, row in enumerate(raw_rows):
        try:
            date = _parse_date(row.get("date"))
            if date is None:
                rejection_reasons.append(f"row {i}: unparseable date {row.get('date')!r}")
                continue

            platform = row.get("platform")
            if not platform:
                rejection_reasons.append(f"row {i}: missing platform")
                continue

            try:
                gross_cents = cents_from_string(row.get("gross_cents", 0))
            except ValueError:
                rejection_reasons.append(f"row {i}: unparseable gross amount {row.get('gross_cents')!r}")
                continue

            if gross_cents < 0:
                rejection_reasons.append(f"row {i}: negative gross amount")
                continue

            try:
                tip_cents = cents_from_string(row.get("tip_cents", 0)) if row.get("tip_cents") not in (None, "") else 0
            except ValueError:
                tip_cents = 0

            try:
                fee_cents = cents_from_string(row.get("platform_fee_cents", 0)) if row.get("platform_fee_cents") not in (None, "") else 0
            except ValueError:
                fee_cents = 0

            net_cents = gross_cents + tip_cents - fee_cents
            time_block = _derive_time_block(row.get("start_hour"))
            trips = int(row.get("trips", 0) or 0)
            hours = float(row.get("hours", 0.0) or 0.0)

            record = {
                "date": date,
                "platform": platform,
                "time_block": time_block,
                "gross_cents": gross_cents,
                "tip_cents": tip_cents,
                "platform_fee_cents": fee_cents,
                "net_cents": net_cents,
                "trips": trips,
                "hours": hours,
                "source": source,
                "precision": precision,
            }

            dedup_key = (date, platform, gross_cents, trips)
            if dedup_key in seen_keys:
                rejection_reasons.append(f"row {i}: duplicate of an earlier row {dedup_key}")
                continue
            seen_keys.add(dedup_key)

            accepted.append(record)

        except Exception as e:
            rejection_reasons.append(f"row {i}: unexpected error {e!r}")
            continue

    health = _build_health(raw_rows, accepted, rejection_reasons, source)
    return accepted, health


def _build_health(raw_rows, accepted, rejection_reasons, source):
    rows_in = len(raw_rows)
    rows_accepted = len(accepted)
    rows_rejected = rows_in - rows_accepted

    if accepted:
        weighted_precision = sum(r["precision"] for r in accepted) / len(accepted)
        # benchmark_prior rows carry date=None (they aren't tied to a real
        # day) -- sorted() can't compare None, so only dated rows count here.
        dates = sorted(r["date"] for r in accepted if r.get("date"))
        if dates:
            date_range = [dates[0], dates[-1]]
            last_date = datetime.strptime(dates[-1], "%Y-%m-%d")
            days_since_last_observation = (datetime.utcnow() - last_date).days
        else:
            date_range = [None, None]
            days_since_last_observation = 9999
    else:
        date_range = [None, None]
        weighted_precision = 0.0
        days_since_last_observation = 9999

    ok = rows_accepted > 0
    reject_ratio = (rows_rejected / rows_in) if rows_in else 0.0
    degraded = ok and (reject_ratio > 0.10 or weighted_precision < 0.6)

    return {
        "ok": ok,
        "rows_in": rows_in,
        "rows_accepted": rows_accepted,
        "rows_rejected": rows_rejected,
        "rejection_reasons": rejection_reasons,
        "date_range": date_range,
        "sources_seen": [source],
        "weighted_precision": round(weighted_precision, 2),
        "days_since_last_observation": days_since_last_observation,
        "degraded": degraded,
    }

PAYOUT_DESCRIPTORS = {
    re.compile(r"GRAB", re.IGNORECASE): "Grab",
    re.compile(r"FOODPANDA|FP\s?SG", re.IGNORECASE): "foodpanda",
    re.compile(r"LALAMOVE", re.IGNORECASE): "Lalamove",
}


def parse_bank_statement(rows, today):
    """Turn bank export rows {"date","description","amount"} into
    delivery_log records. Only matches CREDITS whose description matches a
    known payout descriptor -- a salary or a friend's transfer is not gig
    income.

    This source is platform-agnostic by design: it keeps working even if a
    platform disappears overnight, as Deliveroo did in Singapore on
    4 March 2026, because it doesn't depend on that platform's own
    statement existing at all.
    """
    bank_rows = []
    for row in rows:
        try:
            amount_cents = cents_from_string(row.get("amount"))
        except ValueError:
            continue  # malformed amount, can't tell if it's even a credit

        if amount_cents <= 0:
            continue  # debit, not gig income

        description = str(row.get("description", ""))
        platform = None
        for pattern, name in PAYOUT_DESCRIPTORS.items():
            if pattern.search(description):
                platform = name
                break
        if platform is None:
            continue  # unrecognised credit, not gig income

        bank_rows.append({
            "date": row.get("date"),
            "platform": platform,
            "gross_cents": row.get("amount"), 
            "tip_cents": 0,
            "platform_fee_cents": 0,
            "trips": 0,
            "hours": 0.0,
        })

    return normalise_records(bank_rows, source="bank_statement")

def add_expenses(delivery_log, expenses):
    """Merge Bob's stated outgoings into the same log so downstream code
    (the forecast engine) only has one list to read for both income and
    outflow. Expenses always carry a NEGATIVE net_cents and are tagged
    self_reported, since Bob is the one telling us about them.
    """
    expense_records = []
    for e in expenses:
        try:
            amount_cents = cents_from_string(e.get("amount_cents"))
        except ValueError:
            continue
        expense_records.append({
            "date": e.get("date"),
            "platform": "expense",
            "time_block": "unknown",
            "gross_cents": 0,
            "tip_cents": 0,
            "platform_fee_cents": 0,
            "net_cents": -abs(amount_cents),
            "trips": 0,
            "hours": 0.0,
            "source": "self_reported",
            "precision": SOURCE_PRECISION["self_reported"],
        })
    return delivery_log + expense_records


def load_benchmark_prior(path, platform):
    """Read the cited cold-start earnings prior from data/benchmarks.json.

    CRITICAL: this must never invent numbers. If the file is missing or
    malformed, it returns [] and prints a warning -- the agent would
    rather say UNKNOWN than fabricate a benchmark from memory. This is
    scaffolding for day one only; it decays out of relevance as Bob's own
    observations accumulate.
    """
    if not os.path.exists(path):
        print(f"WARNING: benchmark file not found at {path!r} -- returning no prior data")
        return []

    try:
        with open(path, "r") as f:
            entries = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARNING: could not read benchmark file ({e!r}) -- returning no prior data")
        return []

    records = []
    for entry in entries:
        if entry.get("platform") != platform:
            continue
        records.append({
            "date": None,
            "platform": entry["platform"],
            "time_block": entry.get("time_block", "unknown"),
            "weekday": entry.get("weekday"),
            "gross_cents": 0,
            "tip_cents": 0,
            "platform_fee_cents": 0,
            "net_cents": entry.get("median_net_per_hour_cents", 0),
            "trips": 0,
            "hours": 1.0,
            "source": "benchmark_prior",
            "precision": SOURCE_PRECISION["benchmark_prior"],
            "citation": entry.get("citation", "uncited"),
        })
    return records

from shared.llm import converse

TRANSCRIBE_SYSTEM_PROMPT = (
    "Transcribe the figures exactly as written. Do not total, adjust, "
    "estimate, or infer any number. If a field is not present, use null. "
    "Return ONLY a JSON array of objects with keys: date (YYYY-MM-DD; use "
    "null if no year is stated -- never guess one), platform, start_hour "
    "(integer hour of day 0-23, e.g. 18 for 6pm or the first hour of a "
    "stated range -- never a string like '18:00'), gross_cents, tip_cents, "
    "platform_fee_cents (despite the field names, put the DOLLAR amount "
    "exactly as written, e.g. \"62.00\" for $62.00 -- NOT the number of "
    "cents; the pipeline converts these to integer cents downstream), "
    "trips, hours. "
    "No prose, no markdown code fences, no explanation."
)


def extract_from_text(raw_text):
    """Pull structured records out of an unstructured payout message or a
    pasted earnings screen.

    THE RULE THAT DEFINES THIS FUNCTION: the model may TRANSCRIBE and may
    NOT calculate, total, adjust, infer or estimate. Even if it transcribes
    perfectly, every record still passes through normalise_records so
    Python -- not the model -- re-derives every sum. Well-formed and
    correct are different properties; only recomputation catches a fluent
    but wrong transcription.

    Goes through shared.llm.converse() -- the one call surface for both
    providers -- rather than talking to boto3/google-genai directly, so
    this function (and the demo) works unchanged whether LLM_PROVIDER is
    bedrock, gemini, or none.
    """
    content = converse(TRANSCRIBE_SYSTEM_PROMPT, raw_text)
    if content is None:
        print("WARNING: no LLM available -- skipping LLM transcription")
        return []

    try:
        if content.startswith("```"):
            content = content.strip("`").replace("json\n", "", 1)
        parsed_rows = json.loads(content)
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"WARNING: LLM transcription returned unparseable output ({e!r}) -- returning no records")
        return []

    records, _health = normalise_records(parsed_rows, source="screenshot_ocr")
    return records

def build_trace(ingest_health):
    """One append-only trace record in the shared shape, describing what
    ingestion checked and found -- including sources that returned
    nothing, which the trace panel renders in grey as proof of work.
    """
    sources_seen = ingest_health.get("sources_seen", [])
    checked = [f"source: {s}" for s in sources_seen] or ["no sources available"]

    rows_accepted = ingest_health.get("rows_accepted", 0)
    rows_rejected = ingest_health.get("rows_rejected", 0)
    weighted_precision = ingest_health.get("weighted_precision", 0.0)

    concluded = (
        f"Read {rows_accepted} valid earnings records "
        f"(rejected {rows_rejected}); average source reliability "
        f"{weighted_precision:.2f}."
    )

    degraded = ingest_health.get("degraded", False) or not ingest_health.get("ok", False)
    confidence = "LOW" if degraded else ("MEDIUM" if weighted_precision < 0.85 else "HIGH")

    return {
        "node": "ingestion",
        "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checked": checked,
        "found": {
            "rows_accepted": rows_accepted,
            "rows_rejected": rows_rejected,
            "weighted_precision": weighted_precision,
        },
        "concluded": concluded,
        "confidence": confidence,
        "degraded": degraded,
    }


def ingestion_node(state: dict):
    """Real ingestion node for graph.py -- reads whatever raw sources are
    present in `state` (all optional; a fresh thread may have none) and
    returns delivery_log/ingest_health/trace in the shape shared/schema.py
    defines (same shape stubs.ingestion_node uses).

    Recognised state inputs, all optional:
      raw_delivery_rows + raw_source -- rows for normalise_records()
      raw_bank_rows                  -- rows for parse_bank_statement()
      raw_text                       -- unstructured text for extract_from_text()
      expenses                       -- outgoings for add_expenses()
    If none of these yield any records, falls back to the benchmarks.json
    cold-start prior rather than returning an empty, UNKNOWN-forcing log.

    ACCUMULATES, does not overwrite: `delivery_log` has no reducer in
    CashFlowState, so a naive node would silently replace Bob's entire
    logged history with just whatever was submitted THIS run -- a second
    "Run agent" with a new earnings message would erase the first one.
    This node instead reads back its own previous output (`state.get
    ("delivery_log")`), keeps every real record from it, and adds this
    run's new ones on top (de-duplicated by (date, platform, gross_cents,
    trips), so accidentally resubmitting the same shift twice doesn't
    double-count it). A benchmark_prior fallback from an earlier cold
    start is dropped the moment real data exists -- it was only ever a
    stand-in for missing history, never something to keep averaging in
    once Bob has logged anything real.

    When raw_text is present (most often Bob's real answer to a clarify
    question, injected by server.py's /answer) but extract_from_text()
    can't pull a usable figure out of it, that fact is surfaced directly
    in this node's own trace record rather than silently falling through
    to whatever data-gap forecast.py re-derives next -- a resurfaced
    question with no explanation reads as a bug even when it's actually
    just an unparseable answer.
    """
    new_records: List[dict] = []
    rows_in = 0
    rejection_reasons: List[str] = []
    answer_text_unusable = False

    if state.get("raw_delivery_rows"):
        records, health = normalise_records(
            state["raw_delivery_rows"], source=state.get("raw_source", "self_reported")
        )
        new_records += records
        rows_in += health["rows_in"]
        rejection_reasons += health["rejection_reasons"]

    if state.get("raw_bank_rows"):
        records, health = parse_bank_statement(state["raw_bank_rows"], today=None)
        new_records += records
        rows_in += health["rows_in"]
        rejection_reasons += health["rejection_reasons"]

    if state.get("raw_text"):
        transcribed = extract_from_text(state["raw_text"])
        if transcribed:
            new_records += transcribed
            rows_in += len(transcribed)  # else rows_rejected = rows_in - accepted goes negative
        else:
            answer_text_unusable = True

    if state.get("expenses"):
        new_records = add_expenses(new_records, state["expenses"])

    previous_log = state.get("delivery_log") or []
    carried_forward = [r for r in previous_log if r.get("source") != "benchmark_prior"]

    delivery_log: List[dict] = []
    if new_records or carried_forward:
        dedup_seen = set()
        for record in carried_forward + new_records:
            key = (record.get("date"), record.get("platform"), record.get("gross_cents"), record.get("trips"))
            if key in dedup_seen:
                continue
            dedup_seen.add(key)
            delivery_log.append(record)

    total_considered = rows_in + len(carried_forward)

    if not delivery_log:
        for platform in ("Grab", "foodpanda", "Lalamove"):
            delivery_log += load_benchmark_prior("data/benchmarks.json", platform)
        if delivery_log:
            total_considered += len(delivery_log)  # every benchmark row is "in" and accepted, none rejected

    health = _build_health([None] * total_considered, delivery_log, rejection_reasons, source="mixed")
    # Reflects every source actually present across the ACCUMULATED log, not
    # just what this one run contributed -- e.g. a thread with a partner
    # statement from last week and a screenshot from today correctly shows
    # both, not whichever one happened to be submitted most recently.
    health["sources_seen"] = sorted({r.get("source", "unknown") for r in delivery_log}) if delivery_log else ["none"]

    trace_record = build_trace(health)
    if answer_text_unusable:
        trace_record["concluded"] = (
            "Couldn't extract a usable figure from your answer -- asking again. "
            + trace_record["concluded"]
        )
        trace_record["degraded"] = True

    return {
        "delivery_log": delivery_log,
        "ingest_health": health,
        "trace": [trace_record],
    }


if __name__ == "__main__":
    print("=" * 60)
    print("STEP 1: normalise_records() -- provenance-weighted ingestion")
    print("=" * 60)

    sample_rows = [
        {"date": "2026-08-24", "platform": "Grab", "start_hour": 18, "gross_cents": "45.00", "tip_cents": "3.00", "platform_fee_cents": "5.00", "trips": 4, "hours": 3.5},
        {"date": "2026-08-24", "platform": "foodpanda", "start_hour": 12, "gross_cents": "22.50", "tip_cents": "0.00", "platform_fee_cents": "2.00", "trips": 2, "hours": 1.5},
        {"date": "2026-08-25", "platform": "Grab", "start_hour": 8, "gross_cents": "30.00", "tip_cents": "1.50", "platform_fee_cents": "3.00", "trips": 3, "hours": 2.0},
        {"date": "2026-08-25", "platform": "Grab", "start_hour": 19, "gross_cents": "-10.00", "tip_cents": "0.00", "platform_fee_cents": "1.00", "trips": 1, "hours": 1.0},
        {"date": "not-a-date", "platform": "Grab", "start_hour": 18, "gross_cents": "40.00", "tip_cents": "0.00", "platform_fee_cents": "4.00", "trips": 3, "hours": 2.0},
        {"date": "2026-08-26", "platform": "foodpanda", "start_hour": 13, "gross_cents": "18.00", "tip_cents": "0.00", "platform_fee_cents": "1.50", "trips": 2, "hours": 1.0},
        {"date": "2026-08-26", "platform": "foodpanda", "start_hour": 13, "gross_cents": "18.00", "tip_cents": "0.00", "platform_fee_cents": "1.50", "trips": 2, "hours": 1.0},
        {"date": "2026-08-27", "platform": "Grab", "start_hour": 20, "gross_cents": "55.00", "tip_cents": "5.00", "platform_fee_cents": "6.00", "trips": 5, "hours": 4.0},
        {"date": "2026-08-27", "platform": "Lalamove", "start_hour": 15, "gross_cents": "28.00", "tip_cents": "0.00", "platform_fee_cents": "2.50", "trips": 2, "hours": 1.5},
        {"date": "2026-08-28", "platform": "Grab", "start_hour": 9, "gross_cents": "20.00", "tip_cents": "0.00", "platform_fee_cents": "2.00", "trips": 2, "hours": 1.5},
        {"date": "2026-08-28", "platform": "foodpanda", "start_hour": 18, "gross_cents": "35.00", "tip_cents": "2.00", "platform_fee_cents": "3.00", "trips": 3, "hours": 2.5},
        {"date": "2026-08-29", "platform": "Grab", "start_hour": 19, "gross_cents": "48.00", "tip_cents": "4.00", "platform_fee_cents": "5.00", "trips": 4, "hours": 3.0},
    ]

    log, health = normalise_records(sample_rows, source="partner_statement")

    print(f"\n{len(log)} records accepted out of {len(sample_rows)} raw rows:")
    for r in log:
        print(f"  {r['date']} {r['platform']:<10} {r['time_block']:<10} net={format_cents(r['net_cents'])}")

    print("\ningest_health:")
    for k, v in health.items():
        print(f"  {k}: {v}")

    assert health["rows_in"] == 12
    assert health["rows_rejected"] == 3
    assert health["rows_accepted"] == 9
    assert len(health["rejection_reasons"]) == 3

    print("\nSTEP 1 ASSERTS PASSED")

    print("\n" + "=" * 60)
    print("STEP 2: parse_bank_statement() -- platform-agnostic payout matching")
    print("=" * 60)

    bank_rows = [
        {"date": "2026-08-25", "description": "GRAB* PAYOUT SG", "amount": "142.50"},
        {"date": "2026-08-26", "description": "FOODPANDA SG PTE LTD", "amount": "88.20"},
        {"date": "2026-08-27", "description": "NETS QR PAYMENT", "amount": "-12.00"},
        {"date": "2026-08-28", "description": "JOHN TAN PAYNOW TRANSFER", "amount": "50.00"},
        {"date": "2026-08-29", "description": "LALAMOVE SG", "amount": "N/A"},
    ]

    bank_log, bank_health = parse_bank_statement(bank_rows, today="2026-09-06")

    print(f"\n{len(bank_log)} bank records accepted:")
    for r in bank_log:
        print(f"  {r['date']} {r['platform']:<10} net={format_cents(r['net_cents'])} (source={r['source']})")

    gross_values_seen = {r["gross_cents"] for r in bank_log}
    assert len(bank_log) == 2, "expected exactly the two real payout credits"
    assert 5000 not in gross_values_seen, "the PayNow transfer must not appear"
    assert 1200 not in gross_values_seen, "the debit must not appear"

    print("\nSTEP 2 ASSERTS PASSED")

    print("\n" + "=" * 60)
    print("STEP 3: add_expenses() + load_benchmark_prior()")
    print("=" * 60)

    expenses = [
        {"date": "2026-09-01", "amount_cents": "1200.00"},
        {"date": "2026-09-05", "amount_cents": "80.00"},
    ]
    log_with_expenses = add_expenses(bank_log, expenses)
    print(f"\nLog grew from {len(bank_log)} to {len(log_with_expenses)} records after adding expenses.")
    for r in log_with_expenses[-2:]:
        print(f"  {r['date']} {r['platform']:<10} net={format_cents(r['net_cents'])}")

    benchmark_records = load_benchmark_prior("data/benchmarks.json", "Grab")
    print(f"\n{len(benchmark_records)} benchmark_prior records loaded for Grab.")
    for r in benchmark_records:
        print(f"  {r['time_block']} weekday={r['weekday']} -> {format_cents(r['net_cents'])}/hr ({r['citation']})")

    missing_case = load_benchmark_prior("data/does_not_exist.json", "Grab")
    assert missing_case == [], "a missing benchmark file must return [] and never raise"

    print("\nSTEP 3 ASSERTS PASSED")
    print("\n" + "=" * 60)
    print("STEP 4: extract_from_text() -- LLM transcription, tightly fenced")
    print("=" * 60)

    payout_message = (
        "Your Grab earnings for 5 Sep: 6 trips, gross $62.00, tips $4.50, "
        "platform fee $6.00, online 18:00-21:30."
    )

    transcribed = extract_from_text(payout_message)
    print(f"\n{len(transcribed)} records transcribed (0 unless LLM_PROVIDER is set to a working provider).")

    print("\nSTEP 4 COMPLETED (ran to completion with no LLM provider configured)")
    print("\n" + "=" * 60)
    print("STEP 5: build_trace() + hostile input test suite")
    print("=" * 60)

    trace = build_trace(health)
    print("\nTrace record from Step 1's ingestion run:")
    for k, v in trace.items():
        print(f"  {k}: {v}")

    # a) negative fare
    neg_log, neg_health = normalise_records(
        [{"date": "2026-09-01", "platform": "Grab", "start_hour": 10, "gross_cents": "-5.00", "trips": 1, "hours": 1.0}],
        source="self_reported",
    )
    assert neg_health["rows_rejected"] == 1

    # b) unknown date format
    _, bad_date_health = normalise_records(
        [{"date": "5th of September", "platform": "Grab", "start_hour": 10, "gross_cents": "20.00", "trips": 1, "hours": 1.0}],
        source="self_reported",
    )
    assert bad_date_health["rows_rejected"] == 1

    # c) exact duplicate
    dup_row = {"date": "2026-09-01", "platform": "Grab", "start_hour": 10, "gross_cents": "20.00", "trips": 1, "hours": 1.0}
    dup_log, dup_health = normalise_records([dup_row, dup_row.copy()], source="self_reported")
    assert len(dup_log) == 1
    assert dup_health["rows_rejected"] == 1

    # d) entirely empty input
    empty_log, empty_health = normalise_records([], source="self_reported")
    assert empty_health["ok"] is False
    assert len(empty_log) == 0

    # e) missing platform
    _, no_platform_health = normalise_records(
        [{"date": "2026-09-01", "start_hour": 10, "gross_cents": "20.00", "trips": 1, "hours": 1.0}],
        source="self_reported",
    )
    assert no_platform_health["rows_rejected"] == 1

    # f) money field reading "N/A"
    _, na_health = normalise_records(
        [{"date": "2026-09-01", "platform": "Grab", "start_hour": 10, "gross_cents": "N/A", "trips": 1, "hours": 1.0}],
        source="self_reported",
    )
    assert na_health["rows_rejected"] == 1

    # g) idempotence: ingesting the SAME file twice must not double income
    same_rows = [
        {"date": "2026-09-01", "platform": "Grab", "start_hour": 18, "gross_cents": "40.00", "tip_cents": "2.00", "platform_fee_cents": "4.00", "trips": 3, "hours": 2.0},
    ]
    first_pass, _ = normalise_records(same_rows, source="partner_statement")
    combined_log, _ = normalise_records(same_rows + same_rows, source="partner_statement")
    assert len(combined_log) == len(first_pass), "ingesting the same rows twice must de-duplicate, not double income"

    # h) mix of partner_statement and self_reported -> weighted_precision between the two
    mixed_a, _ = normalise_records(
        [{"date": "2026-09-02", "platform": "Grab", "start_hour": 18, "gross_cents": "40.00", "trips": 3, "hours": 2.0}],
        source="partner_statement",
    )
    mixed_b, _ = normalise_records(
        [{"date": "2026-09-03", "platform": "Grab", "start_hour": 18, "gross_cents": "35.00", "trips": 2, "hours": 1.5}],
        source="self_reported",
    )
    mixed_health = _build_health(mixed_a + mixed_b, mixed_a + mixed_b, [], "mixed")
    assert SOURCE_PRECISION["self_reported"] < mixed_health["weighted_precision"] < SOURCE_PRECISION["partner_statement"]

    print("\n" + "=" * 60)
    print("STEP 6: ingestion_node() ACCUMULATES across two calls, not overwrites")
    print("=" * 60)
    print("Previously each call rebuilt delivery_log from scratch off only THIS")
    print("run's raw input -- a second submission silently erased the first one.")

    node_state_run1 = {
        "raw_delivery_rows": [
            {"date": "2026-08-15", "platform": "Grab", "start_hour": 18,
             "gross_cents": "70.00", "tip_cents": "5.00", "platform_fee_cents": "6.00",
             "trips": 5, "hours": 4.0},
        ],
        "raw_source": "self_reported",
    }
    node_result_1 = ingestion_node(node_state_run1)
    assert len(node_result_1["delivery_log"]) == 1, "first submission should log exactly 1 record"

    # Simulate what the checkpointer does between two graph.invoke() calls on
    # the same thread: this node's own previous output (delivery_log) is
    # exactly what a second call would see in `state`, PLUS a fresh raw_text
    # submission (a second shift, submitted separately -- this is exactly
    # the "3 Saturday dinners in one message" scenario, done as 2 SEPARATE
    # messages instead, which used to silently lose the first one).
    node_state_run2 = {
        "delivery_log": node_result_1["delivery_log"],
        "raw_delivery_rows": [
            {"date": "2026-08-22", "platform": "Grab", "start_hour": 18,
             "gross_cents": "72.00", "tip_cents": "4.00", "platform_fee_cents": "6.00",
             "trips": 5, "hours": 4.0},
        ],
        "raw_source": "self_reported",
    }
    node_result_2 = ingestion_node(node_state_run2)
    assert len(node_result_2["delivery_log"]) == 2, (
        f"second submission should ADD to the first, giving 2 total records, "
        f"got {len(node_result_2['delivery_log'])} -- the first shift was lost"
    )
    dates_on_file = sorted(r["date"] for r in node_result_2["delivery_log"])
    assert dates_on_file == ["2026-08-15", "2026-08-22"], "both separately-submitted shifts must both be present"

    # Resubmitting the EXACT same shift a third time must not double-count it.
    node_state_run3 = {**node_state_run2, "delivery_log": node_result_2["delivery_log"]}
    node_result_3 = ingestion_node(node_state_run3)
    assert len(node_result_3["delivery_log"]) == 2, "resubmitting the same raw_source rows must de-duplicate, not grow"

    # A cold-start thread that only ever got a benchmark_prior fallback, then
    # LATER gets a real shift, must drop the placeholder rather than average
    # a real logged shift together with four fabricated cold-start rows.
    cold_start_result = ingestion_node({})
    assert cold_start_result["ingest_health"]["sources_seen"] == ["benchmark_prior"]
    warmed_up_result = ingestion_node({
        "delivery_log": cold_start_result["delivery_log"],
        "raw_delivery_rows": node_state_run1["raw_delivery_rows"],
        "raw_source": "self_reported",
    })
    assert warmed_up_result["ingest_health"]["sources_seen"] == ["self_reported"], (
        "the benchmark_prior placeholder must be dropped the moment real data lands"
    )
    assert len(warmed_up_result["delivery_log"]) == 1

    print(f"Run 1 (1 shift submitted):              delivery_log has {len(node_result_1['delivery_log'])} record(s)")
    print(f"Run 2 (2nd shift submitted separately):  delivery_log has {len(node_result_2['delivery_log'])} record(s) -- both kept")
    print(f"Run 3 (same shift resubmitted):          delivery_log has {len(node_result_3['delivery_log'])} record(s) -- deduplicated")
    print(f"Cold start -> real data: benchmark_prior dropped, sources_seen={warmed_up_result['ingest_health']['sources_seen']}")
    print("\nSTEP 6 ASSERTS PASSED -- delivery_log now genuinely accumulates across ingestion_node calls")

    print("\nALL INGESTION TESTS PASSED")



