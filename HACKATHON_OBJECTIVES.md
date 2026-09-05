# HACKATHON_OBJECTIVES.md

Referenced by README.md as the single source of truth for what's being
built and whether it's done. This file did not exist until 2026-09-05 —
created now, populated only with statuses actually verified by running the
code (see the "Done when" evidence under each item), not by prior claims.

## §7.1 Member 1's 5 steps

- [x] **Step 1 — Shared contract.** `shared/schema.py`, `shared/money.py`,
  `shared/resilience.py` all exist and pass their own tests.
  Evidence: `python shared/schema.py`, `python shared/money.py` (prints
  `ALL MONEY TESTS PASSED`), `python shared/resilience.py` (prints
  `ALL RESILIENCE TESTS PASSED`) all ran clean.

- [x] **Step 2 — Graph skeleton on stubs, with persistence.**
  Evidence: `python graph.py`'s "Demo 0" block ran and printed
  `Constraint survived across two invocations: ['never touch the phone bill']`
  after running the graph twice on thread `bob-persistence-demo`.

- [x] **Step 3 — Conditional routing and human-in-the-loop gate.**
  Evidence: `python graph.py` printed three distinctly labeled demo runs —
  (a) LOW confidence → `clarify_node` → HIGH confidence → `gate` → `planner`
  (`loop_count=1`); (b) gate stayed silent, run ended without reaching
  `planner`; (c) Tier 2 action halted with `awaiting_approval=True`, then
  `resume_after_approval(..., True)` completed it. Full console output
  pasted in the conversation log dated 2026-09-05.

