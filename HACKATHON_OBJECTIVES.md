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
  **Update 2026-09-05:** `graph.py` now imports the real
  `modules.forecast.forecast_node` (swap applied, not just verified
  compatible) — `python graph.py` and the full `/run → /approve reject →
  /run` HTTP sequence were both re-run against it and pass. **Still, the
  module-swap half of this step's "done when" line ("at least two real
  modules are swapped in") is NOT yet met — 1 of 4 nodes is real** (see
  [docs/SWAP_STATUS.md](docs/SWAP_STATUS.md)). The swap seam itself (does
  the graph fail loudly on a malformed swapped node?) was tested and
  confirmed working — also in docs/SWAP_STATUS.md.

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
  partner-statement data (`precision: 1.0`) prints `HIGH` in practice, but
  the code's own assertion is deliberately looser
  (`confidence in ("HIGH", "MEDIUM")`) with a comment explaining why: some
  cells in the synthetic data legitimately land `UNKNOWN` depending on
  gap timing, which can cap confidence at `MEDIUM` even with clean data —
  a defensible call, just worth stating precisely rather than as "always HIGH."

- [x] **Step 5 — Trace + full assert suite.**
  Evidence: `python modules/forecast.py` prints `ALL FORECAST TESTS PASSED`.
  `grep -c boto3 modules/forecast.py` returns 0. Every trace record carries
  all 7 required fields (`node`, `ts`, `checked`, `found`, `concluded`,
  `confidence`, `degraded`), asserted.
  **Also verified beyond the module's own suite:** swapped
  `modules.forecast.forecast_node` into a real (non-stub) LangGraph build
  of the actual graph topology from `graph.py` — the graph ran end to end, hit `MAX_REPLAN_LOOPS`, and
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

**Update 2026-09-07 (current, corrected — this table had drifted stale,
still claiming ingestion/materiality/planner "don't exist" after all three
had long since landed and been swapped in):** All 4 worker nodes are real
and swapped into `graph.py` (see `docs/SWAP_STATUS.md`). The recurring-bill
chat feature (schema field, forecast merge, server-side intent
classification, chat-log UI) is implemented and committed on `main`. The
materiality staleness false-positive bug (fired an urgent alert on a
brand-new, zero-history thread) is fixed and regression-tested. SSE
streaming (`GET /run/{id}/stream`) replaced polling. `data/benchmarks.json`
still has placeholder (all-zero, uncited) rates — the one real remaining
gap.

