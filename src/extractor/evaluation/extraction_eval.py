"""Phase 5 evaluation: does value extraction return the right value, or abstain, or claim absence wrongly?

Every labeled VALUE task is run through retrieval and then S07 (no validation or
acceptance yet), and the proposal is compared with the human label:

    correct            a value was found and equals the label
    wrong              a value was found but differs from the label
        wrong_but_cited  ...and the labeled evidence quote is in the passage the value was taken from
    abstained          the engine said `uncertain` (conflict or incomplete evidence) where a value exists
    false_absence      the engine said the value is absent, but the label has one   <- the worst error
    correct_absence    absent and the label says `not_found`
    false_value        a value was found but the label says `not_found`            <- hallucination
    correct_uncertain  `uncertain` and the label says `uncertain`
    failed             the model call or its output was unusable
    other              anything else (e.g. label `uncertain`, engine found a value)

Rows are per retrieval profile AND per grouping (S05), so `light` and `deep`, or one call per task and
tasks that share a call (`--grouping off,auto`), can be compared on the same cases. The evidence is retrieved once per
case and profile and reused by every grouping, so the only difference between two rows is how the model was asked.
`ExtractionReport.differences()` lists the tasks whose outcome changed between groupings: read those one by one.
"""

import time
from collections import Counter
from pathlib import Path

from ..adapters.registry import AdapterSet
from ..artifacts import ArtifactStore
from ..config import RuntimeConfig
from ..contracts.candidate import ProposalOutcome
from ..contracts.common import StrictModel, TaskKind, TaskStatus
from ..events import emit, progress
from ..extraction import ask_batch, conclude_task
from ..indexing.builder import IndexBuilder
from ..interpretation.interpretation_pipeline import interpret_task
from ..parsing.metrics import normalize_text
from ..planning import plan_extraction
from ..retrieval.retrieval_pipeline import gather_evidence
from ..retrieval.tools import build_retrieval_tools
from ..retrieval.types import request_from_task
from ..scope import DocumentRequest, build_manifest, resolve_task_documents
from ..spec import compile_specification_file
from .attribution import save_record
from .corpus import Corpus
from .matching import same_value  # noqa: F401  (re-exported: other evaluators import it from here)

# Task kinds the engine can extract today. Add a kind here when its extractor exists (records: phase 8);
# the evaluator, CLI (`--kinds`) and UI then accept it without other changes.
IMPLEMENTED_KINDS = [TaskKind.VALUE, TaskKind.INTERPRETATION]

OUTCOMES = ["correct", "wrong", "wrong_but_cited", "abstained", "false_absence", "correct_absence",
            "false_value", "correct_uncertain", "failed", "other"]


class ExtractionRow(StrictModel):
    profile: str
    tasks: int
    counts: dict[str, int]
    mean_seconds: float
    grouping: str = "off"  # S05: off = one extraction call per task, auto = tasks that share evidence share a call
    model_calls: int = 0  # distinct calls to the chat model (a shared call counts once)
    extraction_calls: int = 0  # ...of which extraction calls (the rest verify / choose)
    prompt_tokens: int = 0  # as reported by the model server (0 when it reports none)
    completion_tokens: int = 0
    model_seconds: float = 0.0  # seconds the chat model took, summed over the distinct calls

    def rate(self, name: str) -> float:
        return self.counts.get(name, 0) / self.tasks if self.tasks else 0.0


class TaskOutcome(StrictModel):
    case_id: str
    task_id: str
    profile: str
    description: str = ""
    outcome: str
    expected: object = None
    got: object = None
    reason: str = ""
    grouping: str = "off"
    shared_with: list[str] = []  # the other tasks that were answered by the same extraction call


