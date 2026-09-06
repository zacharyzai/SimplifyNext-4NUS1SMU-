# SYSTEM_DESIGN.md — Agentic Cash-Flow Copilot for Bob

One-page spec. If this file and the code disagree, the code is probably
wrong (or this file is stale) — fix whichever is out of date, don't just
pick one silently.

## Agent purpose

Forecast income shortfalls for a Singapore gig worker from their own
earnings history, and speak up only when a shortfall is material — staying
silent otherwise and asking before doing anything irreversible.

## Agentic properties this system is graded against

Each property exists to prevent one specific failure mode — not as an
abstract checklist item:

| Property | Failure mode it exists to prevent |
|---|---|
| Explicit Permission Boundaries | An irreversible action gets taken without asking. |
| Grounded & Deterministic | The system states a confidently wrong figure. |
| High Materiality Threshold | Alert fatigue — Bob learns to ignore every message because most of them didn't matter. |
| Resilient Replanning | Silent degradation into a plausible-looking but wrong output, instead of surfacing the uncertainty. |
| Legibility & Tracing | Reasoning nobody can audit, challenge, or replay after the fact. |
| State Persistence | Bob has to re-explain the same context (constraints, prior decisions) every single session. |

**Control flow is deterministic Python, not LLM-directed.** Routing
(`route_after_forecast`, `route_after_gate`), the replanning trigger, and
permission tiering all live in plain conditional Python reading state —
the model is never asked to decide which edge to take. By Anthropic's own
distinction between workflows (predefined code paths orchestrating LLM
calls) and agents (the model dynamically directs its own process), this
system is architecturally a **workflow** with two narrowly-scoped LLM
augmentations bolted on — transcription (turning a screenshot into
structured numbers) and narration (turning an already-made decision into
a plain-English sentence). That is a deliberate choice appropriate for a
system reasoning about someone's money, not an autonomy shortfall: the
places where the model could go wrong are fenced to the two jobs it's
least dangerous at, and everything that decides or calculates stays
verifiable, testable Python.

## Permission boundaries

Full table with examples lives in [permission_boundaries.md](permission_boundaries.md).
Summary of the tiering rule (from `shared/schema.py`):

| Tier | Value | Meaning |
|---|---|---|
| `TIER_AUTONOMOUS` | 0 | Reversible, small blast radius → agent acts and logs it, no approval needed. |
| `TIER_NOTIFY` | 1 | Informational only — surfaced to Bob, no action taken. |
| `TIER_APPROVAL` | 2 | Irreversible or materially large → agent halts and asks Bob before proceeding. |

The cautious default: when in doubt about reversibility or blast radius, the
router always resolves to the higher (more restrictive) tier.

<!-- TODO: as `modules/materiality.py` lands, link the actual scoring function that assigns tier_level -->

## State schema

Canonical definition: `shared/schema.py` → `CashFlowState` (TypedDict). There
is no separate mirrored schema file -- every module's trace record follows
the shape documented below directly against this one definition.
Persists across sessions via the LangGraph checkpointer, keyed by thread id
(e.g. `"bob-001"`).

Key fields that persist between sessions:
- `user_id` — identifies which gig worker's state this is.
- `user_constraints` — Bob's stated constraints (e.g. "never touch my rent buffer"), accumulated over time.
- `delivery_log` / `ingest_health` — raw earnings history and its data-quality signal.
- `forecast`, `cells`, `data_gaps`, `open_questions` — the forecast engine's output and its confidence gaps.
- `materiality_flag`, `tier_level` — the decision layer's "should I speak" and "what tier" outputs.
- `candidate_plans`, `rejected_plans`, `chosen_plan`, `explanation` — the planner's reasoning trail.
- `trace` — append-only audit log across the whole run (the one field with a reducer; see [[Claude.md]] gotchas).
- `tool_health`, `loop_count`, `awaiting_approval` — orchestration bookkeeping.

## Data sources it trusts (truth registry)

Full registry: [data_source_registry.md](data_source_registry.md). At a
glance:

1. **Bob's own delivery/earnings log** (`delivery_log`) — authoritative, first-party.
2. **`data/benchmarks.json`** — cited cold-start earnings prior, used only when Bob's own history is too thin. Never model-generated; provenance-weighted below his own data.
3. **AWS Bedrock (Claude Haiku)** — used for transcription/narration only, never treated as a source of numeric truth.
4. <!-- TODO: add any live external APIs (bank balance, platform APIs) once integrated -->

## Failure modes it anticipates

Full playbook: [failure_mode_playbook.md](failure_mode_playbook.md). Top-level
categories:

- **Data ingestion failure** — missing/malformed delivery log → falls back to `data/benchmarks.json` prior, flags `data_gaps`, never averages over missing data (returns `UNKNOWN` instead).
- **Low forecast confidence** — triggers the bounded replan loop (`MAX_REPLAN_LOOPS`), asking Bob targeted follow-up questions rather than guessing.
- **AWS/Bedrock unavailable** — every module has a credential-free fallback path and must complete without AWS credentials present.
- **Tool/API errors of any kind** — no module raises; every function returns a status dict merged via `merge_tool_health()`, surfaced in `tool_health`.
- **Irreversible or materially large action proposed** — routed to `TIER_APPROVAL`, agent halts and asks Bob explicitly.
