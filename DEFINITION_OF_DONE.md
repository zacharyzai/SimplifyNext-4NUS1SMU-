# DEFINITION_OF_DONE.md

Checklist to run through before calling any module or feature "done".
Copy this block per feature/module (e.g. into a PR description) and fill
it in — don't just link to this file.

## Per-feature checklist

- [ ] **Permission boundary documented?** — the action(s) this feature introduces have a row in [permission_boundaries.md](permission_boundaries.md) (reversible?, blast radius, approval required?, reason). No new action ships without one.
- [ ] **Data sources + fallbacks listed?** — any data source this feature reads/writes has an entry in [data_source_registry.md](data_source_registry.md) (latency SLA, fallback strategy, failure modes, truth level).
- [ ] **Happy-path + error-path tests?** — the module runs standalone (`python modules/<name>.py`) and prints a passing test summary, per README convention (`ALL <X> TESTS PASSED`); at least one error-path test exists per failure mode listed in [failure_mode_playbook.md](failure_mode_playbook.md) for that tool.
- [ ] **Tracing/audit log entry?** — every consequential decision the feature makes calls something equivalent to `TraceLog.record(...)` from [tracing_scaffold.py](tracing_scaffold.py) (or appends to `CashFlowState["trace"]` directly), with `query`, `sources_checked`, `decision`, `reasoning`, and `approval_status` all populated — no empty/placeholder reasoning strings.
- [ ] **User-facing explanation drafted?** — if this feature can ever produce something Bob sees (a notification, a proposed plan, a question), the exact copy has been drafted and reviewed, not left as a dev-facing string like `"shortfall_detected"`.
- [ ] **No module raises** — verified the feature returns a status dict on every failure path rather than throwing, per README non-negotiable rule #4.
- [ ] **Runs without AWS credentials** — verified the feature completes via its fallback path with no AWS credentials present, per README non-negotiable rule #5.
- [ ] **Money stays integer cents** — if the feature touches money at all, verified no floats are introduced anywhere in the path (README non-negotiable rule #1).

<!-- TODO: as the team settles on a PR template, move this checklist there so it's enforced per-PR rather than relying on manual discipline -->
