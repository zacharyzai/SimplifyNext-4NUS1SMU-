"""tracing_scaffold.py -- audit log structure for agent decisions.

Every consequential decision the agent makes (speak vs. stay silent,
which plan to propose, which tier to route to) should produce one
LogEntry. This is what makes the trace panel (static/index.html) and any
post-hoc audit possible.

TODO: once graph.py exists, have each node append a LogEntry to
CashFlowState["trace"] (which already has an operator.add reducer -- see
shared/schema.py) instead of / in addition to writing here.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Optional
import json


@dataclass
class LogEntry:
    """One audited decision point."""

    timestamp: str                     # ISO 8601, e.g. datetime.utcnow().isoformat()
    query: str                         # what question/trigger prompted this decision, e.g. "weekly forecast check for bob-001"
    sources_checked: List[str]         # names of data sources consulted, e.g. ["delivery_log", "benchmarks.json"]
    decision: str                      # the decision reached, e.g. "notify_shortfall", "stay_silent"
    reasoning: str                     # human-readable why, e.g. "forecast confidence 0.82, shortfall $45 > materiality threshold"
    approval_status: str               # one of: "not_required", "pending", "approved", "rejected"
    # TODO: promote approval_status to a Literal[...] once the exact vocabulary is finalized with Member 4 (materiality.py)


class TraceLog:
    """In-memory audit log for a single run. Swap the backing store for
    something durable (file/DB) before relying on this beyond a demo.
    """

    def __init__(self) -> None:
        self._entries: List[LogEntry] = []

    def record(
        self,
        query: str,
        sources_checked: List[str],
        decision: str,
        reasoning: str,
        approval_status: str = "not_required",
    ) -> LogEntry:
        """Log one decision. Returns the entry so callers can also attach
        it to CashFlowState["trace"] if needed.
        """
        entry = LogEntry(
            timestamp=datetime.utcnow().isoformat(),
            query=query,
            sources_checked=sources_checked,
            decision=decision,
            reasoning=reasoning,
            approval_status=approval_status,
        )
        self._entries.append(entry)
        return entry

    def entries(self) -> List[LogEntry]:
        return list(self._entries)

    def to_json(self) -> str:
        return json.dumps([asdict(e) for e in self._entries], indent=2)


def replay(entries: List[LogEntry], decision_filter: Optional[str] = None) -> None:
    """Print a human-readable replay of a trace log, in order.

    decision_filter: if given, only replay entries whose `decision` matches
    exactly -- useful for auditing e.g. every "notify_shortfall" the agent
    ever sent to Bob.
    """
    for entry in entries:
        if decision_filter is not None and entry.decision != decision_filter:
            continue
        print(f"[{entry.timestamp}] query={entry.query!r}")
        print(f"    sources_checked = {entry.sources_checked}")
        print(f"    decision        = {entry.decision}")
        print(f"    reasoning       = {entry.reasoning}")
        print(f"    approval_status = {entry.approval_status}")


if __name__ == "__main__":
    # Example: how to log a decision, then replay/audit it.
    log = TraceLog()

    log.record(
        query="weekly forecast check for bob-001",
        sources_checked=["delivery_log", "benchmarks.json"],
        decision="stay_silent",
        reasoning="Forecast shows a $12 shortfall next Wednesday; below the $30 materiality threshold, so the gate does not fire.",
        approval_status="not_required",
    )

    log.record(
        query="weekly forecast check for bob-001 (following Wednesday)",
        sources_checked=["delivery_log"],
        decision="notify_shortfall",
        reasoning="Forecast shows a $65 shortfall against a $500 rent due in 4 days; exceeds materiality threshold -> TIER_NOTIFY.",
        approval_status="not_required",
    )

    log.record(
        query="propose plan: defer utility bill by 3 days",
        sources_checked=["delivery_log", "chosen_plan"],
        decision="propose_defer_bill",
        reasoning="Deferring the utility bill closes the shortfall without touching Bob's stated rent buffer constraint. Irreversible once executed -> TIER_APPROVAL.",
        approval_status="pending",
    )

    print("--- Full trace ---")
    replay(log.entries())

    print("\n--- Filtered: only 'notify_shortfall' decisions ---")
    replay(log.entries(), decision_filter="notify_shortfall")

    print("\n--- JSON export ---")
    print(log.to_json())

    assert len(log.entries()) == 3
    print("\ntracing_scaffold.py OK")
