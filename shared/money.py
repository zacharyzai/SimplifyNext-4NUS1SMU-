"""shared/money.py -- OWNED BY MEMBER 1. Nobody else edits this file.

All money in this project is an integer number of cents. Never a float.
Three separate LLM sessions (Member 2/3/4's modules) each touch money at
some point, and floats drift silently across repeated add/subtract/round
operations -- $0.1 + $0.2 != $0.3 in binary floating point. Integer cents
sidestep that class of bug entirely: every downstream module can add,
subtract and compare cents with ordinary integer arithmetic and get an
exact answer every time.
"""

def cents_from_string(s) -> int:
    """Parse "$1,234.50", "1234.5", or the number 1234.5 into 123450 cents.

    Raises ValueError on unparseable input.
    """
    if isinstance(s, (int, float)):
        s = str(s)
    if not isinstance(s, str):
        raise ValueError(f"cannot parse money from type {type(s)!r}")

    raw = s.strip()
    if not raw:
        raise ValueError("empty string is not a money value")

    negative = raw.startswith("-")
    if negative:
        raw = raw[1:].strip()

    cleaned = raw.replace("$", "").replace(",", "").strip()
    if not cleaned:
        raise ValueError(f"unparseable money string: {s!r}")

    try:
        dollars = float(cleaned)
    except ValueError:
        raise ValueError(f"unparseable money string: {s!r}")

    cents = round(dollars * 100)
    return -cents if negative else cents


def format_cents(cents: int) -> str:
    """123450 -> "$1,234.50"; negative cents render as "-$12.00"."""
    if not isinstance(cents, int):
        raise ValueError(f"format_cents expects an int, got {type(cents)!r}")

    negative = cents < 0
    whole_cents = abs(cents)
    dollars, remainder = divmod(whole_cents, 100)
    formatted = f"${dollars:,}.{remainder:02d}"
    return f"-{formatted}" if negative else formatted


if __name__ == "__main__":
    # cents_from_string demonstrations
    assert cents_from_string("$1,234.50") == 123450
    assert cents_from_string("1234.5") == 123450
    assert cents_from_string(1234.5) == 123450
    assert cents_from_string("-$12.00") == -1200
    assert cents_from_string("$0.00") == 0
    print("cents_from_string OK:")
    print(" ", cents_from_string("$1,234.50"))
    print(" ", cents_from_string("1234.5"))
    print(" ", cents_from_string(1234.5))
    print(" ", cents_from_string("-$12.00"))

    try:
        cents_from_string("not a number")
        raise AssertionError("expected ValueError on junk input")
    except ValueError as e:
        print("cents_from_string correctly raised on junk input:", e)

    # format_cents demonstrations
    assert format_cents(123450) == "$1,234.50"
    assert format_cents(-1200) == "-$12.00"
    assert format_cents(0) == "$0.00"
    print("format_cents OK:")
    print(" ", format_cents(123450))
    print(" ", format_cents(-1200))
    print(" ", format_cents(0))

    print("ALL MONEY TESTS PASSED")
