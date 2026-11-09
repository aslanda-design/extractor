"""S10 layer 3 - Normalization: the canonical value still means what the document says.

Two traps found on real contracts:
  scale        "NTD 12 million" normalized to 12
  digits/words "NTD 1.2 million (SAY ONE MILLION AND TWENTY THOUSAND ONLY)": the two spellings of the amount disagree

Pure code; no model.
"""

import re
from decimal import Decimal

from ..contracts.candidate import Candidate, ProposalOutcome, ValueProposal
from ..contracts.task import OutputType, TaskSpecification
from ..contracts.validation import Category, ValidationFinding, ValidationLayer
from ..extraction.normalize import _NUMBER_WORDS, _run_value, parse_number
from .findings import finding

NUMERIC = (OutputType.NUMBER, OutputType.INTEGER, OutputType.AMOUNT)
_NUM = r"\$?\s*\d[\d.,]*(?:\s*(?:thousand|million|billion))?"
_WORDS_THEN_DIGITS = re.compile(r"((?:[A-Za-z][A-Za-z-]*[\s,-]+){0,8}[A-Za-z][A-Za-z-]*)\s*\(\s*(" + _NUM + r")\s*\)", re.I)
_DIGITS_THEN_WORDS = re.compile(
    r"(" + _NUM + r")\s*(?:[A-Za-z$]{1,6}\s*)?\(+\s*(?:say\s+|i\.e\.\s*|in words:?\s*)?([A-Za-z][A-Za-z ,-]{2,120}?)\s*(?:only)?\s*\)", re.I)
_SCALED = re.compile(r"(\d[\d.,]*)\s*(thousand|million|billion|mn|bn)\b", re.I)


def check_normalization(task: TaskSpecification, proposal: ValueProposal) -> list[ValidationFinding]:
    if proposal.outcome != ProposalOutcome.FOUND:
        return []
    out = []
    for c in proposal.chosen:
        if c.derived or not c.quote:
            continue
        label = f"'{c.source_value}'"
        if "not_normalized" in c.flags:
            out.append(finding(ValidationLayer.NORMALIZATION, Category.NOT_NORMALIZED,
                               f"{label} could not be read as a {task.output.type.value}", task.id))
        if task.output.type in NUMERIC and _scale_lost(c):
            out.append(finding(ValidationLayer.NORMALIZATION, Category.SCALE_MISMATCH,
                               f"{label} was normalized without the scale word the quote gives (thousand / million / billion)", task.id))
        if task.output.type in NUMERIC or task.output.type == OutputType.TEXT:
            for digits, words in numeral_word_conflicts(c.quote):
                out.append(finding(ValidationLayer.NORMALIZATION, Category.DIGITS_WORDS_DISAGREE,
                                   f"the quote writes the number as {digits:,} in digits but as {words:,} in words", task.id))
    return out


def numeral_word_conflicts(text: str) -> list[tuple[Decimal, Decimal]]:
    """(digits value, words value) for every place where a number is written both ways and the two differ."""
    pairs = []
    for m in _WORDS_THEN_DIGITS.finditer(text):
        tokens = re.findall(r"[a-z]+", m.group(1).lower())
        if tokens and tokens[-1] in _NUMBER_WORDS:
            pairs.append((parse_number(m.group(2)), _words_value(tokens)))
    for m in _DIGITS_THEN_WORDS.finditer(text):
        pairs.append((parse_number(m.group(1)), _words_value(re.findall(r"[a-z]+", m.group(2).lower()))))
    return [(d, w) for d, w in pairs if d is not None and w is not None and d != w]


def _words_value(tokens: list[str]) -> Decimal | None:
    """The number spelled by the trailing run of number words in `tokens`."""
    run = []
    for token in reversed(tokens):
        if token in _NUMBER_WORDS or (token == "and" and run):
            run.append(token)
        elif run:
            break
    run.reverse()
    while run and run[0] == "and":
        run.pop(0)
    return _run_value(run) if run and any(t != "and" for t in run) else None


def _scale_lost(c: Candidate) -> bool:
    """The quote says "12 million" but the normalized value is 12 (or 12.0)."""
    value = c.normalized_value
    value = value.get("amount") if isinstance(value, dict) else value
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    for number, scale in _SCALED.findall(c.quote or ""):
        plain = parse_number(number)
        scaled = parse_number(f"{number} {scale}")
        if plain is not None and scaled is not None and plain != scaled and Decimal(str(value)) == plain:
            return True
    return False
