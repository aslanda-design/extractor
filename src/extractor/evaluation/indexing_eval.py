"""Phase 3 evaluation: do chunk boundaries respect what the labels point at?

For every labeled evidence quote: is it found inside ONE passage (good), only
across several passages (the chunking cut through the evidence), or nowhere
(a parsing problem, already visible in the Phase 2 metrics)?
Structural invariants (no content dropped, mappings valid) are checked on
every document as well.
"""

from ..contracts.common import StrictModel
from ..contracts.passage import IndexedDocument
from ..indexing.builder import IndexBuilder
from ..indexing.provenance import check_invariants
from ..parsing.metrics import normalize_text
from ..scope import DocumentRequest, build_manifest
from .corpus import Corpus
from .parsing_eval import _all_evidence


class IndexingMetrics(StrictModel):
    documents: int
    passages: int
    mean_tokens: float
    max_tokens: int
    invariant_violations: list[str]
    quotes_total: int
    in_single_passage: int
    split_across_passages: int
    not_found: int

    @property
    def containment(self) -> float | None:
        return self.in_single_passage / self.quotes_total if self.quotes_total else None


def evaluate_indexing(corpus: Corpus, builder: IndexBuilder) -> IndexingMetrics:
    docs: dict[str, IndexedDocument] = {}
    for name in sorted({n for case in corpus.cases for n in case.documents}):
        path = corpus.root / "documents" / name
        entry = build_manifest(DocumentRequest.from_paths([path])).documents[0]
        docs[name] = builder.index(entry, path)

    violations = [f"{name}: {v}" for name, d in docs.items() for v in check_invariants(d)]
    single = split = missing = total = 0
    for ev in _all_evidence(corpus):
        if not ev.quote or ev.document not in docs:
            continue
        total += 1
        doc = docs[ev.document]
        wanted = normalize_text(ev.quote)
        if any(wanted in normalize_text(p.text) for p in doc.passages):
            single += 1
        elif wanted in normalize_text(" ".join(p.text for p in doc.passages)):
            split += 1
        else:
            missing += 1
    sizes = [p.token_count for d in docs.values() for p in d.passages]
    return IndexingMetrics(
        documents=len(docs), passages=len(sizes),
        mean_tokens=sum(sizes) / len(sizes) if sizes else 0.0, max_tokens=max(sizes, default=0),
        invariant_violations=violations, quotes_total=total,
        in_single_passage=single, split_across_passages=split, not_found=missing)
