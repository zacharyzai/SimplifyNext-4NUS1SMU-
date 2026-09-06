# SWAP_STATUS.md — module swap-in status

**Update 2026-09-07 (same branch) — a second review pass found three more
real gaps, all fixed and covered by new integration tests, not just unit
math:**

1. **Cooldown suppression never actually ran in the live graph.**
   `modules/materiality.py`'s `gate_node()` hardcoded `score_materiality(...,
   None, today)` and `score_staleness(..., previous_alert=None)` on every
   single call — the standalone `__main__` tests proved the cooldown MATH
   worked (by hand-constructing a fake `last_alert`), but the real node
   wired into `graph.py` could never suppress a repeat alert, because it
   never had a real `last_alert` to check against. Fixed by reading back
   `state.get("materiality_flag")` — this node's own previous output,
   already checkpointed, last-write-wins — as `previous_alert` before this
   run overwrites it. No schema change needed: `materiality_flag` already
   carried a `signature`; added a `"date"` key to both `score_materiality`'s
   and `score_staleness`'s return dicts so the same dict works as next
   run's `last_alert` input directly. New test (`materiality.py` STEP 5)
   calls `gate_node()` twice back-to-back, simulating exactly what the
   checkpointer hands back between two `graph.invoke()` calls on one
   thread, and asserts the second call is genuinely suppressed.
2. **`delivery_log` overwrote instead of accumulating.** `modules/
   ingestion.py`'s `ingestion_node()` rebuilt `delivery_log` from scratch
   every call, off only that call's raw input — since the field has no
   reducer in `CashFlowState`, a second "Run agent" with a new earnings
   message silently erased the first one. This made any multi-shift demo
   (e.g. "log 3 Saturday dinners to unlock a play that needs 3
   observations") fragile: it only worked if all 3 landed in one message,
   in one LLM call, in one shot. Fixed: `ingestion_node()` now reads back
   `state.get("delivery_log")`, keeps every real record from it, and adds
   this run's new ones on top, de-duplicated by `(date, platform,
   gross_cents, trips)`. A `benchmark_prior` cold-start fallback is
   dropped the moment real data exists. New test (`ingestion.py` STEP 6)
   submits two different shifts across two separate `ingestion_node()`
   calls and asserts both survive; also asserts resubmitting the same
   shift doesn't double-count it, and that warming up from cold-start
   correctly drops the placeholder benchmark rows.
3. **`data/benchmarks.json` had `median_net_per_hour_cents: 0` for all
   four entries**, cited only as `"TODO: public source"` — a cold-start
   forecast was honest and non-crashing (per the 2026-09-06 fix above) but
   projected literally $0/day. Replaced with real published figures (SGD
   8-12/hr typical range for SG food-delivery riders) and a real citation.
   Lalamove's figure is flagged in its own citation as a same-ballpark
   placeholder pending a courier-specific source, rather than silently
   presented as equally well-sourced as the other three.

Also deleted `tracing_scaffold.py` and `state_schema.py` from the repo
root — neither was imported anywhere, both were abandoned early-planning
drafts full of their own unresolved TODOs, and `docs/SYSTEM_DESIGN.md` was
incorrectly citing `state_schema.py` as if it were the real, current
schema mirror. Fixed that doc and `docs/DEFINITION_OF_DONE.md` (which
pointed at `tracing_scaffold.py`'s `TraceLog.record(...)`, a function that
was never actually used anywhere) to describe the trace pattern every
module actually uses instead.

**Update 2026-09-06 (on branch `member2-fixes-and-ui-polish`, not yet on main)
— live-tested the running site end-to-end and fixed what broke:**

1. **Cold start gave up entirely instead of forecasting.** A brand-new
   thread with zero dated history falls back to the `data/benchmarks.json`
   prior (correctly), but `modules/forecast.py`'s `run_forecast()` only
   ever looked at *dated* daily totals for its baseline, saw none, and
   returned `confidence: UNKNOWN, "cannot project"` — silently ignoring
   the benchmark-derived cells it had just built. This is exactly the
   click a judge makes first. Fixed with `_cold_start_daily_cents()`: when
   there's no dated history but there ARE `benchmark_prior` rows, estimate
   a rough daily figure from their rate × `ASSUMED_SHIFT_HOURS` and
   forecast from that instead, at whatever confidence its precision (0.2)
   actually earns — which the `determine_confidence()` fix below now
   correctly reports as `LOW` instead of forcing `UNKNOWN`.
2. **`HIGH_CONFIDENCE_MIN_DAYS = 14` was inconsistent with
   `MIN_OBSERVATIONS = 3`.** A worker who logs a shift every day spreads
   history evenly across all 7 weekdays, so 14 days gives every
   `(platform, weekday, time_block)` cell only 2 observations — always
   `UNKNOWN`, so `HIGH` confidence was mathematically unreachable for that
   pattern regardless of data quality. This directly contradicts this
   file's own 2026-09-06 note above claiming demo (b)'s "14 days of
   healthy history" scenario verifies `HIGH` — it verifies `MEDIUM` (rerun
   and confirmed via `python graph.py`). Bumped to 21 days (3 full weeks),
   which is the actual bottleneck MIN_OBSERVATIONS imposes. `graph.py`'s
   own demo (b) fixture still uses 14 days and still reports `MEDIUM` —
   that fixture wasn't touched (owned by Member 1); only the constants
   are now internally consistent about why 14 isn't enough.
3. **Frontend showed "Projected shortfall of unknown on an unknown date"**
   whenever there was no shortfall, instead of the trace panel's correct
   "No shortfall projected." `static/app.js`'s `renderConversation()` had
   one template that assumed a shortfall always exists; it now branches on
   `shortfall_date` being present, `null` with `UNKNOWN` confidence
   ("not enough history yet"), or `null` with a real confidence
   ("you're on track").
4. **"Simulate next Wednesday" removed** — it called the exact same
   endpoint as "Run agent" with no distinguishing parameter, and nothing
   in `server.py` or `graph.py` supported any date-simulation at all. It
   was a dead, misleading control, not a working feature being disabled.
5. **Added `requirements.txt`** (didn't exist) and pointed `README.md` at
   it — `pip install <ad-hoc list>` was already missing `google-genai` and
   there was no single reproducible install step for a clean clone.

Verified: all 7 module self-tests + `python graph.py`'s 3-path demo still
pass; added a new standalone assert (`modules/forecast.py` Step 6)
covering the cold-start fix specifically.

**Still open, not touched here:** `data/benchmarks.json`'s rates are still
`0` (placeholder), so a cold-start forecast is now honest and non-crashing
but currently projects $0/day until real cited figures land. And there's
still no LLM key configured in this environment, so the "answer a
question, confidence improves" success path is untested live — only the
"hit the ceiling" path is exercised.

**Update 2026-09-06 (gate + planner swap applied, all 4 nodes now real):**
`graph.py` now imports `from modules.materiality import gate_node` and
`from modules.planner import planner_node`. This is the first time `python
graph.py` has actually been run to completion with `langgraph` installed
(a `.venv` was created for this, since the system Python is
externally-managed) — which surfaced bugs the standalone module self-tests
never could, because they don't exercise the modules together through the
real graph:

1. **`modules/planner.py` had the same `import boto3` bug as
   `modules/ingestion.py` before it** — module-scope import, no try/except,
   crashes on any machine without boto3 installed. Fixed the same way:
   `render_explanation()` now goes through `shared.llm.converse()` instead
   of building its own Bedrock client, removing `get_bedrock_client()`
   entirely.
2. **`generate_plans()` crashed (`TypeError`) whenever the forecast was
   genuinely `UNKNOWN`.** `forecast.get("shortfall_amount_cents", 0)`
   only substitutes the default for a *missing* key — an `UNKNOWN`
   forecast (`modules/forecast.py`) sets that key present but `None`
   (e.g. when the only applicable play, `log_a_shift`, doesn't itself
   depend on a shortfall existing), and `None > 0` raises. Same class of
   bug repeated in `_template_explanation()`, `_call_llm_for_explanation()`,
   and `render_explanation()`'s figure-verification list. Fixed all four
   call sites to `.get(key) or 0` / `.get(key) or "an upcoming date"`.
3. **`gate_node`/`planner_node` wrappers didn't exist** — same gap as
   ingestion had. Added `gate_node(state)` to `materiality.py` (calls
   `score_materiality`/`score_staleness`, sets a provisional
   `TIER_NOTIFY`) and `planner_node(state)` to `planner.py` (calls
   `generate_plans`/`choose_plan`, then — since this is the first point in
   the run a specific action exists — runs it through
   `materiality.classify_action()` to get the real `tier_level`, possibly
   upgrading gate's provisional `TIER_NOTIFY` to `TIER_APPROVAL`).
4. **The bigger discovery: LangGraph silently strips any state key not
   declared in `CashFlowState`.** `graph.py`'s demo was passing
   `inputs={"raw_delivery_rows": [...], ...}` to exercise real ingestion
   scenarios — and `ingest_health.sources_seen` kept coming back
   `['benchmark_prior']` regardless, proving the raw input never reached
   `ingestion_node` at all. `shared/schema.py` (frozen, Member-1-owned)
   had no field for caller-supplied raw input — `raw_text`,
   `raw_delivery_rows`, `raw_source`, `raw_bank_rows`, `expenses` are
   pure inputs nobody's node writes, but LangGraph requires them declared
   to pass through. **Extended `CashFlowState` with these five optional
   fields** (flagging this prominently since it touches the frozen
   contract) — this is also what makes the trace-panel UI's new "Earnings
   message" box (paste text → transcribed by the LLM → ingested) actually
   work; before this fix it silently fell back to the benchmark prior no
   matter what the UI sent.
5. **`graph.py`'s own demo scenarios (a)/(b)/(c) relied on a `user_id`
   string trick** (`"bob-silent-001"` → stub's hardcoded "silent"
   scenario) that the real `gate_node` has no knowledge of — it doesn't
   look at `user_id` at all, correctly. Rewrote the three scenarios to
   drive real outcomes with real `raw_delivery_rows`: (a) 3 days of thin
   history → a genuine shortfall at non-HIGH confidence → `TIER_NOTIFY`;
   (b) 14 days of healthy recent history → `HIGH` confidence, no shortfall
   → both `score_materiality` and `score_staleness` genuinely stay quiet;
   (c) reuses (a)'s shortfall but pre-seeds constraints excluding every
   reversible play in `PLAY_LIBRARY`, forcing `defer_phone_bill`
   (irreversible + third-party) as the sole survivor → real
   `TIER_APPROVAL` through `classify_action`, not a scripted one. (One
   of those constraint strings, `"never defer bike servicing"`, initially
   also excluded `defer_phone_bill` by accident — both names share the
   word "defer" — a live example of the whole-word-overlap limitation
   `docs/SWAP_STATUS.md` already documented below; reworded to `"never
   touch bike servicing"`.)