class ExtractionReport(StrictModel):
    rows: list[ExtractionRow]
    outcomes: list[TaskOutcome]  # one per task, profile and grouping, so every miss can be inspected

    def differences(self) -> list[dict]:
        """Tasks whose outcome or value differs between groupings (same case, task and profile): the A/B comparison, task by task."""
        by_task: dict[tuple, dict[str, TaskOutcome]] = {}
        for o in self.outcomes:
            by_task.setdefault((o.case_id, o.task_id, o.profile), {})[o.grouping] = o
        rows = []
        for (case_id, task_id, profile), arms in by_task.items():
            if len(arms) > 1 and len({(a.outcome, repr(a.got)) for a in arms.values()}) > 1:
                rows.append({"case_id": case_id, "task_id": task_id, "profile": profile, "description": next(iter(arms.values())).description,
                             "arms": {g: {"outcome": a.outcome, "got": a.got, "shared_with": a.shared_with, "reason": a.reason}
                                      for g, a in arms.items()}})
        return rows

    def format(self) -> str:
        head = (f"{'profile':<10}{'group':<6}{'tasks':>6}{'correct':>9}{'wrong':>7}{'cited':>7}{'abst.':>7}{'falseabs':>9}{'falseval':>9}{'fail':>6}"
                f"{'sec':>7}{'calls':>7}{'extract':>8}{'prompt tok':>11}")
        pct = lambda r, n: f"{r.rate(n) * 100:5.0f}%"
        lines = [head]
        for r in self.rows:
            lines.append(f"{r.profile:<10}{r.grouping:<6}{r.tasks:>6}{pct(r, 'correct'):>9}{pct(r, 'wrong'):>7}{pct(r, 'wrong_but_cited'):>7}"
                         f"{pct(r, 'abstained'):>7}{pct(r, 'false_absence'):>9}{pct(r, 'false_value'):>9}{pct(r, 'failed'):>6}"
                         f"{r.mean_seconds:>7.1f}{r.model_calls:>7}{r.extraction_calls:>8}{r.prompt_tokens:>11}")
        lines.append("")
        lines += [f"  {o.outcome:<16}{o.case_id}/{o.task_id} [{o.profile}/{o.grouping}] expected={o.expected!r} got={o.got!r} {o.reason}"
                  for o in self.outcomes if o.outcome not in ("correct", "correct_absence", "correct_uncertain")]
        changed = self.differences()
        if changed:
            lines += ["", f"Tasks whose result changed between groupings ({len(changed)}):"]
            for d in changed:
                lines.append(f"  {d['case_id']}/{d['task_id']} [{d['profile']}]: " + "  |  ".join(
                    f"{g}: {a['outcome']} {a['got']!r}" + (f" (call shared with {', '.join(a['shared_with'])})" if a["shared_with"] else "")
                    for g, a in d["arms"].items()))
        return "\n".join(lines)


def classify(expected_status: TaskStatus, expected_value, proposal, passage_texts: dict[str, str], gold_quotes: list[str]) -> str:
    outcome = proposal.outcome
    if outcome == ProposalOutcome.FAILED:
        return "failed"
    if expected_status == TaskStatus.SUPPORTED:
        if outcome == ProposalOutcome.FOUND:
            if same_value(expected_value, proposal.value):
                return "correct"
            cited = any(normalize_text(q) in normalize_text(passage_texts.get(c.passage_id or "", ""))
                        for c in proposal.chosen for q in gold_quotes)
            return "wrong_but_cited" if cited else "wrong"
        return {ProposalOutcome.ABSENT: "false_absence", ProposalOutcome.UNCERTAIN: "abstained"}.get(outcome, "other")
    if expected_status == TaskStatus.NOT_FOUND:
        return {ProposalOutcome.ABSENT: "correct_absence", ProposalOutcome.FOUND: "false_value"}.get(outcome, "other")
    if expected_status == TaskStatus.UNCERTAIN and outcome == ProposalOutcome.UNCERTAIN:
        return "correct_uncertain"
    return "other"


