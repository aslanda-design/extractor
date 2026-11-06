"""S06 step 5 - Choose the primary evidence from the reranked candidates.

Rules, in order (all thresholds come from RetrievalSettings):
  1. adaptive cut      stop when a passage scores below `score_drop_ratio` x the best one
                       (at least `min_passages` are always kept)
  2. no near-duplicates a passage whose words AND numbers repeat an already chosen one is skipped
  3. diversity         at most `max_per_section` per section; with several documents, each one
                       gets a fair share first, then the rest is filled by score
  4. qualifier slots   up to `qualifier_slots` passages found by the qualifier pass are added
                       even when they rank lower, so exceptions and amendments are not crowded out
"""

import math
import re
from dataclasses import dataclass

from ..config.runtime import RetrievalSettings
from .rerank import Scored
from .types import Corpus

_WORD = re.compile(r"\w+", re.UNICODE)
SHINGLE = 3


def shingles(text: str) -> set[tuple[str, ...]]:
    words = _WORD.findall(text.lower())
    if len(words) <= SHINGLE:
        return {tuple(words)} if words else set()
    return {tuple(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def body_text(passage) -> str:
    """The passage text without the lines that are just its own headings (those differ between copies)."""
    titles = {" ".join(h.split()) for h in passage.heading_path}
    return "\n".join(line for line in passage.text.split("\n") if " ".join(line.split()) not in titles)


def same_content(a, b, threshold: float) -> bool:
    """Same words (word-triple overlap) AND same numbers: "10%" vs "12%" are different clauses."""
    text_a, text_b = body_text(a), body_text(b)
    sa, sb = shingles(text_a), shingles(text_b)
    overlap = len(sa & sb) / len(sa | sb) if sa and sb else 0.0
    return overlap >= threshold and set(_NUMBER.findall(text_a)) == set(_NUMBER.findall(text_b))


@dataclass
class Selection:
    primary: list[Scored]
    qualifiers: list[Scored]  # extra passages reserved by the qualifier pass
    reasons: dict[str, str] | None = None  # passage id -> why a reranked candidate was NOT selected


def select(scored: list[Scored], corpus: Corpus, settings: RetrievalSettings) -> Selection:
    if not scored:
        return Selection([], [], {})
    best = scored[0].score
    cutoff = settings.score_drop_ratio * best
    n_docs = len(corpus.docs) or 1
    doc_quota = math.ceil(settings.max_passages / n_docs)

    def duplicate(item: Scored, chosen: list[Scored]) -> bool:
        """Same words AND same numbers: two clauses differing only by "10%" vs "12%" are NOT duplicates."""
        p = corpus.passages[item.passage_id]
        return any(same_content(p, corpus.passages[c.passage_id], settings.dedupe_similarity) for c in chosen)

    def section_of(item: Scored) -> tuple[str, str | None]:
        p = corpus.passages[item.passage_id]
        return p.document_id, p.section_block_id

    chosen: list[Scored] = []
    per_section: dict = {}
    per_doc: dict = {}
    for enforce_quota in ((True, False) if n_docs > 1 else (False,)):
        for item in scored:
            if len(chosen) >= settings.max_passages:
                break
            if item in chosen or (item.score < cutoff and len(chosen) >= settings.min_passages):
                continue
            doc_id = corpus.passages[item.passage_id].document_id
            if enforce_quota and per_doc.get(doc_id, 0) >= doc_quota:
                continue
            if per_section.get(section_of(item), 0) >= settings.max_per_section or duplicate(item, chosen):
                continue
            chosen.append(item)
            per_section[section_of(item)] = per_section.get(section_of(item), 0) + 1
            per_doc[doc_id] = per_doc.get(doc_id, 0) + 1

    qualifiers: list[Scored] = []
    if settings.qualifier_pass and settings.qualifier_slots:
        floor = 0.5 * cutoff  # qualifier candidates may rank lower, but must still be somewhat relevant
        for item in scored:
            if len(qualifiers) >= settings.qualifier_slots:
                break
            if "qualifier" in item.fused.sources and item not in chosen and item.score >= floor \
                    and not duplicate(item, chosen + qualifiers):
                qualifiers.append(item)

    def why_not(item: Scored) -> str:
        if item.score < cutoff:
            return f"below the relevance cutoff ({settings.score_drop_ratio:g} x best = {cutoff:.3f})"
        p = corpus.passages[item.passage_id]
        for c in chosen:
            if same_content(p, corpus.passages[c.passage_id], settings.dedupe_similarity):
                return f"near-duplicate of {c.passage_id.rsplit(':', 1)[-1]} (same words and numbers)"
        if per_section.get(section_of(item), 0) >= settings.max_per_section:
            return f"section already has {settings.max_per_section} passage(s)"
        if len(chosen) >= settings.max_passages:
            return f"limit of {settings.max_passages} primary passage(s) reached"
        return "document share used up by better-ranked passages"

    kept = {c.passage_id for c in chosen} | {q.passage_id for q in qualifiers}
    reasons = {item.passage_id: why_not(item) for item in scored if item.passage_id not in kept}
    return Selection(chosen, qualifiers, reasons)
