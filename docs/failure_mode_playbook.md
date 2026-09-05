# failure_mode_playbook.md — Pre-written recovery paths

For each critical tool, what "success" looks like, the failure scenarios
we anticipate, and the fallback for each. No "TBD" entries — if a fallback
is genuinely unknown, the entry below states the safest conservative
default (`UNKNOWN` / halt-and-ask) rather than leaving it blank.

## `fetch_data` (ingestion — reading `delivery_log` / benchmarks)

**Expected output:** a populated `delivery_log` list of earnings records, or a clean `data_gaps` entry explaining what's missing.

| Failure scenario | Fallback strategy |
|---|---|
| Source file missing entirely | Fall back to `data/benchmarks.json` cold-start prior; mark `data_gaps` with `{"reason": "no_delivery_log"}`; forecast confidence is capped low so the replan loop can ask Bob for input. |
| File present but malformed (bad JSON/CSV row) | Skip and log the malformed row via `merge_tool_health()`; continue with whatever valid rows remain rather than failing the whole ingest. |
| Partial history (e.g. only 2 days logged) | Proceed but flag `open_questions` (e.g. "only 2 days of data — forecast confidence is low"); blend with `benchmarks.json` prior, weighted toward the prior more heavily. |
| Timeout / auth error (once a live platform API is wired in) | Retry once via `resilient_call()`; on second failure, fall back to the last successfully checkpointed `delivery_log` for this thread, flagged as stale. |

## `compute` (forecast engine — `modules/forecast.py`)

**Expected output:** a `forecast` dict + per-cell confidence, deterministic given the same `delivery_log`.

| Failure scenario | Fallback strategy |
|---|---|
| Input `delivery_log` is empty | Return `forecast: UNKNOWN` explicitly (never an average standing in for missing data, per README rule #3); set `open_questions` to prompt Bob for basic income info. |
| Confidence below threshold on a forecast cell | Add to `open_questions`, trigger the bounded replan loop (`loop_count += 1`, capped at `MAX_REPLAN_LOOPS`). |
| `loop_count` reaches `MAX_REPLAN_LOOPS` and confidence is still low | Halt the replan loop; surface remaining uncertainty directly to Bob rather than silently accepting a low-confidence forecast as final (routes to `TIER_NOTIFY` at minimum). |
| Unexpected exception inside the compute function | Should never happen (no module raises, per README rule #4) — if it does, treat as a bug: `resilient_call()` catches it, returns a status dict with `success: False`, and the run degrades to `forecast: UNKNOWN` rather than crashing the graph. |

## `approve` (human-in-the-loop gate — `awaiting_approval` / `TIER_APPROVAL`)

**Expected output:** Bob's explicit approve/reject response to a proposed irreversible action, resuming the graph from the checkpoint.

| Failure scenario | Fallback strategy |
|---|---|
| Bob never responds (times out) | Do **not** default to acting. Leave `awaiting_approval: True` and the plan un-executed; re-surface the pending approval next session via checkpointed state. |
| Bob's response is ambiguous (not a clear approve/reject) | Ask again with a clarifying question in the same tier; do not interpret ambiguity as consent. |
| Approval given, but underlying state has changed since the plan was proposed (e.g. a new delivery_log entry shifted the forecast) | Re-validate the plan against current state before executing; if materially different, withdraw the stale proposal and re-propose rather than executing on stale grounds. |
| Checkpoint restore fails mid-approval-flow | Restart the approval request from scratch on a fresh state rather than guessing what was previously pending — safer to re-ask than to assume. |

## `notify` (materiality gate firing — `TIER_NOTIFY`)

**Expected output:** a single, clear notification to Bob about a forecast shortfall, sent once per materially distinct event.

| Failure scenario | Fallback strategy |
|---|---|
| Bedrock (Claude Haiku) unavailable for narration | Fall back to a template-based, non-LLM narration string built directly from the deterministic `materiality_flag` / `forecast` data — the notification still goes out, just without LLM-polished phrasing. |
| Materiality score flickers near the threshold across runs (noisy near-boundary cases) | Apply hysteresis: once notified for a given shortfall event, don't re-notify for the same event unless the shortfall grows by a defined margin — prevents spammy back-and-forth notifications. |
| Duplicate notification risk (same shortfall detected twice across re-runs) | De-duplicate using `trace` history for the thread — check whether an equivalent `notify_shortfall` decision was already logged for this event before sending again. |
| Notification channel itself fails (e.g. server/socket error in `server.py`) | Log the failed send attempt in `tool_health`; keep the decision in `trace` as "decided but not delivered" so it surfaces in the trace panel and can be manually resent. |

<!-- TODO: add a table for any new critical tool as modules/planner.py and the live delivery-platform integration land -->
