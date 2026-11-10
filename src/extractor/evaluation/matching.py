"""How an answer is compared with the labeled one (shared by every evaluator)."""

from ..parsing.metrics import normalize_text


def _plain(value) -> str:
    """Text compared the way a human labeler would: case, spacing, punctuation and a leading article do not matter."""
    import re

    return re.sub(r"^(the|a|an) ", "", normalize_text(str(value)))


def same_value(expected, got) -> bool:
    """Equality that ignores case, spacing and numeric type (5 == 5.0 == "5"); amounts compare amount and currency."""
    if isinstance(expected, dict) and isinstance(got, dict):
        return (float(expected.get("amount", 0)) == float(got.get("amount", 0))
                and (expected.get("currency") or "").upper() == (got.get("currency") or "").upper())
    if isinstance(expected, list) or isinstance(got, list):
        a, b = (expected if isinstance(expected, list) else [expected]), (got if isinstance(got, list) else [got])
        return len(a) == len(b) and all(any(same_value(x, y) for y in b) for x in a)
    try:
        return float(str(expected).replace(",", "")) == float(str(got).replace(",", ""))
    except ValueError:
        return _plain(expected) == _plain(got)
