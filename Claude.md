# Claude.md — Project Running Context

This file is the fast-load context for any agent (human or AI) picking up
work on this project mid-hackathon. It is a snapshot, not a spec — for the
authoritative "what are we building and is it done" answer, see
`HACKATHON_OBJECTIVES.md` (TODO: create if missing) and `README.md`.

## Current status

<!-- TODO: update this checklist as work lands; keep it in sync with README.md's "Current status" section -->

- [x] `shared/schema.py` — `CashFlowState` TypedDict, permission tiers, frozen.
- [x] `shared/money.py` — integer-cents money handling, tests pass.
- [x] `shared/resilience.py` — `resilient_call()` / `merge_tool_health()`.
- [x] `graph.py` / `stubs.py` — LangGraph skeleton on stubs + checkpointing.
- [x] `server.py`, `static/index.html` — FastAPI backend + trace-panel demo UI (trace panel not yet browser-verified, see HACKATHON_OBJECTIVES.md §7.1 Step 5).
- [x] `modules/forecast.py` — deterministic forecast engine (zero LLM calls). Tests pass; not yet swapped into `graph.py` (docs/SWAP_STATUS.md).
- [ ] `modules/ingestion.py` — ingestion & grounding, provenance-weighted.
- [ ] `modules/materiality.py` — materiality scoring + permission tiering.
- [ ] `modules/planner.py` — play library + plan selection.
- [ ] `data/benchmarks.json` — cited cold-start earnings prior.

## Tech stack

- Python 3.11+
- LangGraph + `langgraph-checkpoint` for orchestration and cross-session state persistence
- FastAPI + uvicorn for the backend API
- Plain HTML/CSS/JS for the trace-panel demo UI (no build step)
- AWS Bedrock (optional locally) — model `global.anthropic.claude-haiku-4-5-20251001-v1:0`, region `ap-southeast-1`, profile `workshop`
- LLM access goes through `shared/llm.py` — one call surface for both providers, switched via `LLM_PROVIDER=bedrock|gemini|none`. Gemini (`google-genai` SDK, API key from Google AI Studio, not Cloud Console) is used for local development; Bedrock is the submission target. No other module should call `boto3` or `google-genai` directly.
- <!-- TODO: add any additional libs (e.g. pandas, pydantic) once modules land -->

## Known assumptions

- Money is always integer cents; floats are never used anywhere in this project.
- The LLM (Claude Haiku via Bedrock) may transcribe and narrate only — it never calculates, ranks, decides, or estimates. All numeric/decision logic is deterministic Python.
- Insufficient evidence returns `UNKNOWN` explicitly; an average is never substituted for missing data.
- No module raises exceptions; every function returns a status dict (see `shared/resilience.py`).
- Every module must run to completion with no AWS credentials present, via its credential-free fallback path.
- `shared/schema.py` is frozen and owned by Member 1 — nobody else edits it directly; new fields go through them.
- <!-- TODO: add project-specific assumptions as they're discovered -->

## Gotchas discovered so far

<!-- TODO: append to this list as the team hits sharp edges during the build -->

- `trace` is the only field in `CashFlowState` with a reducer (`operator.add`); every other field is last-write-wins, so only one module should ever produce a given key per run — don't accidentally have two modules write the same non-trace key.
- The materiality gate is the core differentiator, not the forecast — resist the urge to over-invest in forecasting precision at the expense of the "stay silent unless it matters" logic.
- `MAX_REPLAN_LOOPS` (currently 2) hard-caps the low-confidence replanning cycle; a module that keeps returning low confidence will hit this ceiling and must degrade gracefully rather than loop forever.
- Play scoring uses each cell's 25th-percentile historical figure for `impact_cents`, not the median. The median already has bad nights baked in, so presenting it as expected earnings overstates certainty on demand-dependent gig income — the same failure mode as a hallucinated number, just produced by deterministic code instead of an LLM. The full range is carried separately as `impact_range` so the renderer can state a range instead of a point estimate. **Open gap, not yet confirmed fixed:** `modules/planner.py` does not exist yet (verified 2026-09-05) — flagging this as the intended design for whoever writes it, not as something already implemented. If a future version of that module is found scoring on the median instead, note it as an open gap rather than silently "fixing" it without discussion.

## Governance docs (imported below, so this context is always loaded)

- `docs/SYSTEM_DESIGN.md` — one-page agent spec (purpose, permission boundaries, state schema, data sources, failure modes)
- `docs/permission_boundaries.md` — table-driven permission spec
- `docs/data_source_registry.md` — truth registry for every data source the agent touches
- `docs/failure_mode_playbook.md` — pre-written recovery paths per tool
- `docs/DEFINITION_OF_DONE.md` — per-feature completion checklist
- `docs/SWAP_STATUS.md` — which graph.py nodes are real modules vs. stubs, updated as teammates land code
- `README.md` — setup and run instructions
- `HACKATHON_OBJECTIVES.md` — single source of truth for scope, per-step status, and checkpoints (§7.1, §13)

@docs/SYSTEM_DESIGN.md
@docs/permission_boundaries.md
@docs/data_source_registry.md
@docs/failure_mode_playbook.md
@docs/DEFINITION_OF_DONE.md
@docs/SWAP_STATUS.md
