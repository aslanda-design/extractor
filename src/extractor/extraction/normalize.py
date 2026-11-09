"""S07 step 5 - Normalization: change the REPRESENTATION of a value, never its meaning.

Deterministic code, not the model: `normalize(literal, output_type, labels)`
returns the canonical value, or `None` with a note when the text cannot be
read unambiguously (the candidate then keeps only its literal and a flag).

  number / integer   "1,250.50" "1.250,50" "twenty-five" "5%"      -> 1250.5, 25, 5.0   (a % sign is dropped, 5% -> 5)
  boolean            "yes" "shall apply" "not applicable"           -> True / False
  date               "1 March 2027" "March 1, 2027" "2027-03-01"    -> "2027-03-01"      (ISO; "03/04/2027" is ambiguous -> None)
  amount             "EUR 1.5 million" "$2,000"                     -> {"amount": 1500000.0, "currency": "EUR"}
  label              "Approved"                                     -> the allowed label with that name
  string / text      whitespace collapsed
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from ..contracts.candidate import Candidate
from ..contracts.task import LabelSpec, OutputType, TaskSpecification

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())} | {"sept": 9})

CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY", "₹": "INR"}
CURRENCY_CODE = re.compile(r"\b(EUR|USD|GBP|CHF|JPY|CAD|AUD|SEK|NOK|DKK|CNY|INR|MXN|BRL)\b", re.I)
SCALES = {"thousand": 10**3, "k": 10**3, "million": 10**6, "mn": 10**6, "m": 10**6,
          "billion": 10**9, "bn": 10**9, "b": 10**9}

UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
TRUE_WORDS = {"yes", "true", "applies", "apply", "shall apply", "is required", "required", "permitted", "allowed"}
FALSE_WORDS = {"no", "false", "not applicable", "does not apply", "not required", "not permitted", "prohibited", "none"}


@dataclass
class Normalized:
    value: Any
    note: str = ""  # why it could not be normalized (empty when it could)

    @property
    def ok(self) -> bool:
        return self.value is not None


def normalize(literal: str | None, output_type: OutputType, labels: list[LabelSpec] | None = None,
              date_order: str | None = None) -> Normalized:
    if literal is None or not literal.strip():
        return Normalized(None, "no literal value")
    text = " ".join(literal.split())
    if output_type in (OutputType.STRING, OutputType.TEXT):
        return Normalized(text)
    if output_type == OutputType.LABEL:
        for label in labels or []:
            if label.name.casefold() == text.casefold():
                return Normalized(label.name)
        return Normalized(None, f"'{text}' is not one of the allowed labels")
    if output_type == OutputType.BOOLEAN:
        return _boolean(text)
    if output_type == OutputType.DATE:
        return _date(text, date_order)
    if output_type == OutputType.AMOUNT:
        return _amount(text)
    number = parse_number(text)
    if number is None and _NOTHING_DUE.match(text):
        number = Decimal(0)  # "No payment is due" is an explicit zero obligation, not a missing value
    if number is None:
        return Normalized(None, f"cannot read a number in '{text}'")
    if output_type == OutputType.INTEGER:
        if number != number.to_integral_value():
            return Normalized(None, f"'{text}' is not a whole number")
        return Normalized(int(number))
    return Normalized(float(number))


_NOTHING_DUE = re.compile(r"^(no|nil|zero)\b[^0-9]*\b(payment|payments|fee|fees|charge|charges|consideration|cost|costs)\b", re.I)

# --- numbers -----------------------------------------------------------------------------------------------------


def parse_number(text: str) -> Decimal | None:
    """The one number `text` states, as digits ("1,250.50", "90", "12 million") and/or English words
    ("thirty", "four-year", "thirty (30) days"). Several different numbers = ambiguous = None."""
    lowered = text.casefold().replace("%", " ").strip()
    values: list[Decimal] = []
    for match in _DIGITS.finditer(lowered):
        value = _decimal(match.group(0))
        if value is None:
            return None
        before = lowered[:match.start()].rstrip("$€£ ")
        if before.endswith(("-", "−")) and not before[:-1].rstrip().endswith(tuple("abcdefghijklmnopqrstuvwxyz0123456789")):
            value = -value
        elif re.fullmatch(r"\W*[$€£]?\s*\([\d.,\s]+\)\W*", lowered):  # a bare "(1,000)" is accounting style
            value = -value
        scale = re.match(r"\s*(thousand|million|billion|mn|bn|k|m|b)\b", lowered[match.end():])
        if scale:
            value *= SCALES[scale.group(1)]
        values.append(value)
    values += _word_numbers(lowered)
    distinct = set(values)
    return distinct.pop() if len(distinct) == 1 else None


_DIGITS = re.compile(r"\d[\d.,']*\d|\d")
_NUMBER_WORDS = set(UNITS) | set(TENS) | {"hundred", "thousand", "million", "billion"}


def _word_numbers(text: str) -> list[Decimal]:
    """Each run of English number words ("twenty-five", "one hundred and five") as a number."""
    tokens = re.findall(r"[a-z]+", text)
    runs, run = [], []
    for i, token in enumerate(tokens):
        if token in _NUMBER_WORDS or (token == "and" and run and i + 1 < len(tokens) and tokens[i + 1] in _NUMBER_WORDS):
            run.append(token)
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    return [_run_value(r) for r in runs if any(t in UNITS or t in TENS for t in r)]  # "million" alone is a scale, not a number


def _run_value(tokens: list[str]) -> Decimal:
    total, current = 0, 0
    for t in tokens:
        if t in UNITS:
            current += UNITS[t]
        elif t in TENS:
            current += TENS[t]
        elif t == "hundred":
            current = max(current, 1) * 100
        elif t != "and":
            total += max(current, 1) * SCALES[t]
            current = 0
    return Decimal(total + current)


def _decimal(digits: str) -> Decimal | None:
    """Decide which of '.' and ',' is the decimal mark: the last one, unless it groups exactly three digits."""
    commas, dots = digits.count(","), digits.count(".")
    if commas and dots:
        decimal_mark = "," if digits.rfind(",") > digits.rfind(".") else "."
    elif commas or dots:
        mark = "," if commas else "."
        parts = digits.split(mark)
        grouping = len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and 1 <= len(parts[0]) <= 3)
        if grouping:
            decimal_mark = None
        elif len(parts) == 2:
            decimal_mark = mark
        else:
            return None
    else:
        decimal_mark = None
    if decimal_mark:
        whole, _, fraction = digits.rpartition(decimal_mark)
        digits = whole.replace(",", "").replace(".", "") + "." + fraction
    else:
        digits = digits.replace(",", "").replace(".", "")
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


# --- booleans, dates, amounts -----------------------------------------------------------------------------------


def _boolean(text: str) -> Normalized:
    lowered = text.casefold().strip(" .")
    if lowered in FALSE_WORDS:
        return Normalized(False)
    if lowered in TRUE_WORDS:
        return Normalized(True)
    return Normalized(None, f"'{text}' is not a clear yes/no")


_MONTH = "(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
_ORDINAL = r"(?:st|nd|rd|th)?"
_DATE_PATTERNS = [  # (regex, how to read its groups as (year, month, day))
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(rf"\b(\d{{1,2}}){_ORDINAL}\s+(?:day\s+)?(?:of\s+)?{_MONTH},?\s*(\d{{4}})\b"), lambda m: (int(m[3]), MONTHS[m[2]], int(m[1]))),
    (re.compile(rf"\b{_MONTH}\s+(\d{{1,2}}){_ORDINAL},?\s*(\d{{4}})\b"), lambda m: (int(m[3]), MONTHS[m[1]], int(m[2]))),
    (re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b"), lambda m: (int(m[3]), int(m[2]), int(m[1]))),  # 01.03.2027: day first
]
_SLASH_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})\b")


def _year(text: str) -> int:
    year = int(text)
    return year if len(text) == 4 else (2000 + year if year < 70 else 1900 + year)


def infer_date_order(text: str) -> str | None:
    """The day/month order a document uses for numeric dates ("mdy" or "dmy"), if its own dates prove it.

    "1/14/02" can only be month-first; "25/05/2008" only day-first. Mixed or no evidence: None (stay ambiguous).
    """
    orders = set()
    for m in _SLASH_DATE.finditer(text):
        a, b = int(m[1]), int(m[2])
        if a > 12 >= b:
            orders.add("dmy")
        elif b > 12 >= a:
            orders.add("mdy")
    return orders.pop() if len(orders) == 1 else None


def _date(text: str, order: str | None = None) -> Normalized:
    """The one calendar date `text` states (surrounding words such as "the 27th day of" are ignored).
    `order` ("mdy" / "dmy") settles numeric dates like 1/9/02 when the document's own dates prove its convention."""
    lowered = text.casefold()
    found: set[tuple[int, int, int]] = set()
    for pattern, read in _DATE_PATTERNS:
        found |= {read(m) for m in pattern.finditer(lowered)}
    for m in _SLASH_DATE.finditer(lowered):
        a, b, y = int(m[1]), int(m[2]), _year(m[3])
        if a > 12 or a == b:
            found.add((y, b, a) if a > 12 else (y, a, b))
        elif b > 12:
            found.add((y, a, b))
        elif order == "mdy":
            found.add((y, a, b))
        elif order == "dmy":
            found.add((y, b, a))
        else:
            return Normalized(None, f"'{text}' is ambiguous (day/month or month/day)")
    if not found:
        return Normalized(None, f"cannot read a full date in '{text}'")
    if len(found) > 1:
        return Normalized(None, f"'{text}' states several different dates")
    year, month, day = found.pop()
    try:
        return Normalized(date(year, month, day).isoformat())
    except ValueError:
        return Normalized(None, f"'{text}' is not a real calendar date")


