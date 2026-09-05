# permission_boundaries.md — What the agent owns vs. delegates

Every action the agent can take must be classified here before it ships.
If an action isn't in this table, it defaults to `TIER_APPROVAL` (see
`shared/schema.py`) until someone adds a row and justifies otherwise.

| Action | Reversible? | Blast Radius | Requires Approval? | Reason |
|---|---|---|---|---|
| Read Bob's delivery/earnings log | Yes (read-only) | None | No | Read-only ingestion of Bob's own data; no side effects. |
| Compute forecast from delivery log | Yes (recomputed each run) | None (in-memory) | No | Deterministic, pure function; no external effect, cheap to redo. |
| Fall back to `data/benchmarks.json` prior when Bob's history is thin | Yes | Low — only affects this run's forecast confidence | No | Clearly flagged as an inferred, provenance-weighted fallback, not presented as Bob's own truth. |
| Ask Bob a targeted follow-up question (replan loop) | Yes | None | No | Purely informational request; Bob can ignore it. |
| Notify Bob of a forecast shortfall (materiality gate fires) | No (an alert, once sent, was sent) | Low — one notification | No (`TIER_NOTIFY`) | Informational; doesn't move money or commit Bob to anything. |
| Stay silent (materiality gate does not fire) | N/A | None | No | Default no-op; the core "don't nag" behavior. |
| Propose a plan (e.g. "defer Bill X by 3 days") | Yes, proposal itself is reversible | None until Bob acts | No | A proposal is just text; nothing executes yet. |
| Execute a plan that moves or schedules money (e.g. auto-defer a payment, transfer between accounts) | No / hard to reverse | High — real money, real due dates | **Yes (`TIER_APPROVAL`)** | Irreversible financial action with real-world consequences; the non-negotiable "ask before anything irreversible" rule. |
| Persist a new `user_constraint` Bob states (e.g. "never touch rent buffer") | Yes (Bob can restate/override later) | Low — affects future planning only | No | Recording stated preference, not acting on money. |
| Retry a failed tool call via `resilient_call()` | Yes | None | No | Internal resilience mechanism; no user-visible effect beyond normal operation. |
| Exceed `MAX_REPLAN_LOOPS` and still have low confidence | N/A (loop halts) | Low | Yes — surfaces to Bob as "I'm not confident, here's what I need" | Prevents infinite low-confidence looping from silently degrading into a bad autonomous guess. |
| Edit `shared/schema.py` (the frozen state contract) | Yes (git), but breaks everyone else's code | High — blocks all other modules | **Yes** — Member 1 only | Explicit team rule in README.md; not an end-user permission issue but a codebase governance one. |

<!-- TODO: add rows for any new tool/action as modules/forecast.py, ingestion.py, materiality.py, planner.py land -->

## How to use this table when adding a new action

1. Ask: if this action is wrong, can Bob (or the team) undo it cheaply? → **Reversible?**
2. Ask: if this action is wrong, how much does it affect — one number on screen, or real money/real deadlines? → **Blast Radius**
3. If either answer trends toward "no" / "large", default to `TIER_APPROVAL` and add the row explaining why, don't skip it because it seems obviously fine.