- [x] **Step 4 (Member 1's half — FastAPI backend and swap seam).**
  Evidence: `curl`/TestClient against `/run`, `/approve`, `/answer`,
  `/trace/{id}`, `/health`, `/demo/reset` all returned valid JSON, including
  `/health` returning `{"ok": true, "bedrock": false}` with zero AWS
  credentials present.
  **However, the module-swap half of this step's "done when" line
  ("at least two real modules are swapped in") is NOT met — see
  [SWAP_STATUS.md](docs/SWAP_STATUS.md). 0 of 4 nodes are real modules.**
  The swap seam itself (does the graph fail loudly on a malformed swapped
  node?) was tested and confirmed working — also in docs/SWAP_STATUS.md.

- [~] **Step 5 — The trace panel.** `static/index.html` exists, is served
  by `server.py` at `/`, uses no external CDN/fonts, and its CSS was
  grep-confirmed to declare `font-size: 16px` body text, a `.found-nothing`
  grey style, and a `.trace-card.degraded` amber left border.
  The two-run persistence-and-rejection sequence was timed end-to-end over
  real HTTP at **190ms**, well under the 15-second criterion.
  **Marked [~] not [x] because:** the page has not actually been opened
  in a browser / on a projector — "renders legibly at 3 metres" is
  unverified, only its CSS declarations are.
  **Update 2026-09-05:** the flagship demo this panel showcases (§4 —
  reject a plan, run again, agent proposes something else and cites the
  constraint) is now fully provable — `stubs.py`'s `planner_node` checks
  `user_constraints` before choosing a plan. Verified over real HTTP,
  same server process: Run 1 proposed `defer_phone_bill`; rejecting it via
  `/approve {"approved": false}` stored `"never defer the phone bill by 3
  days"`; Run 2 on the same thread proposed `pause_streaming_subscription`
  instead, with a trace record citing the exact constraint that excluded
  the first plan. See docs/SWAP_STATUS.md for the full transcript and the
  matching requirement now written down for Member 4's real `planner.py`.

## §7.2 Member 2's 5 steps — deterministic forecast engine

- [x] **Step 1 — Daily totals + earnings profile.**
  Evidence: `python modules/forecast.py` builds 28 synthetic days,
  `compute_daily_totals` returns all 28, and `earnings_profile` reports
  `volatility_ratio: 0.085` — plausible (bounded strictly between 0 and 1,
  asserted).

- [x] **Step 2 — Cell profile + UNKNOWN rule.**
  Evidence: a fixture with exactly 2 same-weekday Grab-dinner rows produces
  one `(platform, weekday, time_block)` cell with
  `{"status": "UNKNOWN", "median_net_per_hour_cents": None, "observations": 2}`
  — asserted, never backfilled with an overall average.

- [x] **Step 3 — Projection + pessimistic stress case.**
  Evidence: `run_forecast()` called twice on identical input returns
  byte-identical `forecast` dicts (asserted equal) — no wall-clock or
  randomness inside the projection loop. The 14-day table prints with a
  shortfall date, and the pessimistic (60%-haircut) scenario's shortfall
  is asserted to be at least as large as the baseline's.

- [x] **Step 4 — Provenance-weighted confidence + gap detection.**
  Evidence: 24 days of entirely self-reported data (`precision: 0.4`)
  asserted to *not* return `HIGH` confidence (returns `LOW`, since 0.4 is
  below the `MEDIUM_CONFIDENCE_PRECISION = 0.5` floor). 28 days of
  partner-statement data (`precision: 1.0`) returns `HIGH`.

- [x] **Step 5 — Trace + full assert suite.**
  Evidence: `python modules/forecast.py` prints `ALL FORECAST TESTS PASSED`.
  `grep -c boto3 modules/forecast.py` returns 0. Every trace record carries
  all 7 required fields (`node`, `ts`, `checked`, `found`, `concluded`,
  `confidence`, `degraded`), asserted.
  **Also verified beyond the module's own suite:** swapped
  `modules.forecast.forecast_node` into a real (non-stub) LangGraph build
  of the actual graph topology from `graph.py` (throwaway test script, not
  committed) — the graph ran end to end, hit `MAX_REPLAN_LOOPS`, and
  reached `END` without raising. Confirms the real module's output shape
  is fully compatible with `route_after_forecast` and the rest of the
  routing logic, not just with its own standalone tests.
  **Known scope note:** `CashFlowState` carries no wallet-balance or bill
  fields, and no upstream module supplies one, so the 14-day projection
  uses a small, clearly-labelled synthetic scenario
  (`DEMO_STARTING_BALANCE_CENTS`, `DEMO_BILLS` at the top of
  `modules/forecast.py`) rather than fabricating a number under a
  different name. Flagged here so it's disclosed as synthetic in the
  deliverables (§9), not mistaken for real Bob data.

## §13 Status Summary

**Update 2026-09-05 (later):** `modules/forecast.py` (Member 2) now exists
and is real — `modules/` is no longer empty. `graph.py` itself is
unchanged (still imports the stub); the real module was verified against
the actual graph topology via a throwaway (uncommitted) test script, per
§7.2 Step 5. `data/` is still empty; `modules/ingestion.py`,
`modules/materiality.py`, `modules/planner.py` still don't exist.

| Area | Status | Notes |
|---|---|---|
| Shared contract (`shared/*`) | ✅ Done | Frozen, tested. |
| Orchestration skeleton (`graph.py`, `stubs.py`) | ✅ Done | Runs end-to-end on stubs, all 3 routing paths verified. |
| Persistence | ✅ Done | Verified across HTTP calls, not just in-process. |
| API surface (`server.py`) | ✅ Done | All 6 endpoints verified, never returns a 500. |
| Trace panel (`static/index.html`) | ⚠️ Built, not browser-verified | CSS spec compliance confirmed via grep only. |
| `modules/forecast.py` (Member 2) | ✅ Done | All 5 steps verified — see §7.2. `ALL FORECAST TESTS PASSED`. Not yet swapped into `graph.py` itself (that one-line edit is Member 1's per docs/SWAP_STATUS.md). |
| `modules/ingestion.py`, `data/benchmarks.json` (Member 3) | ❌ Not started | |
| `modules/materiality.py`, `modules/planner.py` (Member 4) | ❌ Not started | |
| Module swap-in | ⚠️ 0 of 4 nodes swapped into `graph.py` | `modules/forecast.py` exists and is graph-compatible (verified) but `graph.py`'s import line is untouched — see docs/SWAP_STATUS.md. |
| AWS Bedrock / SSO credentials | ❓ Unverified | `/health` reports `bedrock: false` in this environment — nobody has confirmed whether `aws sso login --profile workshop` has been run by any teammate. |
| Flagship "3 properties in 15 seconds" demo (§4) | ✅ Fully provable on stubs | Verified 2026-09-05: reject → different plan proposed, with a trace record naming the excluded plan and constraint. Real `planner.py` still needs the same check (documented in docs/SWAP_STATUS.md). |
