"""S06 step 3 - Reciprocal rank fusion.

Raw scores from BM25, cosine similarity and overlap counts are not comparable,
so they are never added. Each list only contributes 1 / (k + rank) for every
passage it contains; passages found by several lists rise to the top.
Provenance (which list found it, at what rank) is kept on every candidate.
"""

from dataclasses import dataclass, field

from .candidates import RankedList


@dataclass
class Fused:
    passage_id: str
    score: float = 0.0
    provenance: list[str] = field(default_factory=list)  # "lexical:3", "vector:1", ...
    sources: set[str] = field(default_factory=set)  # base sources: lexical / vector / structural / qualifier


def reciprocal_rank_fusion(lists: list[RankedList], k: int = 60) -> list[Fused]:
    """Best first; ties keep the order of first appearance. Each passage appears once."""
    fused: dict[str, Fused] = {}
    for ranked in lists:
        for rank, pid in enumerate(ranked.ids, start=1):
            entry = fused.setdefault(pid, Fused(pid))
            entry.score += 1.0 / (k + rank)
            entry.provenance.append(f"{ranked.name}:{rank}")
            entry.sources.add(ranked.source)
    order = {pid: i for i, pid in enumerate(fused)}
    return sorted(fused.values(), key=lambda f: (-f.score, order[f.passage_id]))
