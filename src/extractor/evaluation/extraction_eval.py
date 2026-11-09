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

Rows are per retrieval profile, so `light` and `deep` can be compared on the same cases.
"""

import time
from collections import Counter

from ..adapters.registry import AdapterSet
from ..artifacts import ArtifactStore
from ..config import RuntimeConfig
from ..contracts.candidate import ProposalOutcome
from ..contracts.common import StrictModel, TaskKind, TaskStatus
from ..events import progress
from ..extraction import extract_value
from ..indexing.builder import IndexBuilder
from ..parsing.metrics import normalize_text
from ..retrieval.retrieval_pipeline import gather_evidence
from ..retrieval.tools import build_retrieval_tools
from ..retrieval.types import request_from_task
from ..scope import DocumentRequest, build_manifest, resolve_task_documents
from ..spec import compile_specification_file
from .corpus import Corpus

# Task kinds the engine can extract today. Add a kind here when its extractor exists (interpretation: phase 7,
# records: phase 8); the evaluator, CLI (`--kinds`) and UI then accept it without other changes.
IMPLEMENTED_KINDS = [TaskKind.VALUE]

OUTCOMES = ["correct", "wrong", "wrong_but_cited", "abstained", "false_absence", "correct_absence",
            "false_value", "correct_uncertain", "failed", "other"]


class ExtractionRow(StrictModel):
    profile: str
    tasks: int
    counts: dict[str, int]
    mean_seconds: float

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


class ExtractionReport(StrictModel):
    rows: list[ExtractionRow]
    outcomes: list[TaskOutcome]  # one per task and profile, so every miss can be inspected

    def format(self) -> str:
        head = f"{'profile':<10}{'tasks':>6}{'correct':>9}{'wrong':>7}{'cited':>7}{'abst.':>7}{'falseabs':>9}{'falseval':>9}{'fail':>6}{'sec':>7}"
        pct = lambda r, n: f"{r.rate(n) * 100:5.0f}%"
        lines = [head]
        for r in self.rows:
            lines.append(f"{r.profile:<10}{r.tasks:>6}{pct(r, 'correct'):>9}{pct(r, 'wrong'):>7}{pct(r, 'wrong_but_cited'):>7}"
                         f"{pct(r, 'abstained'):>7}{pct(r, 'false_absence'):>9}{pct(r, 'false_value'):>9}{pct(r, 'failed'):>6}"
                         f"{r.mean_seconds:>7.1f}")
        lines.append("")
        lines += [f"  {o.outcome:<16}{o.case_id}/{o.task_id} [{o.profile}] expected={o.expected!r} got={o.got!r} {o.reason}"
                  for o in self.outcomes if o.outcome not in ("correct", "correct_absence", "correct_uncertain")]
        return "\n".join(lines)


def _plain(value) -> str:
    """Text compared the way a human labeler would: case, spacing, punctuation and a leading article do not matter."""
    import re

    return re.sub(r"^(the|a|an) ", "", normalize_text(str(value)))


def same_value(expected, got) -> bool:
    """Equality that ignores case, spacing and numeric type (5 == 5.0 == "5"); amounts compare amount and currency."""
    if isinstance(expected, dict) and isinstance(got, dict):
        return (float(expected.get("amount", 0)) == float(got.get("amount", 0))
                and (expected.get("currency") or "").upper() == (got.get("currency") or "").upper())
    if isinstance(expected, list) or isinstance(got, list):
        a, b = (expected if isinstance(expected, list) else [expected]), (got if isinstance(got, list) else [got])
        return len(a) == len(b) and all(any(same_value(x, y) for y in b) for x in a)
    try:
        return float(str(expected).replace(",", "")) == float(str(got).replace(",", ""))
    except ValueError:
        return _plain(expected) == _plain(got)


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
) -> ExtractionReport:
    """Score extraction on the labeled tasks of the selected `kinds` (default: all implemented) and, optionally,
    only on cases carrying every tag in `tags` (e.g. `split:test`, `digital`) and, if `case_ids` is given, only on those cases."""
    kinds = kinds or list(IMPLEMENTED_KINDS)
    missing = [k.value for k in kinds if k not in IMPLEMENTED_KINDS]
    if missing:
        raise ValueError(f"extraction for task kind(s) {missing} is not implemented yet; "
                         f"available: {[k.value for k in IMPLEMENTED_KINDS]}")
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
    for profile in profiles:
        settings = config.retrieval.resolve(profile)
        counts: Counter = Counter()
        tasks, seconds = 0, 0.0
        for case_number, (case, spec, manifest, indexed) in enumerate(prepared, start=1):
            progress("eval", case_number, len(prepared), f"cases ({profile})")
            texts = {p.passage_id: p.text for d in indexed.values() for p in d.passages}
            for task in spec.tasks:
                expected = case.expected.get(task.id)
                if task.kind not in kinds or expected is None:
                    continue
                started = time.time()
                document_ids = resolve_task_documents(task, manifest)
                documents = [indexed[d] for d in document_ids if d in indexed]
                package = gather_evidence(request_from_task(task, document_ids), documents, tools, settings,
                                          manifest=manifest, profile=profile)
                proposal = extract_value(task, package, documents, adapters.extractor, config.extraction_for(profile))
                seconds += time.time() - started
                tasks += 1
                outcome = classify(expected.status, expected.value, proposal, texts,
                                   [e.quote for e in expected.evidence if e.quote])
                counts[outcome] += 1
                if outcome in ("wrong", "wrong_but_cited"):
                    counts["wrong"] += outcome == "wrong_but_cited"  # "wrong" counts both; "wrong_but_cited" is a subset
                outcomes.append(TaskOutcome(case_id=case.case_id, task_id=task.id, profile=profile, description=task.description, outcome=outcome,
                                            expected=expected.value if expected.status == TaskStatus.SUPPORTED else expected.status.value,
                                            got=proposal.value if proposal.outcome == ProposalOutcome.FOUND else proposal.outcome.value,
                                            reason=proposal.reason[:160]))
        rows.append(ExtractionRow(profile=profile, tasks=tasks, counts=dict(counts), mean_seconds=seconds / tasks if tasks else 0.0))
    return ExtractionReport(rows=rows, outcomes=outcomes)
