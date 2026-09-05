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

## §13 Status Summary

**No real teammate module exists yet.** `modules/` and `data/` are empty
directories — confirmed via `find . -name "*.py"` and `ls modules/ data/`
on 2026-09-05. `graph.py` imports all four nodes from `stubs.py`
(`from stubs import ingestion_node, forecast_node, gate_node, planner_node`).

| Area | Status | Notes |
|---|---|---|
| Shared contract (`shared/*`) | ✅ Done | Frozen, tested. |
| Orchestration skeleton (`graph.py`, `stubs.py`) | ✅ Done | Runs end-to-end on stubs, all 3 routing paths verified. |
| Persistence | ✅ Done | Verified across HTTP calls, not just in-process. |
| API surface (`server.py`) | ✅ Done | All 6 endpoints verified, never returns a 500. |
| Trace panel (`static/index.html`) | ⚠️ Built, not browser-verified | CSS spec compliance confirmed via grep only. |
| `modules/forecast.py` (Member 2) | ❌ Not started | |
| `modules/ingestion.py`, `data/benchmarks.json` (Member 3) | ❌ Not started | |
| `modules/materiality.py`, `modules/planner.py` (Member 4) | ❌ Not started | |
| Module swap-in | ❌ 0 of 4 nodes real | See docs/SWAP_STATUS.md. Seam tested and works. |
| AWS Bedrock / SSO credentials | ❓ Unverified | `/health` reports `bedrock: false` in this environment — nobody has confirmed whether `aws sso login --profile workshop` has been run by any teammate. |
| Flagship "3 properties in 15 seconds" demo (§4) | ✅ Fully provable on stubs | Verified 2026-09-05: reject → different plan proposed, with a trace record naming the excluded plan and constraint. Real `planner.py` still needs the same check (documented in docs/SWAP_STATUS.md). |