def _amount(text: str) -> Normalized:
    currency = None
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            currency = code
    if match := CURRENCY_CODE.search(text):
        currency = match.group(1).upper()
    number = parse_number(CURRENCY_CODE.sub(" ", re.sub("[" + re.escape("".join(CURRENCY_SYMBOLS)) + "]", " ", text)))
    if number is None:
        return Normalized(None, f"cannot read an amount in '{text}'")
    return Normalized({"amount": float(number), "currency": currency})


REDACTED = re.compile(r"\[\s*(?:\*+|x+|#+|_+|redacted|omitted)\s*\]|\*{3,}|\bredacted\b", re.I)


def is_redacted(literal: str | None) -> bool:
    """A confidentiality blank such as "[***]": the value exists but the document hides it."""
    return bool(literal and REDACTED.search(literal))


def all_dates(text: str) -> list[str]:
    """Every unambiguous calendar date in `text`, ISO formatted."""
    out = []
    lowered = text.casefold()
    for pattern, read in _DATE_PATTERNS:
        for m in pattern.finditer(lowered):
            try:
                out.append(date(*read(m)).isoformat())
            except ValueError:
                pass
    return out


def all_numbers(text: str, output_type: OutputType) -> list:
    """Every number `text` states (digits and English words), as the given type."""
    lowered = text.casefold()
    out = []
    for m in _DIGITS.finditer(lowered):
        out.append(normalize(m.group(0), output_type).value)
    out += [normalize(str(w), output_type).value for w in _word_numbers(lowered)]
    if _NOTHING_DUE.match(text.strip()):
        out.append(normalize("0", output_type).value)
    return [v for v in out if v is not None]


def normalize_candidate(candidate: Candidate, task: TaskSpecification, date_order: str | None = None) -> Candidate:
    """The candidate with its normalized value filled in, or flagged `not_normalized` (the literal is kept either way)."""
    if candidate.derived or candidate.normalized_value is not None:
        return candidate
    if is_redacted(candidate.source_value):
        return candidate.model_copy(update={"flags": [*candidate.flags, "redacted"]})
    result = normalize(candidate.source_value, task.output.type, task.output.labels, date_order)
    if result.ok:
        return candidate.model_copy(update={"normalized_value": result.value})
    return candidate.model_copy(update={"flags": [*candidate.flags, "not_normalized"],
                                        "qualifications": [*candidate.qualifications, result.note]})
