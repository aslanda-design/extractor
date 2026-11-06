"""S06 step 6 - Add the context a selected passage needs to be understood.

For every selected passage:
  reference   the section it points at ("see Section 5.2")        -> those passages
  definition  the definition of every defined term it uses          -> that passage
  neighbour   `neighbours` passages on each side                    -> context (intro lines, table notes)

Everything found here is reranked by the pipeline: a strongly relevant
addition is promoted to primary evidence (and expanded again, up to
`max_expansion_rounds`). Unresolved references are returned so they can
become explicit gaps instead of being ignored.
"""

from dataclasses import dataclass

from ..config.runtime import RetrievalSettings
from ..contracts.evidence import EvidenceOrigin
from ..contracts.passage import Passage
from .definitions import terms_used
from .types import Corpus


@dataclass
class Expansion:
    passage_id: str
    origin: EvidenceOrigin
    from_passage_id: str
    detail: str = ""


@dataclass
class ExpansionResult:
    found: list[Expansion]
    unresolved_references: list[str]


def _section_passages(corpus: Corpus, document_id: str, heading_block_id: str, limit: int) -> list[str]:
    """First passages of the section opened by a heading block."""
    doc = corpus.docs[document_id]
    ids = [p.passage_id for p in doc.passages if p.passage_id in corpus.passages]
    for i, pid in enumerate(ids):
        if any(s.block_id == heading_block_id for s in corpus.passages[pid].segments):
            return ids[i:i + limit]
    return []


def _walk(corpus: Corpus, start: Passage, steps: int, attr: str) -> list[str]:
    out, current = [], start
    for _ in range(steps):
        next_id = getattr(current, attr)
        if next_id is None or next_id not in corpus.passages:
            break
        out.append(next_id)
        current = corpus.passages[next_id]
    return out


def expand(
    selected_ids: list[str], corpus: Corpus, settings: RetrievalSettings, definitions: dict[tuple[str, str], str]
) -> ExpansionResult:
    found: list[Expansion] = []
    unresolved: list[str] = []
    for pid in selected_ids:
        p = corpus.passages[pid]
        blocks = {s.block_id for s in p.segments}

        if settings.expand_references:
            for ref in corpus.docs[p.document_id].references:
                if ref.source_block_id not in blocks:
                    continue
                if not ref.resolved or ref.target_block_id is None:
                    unresolved.append(f"unresolved reference '{ref.text}' in {pid}")
                    continue
                for target in _section_passages(corpus, p.document_id, ref.target_block_id, settings.max_reference_passages):
                    found.append(Expansion(target, EvidenceOrigin.REFERENCE, pid, ref.text))

        if settings.expand_definitions:
            for term, definition in terms_used(p.text, p.document_id, definitions, pid):
                found.append(Expansion(definition, EvidenceOrigin.DEFINITION, pid, term))

        if settings.neighbours:
            for attr in ("prev_id", "next_id"):
                for neighbour in _walk(corpus, p, settings.neighbours, attr):
                    found.append(Expansion(neighbour, EvidenceOrigin.NEIGHBOUR, pid))

    # first reason wins; passages already selected are not "added"
    seen, unique = set(selected_ids), []
    for item in found:
        if item.passage_id not in seen and item.passage_id in corpus.passages:
            seen.add(item.passage_id)
            unique.append(item)
    return ExpansionResult(unique, list(dict.fromkeys(unresolved)))
