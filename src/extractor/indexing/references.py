"""S04 step 6 - Explicit structural references ("see Section 5.2", "Schedule A").

Only references that name a section-like target are handled. Each is resolved
to the heading block it points at by numbering or title; anything that cannot
be matched is kept as `resolved=False`. Nothing is guessed.
"""

import re

from ..contracts.document import BlockKind, SourceBlock
from ..contracts.passage import Reference

_REFERENCE = re.compile(
    r"\b(Sections?|Articles?|Clauses?|Paragraphs?|Schedules?|Appendix|Appendices|Annex(?:es)?|Exhibits?)"
    r"\s+(\d+(?:\.\d+)*|[A-Z]\b|[IVXLC]+\b)",
    re.IGNORECASE,
)
_SINGULAR = {"sections": "section", "articles": "article", "clauses": "clause", "paragraphs": "paragraph",
             "schedules": "schedule", "appendices": "appendix", "annexes": "annex", "exhibits": "exhibit"}
_NUMERIC_KINDS = {"section", "clause", "paragraph"}  # these also match a bare "5.2" heading


def _kind(word: str) -> str:
    word = word.lower()
    return _SINGULAR.get(word, word)


def _heading_labels(title: str) -> set[str]:
    """Lookup keys a heading answers to: 'article 5', 'schedule a', '5.2', ..."""
    title = " ".join(title.split())
    labels: set[str] = set()
    m = re.match(r"^(\d+(?:\.\d+)*)[.)]?(\s|$)", title)
    if m:
        labels.add(m.group(1))
    m = re.match(r"^(Article|Part|Chapter|Schedule|Appendix|Annex|Exhibit|Section|Clause)\s+(\S+?)[.:)\-–—]?(\s|$)",
                 title, re.IGNORECASE)
    if m:
        labels.add(f"{m.group(1).lower()} {m.group(2).lower()}")
    return labels


def build_label_index(blocks: list[SourceBlock]) -> dict[str, str]:
    """label -> heading block id (the first heading wins)."""
    index: dict[str, str] = {}
    for b in blocks:
        if b.kind == BlockKind.HEADING:
            for label in _heading_labels(b.text):
                index.setdefault(label, b.block_id)
    return index


def find_references(blocks: list[SourceBlock]) -> list[Reference]:
    labels = build_label_index(blocks)
    found: list[Reference] = []
    for b in blocks:
        if b.kind == BlockKind.HEADING:
            continue
        for m in _REFERENCE.finditer(b.text):
            kind, ident = _kind(m.group(1)), m.group(2).lower()
            label = f"{kind} {ident}"
            target = labels.get(label)
            if target is None and kind in _NUMERIC_KINDS and re.fullmatch(r"\d+(?:\.\d+)*", ident):
                target = labels.get(ident)
            found.append(Reference(source_block_id=b.block_id, text=m.group(0), label=label,
                                   target_block_id=target, resolved=target is not None))
    return found
