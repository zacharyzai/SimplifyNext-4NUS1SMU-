# data_source_registry.md — Truth registry

Every data source the agent touches, its reliability contract, and what
happens when it's unavailable. If a source isn't listed here, no module
should be depending on it yet.

## Bob's delivery/earnings log (`delivery_log`)

- **Source name:** `delivery_log` (ingested by `modules/ingestion.py`)
- **Latency SLA:** <!-- TODO: define once real ingestion source (CSV upload? platform API scrape?) is chosen. Local file read: expect <100ms. -->
- **Fallback strategy:** If missing or too sparse to forecast confidently, fall back to `data/benchmarks.json` cold-start prior, and record the gap in `data_gaps` / `open_questions` rather than guessing silently.
- **Failure modes:** missing file, malformed rows, partial history (e.g. only 3 days logged), duplicate entries.
- **Truth level:** **Authoritative** — this is Bob's own first-party data; nothing overrides it when present.

## Cold-start earnings benchmark (`data/benchmarks.json`)

- **Source name:** `data/benchmarks.json`
- **Latency SLA:** <100ms (local file read).
- **Fallback strategy:** None further down the chain — if this file is also missing/corrupt, the forecast must return `UNKNOWN` rather than fabricate a number. Never model-generated; must stay a cited, static reference.
- **Failure modes:** file missing, JSON malformed, stale/outdated benchmark data.
- **Truth level:** **Inferred** — a cited population-level prior, explicitly weighted below Bob's own history whenever both are available.

## AWS Bedrock — Claude Haiku (`us.anthropic.claude-haiku-4-5-20251001-v1:0`)

- **Source name:** Bedrock LLM, region `us-east-1` (also `us-east-2`/`us-west-2`, confirmed by organisers), profile `workshop`
- **Latency SLA:** <!-- TODO: measure typical Bedrock invoke latency in this region; budget ~1-3s for narration calls -->
- **Fallback strategy:** Every module must have a credential-free fallback path and complete without AWS credentials present (per README non-negotiable rule). On Bedrock failure, fall back to template-based narration rather than blocking the run.
- **Failure modes:** missing/expired AWS SSO credentials (`aws sso login --profile workshop` not run) or missing raw env credentials, throttling, timeout, malformed/refused completion. **Confirmed 2026-09-07:** the `global.` cross-region inference profile for this model resolves to an unregioned resource ARN that this hackathon account's org-level Service Control Policy explicitly denies `bedrock:InvokeModel` on — this is an account/region restriction, not a code or credential bug. The regional `us.` inference profile for the same model works. Also confirmed: `ap-southeast-1` is blocked entirely for this account; only `us-east-1`/`us-east-2`/`us-west-2` work.
- **Truth level:** **Inferred** — used only for transcription/narration of decisions already made deterministically; never a source of numeric or decision truth (see non-negotiable rule #2 in README.md).

## LangGraph checkpointer (session state persistence)

- **Source name:** LangGraph checkpoint store, keyed by thread id (e.g. `"bob-001"`)
- **Latency SLA:** <!-- TODO: define once checkpoint backend (in-memory vs. SQLite vs. other) is chosen in graph.py -->
- **Fallback strategy:** If checkpoint read fails, start a fresh `CashFlowState` for the thread and flag it via `tool_health`, rather than crashing the run.
- **Failure modes:** checkpoint store unavailable, corrupted checkpoint, thread id collision.
- **Truth level:** **Authoritative** for session continuity (constraints, prior decisions) — but always re-validated against fresh `delivery_log` data each run, never trusted as current financial truth on its own.

<!-- TODO: add a row for any live external API (bank balance API, Grab/foodpanda/Lalamove platform APIs) if/when the ingestion stub is replaced with a real integration -->

## Truth precedence, when sources conflict

1. Bob's own `delivery_log` (authoritative)
2. LangGraph checkpointed `user_constraints` / prior state (authoritative for continuity, re-validated each run)
3. `data/benchmarks.json` cold-start prior (inferred, only used to fill genuine gaps)
4. Bedrock LLM output (inferred narration only — never a decision input)
