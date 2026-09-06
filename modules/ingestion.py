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


def _derive_time_block(hour):
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
        dates = sorted(r["date"] for r in accepted)
        date_range = [dates[0], dates[-1]]
        weighted_precision = sum(r["precision"] for r in accepted) / len(accepted)
        last_date = datetime.strptime(dates[-1], "%Y-%m-%d")
        days_since_last_observation = (datetime.utcnow() - last_date).days
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

import boto3

MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
AWS_REGION = "ap-southeast-1"


def get_bedrock_client():
    session = boto3.Session(
        profile_name=os.getenv("AWS_PROFILE", "workshop"),
        region_name=os.getenv("AWS_DEFAULT_REGION", AWS_REGION),
    )
    return session.client("bedrock-runtime")


TRANSCRIBE_SYSTEM_PROMPT = (
    "Transcribe the figures exactly as written. Do not total, adjust, "
    "estimate, or infer any number. If a field is not present, use null. "
    "Return ONLY a JSON array of objects with keys: date, platform, "
    "start_hour, gross_cents, tip_cents, platform_fee_cents, trips, hours. "
    "No prose, no markdown code fences, no explanation."
)


def extract_from_text(raw_text, bedrock_client):
    """Pull structured records out of an unstructured payout message or a
    pasted earnings screen.

    THE RULE THAT DEFINES THIS FUNCTION: the model may TRANSCRIBE and may
    NOT calculate, total, adjust, infer or estimate. Even if it transcribes
    perfectly, every record still passes through normalise_records so
    Python -- not the model -- re-derives every sum. Well-formed and
    correct are different properties; only recomputation catches a fluent
    but wrong transcription.
    """
    if bedrock_client is None:
        print("WARNING: no Bedrock client available -- skipping LLM transcription")
        return []

    try:
        response = bedrock_client.converse(
            modelId=MODEL_ID,
            system=[{"text": TRANSCRIBE_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": raw_text}]}],
            inferenceConfig={"temperature": 0},
        )
        usage = response.get("usage", {})
        print(f"Bedrock usage -- input tokens: {usage.get('inputTokens')}, output tokens: {usage.get('outputTokens')}")

        content = response["output"]["message"]["content"][0]["text"].strip()
        if content.startswith("```"):
            content = content.strip("`").replace("json\n", "", 1)

        parsed_rows = json.loads(content)
    except Exception as e:
        print(f"WARNING: Bedrock transcription unavailable or failed ({e!r}) -- returning no records")
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

    try:
        client = get_bedrock_client()
    except Exception:
        client = None

    transcribed = extract_from_text(payout_message, client)
    print(f"\n{len(transcribed)} records transcribed (expected 0 with no AWS credentials).")

    print("\nSTEP 4 COMPLETED (ran to completion with no AWS credentials present)")
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

    print("\nALL INGESTION TESTS PASSED")



