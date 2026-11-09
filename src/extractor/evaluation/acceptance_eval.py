"""Phase 6 evaluation: how trustworthy are the answers the engine ACCEPTS?

Every labeled VALUE task goes through retrieval, S07, and then S10/S11. The final status is compared
with the human label:

    accepted_correct   `supported` and the value equals the label
    false_accept       `supported` but wrong, or `supported` where the label says not_found / uncertain   <- the worst error
    over_abstention    `uncertain` although the label has a value
        ...proposal_right  (subset) the proposal had the right value and acceptance blocked it
    correct_absence    `not_found` and the label says not_found
    false_absence      `not_found` but the label has a value or says uncertain
    correct_uncertain  `uncertain` and the label says uncertain
    abstained_absent   `uncertain` where the label says not_found
    failed             technical failure

Headline numbers: automation rate (share `supported`), accepted-answer precision (share of `supported` that are right),
false accepts and false absences. `save_dir` stores every package and proposal so `from_dir` can re-run only
validation and acceptance (no model): that is how profiles and thresholds are tuned in seconds.
`qualify=True` writes a qualification record when the thresholds of `acceptance.qualification` are met.
"""

import json
import time
from collections import Counter
from pathlib import Path

from ..adapters.registry import AdapterSet
from ..artifacts import ArtifactStore
from ..config import RuntimeConfig
from ..contracts.candidate import ValueProposal
from ..contracts.common import StrictModel, TaskKind, TaskStatus
from ..contracts.evidence import EvidencePackage
from ..events import progress
from ..extraction import extract_value
from ..indexing.builder import IndexBuilder
from ..retrieval.queries import build_queries
from ..retrieval.retrieval_pipeline import gather_evidence
from ..retrieval.tools import build_retrieval_tools
from ..retrieval.types import request_from_task
from ..scope import DocumentRequest, build_manifest, resolve_task_documents
from ..spec import compile_specification_file
from ..validation import validate_and_accept
from ..validation.qualification import qualification_key, write_qualification
from .corpus import Corpus
from .extraction_eval import IMPLEMENTED_KINDS, same_value

GOOD = {"accepted_correct", "correct_absence", "correct_uncertain"}


class TaskAcceptance(StrictModel):
    case_id: str
    task_id: str
    profile: str
    description: str = ""
    outcome: str
    expected: object = None
    status: str
    value: object = None
    proposal_value: object = None
    reasons: list[str] = []
    categories: list[str] = []  # material finding categories


class AcceptanceRow(StrictModel):
    profile: str
    tasks: int
    counts: dict[str, int]
    categories: dict[str, int]  # material findings by category
    mean_seconds: float
    qualified: bool | None = None  # None = not asked

    def _n(self, *names: str) -> int:
        return sum(self.counts.get(n, 0) for n in names)

    @property
    def supported(self) -> int:
        return self._n("accepted_correct", "false_accept")

    @property
    def automation_rate(self) -> float | None:
        return self.supported / self.tasks if self.tasks else None

    @property
    def accepted_precision(self) -> float | None:
        return self._n("accepted_correct") / self.supported if self.supported else None

    @property
    def false_accept_rate(self) -> float | None:
        return self._n("false_accept") / self.tasks if self.tasks else None

    @property
    def false_absence_rate(self) -> float | None:
        return self._n("false_absence") / self.tasks if self.tasks else None


