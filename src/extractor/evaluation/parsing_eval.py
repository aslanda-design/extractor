"""Phase 2 evaluation: how well does the parser preserve what the labels point at?

Parses every document used by the corpus once, then checks each labeled
evidence quote (found? on the right page?) and summarizes parsing health.
"""

from pathlib import Path

from ..adapters.base import ParserAdapter
from ..contracts.document import DocumentRepresentation
from ..parsing.metrics import ParsingMetrics, QuoteCheck, check_quote, summarize
from ..scope import DocumentRequest, build_manifest
from .corpus import Corpus, ExpectedEvidence


def _all_evidence(corpus: Corpus) -> list[ExpectedEvidence]:
    items: list[ExpectedEvidence] = []
    for case in corpus.cases:
        for expected in case.expected.values():
            items += expected.evidence
            for record in expected.records:
                items += record.evidence
    return items


def parse_corpus_documents(corpus: Corpus, parser: ParserAdapter) -> dict[str, DocumentRepresentation]:
    """filename -> representation, for every document referenced by any case."""
    names = sorted({name for case in corpus.cases for name in case.documents})
    out: dict[str, DocumentRepresentation] = {}
    for name in names:
        path = corpus.root / "documents" / name
        entry = build_manifest(DocumentRequest.from_paths([path])).documents[0]
        out[name] = parser.parse(entry, path)
    return out


def evaluate_parsing(corpus: Corpus, parser: ParserAdapter) -> tuple[ParsingMetrics, list[QuoteCheck]]:
    reps = parse_corpus_documents(corpus, parser)
    checks = [
        check_quote(reps[ev.document], ev.document, ev.page, ev.quote)
        for ev in _all_evidence(corpus)
        if ev.quote and ev.document in reps
    ]
    return summarize(list(reps.values()), checks), checks
