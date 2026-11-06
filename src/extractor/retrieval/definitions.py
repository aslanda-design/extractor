"""Defined terms: find where a document defines "Maturity Date" and who uses it.

A passage that says "the Maturity Date" is only fully understood together with
the passage that defines it. Detection is deliberately simple and exact:

    "Term" means ...      "Term" shall mean ...      "Term" has the meaning ...
    (the "Term")          (each, a "Term")

Only terms that start with a capital letter count. A term is "used" by a
passage when it appears as whole words, with the same capitalization.
"""

import re

from .types import Corpus

_QUOTE = r"[\"“”]"
_TERM = r"([A-Z][A-Za-z0-9&'\- ]{1,58}?)"
_DEFINING = [
    re.compile(_QUOTE + _TERM + _QUOTE + r"\s+(?:shall\s+mean|means|has\s+the\s+meaning|is\s+defined\s+as|refers\s+to)"),
    re.compile(r"\(\s*(?:the|a|an|each|collectively)?[ ,a-z]*" + _QUOTE + _TERM + _QUOTE + r"\s*\)"),
]


def defined_terms(text: str) -> list[str]:
    found: list[str] = []
    for pattern in _DEFINING:
        found += [m.group(1).strip() for m in pattern.finditer(text)]
    return list(dict.fromkeys(found))


def build_definition_index(corpus: Corpus) -> dict[tuple[str, str], str]:
    """(document_id, term) -> id of the first passage defining it."""
    index: dict[tuple[str, str], str] = {}
    for pid in corpus.order:
        p = corpus.passages[pid]
        for term in defined_terms(p.text):
            index.setdefault((p.document_id, term), pid)
    return index


def terms_used(text: str, document_id: str, index: dict[tuple[str, str], str], own_id: str) -> list[tuple[str, str]]:
    """(term, defining passage id) for every defined term appearing in `text`, except its own definition."""
    used = []
    for (doc_id, term), definition in index.items():
        if doc_id == document_id and definition != own_id and re.search(r"\b" + re.escape(term) + r"\b", text):
            used.append((term, definition))
    return used