class AcceptanceReport(StrictModel):
    rows: list[AcceptanceRow]
    outcomes: list[TaskAcceptance]

    def format(self) -> str:
        pct = lambda v: "   -  " if v is None else f"{v * 100:5.0f}%"
        lines = [f"{'profile':<10}{'tasks':>6}{'automation':>11}{'precision':>10}{'falseacc':>9}{'falseabs':>9}{'overabst':>9}{'sec':>7}  qualified"]
        for r in self.rows:
            lines.append(f"{r.profile:<10}{r.tasks:>6}{pct(r.automation_rate):>11}{pct(r.accepted_precision):>10}{r.counts.get('false_accept', 0):>9}"
                         f"{r.counts.get('false_absence', 0):>9}{r.counts.get('over_abstention', 0):>9}{r.mean_seconds:>7.1f}  "
                         f"{'-' if r.qualified is None else 'yes' if r.qualified else 'no'}")
            if r.categories:
                lines.append("          blocking findings: " + ", ".join(f"{k} x{v}" for k, v in sorted(r.categories.items(), key=lambda kv: -kv[1])))
        lines.append("")
        lines += [f"  {o.outcome:<17}{o.case_id}/{o.task_id} [{o.profile}] expected={o.expected!r} status={o.status} value={o.value!r}"
                  f"{' (proposal had ' + repr(o.proposal_value) + ')' if o.outcome.startswith('over') else ''} {'; '.join(o.reasons[:1])}"
                  for o in self.outcomes if o.outcome not in GOOD]
        return "\n".join(lines)


def judge(expected_status: TaskStatus, expected_value, status: TaskStatus, value, proposal_value) -> str:
    """The outcome of one task: the final status against the human label."""
    if status == TaskStatus.FAILED:
        return "failed"
    if expected_status == TaskStatus.SUPPORTED:
        if status == TaskStatus.SUPPORTED:
            return "accepted_correct" if same_value(expected_value, value) else "false_accept"
        if status == TaskStatus.NOT_FOUND:
            return "false_absence"
        return "over_abstention"
    if expected_status == TaskStatus.NOT_FOUND:
        return {TaskStatus.NOT_FOUND: "correct_absence", TaskStatus.SUPPORTED: "false_accept"}.get(status, "abstained_absent")
    # the label says uncertain
    return {TaskStatus.UNCERTAIN: "correct_uncertain", TaskStatus.SUPPORTED: "false_accept"}.get(status, "false_absence")


