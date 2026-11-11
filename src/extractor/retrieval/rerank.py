"""S06 step 4 - Rerank candidates with a cross-encoder.

The reranker reads (task statement, passage) together, which separates
"talks about the same topic" from "answers this question". Its scores express
relevance, not the probability that an answer is correct.

Short documents: when the whole document has few passages, every passage is
reranked and first-stage retrieval cannot lose evidence.
"""

import math
from dataclasses import dataclass, field

from ..config.runtime import RetrievalSettings
from ..execution.scheduler import call_slot
from .fusion import Fused
from .types import Corpus, RetrievalTools


@dataclass
class Scored:
    passage_id: str
    score: float  # relevance in [0, 1] (rerank) or fused score scaled to [0, 1] (no rerank)
    fused: Fused = field(default_factory=lambda: Fused(""))


def to_unit_range(scores: list[float]) -> list[float]:
    """Keep scores already in [0, 1] (probabilities); squash raw logits with a sigmoid.

    The sigmoid is monotone and does not depend on the batch, so scores from
    different calls (candidates now, expansion passages later) stay comparable.
    """
    if all(0.0 <= s <= 1.0 for s in scores):
        return list(scores)
    return [1.0 / (1.0 + math.exp(-max(min(s, 60.0), -60.0))) for s in scores]


def rerank_pool(fused: list[Fused], settings: RetrievalSettings) -> list[Fused]:
    """Top `rerank_top_n` fused candidates, plus qualifier candidates that fell below the cut."""
    pool = fused[: settings.rerank_top_n]
    chosen = {f.passage_id for f in pool}
    extra = [f for f in fused[settings.rerank_top_n:] if "qualifier" in f.sources][: settings.qualifier_candidates]
    return pool + [f for f in extra if f.passage_id not in chosen]


def score_passages(
    request_text: str, candidates: list[Fused], corpus: Corpus, tools: RetrievalTools, settings: RetrievalSettings
) -> list[Scored]:
    """Scored candidates, best first."""
    if not candidates:
        return []
    if settings.rerank:
        with call_slot(tools.reranker):
            raw = tools.reranker.score(request_text, [corpus.passages[f.passage_id].indexed_text() for f in candidates])
        scores = to_unit_range(raw)
    else:  # keep the fused order
        top = max(f.score for f in candidates) or 1.0
        scores = [f.score / top for f in candidates]
    scored = [Scored(f.passage_id, s, f) for f, s in zip(candidates, scores)]
    return sorted(scored, key=lambda x: (-x.score, corpus.position(x.passage_id)))


def score_extra(request_text: str, passage_ids: list[str], corpus: Corpus, tools: RetrievalTools, settings: RetrievalSettings) -> dict[str, float]:
    """Relevance of passages found during expansion (same reranker; fused order impossible, so 0 without one)."""
    if not passage_ids or not settings.rerank:
        return {pid: 0.0 for pid in passage_ids}
    with call_slot(tools.reranker):
        raw = tools.reranker.score(request_text, [corpus.passages[pid].indexed_text() for pid in passage_ids])
    return dict(zip(passage_ids, to_unit_range(raw)))
