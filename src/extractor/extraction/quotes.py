"""S07 step 4 (part) - Find the model's quote in the evidence and map it to source blocks.

The model says "passage P3, quote '...'". It can paraphrase or mis-copy, so the
engine never trusts that: it searches the quote (whitespace/case/quote-style
insensitive) in the cited passage first, then in the other passages, and turns
the match into the source blocks that contain it. Nothing is invented: a quote
that is not in the evidence is reported as not located.
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ..contracts.common import SourceRef
from ..contracts.passage import Passage
from ..contracts.task import OutputType
from .normalize import all_dates, all_numbers, normalize

APPROXIMATE_RATIO = 0.85  # share of the quote that must match to accept a near copy

_TRANSLATE = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "–": "-", "—": "-", " ": " "})


@dataclass
class QuoteMatch:
    passage_id: str | None = None
    refs: list[SourceRef] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)  # quote_not_located | quote_approximate | quote_in_other_passage

    @property
    def located(self) -> bool:
        return self.passage_id is not None


def _fold(text: str) -> tuple[str, list[int]]:
    """Lowercased text with collapsed whitespace, plus the original offset of every kept character."""
    out, offsets, space = [], [], True
    for i, ch in enumerate(text.translate(_TRANSLATE)):
        if ch.isspace():
            if not space:
                out.append(" ")
                offsets.append(i)
            space = True
        else:
            out.append(ch.casefold())
            offsets.append(i)
            space = False
    return "".join(out).rstrip(), offsets


def _search(passage: Passage, quote: str) -> tuple[int, int, bool] | None:
    """(start, end) of the quote in `passage.text`, and whether the match is only approximate."""
    haystack, offsets = _fold(passage.text)
    needle = _fold(quote)[0].strip(" .\"'")
    if not needle:
        return None
    at = haystack.find(needle)
    approximate = False
    if at < 0:
        block = SequenceMatcher(None, haystack, needle, autojunk=False).find_longest_match(0, len(haystack), 0, len(needle))
        if block.size < APPROXIMATE_RATIO * len(needle) or block.size < 8:
            return None
        at, approximate = block.a, True
        length = block.size
    else:
        length = len(needle)
    return offsets[at], offsets[min(at + length, len(offsets)) - 1] + 1, approximate


def _blocks(passage: Passage, start: int, end: int) -> list[SourceRef]:
    seen = dict.fromkeys(
        s.block_id for s in passage.segments if s.passage_start < end and s.passage_end > start)
    return [SourceRef(document_id=passage.document_id, block_id=b) for b in seen]


def locate_quote(quote: str | None, cited: Passage | None, others: list[Passage]) -> QuoteMatch:
    """Where is `quote`? Searches the cited passage, then every other passage the model was shown."""
    if not quote or not quote.strip():
        return QuoteMatch(flags=["no_quote"])
    for passage, flag in [(cited, None)] * (cited is not None) + [(p, "quote_in_other_passage") for p in others]:
        found = _search(passage, quote)
        if found:
            start, end, approximate = found
            flags = ([flag] if flag else []) + (["quote_approximate"] if approximate else [])
            return QuoteMatch(passage.passage_id, _blocks(passage, start, end), flags)
    return QuoteMatch(flags=["quote_not_located"])


def contains_value(quote: str | None, literal: str | None, output_type: OutputType | None = None) -> bool:
    """Is the reported value in its quote? Text must appear in it (ignoring spacing and case). A typed value
    (date, number, amount...) may be written differently as long as it MEANS what the quote says:
    "March 27, 2017" for "27th day of March, 2017"; 0 for "No payment is due"."""
    if not quote or not literal:
        return False
    if _fold(literal)[0].strip(" .") in _fold(quote)[0]:
        return True
    if output_type in (OutputType.DATE, OutputType.NUMBER, OutputType.INTEGER, OutputType.AMOUNT):
        wanted = normalize(literal, output_type).value
        return wanted is not None and wanted in normalized_values(quote, output_type)
    return False


_SCALE_AFTER = r"\s*(?:thousand|million|billion|mn|bn)\b"


def with_scale_word(literal: str, quote: str | None, output_type: OutputType | None) -> str:
    """"12" copied from "NTD 12 million" is 12 million: when the quote puts a scale word right after the number,
    the value keeps it (the model often drops it, which changes the amount a thousand- or million-fold)."""
    if output_type not in (OutputType.NUMBER, OutputType.INTEGER, OutputType.AMOUNT) or not quote:
        return literal
    if re.search(_SCALE_AFTER, literal, re.I) or not re.fullmatch(r"[^\d]*\d[\d.,]*\s*", literal):
        return literal
    number = re.search(r"\d[\d.,]*", literal).group(0)
    found = re.search(re.escape(number) + r"\s*(thousand|million|billion|mn|bn)\b", quote, re.I)
    return f"{literal.strip()} {found.group(1)}" if found else literal


def normalized_values(text: str, output_type: OutputType) -> list:
    """Every value of the given type that `text` states, normalized (each sentence fragment tried on its own)."""
    out = []
    pieces = [text, *re.split(r"[;,.]\s+|\s+(?:and|or|of)\s+", text)]
    if output_type == OutputType.DATE:
        found = normalize(text, output_type)
        if found.ok:
            out.append(found.value)
        out += [d for d in all_dates(text)]
    else:
        for piece in pieces:
            value = normalize(piece, output_type).value
            if value is not None:
                out.append(value)
        out += [v for v in all_numbers(text, output_type)]
    return out