def evaluate_acceptance(
    corpus: Corpus, config: RuntimeConfig, adapters: AdapterSet, store: ArtifactStore, profiles: list[str],
    kinds: list[TaskKind] | None = None, tags: list[str] | None = None, case_ids: list[str] | None = None,
    save_dir: Path | None = None, from_dir: Path | None = None, qualify: bool = False,
) -> AcceptanceReport:
    kinds = kinds or list(IMPLEMENTED_KINDS)
    missing = [k.value for k in kinds if k not in IMPLEMENTED_KINDS]
    if missing:
        raise ValueError(f"acceptance for task kind(s) {missing} is not implemented yet; available: {[k.value for k in IMPLEMENTED_KINDS]}")
    unknown = [i for i in case_ids or [] if i not in {c.case_id for c in corpus.cases}]
    if unknown:
        raise ValueError(f"unknown case id(s) {unknown}")
    cases = [c for c in corpus.cases if all(t in c.tags for t in tags or []) and (not case_ids or c.case_id in case_ids)]
    if not cases:
        raise ValueError(f"no corpus case has all the tags {tags}" + (f" among {case_ids}" if case_ids else ""))
    if from_dir and not Path(from_dir).is_dir():
        raise ValueError(f"saved proposals folder '{from_dir}' does not exist (create it with --save)")
    builder = IndexBuilder(config, adapters, store)
    tools = build_retrieval_tools(config, adapters, store)
    prepared = []
    for case in cases:
        spec = compile_specification_file(corpus.root / case.spec)
        paths = [corpus.root / "documents" / n for n in case.documents]
        manifest = build_manifest(DocumentRequest.from_paths(paths))
        by_name = {e.filename: e for e in manifest.documents}
        indexed = {e.document_id: builder.index(e, p) for e, p in zip((by_name[p.name] for p in paths), paths)}
        prepared.append((case, spec, manifest, indexed))

    rows, outcomes = [], []
    for profile in profiles:
        settings = config.retrieval.resolve(profile)
        extraction = config.extraction_for(profile)
        counts: Counter = Counter()
        categories: Counter = Counter()
        tasks, seconds = 0, 0.0
        for case_number, (case, spec, manifest, indexed) in enumerate(prepared, start=1):
            progress("eval", case_number, len(prepared), f"cases ({profile})")
            accepted: set[str] = set()
            for task_id in spec.graph.order:
                task, expected = spec.task(task_id), case.expected.get(task_id)
                if task.kind not in kinds or expected is None:
                    continue
                started = time.time()
                document_ids = resolve_task_documents(task, manifest)
                documents = [indexed[d] for d in document_ids if d in indexed]
                package, proposal = _package_and_proposal(
                    task, case.case_id, profile, documents, document_ids, manifest, tools, settings, adapters, extraction, save_dir, from_dir)
                query = build_queries(request_from_task(task, document_ids), settings).rerank
                validated = validate_and_accept(
                    task, proposal, package, documents, config.acceptance, scorer=lambda q, t: tools.reranker.score(q, t), query=query,
                    verifier=adapters.verifier, accepted_tasks=accepted, retries=extraction.retries)
                seconds += time.time() - started
                status = validated.decision.status
                value = proposal.value if status == TaskStatus.SUPPORTED else None
                if status == TaskStatus.SUPPORTED:
                    accepted.add(task_id)
                outcome = judge(expected.status, expected.value, status, value, proposal.value)
                if outcome == "over_abstention" and proposal.outcome.value == "found" and same_value(expected.value, proposal.value):
                    counts["proposal_right"] += 1  # a subset of over_abstention: acceptance blocked a right answer
                tasks += 1
                counts[outcome] += 1
                cats = sorted({f.category for f in validated.report.material})
                categories.update(cats)
                outcomes.append(TaskAcceptance(
                    case_id=case.case_id, task_id=task_id, profile=profile, description=task.description, outcome=outcome,
                    expected=expected.value if expected.status == TaskStatus.SUPPORTED else expected.status.value, status=status.value,
                    value=value, proposal_value=proposal.value if proposal.outcome.value == "found" else None,
                    reasons=validated.decision.reasons, categories=cats))
        row = AcceptanceRow(profile=profile, tasks=tasks, counts=dict(counts), categories=dict(categories),
                            mean_seconds=seconds / tasks if tasks else 0.0)
        if qualify:
            row = row.model_copy(update={"qualified": _qualify(row, config, adapters, store, profile)})
        rows.append(row)
    return AcceptanceReport(rows=rows, outcomes=outcomes)


def _package_and_proposal(task, case_id, profile, documents, document_ids, manifest, tools, settings, adapters, extraction, save_dir, from_dir):
    name = f"{case_id}__{task.id}__{profile}.json"
    if from_dir:
        saved = json.loads((Path(from_dir) / name).read_text(encoding="utf-8"))
        return EvidencePackage.model_validate(saved["package"]), ValueProposal.model_validate(saved["proposal"])
    package = gather_evidence(request_from_task(task, document_ids), documents, tools, settings, manifest=manifest, profile=profile)
    proposal = extract_value(task, package, documents, adapters.extractor, extraction)
    if save_dir:
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        (Path(save_dir) / name).write_text(json.dumps({"package": package.model_dump(mode="json"), "proposal": proposal.model_dump(mode="json")},
                                                      ensure_ascii=False), encoding="utf-8")
    return package, proposal


def _qualify(row: AcceptanceRow, config: RuntimeConfig, adapters: AdapterSet, store: ArtifactStore, profile: str) -> bool:
    t = config.acceptance.qualification
    precision = row.accepted_precision
    ok = (row.tasks >= t.min_tasks and precision is not None and precision >= t.min_accepted_precision
          and (row.false_absence_rate or 0.0) <= t.max_false_absence_rate)
    if ok:
        write_qualification(store, qualification_key(config, adapters, profile),
                            {"tasks": row.tasks, "accepted_precision": precision, "automation_rate": row.automation_rate,
                             "false_absence_rate": row.false_absence_rate, "profile": profile}, t.model_dump())
    return ok
