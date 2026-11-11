"""S06 step 2 - Candidate passages from complementary sources.

    lexical     BM25 over passage text                 finds exact words and numbers
    vector      embedding similarity                   finds paraphrases
    structural  heading / table-header word overlap    finds "Article 5 - Payment" for a payment question
    qualifier   qualifier phrases + task words, and    finds exceptions, conditions, amendments
                passages of amendment / replacement documents

Each function returns RankedLists (best first). Lists are NOT scored against
each other: fusion (step 3) combines them by rank only.
"""

import re
from dataclasses import dataclass

from ..config.runtime import RetrievalSettings
from ..execution.scheduler import call_slot
from .queries import Queries, keywords
from .types import Corpus, RetrievalRequest, RetrievalTools


@dataclass
class RankedList:
    name: str  # e.g. "lexical", "lexical#2", "vector@doc_ab12"; the part before : or @ or # is the source
    ids: list[str]  # passage ids, best first

    @property
    def source(self) -> str:
        return re.split(r"[#@]", self.name)[0]


def _suffix(variant: int, doc_id: str, n_docs: int) -> str:
    return (f"#{variant + 1}" if variant else "") + (f"@{doc_id[:10]}" if n_docs > 1 else "")


def lexical_candidates(queries: Queries, corpus: Corpus, tools: RetrievalTools, settings: RetrievalSettings) -> list[RankedList]:
    lists = []
    overfetch = settings.candidates_per_list * (3 if len(corpus.passages) != sum(len(d.passages) for d in corpus.docs.values()) else 1)
    for v, query in enumerate(queries.lexical):
        for doc_id, doc in corpus.docs.items():
            hits = tools.lexical.search(doc.index_key, query, overfetch)
            ids = [h.id for h in hits if h.id in corpus.passages][: settings.candidates_per_list]
            if ids:
                lists.append(RankedList("lexical" + _suffix(v, doc_id, len(corpus.docs)), ids))
    return lists


def vector_candidates(queries: Queries, corpus: Corpus, tools: RetrievalTools, settings: RetrievalSettings) -> list[RankedList]:
    lists = []
    allowed = list(corpus.passages) if len(corpus.passages) != sum(len(d.passages) for d in corpus.docs.values()) else None
    for v, query in enumerate(queries.semantic):
        with call_slot(tools.embedder):
            vector = tools.embedder.embed_query(query)
        for doc_id, doc in corpus.docs.items():
            hits = tools.vectors.query(doc.index_key, vector, settings.candidates_per_list, allowed_ids=allowed)
            ids = [h.id for h in hits if h.id in corpus.passages]
            if ids:
                lists.append(RankedList("vector" + _suffix(v, doc_id, len(corpus.docs)), ids))
    return lists


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def structural_candidates(queries: Queries, corpus: Corpus, settings: RetrievalSettings) -> list[RankedList]:
    """Passages whose heading path (or table header row) shares words with the task."""
    wanted = {_stem(t) for t in queries.terms}
    if not wanted:
        return []
    lists = []
    for doc_id, doc in corpus.docs.items():
        scored = []
        for p in doc.passages:
            if p.passage_id not in corpus.passages:
                continue
            label = " ".join(p.heading_path) + (" " + p.text.split("\n", 1)[0] if p.kind.value == "table" else "")
            overlap = len(wanted & {_stem(w) for w in keywords(label)})
            if overlap:
                scored.append((-overlap / len(wanted), corpus.position(p.passage_id), p.passage_id))
        if scored:
            ids = [pid for _, _, pid in sorted(scored)][: settings.candidates_per_list]
            lists.append(RankedList("structural" + _suffix(0, doc_id, len(corpus.docs)), ids))
    return lists


def qualifier_candidates(queries: Queries, corpus: Corpus, tools: RetrievalTools, settings: RetrievalSettings) -> list[RankedList]:
    """Passages with qualifier language about the task, and passages of amendment/replacement documents."""
    lists = []
    phrases = [k.lower() for k in settings.qualifier_keywords]
    for doc_id, doc in corpus.docs.items():
        hits = tools.lexical.search(doc.index_key, queries.qualifier, settings.qualifier_candidates * 4)
        ids = [h.id for h in hits if h.id in corpus.passages and any(ph in corpus.passages[h.id].text.lower() for ph in phrases)]
        if ids:
            lists.append(RankedList("qualifier" + _suffix(0, doc_id, len(corpus.docs)), ids[: settings.qualifier_candidates]))
        if corpus.relationship_kinds(doc_id) & {"amendment", "replacement"}:  # the document itself overrides others
            task_hits = tools.lexical.search(doc.index_key, " ".join(queries.terms), settings.qualifier_candidates)
            amend = [h.id for h in task_hits if h.id in corpus.passages]
            if amend:
                lists.append(RankedList("qualifier" + _suffix(1, doc_id, len(corpus.docs)), amend))
    return lists


def gather_candidates(
    request: RetrievalRequest, queries: Queries, corpus: Corpus, tools: RetrievalTools, settings: RetrievalSettings
) -> list[RankedList]:
    lists: list[RankedList] = []
    if settings.use_lexical:
        lists += lexical_candidates(queries, corpus, tools, settings)
    if settings.use_vector:
        lists += vector_candidates(queries, corpus, tools, settings)
    if settings.use_structural:
        lists += structural_candidates(queries, corpus, settings)
    if settings.qualifier_pass:
        lists += qualifier_candidates(queries, corpus, tools, settings)
    return lists
