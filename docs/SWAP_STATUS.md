# SWAP_STATUS.md — module swap-in status

**Update 2026-09-05:** `modules/forecast.py` (Member 2) now exists and is
real — `python modules/forecast.py` prints `ALL FORECAST TESTS PASSED`,
and it was additionally verified against the actual graph topology from
`graph.py` (a throwaway, uncommitted test script built the real
`StateGraph` with `modules.forecast.forecast_node` swapped in for the
stub; the graph ran end to end, exercised the replan loop up to
`MAX_REPLAN_LOOPS`, and reached `END` without raising). **`graph.py`
itself has not been edited** — the one-line import swap below is still
outstanding.

| Node in graph.py | Real module on disk? | Currently imports from |
|---|---|---|
| `ingestion` | ❌ `modules/ingestion.py` does not exist | `stubs.ingestion_node` |
| `forecast` | ✅ `modules/forecast.py` exists, tests pass, graph-compatibility verified | `stubs.forecast_node` (swap not yet applied in `graph.py`) |
| `gate` | ❌ `modules/materiality.py` does not exist | `stubs.gate_node` |
| `planner` | ❌ `modules/planner.py` does not exist | `stubs.planner_node` |

**0 of 4 nodes are swapped into `graph.py` itself yet** (1 of 4 real
modules exists and is ready to swap). The handbook's Step 4 "done when"
criterion ("at least two real modules are swapped in") is still NOT met.
This file exists instead of a fabricated pass — see server.py's swap-in
comment block, reproduced below, for the exact change each swap needs
once the real files land.

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

- `data/benchmarks.json` does not exist (Member 3, ingestion step 3).
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