Verified: `python graph.py` (both `LLM_PROVIDER=none` and
`LLM_PROVIDER=gemini`) runs all three routing paths to completion with
all four real modules and prints `ALL THREE ROUTING PATHS DEMONSTRATED.`
All six module self-tests (`shared/schema.py`, `shared/money.py`,
`shared/resilience.py`, `modules/ingestion.py`, `modules/forecast.py`,
`modules/materiality.py`, `modules/planner.py`) still pass.

**Environment note:** the system Python here is Homebrew-managed and
refuses `pip install`. Created a project `.venv` (gitignored) with
`langgraph`, `langgraph-checkpoint`, `fastapi`, `uvicorn`, `boto3`, and
`google-genai` installed — use `.venv/bin/python3` / `.venv/bin/uvicorn`
to run anything in this repo, not the system `python3`.

| Node in graph.py | Real module on disk? | Currently imports from |
|---|---|---|
| `ingestion` | ✅ real, swapped in, verified inside `graph.py` itself | `modules.ingestion.ingestion_node` |
| `forecast` | ✅ real, swapped in and verified | `modules.forecast.forecast_node` |
| `gate` | ✅ real, swapped in, verified inside `graph.py` itself | `modules.materiality.gate_node` |
| `planner` | ✅ real, swapped in, verified inside `graph.py` itself | `modules.planner.planner_node` |

