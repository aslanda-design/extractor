"""Phase 4 evaluation: does retrieval bring the labeled evidence into the package?

Gold passages are found by locating each labeled evidence quote inside the
indexed documents (a quote that no single passage contains is counted as
"unmappable"; that is a chunking problem, visible in `eval --indexing`).

Per task and evidence item we ask three questions, one per stage:
    candidates   was the gold passage among the candidates sent to the reranker?
    selected     did it survive selection (primary evidence)?
    package      is it in the final package (primary + qualifier + expansion)?
Evidence labeled `contradicts` / `qualifies` is also reported on its own: that is
the exception recall retrieval must not sacrifice.

Variants switch single steps off (no rerank, lexical only, ...) so you can see
which step earns its cost. Ablation variants disable the short-document
shortcut, otherwise the candidate stage would be bypassed.
"""

from pathlib import Path

from ..adapters.registry import AdapterSet
from ..artifacts import ArtifactStore
from ..config import RuntimeConfig
from ..contracts.common import StrictModel, TaskKind
from ..indexing.builder import IndexBuilder
from ..parsing.metrics import normalize_text
from ..retrieval.retrieval_pipeline import gather_evidence
from ..retrieval.tools import build_retrieval_tools
from ..retrieval.types import request_from_task
from ..scope import DocumentRequest, build_manifest, resolve_task_documents
from ..spec import compile_specification_file
from .corpus import Corpus

EXCEPTION_ROLES = {"contradicts", "qualifies"}

VARIANTS: dict[str, dict] = {
    "full": {},
    "stage_one": {"short_doc_passages": 0},
    "no_rerank": {"short_doc_passages": 0, "rerank": False},
    "lexical_only": {"short_doc_passages": 0, "use_vector": False, "use_structural": False, "qualifier_pass": False},
    "vector_only": {"short_doc_passages": 0, "use_lexical": False, "use_structural": False, "qualifier_pass": False},
    "no_structural": {"short_doc_passages": 0, "use_structural": False},
    "no_qualifier": {"qualifier_pass": False},
    "no_expansion": {"neighbours": 0, "expand_references": False, "expand_definitions": False},
}


class RetrievalRow(StrictModel):
    profile: str
    variant: str
    evidence_items: int
    unmappable: int  # labeled quotes that sit in no single passage (excluded from the recalls)
    candidate_recall: float | None
    selected_recall: float | None
    package_recall: float | None
    exception_items: int
    exception_package_recall: float | None
    mean_passages: float
    mean_tokens: float
    tasks_with_gaps: int


class RetrievalReport(StrictModel):
    rows: list[RetrievalRow]

    def format(self) -> str:
        head = f"{'profile':<10}{'variant':<14}{'items':>6}{'cand%':>8}{'sel%':>8}{'pkg%':>8}{'exc%':>8}{'psg':>6}{'tok':>7}{'gaps':>6}"
        pct = lambda v: "  -  " if v is None else f"{v * 100:5.0f}"
        lines = [head]
        for r in self.rows:
            lines.append(f"{r.profile:<10}{r.variant:<14}{r.evidence_items:>6}{pct(r.candidate_recall):>8}"
                         f"{pct(r.selected_recall):>8}{pct(r.package_recall):>8}{pct(r.exception_package_recall):>8}"
                         f"{r.mean_passages:>6.1f}{r.mean_tokens:>7.0f}{r.tasks_with_gaps:>6}")
        return "\n".join(lines)


def _ratio(hit: int, total: int) -> float | None:
    return hit / total if total else None


def evaluate_retrieval(
    corpus: Corpus,
    config: RuntimeConfig,
    adapters: AdapterSet,
    store: ArtifactStore,
    profiles: list[str],
    variants: list[str] | None = None,
) -> RetrievalReport:
    builder = IndexBuilder(config, adapters, store)
    tools = build_retrieval_tools(config, adapters, store)

    # index every case once
    prepared = []
    for case in corpus.cases:
        spec = compile_specification_file(corpus.root / case.spec)
        paths = [corpus.root / "documents" / n for n in case.documents]
        manifest = build_manifest(DocumentRequest.from_paths(paths))
        by_name = {e.filename: e for e in manifest.documents}
        indexed = {e.document_id: builder.index(e, p) for e, p in zip((by_name[p.name] for p in paths), paths)}
        prepared.append((case, spec, manifest, by_name, indexed))

    rows = []
    for profile in profiles:
        for variant in (variants or list(VARIANTS)):
            settings = config.retrieval.resolve(profile).model_copy(update=VARIANTS[variant])
            hits = {"cand": 0, "sel": 0, "pkg": 0, "exc": 0}
            items = exc_items = unmappable = tasks = gap_tasks = passages = tokens = 0
            for case, spec, manifest, by_name, indexed in prepared:
                for task in spec.tasks:
                    expected = case.expected.get(task.id)
                    if task.kind == TaskKind.RECORDS or expected is None or not expected.evidence:
                        continue
                    package = gather_evidence(
                        request_from_task(task, resolve_task_documents(task, manifest)),
                        list(indexed.values()), tools, settings, manifest=manifest, profile=profile)
                    tasks += 1
                    passages += len(package.passages)
                    tokens += package.trace.used_tokens
                    gap_tasks += bool(package.gaps)
                    in_cand = {pid for pid, _ in package.trace.reranked}
                    in_sel = set(package.trace.selected) | {p.passage_id for p in package.passages
                                                            if p.origin.value == "qualifier" or p.role.value == "supports"}
                    in_pkg = {p.passage_id for p in package.passages}
                    for ev in expected.evidence:
                        if not ev.quote or ev.document not in by_name:
                            continue
                        doc = indexed[by_name[ev.document].document_id]
                        wanted = normalize_text(ev.quote)
                        gold = {p.passage_id for p in doc.passages if wanted in normalize_text(p.text)}
                        if not gold:
                            unmappable += 1
                            continue
                        items += 1
                        hits["cand"] += bool(gold & in_cand)
                        hits["sel"] += bool(gold & in_sel)
                        hits["pkg"] += bool(gold & in_pkg)
                        if ev.role in EXCEPTION_ROLES:
                            exc_items += 1
                            hits["exc"] += bool(gold & in_pkg)
            rows.append(RetrievalRow(
                profile=profile, variant=variant, evidence_items=items, unmappable=unmappable,
                candidate_recall=_ratio(hits["cand"], items), selected_recall=_ratio(hits["sel"], items),
                package_recall=_ratio(hits["pkg"], items), exception_items=exc_items,
                exception_package_recall=_ratio(hits["exc"], exc_items),
                mean_passages=passages / tasks if tasks else 0.0, mean_tokens=tokens / tasks if tasks else 0.0,
                tasks_with_gaps=gap_tasks))
    return RetrievalReport(rows=rows)