def evaluate_extraction(
    corpus: Corpus, config: RuntimeConfig, adapters: AdapterSet, store: ArtifactStore, profiles: list[str],
    kinds: list[TaskKind] | None = None, tags: list[str] | None = None, case_ids: list[str] | None = None,
    groupings: list[str] | None = None, save_dir: Path | None = None,
) -> ExtractionReport:
    """Score extraction on the labeled tasks of the selected `kinds` (default: all implemented) and, optionally,
    only on cases carrying every tag in `tags` (e.g. `split:test`, `digital`) and, if `case_ids` is given, only on those cases.

    `groupings` (default: the runtime config's `planning.grouping`) runs the same cases once per setting, e.g. ["off", "auto"]
    to compare one call per task with tasks that share a call. Each case is planned (S05) over its labeled tasks."""
    kinds = kinds or list(IMPLEMENTED_KINDS)
    missing = [k.value for k in kinds if k not in IMPLEMENTED_KINDS]
    if missing:
        raise ValueError(f"extraction for task kind(s) {missing} is not implemented yet; "
                         f"available: {[k.value for k in IMPLEMENTED_KINDS]}")
    groupings = groupings or [config.planning.grouping]
    bad = [g for g in groupings if g not in ("off", "auto")]
    if bad:
        raise ValueError(f"unknown grouping {bad}; use off or auto")
    unknown = [i for i in case_ids or [] if i not in {c.case_id for c in corpus.cases}]
    if unknown:
        raise ValueError(f"unknown case id(s) {unknown}")
    cases = [c for c in corpus.cases if all(t in c.tags for t in tags or []) and (not case_ids or c.case_id in case_ids)]
    if not cases:
        raise ValueError(f"no corpus case has all the tags {tags}" + (f" among {case_ids}" if case_ids else ""))
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
    retrieved: dict[tuple, tuple] = {}  # (profile, case) -> evidence of every labeled task, shared by all groupings
    for profile in profiles:
        settings = config.retrieval.resolve(profile)
        extraction = config.extraction_for(profile)
        for grouping in groupings:
            planning = config.planning.model_copy(update={"grouping": grouping})
            counts: Counter = Counter()
            tasks, seconds = 0, 0.0
            calls: dict[int, object] = {}  # distinct model calls (a shared call is one object seen by several tasks)
            for case_number, (case, spec, manifest, indexed) in enumerate(prepared, start=1):
                progress("eval", case_number, len(prepared), f"cases ({profile}, grouping {grouping})")
                texts = {p.passage_id: p.text for d in indexed.values() for p in d.passages}
                wanted = [t for t in spec.tasks if t.kind in kinds and case.expected.get(t.id) is not None]
                if not wanted:
                    continue
                if (profile, case.case_id) not in retrieved:  # S06 for every labeled task, once
                    started = time.time()
                    packages, document_ids = {}, {}
                    for task in wanted:
                        document_ids[task.id] = resolve_task_documents(task, manifest)
                        packages[task.id] = gather_evidence(
                            request_from_task(task, document_ids[task.id]), [indexed[d] for d in document_ids[task.id] if d in indexed],
                            tools, settings, manifest=manifest, profile=profile)
                    retrieved[(profile, case.case_id)] = (packages, document_ids, time.time() - started)
                packages, document_ids, retrieval_seconds = retrieved[(profile, case.case_id)]

                started = time.time()
                by_id = {t.id: t for t in wanted if t.kind == TaskKind.VALUE}  # S05 plans value tasks; S08 answers each interpretation alone
                plan = plan_extraction({t: packages[t] for t in by_id}, by_id, planning, extraction, adapters.extractor) if by_id else None   # S05
                asked = {}
                for batch in plan.batches if plan else []:                                                          # S07 steps 1-4, once per batch
                    batch_documents = list(dict.fromkeys(d for t in batch.task_ids for d in document_ids[t]))
                    asked.update(ask_batch([by_id[t] for t in batch.task_ids], packages, [indexed[d] for d in batch_documents if d in indexed],
                                           adapters.extractor, extraction, planning))
                for task in wanted:                                                                 # S07 steps 5-10, once per task
                    if task.kind == TaskKind.VALUE:
                        proposal = conclude_task(asked[task.id], adapters.extractor, extraction)
                    else:                                                                           # S08, one task at a time
                        proposal = interpret_task(task, packages[task.id], [indexed[d] for d in document_ids[task.id] if d in indexed],
                                                  adapters.interpretation_model, config.interpretation_for(profile))
                    tasks += 1
                    expected = case.expected[task.id]
                    outcome = classify(expected.status, expected.value, proposal, texts, [e.quote for e in expected.evidence if e.quote])
                    counts[outcome] += 1
                    if outcome in ("wrong", "wrong_but_cited"):
                        counts["wrong"] += outcome == "wrong_but_cited"  # "wrong" counts both; "wrong_but_cited" is a subset
                    calls.update({id(c): c for c in proposal.calls})
                    if save_dir:
                        save_record(save_dir, f"{case.case_id}__{task.id}__{profile}.json", task, expected, packages[task.id], proposal, texts, case.case_id)
                    outcomes.append(TaskOutcome(
                        case_id=case.case_id, task_id=task.id, profile=profile, grouping=grouping, description=task.description, outcome=outcome,
                        expected=expected.value if expected.status == TaskStatus.SUPPORTED else expected.status.value,
                        got=proposal.value if proposal.outcome == ProposalOutcome.FOUND else proposal.outcome.value,
                        reason=proposal.reason[:160], shared_with=asked[task.id].shared_with if task.id in asked else []))
                seconds += retrieval_seconds + time.time() - started
            replies = [c.reply for c in calls.values()]
            rows.append(ExtractionRow(
                profile=profile, grouping=grouping, tasks=tasks, counts=dict(counts), mean_seconds=seconds / tasks if tasks else 0.0,
                model_calls=len(calls), extraction_calls=sum(c.purpose.startswith(("extract", "interpret")) for c in calls.values()),
                prompt_tokens=sum(r.prompt_tokens or 0 for r in replies), completion_tokens=sum(r.completion_tokens or 0 for r in replies),
                model_seconds=round(sum(r.seconds or 0 for r in replies), 1)))
            emit("eval", f"{profile} / grouping {grouping}: {tasks} task(s), {len(calls)} model call(s)", "ok")
    return ExtractionReport(rows=rows, outcomes=outcomes)
