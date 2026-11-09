"""S06 - THE EVIDENCE RETRIEVAL PIPELINE, start to finish, in one place.

Read `gather_evidence` top to bottom: each numbered step is one call, and each
call lives in its own small module (named in the comment). To change how
evidence is found, edit a step here or the helper it points to.

    task + indexed documents
        |  1. queries      queries.py     task -> lexical / semantic / rerank / qualifier phrasings
        |  2. candidates   candidates.py  lexical + vector + structural (+ qualifier) lists
        |  3. fuse         fusion.py      reciprocal rank fusion, provenance kept
        |  4. rerank       rerank.py      cross-encoder over (task, passage); short docs: all passages
        |  5. select       selection.py   adaptive cut, no duplicates, diversity, qualifier slots
        |  6. expand       expansion.py   references, definitions, neighbours (reranked, may be promoted)
        |  7. package      packaging.py   token budget, source order, roles, explicit gaps
        v
    EvidencePackage (+ RetrievalTrace explaining every inclusion)

HOW DEEP it goes (candidates per list, rerank pool, passages kept, budget,
expansion...) is not decided here: it all comes from `RetrievalSettings`, i.e.
the chosen profile (light / standard / deep / yours) in the runtime config.
Retrieval never concludes anything: finding no good passage is reported as
low scores and gaps, not as "not found" (that is decided after extraction).
"""

from ..config.runtime import RetrievalSettings
from ..contracts.coverage import CoverageLedger
from ..contracts.evidence import EvidenceOrigin, EvidencePackage, ExpansionRecord, RetrievalTrace
from ..contracts.manifest import DocumentManifest
from ..contracts.passage import IndexedDocument
from ..events import emit
from .candidates import gather_candidates
from .definitions import build_definition_index
from .expansion import expand
from .fusion import Fused, reciprocal_rank_fusion
from .packaging import Entry, build_package
from .queries import NoExpansion, QueryExpander, build_queries
from .rerank import rerank_pool, score_extra, score_passages
from .selection import select
from .types import Corpus, RetrievalRequest, RetrievalTools


def gather_evidence(
    request: RetrievalRequest,
    documents: list[IndexedDocument],
    tools: RetrievalTools,
    settings: RetrievalSettings,
    *,
    manifest: DocumentManifest | None = None,
    expander: QueryExpander | None = None,
    ledger: CoverageLedger | None = None,
    profile: str = "",
) -> EvidencePackage:
    trace = RetrievalTrace(profile=profile)

    # 0. Scope: the passages this task may read (its documents, or one record's passages).
    corpus = Corpus.build(documents, request, manifest)
    emit("S06", f"retrieving evidence for '{request.task_id}' ({profile or 'default'} profile): "
                f"{len(corpus.passages)} passage(s) in scope")
    if not corpus.passages:
        emit("S06", f"{request.task_id}: no indexed passages in scope", "warn")
        return EvidencePackage(task_id=request.task_id, trace=trace,
                               gaps=["no indexed passages in scope: nothing could be searched"])

    # 1. Queries (an expander may add phrasings, never change the task).
    queries = (expander or NoExpansion()).expand(request, build_queries(request, settings))
    trace.queries = queries.as_dict()

    # 2. Candidates from complementary sources.
    ranked_lists = gather_candidates(request, queries, corpus, tools, settings)
    trace.candidate_lists = {r.name: r.ids for r in ranked_lists}
    emit("S06", "candidates: " + ", ".join(f"{r.name} {len(r.ids)}" for r in ranked_lists))

    # 3. Fuse by rank (never by adding raw scores).
    fused = reciprocal_rank_fusion(ranked_lists, settings.rrf_k)
    trace.fused = [f.passage_id for f in fused]
    emit("S06", f"fused into {len(fused)} distinct passage(s)")

    # 4. Rerank. A small document is reranked whole, so stage one cannot lose evidence.
    trace.short_document_mode = len(corpus.passages) <= settings.short_doc_passages
    if trace.short_document_mode:
        by_id = {f.passage_id: f for f in fused}
        pool = [by_id.get(pid) or Fused(pid, provenance=["all"]) for pid in corpus.order]
    else:
        pool = rerank_pool(fused, settings)
    scored = score_passages(queries.rerank, pool, corpus, tools, settings)
    trace.reranked = [(s.passage_id, round(s.score, 4)) for s in scored]
    emit("S06", f"reranked {len(scored)} passage(s)" + (" (short document: all passages)" if trace.short_document_mode else "")
                + (f"; best score {scored[0].score:.3f}" if scored else ""))

    # 5. Select the primary evidence.
    selection = select(scored, corpus, settings)
    trace.selected = [s.passage_id for s in selection.primary]
    trace.not_selected = selection.reasons or {}
    best = scored[0].score if scored else 0.0
    emit("S06", f"selected {len(selection.primary)} primary passage(s), {len(selection.qualifiers)} qualifier slot(s)")

    entries = [Entry(s.passage_id, EvidenceOrigin.RETRIEVED, [*s.fused.provenance, f"rank:{i}"], s.score)
               for i, s in enumerate(selection.primary, start=1)]
    entries += [Entry(s.passage_id, EvidenceOrigin.QUALIFIER, [*s.fused.provenance, "qualifier slot"], s.score)
                for s in selection.qualifiers]

    # 6. Expand: add what the selected passages need to be understood, and judge it too.
    gaps: list[str] = []
    definitions = build_definition_index(corpus) if settings.expand_definitions else {}
    known = {e.passage_id for e in entries}
    frontier = list(known)
    extra: list[Entry] = []
    for _ in range(settings.max_expansion_rounds):
        found = expand(frontier, corpus, settings, definitions)
        gaps += [g for g in found.unresolved_references if g not in gaps]
        new = [x for x in found.found if x.passage_id not in known]
        if not new:
            break
        scores = score_extra(queries.rerank, [x.passage_id for x in new], corpus, tools, settings)
        frontier = []
        for x in new:
            score = scores[x.passage_id]
            promoted = settings.rerank and best > 0 and score >= settings.promote_ratio * best
            known.add(x.passage_id)
            trace.expansions.append(ExpansionRecord(
                passage_id=x.passage_id, origin=x.origin, from_passage_id=x.from_passage_id, detail=x.detail,
                rerank_score=round(score, 4) if settings.rerank else None, promoted=promoted))
            why = f"{x.origin.value} of {x.from_passage_id}" + (f": {x.detail}" if x.detail else "")
            extra.append(Entry(x.passage_id, x.origin, [why], score if settings.rerank else None, promoted))
            if promoted:
                frontier.append(x.passage_id)
        if not frontier:
            break
    order = {EvidenceOrigin.REFERENCE: 1, EvidenceOrigin.DEFINITION: 2, EvidenceOrigin.NEIGHBOUR: 3}
    extra.sort(key=lambda e: (not e.promoted, order.get(e.origin, 4), -(e.score or 0.0)))  # promoted first
    entries += extra

    # 7. Package within the token budget; whatever does not fit becomes an explicit gap.
    package = build_package(request.task_id, corpus, entries, gaps, trace, settings.budget_tokens,
                            tools.count_tokens, ledger)
    emit("S06", f"{request.task_id}: evidence ready, {len(package.passages)} passage(s), "
                f"{package.trace.used_tokens}/{package.trace.budget_tokens} tokens, {len(package.gaps)} gap(s)",
         "warn" if package.gaps else "ok")
    return package
