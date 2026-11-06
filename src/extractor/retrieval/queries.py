"""S06 step 1 - Turn a task into search queries.

One job, several phrasings, each for the index that understands it best:

  lexical   keyword strings for BM25 (words that must appear somewhere)
  semantic  natural sentences for the embedding model, plus one per allowed
            label for interpretation tasks ("what evidence would support each answer?")
  rerank    the full statement the cross-encoder judges passages against
  qualifier keywords that signal exceptions, conditions and amendments

Expansion may only add phrasings (synonyms); it can never change the task.
The default expander does nothing; a model-based one plugs in with Phase 5.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from ..config.runtime import RetrievalSettings
from .types import RetrievalRequest

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")
STOPWORDS = frozenset(
    "a an and are as at be been by for from has have how in is it its of on or that the their this to was were "
    "what when where which who whom whose why will with would can could should shall may might do does did not "
    "any all each if than then there these those such into about under over per via".split())
MAX_LABEL_QUERIES = 6


def keywords(text: str) -> list[str]:
    """Content words of `text`, lowercase, in order, without duplicates."""
    seen: dict[str, None] = {}
    for word in _WORD.findall(text.lower()):
        if word not in STOPWORDS and len(word) > 1:
            seen.setdefault(word)
    return list(seen)


@dataclass
class Queries:
    lexical: list[str] = field(default_factory=list)
    semantic: list[str] = field(default_factory=list)
    rerank: str = ""
    qualifier: str = ""  # lexical query of task keywords + qualifier phrases
    terms: list[str] = field(default_factory=list)  # task keywords, reused by the structural match

    def as_dict(self) -> dict[str, list[str]]:
        return {"lexical": self.lexical, "semantic": self.semantic, "rerank": [self.rerank],
                "qualifier": [self.qualifier] if self.qualifier else []}


class QueryExpander(Protocol):
    def expand(self, request: RetrievalRequest, queries: Queries) -> Queries: ...


class NoExpansion:
    """Default: no model-based expansion."""

    def expand(self, request: RetrievalRequest, queries: Queries) -> Queries:
        return queries


def build_queries(request: RetrievalRequest, settings: RetrievalSettings) -> Queries:
    scope = " ".join(x for x in (request.entity, request.time) if x)
    statement = request.description.strip()
    if request.entity:
        statement += f" Entity: {request.entity}."
    if request.time:
        statement += f" Time scope: {request.time}."

    terms = keywords(" ".join([request.description, scope, *request.hints, *(l.name for l in request.labels)]))

    semantic = [statement]
    for label in request.labels[:MAX_LABEL_QUERIES]:  # evidence that would support each possible answer
        semantic.append(f"{request.description.strip()} Answer '{label.name}': {label.meaning}")

    lexical = [" ".join(terms)] if terms else []
    for label in request.labels[:MAX_LABEL_QUERIES]:
        label_terms = keywords(f"{label.name} {label.meaning}")
        if label_terms:
            lexical.append(" ".join(keywords(scope) + label_terms))

    rerank = statement
    if request.labels:
        rerank += " Possible answers: " + "; ".join(f"{l.name} ({l.meaning})" for l in request.labels) + "."
    if request.criteria:
        rerank += " Criteria: " + " ".join(request.criteria[:3])

    qualifier = ""
    if settings.qualifier_pass:
        qualifier = " ".join(terms + [w for phrase in settings.qualifier_keywords for w in keywords(phrase)])
    return Queries(lexical=lexical, semantic=semantic, rerank=rerank, qualifier=qualifier, terms=terms)
