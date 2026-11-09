"""S05 evaluation: how much evidence do the value tasks of a case share? (retrieval only, no extraction model)

For every case of the corpus, every value task goes through S06 and `measure_overlap` compares the
evidence packages. The numbers say how much a shared model call COULD save before any is built:

    tokens        evidence tokens summed over the tasks (what separate calls read)
    distinct      the same, counting each passage once (what one call for all of them would read)
    shareable     1 - distinct / tokens: the most grouping could save on evidence tokens
    pairs >= 80%  pairs of tasks where one evidence set is (almost) inside the other: the easy groups
"""

from pydantic import computed_field

from ..adapters.registry import AdapterSet
from ..artifacts import ArtifactStore
from ..config import RuntimeConfig
from ..contracts.common import StrictModel, TaskKind
from ..events import progress
from ..indexing.builder import IndexBuilder
from ..planning.overlap import measure_overlap
from ..retrieval.retrieval_pipeline import gather_evidence
from ..retrieval.tools import build_retrieval_tools
from ..retrieval.types import request_from_task
from ..scope import DocumentRequest, build_manifest, resolve_task_documents
from ..spec import compile_specification_file
from .corpus import Corpus


class OverlapRow(StrictModel):
    case_id: str
    profile: str
    tasks: int
    tokens: int
    distinct_tokens: int
    pairs: int  # task pairs sharing at least one passage
    pairs_80: int  # ...with containment >= 80%
    pairs_50: int  # ...with containment >= 50%

    @computed_field  # type: ignore[prop-decorator]
    @property
    def shareable(self) -> float:
        return 1 - self.distinct_tokens / self.tokens if self.tokens else 0.0


class OverlapReport(StrictModel):
    rows: list[OverlapRow]

    def format(self) -> str:
        lines = [f"{'case':<20}{'profile':<10}{'tasks':>6}{'tokens':>8}{'distinct':>9}{'shareable':>10}{'pairs':>7}{'>=80%':>7}{'>=50%':>7}"]
        for r in self.rows:
            lines.append(f"{r.case_id:<20}{r.profile:<10}{r.tasks:>6}{r.tokens:>8}{r.distinct_tokens:>9}{r.shareable * 100:>9.0f}%"
                         f"{r.pairs:>7}{r.pairs_80:>7}{r.pairs_50:>7}")
        return "\n".join(lines)


def evaluate_overlap(
    corpus: Corpus, config: RuntimeConfig, adapters: AdapterSet, store: ArtifactStore, profiles: list[str],
    case_ids: list[str] | None = None,
) -> OverlapReport:
    """Overlap of the value tasks' evidence per case and retrieval profile. Uses the embedder and reranker, never the chat model."""
    unknown = [i for i in case_ids or [] if i not in {c.case_id for c in corpus.cases}]
    if unknown:
        raise ValueError(f"unknown case id(s) {unknown}")
    cases = [c for c in corpus.cases if not case_ids or c.case_id in case_ids]
    builder = IndexBuilder(config, adapters, store)
    tools = build_retrieval_tools(config, adapters, store)
    rows = []
    for profile in profiles:
        settings = config.retrieval.resolve(profile)
        for number, case in enumerate(cases, start=1):
            progress("eval", number, len(cases), f"cases ({profile})")
            spec = compile_specification_file(corpus.root / case.spec)
            paths = [corpus.root / "documents" / n for n in case.documents]
            manifest = build_manifest(DocumentRequest.from_paths(paths))
            by_name = {e.filename: e for e in manifest.documents}
            indexed = {e.document_id: builder.index(e, p) for e, p in zip((by_name[p.name] for p in paths), paths)}
            packages = {}
            for task in spec.tasks:
                if task.kind != TaskKind.VALUE:
                    continue
                documents = [indexed[d] for d in resolve_task_documents(task, manifest) if d in indexed]
                packages[task.id] = gather_evidence(request_from_task(task, [d.document_id for d in documents]), documents, tools,
                                                    settings, manifest=manifest, profile=profile)
            pairs = measure_overlap(packages)
            each = [{p.passage_id: p.token_count for p in pkg.passages} for pkg in packages.values()]
            rows.append(OverlapRow(
                case_id=case.case_id, profile=profile, tasks=len(packages), tokens=sum(sum(e.values()) for e in each),
                distinct_tokens=sum({k: v for e in each for k, v in e.items()}.values()), pairs=len(pairs),
                pairs_80=sum(r.containment >= 0.8 for r in pairs), pairs_50=sum(r.containment >= 0.5 for r in pairs)))
    return OverlapReport(rows=rows)