**All 4 of 4 nodes are now real and swapped into `graph.py`.**

---

**Update 2026-09-06 (ingestion swap applied, bugs fixed):** `graph.py`'s
import line now reads `from modules.ingestion import ingestion_node`.
Landing this required three bug fixes, not just the import line:

1. **`modules/ingestion.py` had `import boto3` at module scope.** With
   boto3 not installed, the module failed at import time — before ever
   reaching AWS-credential logic — which broke the non-negotiable
   "every module runs to completion with no AWS credentials present" rule.
   Fixed by moving the import inside `get_bedrock_client()` behind a
   `try/except ImportError`, matching the lazy-import pattern `server.py`
   already used for the same reason. Verified: `python modules/ingestion.py`
   now runs to completion and prints `ALL INGESTION TESTS PASSED` with
   boto3 absent.
2. **`modules/forecast.py` silently dropped every `benchmark_prior` record.**
   `build_cell_profiles()` derived `weekday` only from a real `"date"`
   string; benchmark rows from `load_benchmark_prior()` carry
   `"date": None` (they're not tied to a specific day) and their weekday
   in a separate `"weekday"` field, so they never grouped into a cell at
   all. Separately, `_recompute_net_cents()` — the anti-hallucination
   "recompute every sum in Python" layer (`failure_mode_playbook.md`) —
   rebuilt `net_cents` from `gross/tip/fee`, all zero on a benchmark row,
   which would have zeroed out the cited rate even if it had reached a
   cell. Fixed both: `build_cell_profiles()` now reads `record["weekday"]`
   when present, falling back to deriving it from `date` otherwise;
   `_recompute_net_cents()` trusts `net_cents` as-is for `source ==
   "benchmark_prior"` rows (a static cited file, not an LLM output, so the
   recompute guarantee it exists for doesn't apply).
3. **`modules/ingestion.py`'s own `_build_health()` crashed on benchmark
   rows.** It called `sorted()` on a list of `date` values without
   filtering `None` — Python 3 can't compare `NoneType < NoneType` — so
   any run that fell back to the benchmark prior raised `TypeError`
   instead of returning a health dict. Fixed by filtering to dated rows
   before sorting, with a `[None, None]` date range when none exist.

Wrote the missing `ingestion_node(state)` wrapper referenced in this
file's own swap notes below (`modules/ingestion.py` had all the pure
functions — `normalise_records`, `parse_bank_statement`, `add_expenses`,
`load_benchmark_prior`, `extract_from_text`, `build_trace` — but no
function in the shape `graph.py` can import). It reads optional raw
inputs off `state` (`raw_delivery_rows`/`raw_source`, `raw_bank_rows`,
`raw_text`, `expenses`), merges whatever sources are present, and falls
back to `data/benchmarks.json` when nothing else produced a record —
returning `delivery_log`/`ingest_health`/`trace` in the exact shape
`stubs.ingestion_node` used.

Verified standalone (not yet over real HTTP — `langgraph` isn't installed
in this environment, so `graph.py` itself couldn't be run end-to-end; this
is a gap in verification, not a claim that it was checked):
- `ingestion_node({})` (fresh thread, no raw data) falls back to the
  benchmark prior instead of returning an empty log, health reports
  `ok: True`, `rows_rejected: 0`, `sources_seen: ['benchmark_prior']`.
- Feeding that benchmark-only `delivery_log` into `forecast_node` no
  longer crashes and no longer silently drops the rows — they now group
  into cells (still `status: UNKNOWN` per cell, since one benchmark row
  per platform/weekday/time_block is still below `MIN_OBSERVATIONS` —
  that's the existing "never backfill with an average" rule doing its
  job correctly, not a bug).
- `ingestion_node({"raw_delivery_rows": [...], "raw_source":
  "partner_statement"})` with real rows produces a normal high-precision
  `delivery_log` and a sane `forecast_node` output.
- Both modules' own `__main__` self-tests (`ALL INGESTION TESTS PASSED`,
  `ALL FORECAST TESTS PASSED`) still pass after all four fixes above.

