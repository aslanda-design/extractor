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

# Quote marks and the replacement character are ignored on both sides: parsers often store curly quotes as U+FFFD
# ("(\ufffdConsultant\ufffd)"), the model copies them as straight quotes, and one such mark in the middle of a quote
# would otherwise split the match in two halves that are each too short to count.
_IGNORED = set("\"'`\u201c\u201d\u201e\u2018\u2019\u00ab\u00bb\ufffd")

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
        if ch in _IGNORED:
            continue
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


def locate_quote(
    quote: str | None, cited: Passage | None, others: list[Passage], literal: str | None = None, output_type: OutputType | None = None,
) -> QuoteMatch:
    """Where is `quote`? Searches the cited passage, then every other passage the model was shown.

    An approximate match is harmless when the VALUE sits inside the matched region (the model only mis-copied words
    around it), so it is not flagged. If the quote cannot be found at all but the value appears (once or twice) in the
    cited passage, the value's own blocks are cited and the match is flagged `quote_relocated`: weaker than a verbatim
    quote, so the strict profile blocks it, but it is still evidence in the document and not the model's invention.
    """
    if not quote or not quote.strip():
        return QuoteMatch(flags=["no_quote"])
    for passage, flag in [(cited, None)] * (cited is not None) + [(p, "quote_in_other_passage") for p in others]:
        found = _search(passage, quote)
        if found:
            start, end, approximate = found
            if approximate and literal and contains_value(passage.text[start:end], literal, output_type):
                approximate = False
            flags = ([flag] if flag else []) + (["quote_approximate"] if approximate else [])
            return QuoteMatch(passage.passage_id, _blocks(passage, start, end), flags)
    if literal and cited is not None:
        refs = _blocks_of_value(cited, literal)
        if refs:
            return QuoteMatch(cited.passage_id, refs, ["quote_relocated"])
    return QuoteMatch(flags=["quote_not_located"])


def _blocks_of_value(passage: Passage, literal: str) -> list[SourceRef]:
    """The blocks holding the value in `passage`, when it occurs at most twice (otherwise we cannot tell which one)."""
    haystack, offsets = _fold(passage.text)
    needle = _fold(literal)[0].strip(" .")
    if len(needle) < 2:
        return []
    spots, at = [], haystack.find(needle)
    while at >= 0 and len(spots) < 3:
        spots.append(at)
        at = haystack.find(needle, at + 1)
    if not 1 <= len(spots) <= 2:
        return []
    refs = []
    for at in spots:
        refs += _blocks(passage, offsets[at], offsets[min(at + len(needle), len(offsets)) - 1] + 1)
    return list(dict.fromkeys(refs))


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


_LEGAL_SUFFIX = (r"(?:S\.?\s?A\.?(?:\s?R\.?\s?L\.?)?|S\.?p\.?A\.?|S\.?L\.?|N\.?V\.?|B\.?V\.?|A\.?G\.?|G\.?m\.?b\.?H\.?|"
                 r"Inc\.?|Incorporated|Corp\.?|Corporation|Co\.?|Company|Ltd\.?|Limited|LLC|L\.L\.C\.|LLP|L\.P\.|LP|PLC|Pte\.?(?:\s+Ltd\.?)?|"
                 r"Pty\.?(?:\s+Ltd\.?)?|GmbH|AB|AS|ASA|OY|K\.?K\.?|Sdn\.?\s+Bhd\.?)")


def with_legal_suffix(literal: str, quote: str | None, output_type: OutputType | None) -> str:
    """"Sanofi Pasteur" copied from "Sanofi Pasteur S.A., a company..." is the short form of the name: when the quote
    continues it with a legal-form suffix (S.A., Inc., Ltd, GmbH...), the party's name includes it."""
    if output_type not in (OutputType.STRING, OutputType.TEXT) or not quote or not literal.strip():
        return literal
    found = re.search(re.escape(literal.strip()) + r"[\s,]+(" + _LEGAL_SUFFIX + r")(?=[\s,;)]|$)", quote)
    if not found or re.search(_LEGAL_SUFFIX + r"$", literal.strip(), re.I):
        return literal
    return f"{literal.strip()} {found.group(1)}"


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
