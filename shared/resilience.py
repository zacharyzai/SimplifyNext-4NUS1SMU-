"""shared/resilience.py -- OWNED BY MEMBER 1. Nobody else edits this file.

Generic retry/fallback infrastructure, shared by every module that calls an
external tool (Bedrock, a file read, anything that can fail). This is the
piece most likely to be quietly broken in a way that poisons every other
module's output, so it lives in the shared contract rather than inside a
feature module (Amendment B, HACKATHON_OBJECTIVES.md).
"""
import time


def resilient_call(fn, *args, retries=2, backoff_seconds=0.5, fallback=None,
                    tool_name="unknown", **kwargs) -> dict:
    """Call fn(*args, **kwargs). Retry with linear backoff on failure.

    If every attempt fails and `fallback` is given, calls fallback() (no
    args) and returns its value as a degraded result. This function NEVER
    raises -- every failure mode is folded into the returned dict so a
    caller can always trust the shape of what comes back.

    Returns:
        {"ok": bool, "value": Any, "tool": str, "attempts": int,
         "degraded": bool, "error": str | None}

    `degraded` is True whenever a fallback was used OR any retry was
    needed -- i.e. whenever the caller did not get a clean first-try
    success.
    """
    attempts = 0
    last_error = None

    for attempt_number in range(1, retries + 2):  # first try + `retries` retries
        attempts = attempt_number
        try:
            value = fn(*args, **kwargs)
            degraded = attempt_number > 1
            return {
                "ok": True,
                "value": value,
                "tool": tool_name,
                "attempts": attempts,
                "degraded": degraded,
                "error": None,
            }
        except Exception as e:  # noqa: BLE001 -- intentionally broad, this must never raise
            last_error = str(e)
            if attempt_number <= retries:
                time.sleep(backoff_seconds * attempt_number)

    if fallback is not None:
        try:
            value = fallback()
            return {
                "ok": True,
                "value": value,
                "tool": tool_name,
                "attempts": attempts,
                "degraded": True,
                "error": last_error,
            }
        except Exception as e:  # noqa: BLE001
            last_error = str(e)

    return {
        "ok": False,
        "value": None,
        "tool": tool_name,
        "attempts": attempts,
        "degraded": True,
        "error": last_error,
    }


def merge_tool_health(results: list) -> dict:
    """Roll up a list of resilient_call() results into one health summary.

    Returns:
        {"all_ok": bool, "degraded_tools": list, "failures": list,
         "confidence_penalty": float}

    Penalty: 0.0 if every tool call succeeded cleanly, 0.3 if any call
    degraded (needed a retry or fallback) but none outright failed,
    0.6 if any call failed outright.
    """
    degraded_tools = [r["tool"] for r in results if r.get("degraded") and r.get("ok")]
    failures = [r["tool"] for r in results if not r.get("ok")]

    if failures:
        penalty = 0.6
    elif degraded_tools:
        penalty = 0.3
    else:
        penalty = 0.0

    return {
        "all_ok": not failures,
        "degraded_tools": degraded_tools,
        "failures": failures,
        "confidence_penalty": penalty,
    }


if __name__ == "__main__":
    # A function that always succeeds on the first try.
    def always_works():
        return "clean result"

    clean = resilient_call(always_works, tool_name="always_works")
    assert clean == {
        "ok": True, "value": "clean result", "tool": "always_works",
        "attempts": 1, "degraded": False, "error": None,
    }
    print("Clean success:", clean)

    # A function that fails twice then succeeds.
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError(f"transient failure #{calls['n']}")
        return "recovered result"

    recovered = resilient_call(flaky, retries=2, backoff_seconds=0.01, tool_name="flaky")
    assert recovered["ok"] is True
    assert recovered["degraded"] is True
    assert recovered["attempts"] == 3
    print("Recovered after retries:", recovered)

    # A function that always fails, with a fallback.
    def always_fails():
        raise RuntimeError("permanent failure")

    def fallback_value():
        return "fallback result"

    degraded = resilient_call(
        always_fails, retries=1, backoff_seconds=0.01,
        fallback=fallback_value, tool_name="always_fails",
    )
    assert degraded["ok"] is True
    assert degraded["value"] == "fallback result"
    assert degraded["degraded"] is True
    print("Fell back:", degraded)

    # A function that always fails, with no fallback -- ok must be False, never raise.
    hard_failure = resilient_call(always_fails, retries=1, backoff_seconds=0.01, tool_name="always_fails")
    assert hard_failure["ok"] is False
    assert hard_failure["value"] is None
    print("Hard failure, did not raise:", hard_failure)

    # merge_tool_health
    all_ok = merge_tool_health([clean])
    assert all_ok == {"all_ok": True, "degraded_tools": [], "failures": [], "confidence_penalty": 0.0}
    print("merge_tool_health, all ok:", all_ok)

    some_degraded = merge_tool_health([clean, recovered])
    assert some_degraded["confidence_penalty"] == 0.3
    print("merge_tool_health, degraded:", some_degraded)

    with_failure = merge_tool_health([clean, recovered, hard_failure])
    assert with_failure["confidence_penalty"] == 0.6
    assert with_failure["all_ok"] is False
    print("merge_tool_health, failure:", with_failure)

    print("ALL RESILIENCE TESTS PASSED")