**Still open, flagged rather than fixed here:** `data/benchmarks.json`'s
four entries all have `median_net_per_hour_cents: 0` and
`"citation": "TODO: public source"` — real cited figures still need to be
sourced before the cold-start prior is anything but an inert placeholder.
And even with real figures, `MIN_OBSERVATIONS` means a single benchmark
row per cell can never alone produce a `KNOWN` cell — worth a deliberate
decision (more granular benchmark rows? a separate threshold for
`benchmark_prior`-sourced cells?) rather than inheriting this ceiling by
accident.

**Update 2026-09-05 (swap applied):** `graph.py`'s import line now reads
`from modules.forecast import forecast_node` — the swap described below is
no longer pending, it's live. Verified after applying it:
- `python graph.py` runs all 3 demo paths clean with the real module (trace
  shows `forecast_engine`, her module's node name, not `forecast_node (STUB)`).
- The demo script's own assertion had to be loosened: it previously asserted
  exactly 1 replan loop, which was actually asserting the *stub's* scripted
  behavior. The real module honestly stays at whatever confidence 2 days of
  stub ingestion data supports (MEDIUM), so it correctly runs to the
  `MAX_REPLAN_LOOPS` ceiling instead of "resolving" after one pass. Fixed the
  assertion to check `1 <= loop_count <= MAX_REPLAN_LOOPS` — the actual
  invariant that matters — rather than the stub-specific exact count.
- Re-ran the full flagship reject-a-plan demo over real HTTP with the real
  forecast module in the pipeline: Run 1 proposed `defer_phone_bill`, reject
  stored the constraint, Run 2 proposed `pause_streaming_subscription`
  instead. Unaffected by the swap, as expected (forecast and planner are
  independent nodes).

| Node in graph.py | Real module on disk? | Currently imports from |
|---|---|---|
| `ingestion` | ✅ real, swapped in and verified standalone (see 2026-09-06 note above; not yet run inside `graph.py` itself — `langgraph` not installed here) | `modules.ingestion.ingestion_node` |
| `forecast` | ✅ real, swapped in and verified | `modules.forecast.forecast_node` |
| `gate` | ✅ written and tested | `stubs.gate_node` |
| `planner` | ✅ written and tested | `stubs.planner_node` |

**2 of 4 nodes are swapped into `graph.py`.** The handbook's Step 4 "done
when" criterion ("at least two real modules are swapped in") is now met.
This file exists instead of a fabricated pass — see
server.py's swap-in comment block, reproduced below, for the exact change
each remaining swap needs once the real files land.

**Note for the next swap:** `graph.py` keeps the corresponding stub import
commented out alongside the real one (`# from stubs import forecast_node`)
rather than deleted, per the fallback convention below — revert that one
line if `modules/forecast.py` ever regresses and blocks a demo.

## Exact one-line import change per node (from server.py's swap notes)

```python
# graph.py line 25 currently:
from stubs import ingestion_node, forecast_node, gate_node, planner_node

# Member 2 (forecast) lands:
from modules.forecast import forecast_node

# Member 3 (ingestion) lands (needs a thin ingestion_node(state) wrapper
# around ingestion.py's real functions -- same shape stubs.py uses):
from modules.ingestion import ingestion_node

# Member 4 (materiality/gate) lands:
from modules.materiality import gate_node

# Member 4 (planner) lands:
from modules.planner import planner_node
```

Each import is independent — swap them in one at a time as each teammate
finishes, keeping the corresponding stub import as a fallback (comment it
out rather than delete it) so a late or broken module never blocks the
demo. Recompile is automatic; nothing else in `graph.py` needs to change
because every stub already returns the exact dict shape `shared/schema.py`
defines.

## Swap seam verified (2026-09-05)

Tested with a deliberately broken stand-in for `forecast_node` that omits
the required `"forecast"` key from its return dict — simulating a real
module shipped with a bug. Result:

```
Invoking graph with a broken forecast_node (missing 'forecast' key)...
CAUGHT LOUDLY AND SPECIFICALLY: KeyError: 'forecast'
This is exactly what should happen -- route_after_forecast tried to read
state['forecast']['confidence'], but the swapped-in node never set 'forecast'.
```

The graph does **not** silently swallow a malformed module's output — it
raises immediately and specifically at the point the missing key is first
read (inside `route_after_forecast`), which is the earliest possible
failure point given LangGraph does not itself validate `TypedDict` shapes
at runtime. This means a teammate's broken module will fail fast and
loudly during integration testing rather than producing a plausible-looking
but wrong result three nodes downstream.

**Caveat:** this only catches a *missing* key being read by name. A module
that returns the wrong *type* for a present key (e.g. `forecast` as a
string instead of a dict) will fail at whatever point downstream code
first tries to use it as the wrong type — also loud, but not necessarily
at the earliest possible node. There is no schema validation layer
(e.g. pydantic) enforcing the full `CashFlowState` shape on every write;
the contract is enforced by convention (Rules Sheet #6/#9) and by each
node's own return-shape discipline, not by the graph itself.

## Also true right now

- `data/benchmarks.json` exists but is a placeholder (see 2026-09-06 note
  above — all-zero rates, uncited).
- **Fixed 2026-09-05:** `stubs.py`'s `planner_node` now checks
  `state["user_constraints"]` before choosing a plan, so the flagship
  "three properties in fifteen seconds" demo (§4 — reject a plan, run
  again, agent proposes something else and cites the constraint) is fully
  provable end to end. Verified over real HTTP, same server process:
  Run 1 on a fresh thread proposed `defer_phone_bill`; rejecting it via
  `POST /approve {"approved": false}` stored the constraint
  `"never defer the phone bill by 3 days"`; Run 2 on the same thread
  proposed `pause_streaming_subscription` instead, with a trace record
  reading `"Rejected plan defer_phone_bill: violates stored constraint
  'never defer the phone bill by 3 days'."` If every candidate is excluded,
  it falls through to `chosen_plan: None`, `tier_level: TIER_NOTIFY` — also
  verified directly against `planner_node`.

### Requirement for Member 4's real `modules/planner.py`

This is not stub-only behaviour — **the real planner must do the same
check.** Before returning `chosen_plan`, it must:
1. Read `state.get("user_constraints", [])`.
2. Reject any candidate play whose action conflicts with a stored
   constraint (append it to `rejected_plans` with reason `"user_constraint"`,
   not `"insufficient_history"` or `"not_applicable"`).
3. Append a trace entry naming the rejected play and the exact constraint
   text that excluded it (e.g. `"Rejected plan X: violates stored
   constraint '...'"`)  — this is what lets the trace panel show the
   rejection was reasoned, not silent.
4. If no candidate survives, return `chosen_plan: None` (explicitly, not
   omitted — omitting the key leaves a stale plan from a previous run on
   the same thread checkpointed) and downgrade to `TIER_NOTIFY` rather than
   halting for approval on a plan that no longer exists.

**Known limitation to carry forward, not silently fix:** the stub's
constraint matching is exact whole-word overlap after stripping a small
stopword list — no stemming, no synonym handling. `"never touch
subscriptions"` will **not** exclude a plan whose description says
"subscription" (singular). A real implementation should decide
deliberately whether to add stemming/lemmatization or move to matching on
a structured field (e.g. `action_type` + a target entity id) rather than
free text, instead of inheriting this gap by accident.
