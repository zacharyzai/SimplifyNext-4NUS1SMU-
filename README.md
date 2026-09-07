# The Agentic Cash-Flow Copilot for Bob

IGNITE Agentic AI Hackathon 2026 · SimplifyNext · Software AI Track

An agentic cash-flow copilot for Singapore gig workers (Grab, foodpanda,
Lalamove). It forecasts income shortfalls from the worker's own earnings
history, stays silent unless the situation warrants interruption, and asks
before doing anything irreversible.

Full project spec, checkpoints and status live in `HACKATHON_OBJECTIVES.md` —
that file is the single source of truth for *what* we're building and
*whether* it's done. This README is about setup and running it.

## Why this exists

Existing budgeting apps assume regular income and generate constant noise,
so gig workers abandon them. Bob's income is lumpy; his bills are not. This
agent is engineered to notice a shortfall coming and to say nothing at all
when nothing is at stake — the materiality gate is the core differentiator,
not the forecasting.

## What makes it agentic, not a chatbot wrapper

- **Replanning loop** — low forecast confidence triggers targeted follow-up
  questions and a re-ingest, bounded at `MAX_REPLAN_LOOPS`.
- **Materiality gate** — a 0–100 scored decision on whether to speak at all.
- **Permission tiers** — `TIER_AUTONOMOUS` / `TIER_NOTIFY` / `TIER_APPROVAL`,
  drawn at "is this reversible and is the blast radius small?"
- **Checkpointed state** — constraints and decisions persist across sessions
  via a LangGraph checkpointer.

## Project structure

```
shared/             Owned by Member 1. The frozen contract everyone codes against.
  schema.py           CashFlowState (TypedDict) + shared constants (tiers, model id, etc.)
  money.py            Integer-cents money handling — no floats anywhere in this project.
  resilience.py       resilient_call() / merge_tool_health() — retry + fallback, never raises.

modules/             One file per worker module, one owner each.
  forecast.py           Member 2 — deterministic forecast engine (zero LLM calls).
  ingestion.py           Member 3 — ingestion & grounding, provenance-weighted.
  materiality.py         Member 4 — materiality scoring + permission tiering.
  planner.py             Member 4 — play library + plan selection.

data/
  benchmarks.json      Member 3 — cited cold-start earnings prior. Never model-generated.

graph.py             Member 1 — LangGraph orchestration, routing, checkpointing.
stubs.py             Member 1 — stub node implementations, swapped for real modules.
server.py            Member 1 — FastAPI backend exposing the graph to the front end.
static/index.html    Member 1 — trace panel + demo UI. Plain HTML/CSS/JS, no build step.
```

Ownership rule: **nobody edits `shared/schema.py` except Member 1.** Everyone
else writes pure functions — plain dicts in, plain dicts out. No module but
`graph.py` imports LangGraph. No module ever raises; every function returns a
status dict.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

For local development without AWS, `LLM_PROVIDER=gemini` (see
`shared/llm.py`) needs a `GEMINI_API_KEY` in a `.env` file at the repo
root — get one from Google AI Studio, not the Cloud Console.

AWS Bedrock is optional for local development — every module has a
credential-free fallback path and must run to completion without AWS
credentials present. If you do have credentials:

```bash
aws sso login --profile workshop
```

Model: `global.anthropic.claude-haiku-4-5-20251001-v1:0`, region
`ap-southeast-1`, profile `workshop` (see `shared/schema.py`).

### LLM provider — required for the clarify loop and chat box to actually work

`shared/llm.py` is the one call surface, switched via `LLM_PROVIDER`. If
this isn't set to a working provider, `extract_from_text()` and
`classify_message()` silently return nothing usable — the clarify loop
will ask its question, get an answer, fail to transcribe it, ask again,
and exhaust `MAX_REPLAN_LOOPS` without ever landing. **Before recording a
demo, set one of:**

```bash
# Local/demo path (recommended — no AWS SSO needed):
export LLM_PROVIDER=gemini
export GEMINI_API_KEY=...        # from Google AI Studio, not Cloud Console
# or put both in a .env file at the repo root (auto-loaded by shared/llm.py)

# Submission-target path:
export LLM_PROVIDER=bedrock
aws sso login --profile workshop
```

Check `GET /health` before recording — it reports `llm_provider` and
`llm_ready`. The UI also shows a small readiness badge for this so a dead
LLM path is visible up front instead of degrading silently mid-demo.

## Running it

Each module runs standalone and prints its own test output — this is how you
verify a module in isolation before it's wired into the graph:

```bash
python shared/money.py          # ALL MONEY TESTS PASSED
python shared/resilience.py     # ALL RESILIENCE TESTS PASSED
python modules/forecast.py      # ALL FORECAST TESTS PASSED
python modules/ingestion.py     # ALL INGESTION TESTS PASSED
python modules/materiality.py   # (materiality + planner) ALL DECISION LAYER TESTS PASSED
```

Once the graph exists:

```bash
python graph.py                 # runs the two-run persistence demo on thread "bob-001"
uvicorn server:app --reload     # serves the API + static/index.html at http://localhost:8000
```

Then open `http://localhost:8000` and use **Run agent** / **Simulate next
Wednesday** / **Reset demo** to drive the trace-panel demo.

## Current status

Build not yet complete — see `HACKATHON_OBJECTIVES.md` §13 (Status Summary)
for the live checkpoint tracker. As of now:

- [x] `shared/schema.py`, `shared/money.py`, `shared/resilience.py` — written, frozen, tests pass.
- [x] `graph.py` / `stubs.py` — graph skeleton on stubs + persistence.
- [x] `server.py`, `static/index.html`.
- [x] `modules/forecast.py` — deterministic forecast engine, `ALL FORECAST TESTS PASSED`. Not yet swapped into `graph.py` (see `docs/SWAP_STATUS.md`).
- [ ] `modules/ingestion.py`, `modules/materiality.py`, `modules/planner.py`, `data/benchmarks.json`.

## Non-negotiable rules (see `HACKATHON_OBJECTIVES.md` §4 for the full list)

1. Money is always integer cents. Never a float.
2. The LLM may transcribe and narrate. It may never calculate, rank, decide, or estimate.
3. Insufficient evidence returns `UNKNOWN`. Never an average standing in for an answer.
4. No module ever raises.
5. Every module must run with no AWS credentials present, via its fallback path.
