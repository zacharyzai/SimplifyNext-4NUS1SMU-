"""scripts/check_objectives_freshness.py -- catches HACKATHON_OBJECTIVES.md
drifting stale on claims that are cheap to check against the actual repo
state. Grep-based on purpose -- this doesn't need to be sophisticated, it
needs to actually get run.

Background: §13's benchmarks.json claim went stale TWICE -- once claiming
the module "doesn't exist" after it had long since landed, and again
claiming data/benchmarks.json was "placeholder (all-zero, uncited)" after
commit 69d1d04 had already replaced it with real cited entries. Both times
the doc was only fixed reactively, after someone else re-audited the repo
against it. This script is the fix for THAT process gap, not just the
wording -- run it (or manually re-read the relevant section) as the last
step of any fix that touches a file this script checks.

Exit code 0 = no known drift detected. Exit code 1 = drift found, printed
to stderr. Add more checks here as more of this class of drift is found;
it is deliberately NOT meant to catch everything, just the specific
recurring failure mode of "the doc says X used to be true, X stopped being
true, nobody updated the doc."
"""
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OBJECTIVES_PATH = REPO_ROOT / "HACKATHON_OBJECTIVES.md"
BENCHMARKS_PATH = REPO_ROOT / "data" / "benchmarks.json"

# Patterns matching the ACTUAL stale claim's wording, not bare keywords --
# a corrected doc legitimately says things like "is NOT placeholder" or
# "zero all-zero rates" while explaining what used to be wrong, and a
# naive "does the word 'all-zero' appear near benchmarks.json" check can't
# tell that apart from an affirmative stale claim. Each pattern here is
# only satisfied by phrasing that asserts the file IS the bad state.
STALE_BENCHMARK_PATTERNS = [
    r"benchmarks\.json[^.]{0,60}(still has|has)\s+placeholder",
    r"placeholder\s*\(all-zero",
    r"all-zero,\s*uncited\)?\s*rates?",
    r"benchmarks\.json[^.]{0,60}\ball-zero\b(?!\s+rates? in)",  # allow "zero all-zero rates in the file"
]


def check_benchmarks_claim() -> list:
    """If data/benchmarks.json has zero all-zero-rate entries and zero
    TODO-style citations, HACKATHON_OBJECTIVES.md must not claim it's
    still placeholder/all-zero/uncited.
    """
    errors = []
    if not BENCHMARKS_PATH.exists():
        return errors  # nothing to compare against; not this check's job

    try:
        entries = json.loads(BENCHMARKS_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        errors.append(f"could not read {BENCHMARKS_PATH}: {e!r}")
        return errors

    has_zero_rate = any(e.get("median_net_per_hour_cents", 0) == 0 for e in entries)
    has_todo_citation = any("TODO" in str(e.get("citation", "")) for e in entries)

    if has_zero_rate or has_todo_citation:
        return errors  # the file genuinely IS still a placeholder -- nothing stale to catch

    if not OBJECTIVES_PATH.exists():
        return errors

    text = OBJECTIVES_PATH.read_text()
    for pattern in STALE_BENCHMARK_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            snippet = text[max(0, m.start() - 40):m.end() + 40].replace("\n", " ")
            errors.append(
                f"HACKATHON_OBJECTIVES.md matches stale pattern {pattern!r} "
                f"(near: ...{snippet}...), but data/benchmarks.json has "
                f"{len(entries)} entries with zero all-zero rates and zero "
                f"TODO citations -- this claim is stale."
            )
    return errors


def main() -> int:
    errors = []
    errors += check_benchmarks_claim()

    if errors:
        print("HACKATHON_OBJECTIVES.md freshness check FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print("HACKATHON_OBJECTIVES.md freshness check OK -- no known drift detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
