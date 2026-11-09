"""S07 step 6 - Derivation: a value that is calculated, never stated, is computed HERE.

Only runs when the task's `rules.derivation` allows it. The model names an
operation over candidates it extracted (with evidence); the engine does the
arithmetic with exact decimals and marks the result `derived=True`. The inputs
must already be normalized, otherwise nothing is computed and the reason is logged.
Supported: add, subtract, multiply, divide (numbers, or amounts in one currency)
and date_difference_days (two ISO dates -> whole days).
"""

from datetime import date
from decimal import Decimal, DivisionByZero, InvalidOperation

from ..contracts.candidate import Candidate
from ..contracts.task import OutputType
from .reading import DerivationRequest

_SYMBOL = {"add": "+", "subtract": "-", "multiply": "x", "divide": "/", "date_difference_days": "days between"}


def derive_candidates(
    requests: list[DerivationRequest], candidates: list[Candidate], output_type: OutputType, allowed: bool,
) -> tuple[list[Candidate], list[str]]:
    """The derived candidates, and a log line for everything that was computed or refused."""
    if not requests:
        return [], []
    if not allowed:
        return [], [f"derivation refused: the task does not permit it ({len(requests)} request(s) ignored)"]
    by_id = {c.candidate_id: c for c in candidates}
    out: list[Candidate] = []
    log: list[str] = []
    for n, request in enumerate(requests, start=1):
        label = f"{request.operation}({', '.join(request.operands)})"
        operands = [by_id.get(o) for o in request.operands]
        if request.operation not in _SYMBOL or len(operands) != 2 or None in operands:
            log.append(f"derivation {label} skipped: needs an operation I know and two extracted candidates")
            continue
        a, b = operands
        try:
            value = _compute(request.operation, a.normalized_value, b.normalized_value)
        except (ValueError, InvalidOperation, DivisionByZero, TypeError) as error:
            log.append(f"derivation {label} skipped: {error}")
            continue
        value = _as_output(value, output_type)
        if value is None:
            log.append(f"derivation {label} skipped: the result does not fit the task's output type ({output_type.value})")
            continue
        formula = f"{a.candidate_id} {_SYMBOL[request.operation]} {b.candidate_id}"
        log.append(f"derived {value!r} = {formula} ({a.source_value!r}, {b.source_value!r})")
        out.append(Candidate(
            candidate_id=f"d{n}", source_value=None, normalized_value=value, derived=True, derivation=formula,
            evidence=list(dict.fromkeys([*a.evidence, *b.evidence])),
            qualifications=[request.explanation] if request.explanation else [], flags=["derived"]))
    return out, log


def _compute(operation: str, a, b):
    if operation == "date_difference_days":
        if not (isinstance(a, str) and isinstance(b, str)):
            raise ValueError("both values must be normalized dates")
        return Decimal((date.fromisoformat(a) - date.fromisoformat(b)).days)
    currency, is_amount = None, False
    if isinstance(a, dict) or isinstance(b, dict):
        is_amount = not (isinstance(a, dict) and isinstance(b, dict) and operation == "divide")  # amount / amount is a ratio
        if not (isinstance(a, dict) and isinstance(b, dict)) and operation in ("add", "subtract"):
            raise ValueError("cannot add or subtract an amount and a plain number")
        currency = (a if isinstance(a, dict) else b).get("currency")
        if isinstance(a, dict) and isinstance(b, dict) and a.get("currency") != b.get("currency") \
                and operation in ("add", "subtract"):
            raise ValueError(f"different currencies ({a.get('currency')} vs {b.get('currency')})")
        a = a["amount"] if isinstance(a, dict) else a
        b = b["amount"] if isinstance(b, dict) else b
    if isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        raise ValueError("both values must be normalized numbers")
    x, y = Decimal(str(a)), Decimal(str(b))
    result = {"add": x + y, "subtract": x - y, "multiply": x * y, "divide": x / y if y else Decimal("NaN")}[operation]
    if result.is_nan():
        raise DivisionByZero("division by zero")
    return {"amount": result, "currency": currency} if is_amount else result


def _as_output(value, output_type: OutputType):
    if output_type == OutputType.AMOUNT:
        return {"amount": float(value["amount"]), "currency": value["currency"]} if isinstance(value, dict) else None
    if isinstance(value, dict):
        return None
    if output_type == OutputType.INTEGER:
        return int(value) if value == value.to_integral_value() else None
    if output_type == OutputType.NUMBER:
        return float(value)
    return None
