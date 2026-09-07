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

modules/             One file per worker module, one owner each. All four are real and
                     swapped into graph.py — see docs/SWAP_STATUS.md for swap history.
  forecast.py           Member 2 — deterministic forecast engine (zero LLM calls).
  ingestion.py           Member 3 — ingestion & grounding, provenance-weighted.
  materiality.py         Member 4 — materiality scoring + permission tiering.
  planner.py             Member 4 — play library + plan selection.

data/
  benchmarks.json      Member 3 — cited cold-start earnings prior. Never model-generated.
  demo_scenarios.py    Named fixtures (steady_earner / shortfall_approval / thin_history)
                       the front end's scenario dropdown seeds a run with.

graph.py             Member 1 — LangGraph orchestration, routing, checkpointing.
server.py            Member 1 — FastAPI backend exposing the graph to the front end.
static/              Member 1 — trace panel + demo UI. Plain HTML/CSS/JS, no build step.
```

Ownership rule: **nobody edits `shared/schema.py` except Member 1.** Everyone
else writes pure functions — plain dicts in, plain dicts out. No module but
`graph.py` imports LangGraph. No module ever raises; every function returns a
status dict.

## Setup

Requires Python 3.11+. The system/Homebrew Python on some machines refuses
`pip install` (externally-managed), so use a project `.venv`:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Use `.venv/bin/python3` / `.venv/bin/uvicorn` for everything below — not the
system `python3`.

For local development without AWS, `LLM_PROVIDER=gemini` (see
`shared/llm.py`) needs a `GEMINI_API_KEY` in a `.env` file at the repo
root — get one from Google AI Studio, not the Cloud Console.

AWS Bedrock is optional for local development — every module has a
credential-free fallback path and must run to completion without AWS
credentials present. If you do have credentials, either:

```bash
aws sso login --profile workshop
```

or set raw credentials directly (e.g. in `.env`) — `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`. Only set `AWS_PROFILE` if
you actually have a named profile configured in `~/.aws/config`;
`shared/llm.py`/`server.py`'s Bedrock calls only pass `profile_name` to
boto3 when `AWS_PROFILE` is explicitly set, so raw env credentials are
picked up by boto3's default credential chain otherwise. (Passing an
explicit `profile_name` boto3 doesn't recognize will fail with
`ProfileNotFound` even when valid raw credentials are sitting right there
in the environment — this was a real bug here, since fixed.)

Model: `us.anthropic.claude-haiku-4-5-20251001-v1:0` (the regional US
inference profile -- the `global.` variant hits an org-level Service
Control Policy explicit deny on this hackathon account), region
`us-east-1` (also works: `us-east-2`, `us-west-2` -- confirmed by
organisers; `ap-southeast-1` is blocked), profile `workshop` (see
`shared/schema.py`).

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
.venv/bin/python3 shared/money.py          # ALL MONEY TESTS PASSED
.venv/bin/python3 shared/resilience.py     # ALL RESILIENCE TESTS PASSED
.venv/bin/python3 modules/forecast.py      # ALL FORECAST TESTS PASSED
.venv/bin/python3 modules/ingestion.py     # ALL INGESTION TESTS PASSED
.venv/bin/python3 modules/materiality.py   # ALL DECISION LAYER TESTS PASSED (materiality.py)
.venv/bin/python3 modules/planner.py       # ALL DECISION LAYER TESTS PASSED (planner.py)
```

Run the full graph (all 4 real modules, three routing paths end to end):

```bash
.venv/bin/python3 graph.py                 # ALL THREE ROUTING PATHS DEMONSTRATED
```

Serve the API + trace-panel UI:

```bash
.venv/bin/uvicorn server:app --reload      # http://localhost:8000
```

Then open `http://localhost:8000` and use the scenario dropdown (seeds a
named fixture — `steady_earner`, `shortfall_approval`, or `thin_history`,
listed by `GET /scenarios`) + **Run agent**, or type your own earnings/bill
message into the chat box. **Reset demo** clears the current thread's
checkpointed state so you can start over live without restarting the server.

## Current status

All four worker modules are real, tested, and swapped into `graph.py` — see
`docs/SWAP_STATUS.md` for the swap history and the bugs each swap surfaced.
`CLAUDE.md` is the fast-load running-context snapshot; `HACKATHON_OBJECTIVES.md`
is the source of truth for scope and checkpoint status.

- [x] `shared/schema.py`, `shared/money.py`, `shared/resilience.py` — written, frozen, tests pass.
- [x] `graph.py` — real LangGraph orchestration, routing, checkpointing (no stub nodes remain).
- [x] `server.py`, `static/` — FastAPI backend + trace-panel UI, including scenario dropdown and Tier-2 approval flow.
- [x] `modules/forecast.py`, `modules/ingestion.py`, `modules/materiality.py`, `modules/planner.py` — all real, all swapped into `graph.py`, all self-tests pass.
- [x] `data/benchmarks.json` — cited cold-start earnings prior with real SGD rates and citations.

## Non-negotiable rules (see `HACKATHON_OBJECTIVES.md` §4 for the full list)

1. Money is always integer cents. Never a float.
2. The LLM may transcribe and narrate. It may never calculate, rank, decide, or estimate.
3. Insufficient evidence returns `UNKNOWN`. Never an average standing in for an answer.
4. No module ever raises.
5. Every module must run with no AWS credentials present, via its fallback path.