| Area | Status | Notes |
|---|---|---|
| Shared contract (`shared/*`) | ✅ Done | Frozen, tested. Extended twice (flagged both times): `raw_text`/`raw_delivery_rows`/`raw_source`/`raw_bank_rows`/`expenses`, then `recurring_bills: List[dict]`. |
| Orchestration skeleton (`graph.py`) | ✅ Done | All 4 real nodes wired; `clarify` and `execute_node` are genuine `interrupt_before` human-in-the-loop pauses. All 3 routing paths verified via `python graph.py` (both `LLM_PROVIDER=none` and `gemini`). |
| Persistence | ✅ Done | Verified across HTTP calls and across the clarify-resume flow specifically (loop_count/state carry forward on `/answer`, not reset). |
| API surface (`server.py`) | ✅ Done | `/run`, `/run/{id}/stream` (SSE), `/approve`, `/answer`, `/trace/{id}`, `/health`, `/demo/reset` — all verified, never returns a 500. |
| Trace panel + chat log (`static/`) | ✅ Done, browser-verified | Redesigned UI (warm-paper/IBM Plex design import), SSE-driven live trace with scroll bounds, plain-English node labels, humanized "checked" strings, and a chat-log UI for the earnings/bill message box (user messages + deterministic acknowledgements). |
| `modules/ingestion.py` (Member 3) | ✅ Done, swapped in | `ALL INGESTION TESTS PASSED`. Real `ingestion_node` live in `graph.py`. |
| `modules/forecast.py` (Member 2) | ✅ Done, swapped in | `ALL FORECAST TESTS PASSED`. Now also merges Bob-stated `recurring_bills` into its bill calendar alongside the synthetic `DEMO_BILLS` fallback. |
| `modules/materiality.py` (Member 4) | ✅ Done, swapped in | `ALL DECISION LAYER TESTS PASSED`. Staleness false-positive bug (see below) fixed with regression coverage. |
| `modules/planner.py` (Member 4) | ✅ Done, swapped in | `ALL DECISION LAYER TESTS PASSED`. |
| Module swap-in | ✅ 4 of 4 nodes swapped into `graph.py` | Handbook's "2+" bar cleared and exceeded — see `docs/SWAP_STATUS.md`. |
| `data/benchmarks.json` | ⚠️ Placeholder | All-zero rates, uncited — still needs real sourced figures before the cold-start prior is anything but inert. |
| AWS Bedrock / SSO credentials | ❓ Unverified | `/health` reports `bedrock: false` in this environment. Gemini (`LLM_PROVIDER=gemini`) is the working local/demo path; Bedrock remains the submission target, untested here. |
| Flagship "3 properties in 15 seconds" demo (§4) | ✅ Fully provable on real modules | Reject → different plan proposed, with a trace record naming the excluded plan and constraint — verified against the real `planner.py`/`materiality.py`, not stubs. |
| Materiality staleness bug | ✅ Fixed (2026-09-07, twice) | Two rounds. First: `score_staleness()`'s `days_since >= STALE_URGENT_DAYS` branch set `score = 90` unconditionally instead of gating on `nearest_bill`; and `gate_node` fed ingestion's "never observed" 9999 sentinel into staleness scoring as if it were 9999 real days of silence on a cold-start (empty/benchmark-only) thread. Second, narrower gap found by re-testing against the live pipeline rather than trusting the first fix's own unit tests: the cold-start check was keyed off `sources_seen` (`== ["benchmark_prior"]`), which misses the case where `raw_delivery_rows` are supplied but every row is rejected (bad dates) — `sources_seen` then becomes a mixed list like `["partner_statement", "benchmark_prior"]` that doesn't match the heuristic, letting the raw 9999 leak into the trace with a fabricated "Phone bill is due in 3 days" urgency. Fixed by keying cold-start detection off `ingest_health["date_range"] == [None, None]` instead — the actually-reliable signal for "no dated data exists," independent of what source labels are present. Regression tests cover: never-observed + no bill (silent), never-observed + bill due (fires), and the mixed-sources_seen case specifically, plus an assertion that no rendered trace string ever contains the raw sentinel value. |
| Demo scenario fixtures (`data/demo_scenarios.py`) | ✅ Added 2026-09-07 | The UI previously had no way to seed real earnings history — only free-text (`raw_text`) reached the graph, so a cold-start run only ever produced a trivial Tier 0 fallback plan with nothing to approve or reject. Added 3 named fixtures (`steady_earner`, `shortfall_approval`, `thin_history`) selectable via a new `<select>` in the UI, passed through `GET /run/{id}/stream?scenario=...` and a new `GET /scenarios` listing endpoint. `shortfall_approval` pre-seeds `user_constraints` excluding every reversible play (same trick `graph.py`'s own `__main__` demo (c) already used) to reliably force the one irreversible play (`defer_phone_bill`) into a genuine `TIER_APPROVAL` halt, rather than hand-tuning scoring constants. All three verified end-to-end over real HTTP against the live server (not just module self-tests): `steady_earner` reaches `materiality_gate` with `fire: False`; `shortfall_approval` reaches `awaiting_approval: True` with `chosen_plan.name: "Defer the phone bill by 3 days"`; `thin_history` reaches `awaiting_clarify: True`. |
| LLM readiness signal | ✅ Added 2026-09-07 | `extract_from_text()`/`classify_message()` degrade silently to "couldn't extract a figure" when no LLM provider is configured — a demo running with `LLM_PROVIDER=none` or a missing `GEMINI_API_KEY` would exhaust the clarify loop with no visible explanation why. `GET /health` now also reports `llm_provider` and `llm_ready`; the UI shows a small badge reading this so a dead LLM path is caught before recording, not mid-demo. README's Setup section documents the exact env vars needed. |
