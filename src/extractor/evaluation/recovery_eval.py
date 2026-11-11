"""Measure S12 on the labeled corpus: what recovery rescued, what it harmed, and what each rescue cost.

The whole engine runs once per case (recovery on, as configured). Every task result carries its recovery trail, and the trail
says what the status was BEFORE recovery, so one run gives both sides of the comparison:

  rescue   not accepted before, and the final answer is right (accepted and correct, or a correct "not found")
  harm     the final answer is a FALSE ACCEPT or a FALSE ABSENCE that the first attempt did not have (it had abstained or failed)
  neutral  recovery ran and the outcome class did not change (its calls were spent for nothing)

An action is worth keeping on when it rescues answers with zero harm; `calls per rescue` is its price. Records tasks are judged by
their status and the number of records only (use `eval --records` for record-level accuracy).
"""

from collections import Counter
from pathlib import Path

from ..adapters.registry import AdapterSet
from ..artifacts import ArtifactStore
from ..config import RuntimeConfig
from ..contracts.common import StrictModel, TaskKind, TaskStatus
from ..contracts.result import TaskResult
from ..events import progress
from ..indexing.builder import IndexBuilder
from ..pipeline import run_pipeline_report
from ..scope import DocumentRequest, build_manifest
from ..spec import compile_specification_file
from .acceptance_eval import GOOD, judge
from .corpus import Corpus, ExpectedResult
from .scoped import measured, split_of

HARM = {"false_accept", "false_absence"}


class RecoveredTask(StrictModel):
    case_id: str
    task_id: str
    split: str
    actions: list[str]  # every action tried, in order
    kept: list[str]  # the ones whose result was kept
    before: str  # outcome class of the first attempt
    after: str  # outcome class of the final result
    effect: str  # rescue | harm | neutral
    calls: int
    seconds: float
    note: str = ""


class ActionRow(StrictModel):
    action: str
    attempts: int = 0
    kept: int = 0
    rescues: int = 0
    harm: int = 0
    calls: int = 0
    seconds: float = 0.0


class RecoveryReport(StrictModel):
    tasks: int  # labeled tasks that were run
    before: dict[str, int]  # outcome class -> tasks, first attempts
    after: dict[str, int]
    actions: list[ActionRow]
    changed: list[RecoveredTask]
    calls_total: int = 0  # every model call of the run
    calls_recovery: int = 0

    def format(self) -> str:
        def line(name: str, counts: dict[str, int]) -> str:
            accepted = counts.get("accepted_correct", 0) + counts.get("false_accept", 0)
            precision = counts.get("accepted_correct", 0) / accepted if accepted else None
            return (f"{name:<10}{accepted / self.tasks if self.tasks else 0:>10.0%}{'    -' if precision is None else f'{precision:>9.0%}':>10}"
                    f"{counts.get('false_accept', 0):>10}{counts.get('false_absence', 0):>10}{counts.get('failed', 0):>8}")

        lines = [f"{self.tasks} labeled task(s); recovery spent {self.calls_recovery} of {self.calls_total} model call(s)", "",
                 f"{'':<10}{'automation':>10}{'precision':>10}{'falseacc':>10}{'falseabs':>10}{'failed':>8}",
                 line("before", self.before), line("after", self.after), "",
                 f"{'action':<16}{'attempts':>9}{'kept':>6}{'rescues':>9}{'harm':>6}{'calls':>7}{'sec':>8}{'calls/rescue':>14}"]
        for a in self.actions:
            price = f"{a.calls / a.rescues:.1f}" if a.rescues else "-"
            lines.append(f"{a.action:<16}{a.attempts:>9}{a.kept:>6}{a.rescues:>9}{a.harm:>6}{a.calls:>7}{a.seconds:>8.0f}{price:>14}")
        lines.append("")
        lines += [f"  {t.effect:<8}{t.case_id}/{t.task_id} [{t.split}] {t.before} -> {t.after} via {', '.join(t.actions)}"
                  f" (kept: {', '.join(t.kept) or 'none'}; {t.calls} call(s)) {t.note}" for t in self.changed]
        lines.append("Keep an action on only if it has rescues and NO harm on your corpus; judge the test split separately.")
        return "\n".join(lines)


def outcome_of(expected: ExpectedResult, result: TaskResult, status: TaskStatus, final: bool) -> str:
    """The outcome class of a task at a given status (the first attempt has no accepted value: recovery only runs on unaccepted tasks)."""
    if result.kind == TaskKind.RECORDS:
        if status == TaskStatus.SUPPORTED and expected.status == TaskStatus.SUPPORTED:
            return "accepted_correct" if len(result.records) == len(expected.records) else "false_accept"
        return judge(expected.status, None, status, None, None)
    return judge(expected.status, expected.value, status, result.value if final else None, None)


@measured
def evaluate_recovery(
    corpus: Corpus, config: RuntimeConfig, adapters: AdapterSet, store: ArtifactStore, profile: str | None = None,
    kinds: list[TaskKind] | None = None, tags: list[str] | None = None, case_ids: list[str] | None = None, save_dir: Path | None = None,
) -> RecoveryReport:
    cases = [c for c in corpus.cases if all(t in c.tags for t in tags or []) and (not case_ids or c.case_id in case_ids)]
    if not cases:
        raise ValueError("no corpus case matches the given tags / case ids")
    builder = IndexBuilder(config, adapters, store)
    before, after = Counter(), Counter()
    rows: dict[str, ActionRow] = {}
    changed: list[RecoveredTask] = []
    tasks = calls_total = calls_recovery = 0
    for number, case in enumerate(cases, start=1):
        progress("eval", number, len(cases), "cases (recovery)")
        spec = compile_specification_file(corpus.root / case.spec)
        paths = [corpus.root / "documents" / n for n in case.documents]
        manifest = build_manifest(DocumentRequest.from_paths(paths))
        by_name = {e.filename: e for e in manifest.documents}
        indexed = {e.document_id: builder.index(e, p) for e, p in zip((by_name[p.name] for p in paths), paths)}
        report = run_pipeline_report(spec, manifest, adapters, config, indexed, store=store, profile=profile)
        calls_total += report.summary.total.calls
        if save_dir:
            Path(save_dir).mkdir(parents=True, exist_ok=True)
            (Path(save_dir) / f"{case.case_id}.json").write_text(report.model_dump_json(indent=1), encoding="utf-8")
        for result in report.results:
            expected = case.expected.get(result.task_id)
            if expected is None or (kinds and result.kind not in kinds):
                continue
            tasks += 1
            tried = [a for a in result.recovery if a.action != "none"]
            first = tried[0].status_before if tried else result.status
            was, now = outcome_of(expected, result, first, final=not tried), outcome_of(expected, result, result.status, final=True)
            before[was] += 1
            after[now] += 1
            if not tried:
                continue
            effect = "rescue" if now in GOOD and was not in GOOD else "harm" if now in HARM and was not in HARM else "neutral"
            kept = [a.action for a in tried if a.kept]
            for a in tried:
                row = rows.setdefault(a.action, ActionRow(action=a.action))
                credited = bool(kept) and a.action == kept[-1]  # the last kept action produced the final result
                rows[a.action] = row.model_copy(update={
                    "attempts": row.attempts + 1, "kept": row.kept + a.kept, "calls": row.calls + a.calls, "seconds": row.seconds + a.seconds,
                    "rescues": row.rescues + (credited and effect == "rescue"), "harm": row.harm + (credited and effect == "harm")})
            calls_recovery += sum(a.calls for a in tried)
            changed.append(RecoveredTask(
                case_id=case.case_id, task_id=result.task_id, split=split_of(case), actions=[a.action for a in tried], kept=kept, before=was,
                after=now, effect=effect, calls=sum(a.calls for a in tried), seconds=sum(a.seconds for a in tried),
                note="; ".join(a.why for a in tried if not a.kept)[:160]))
    return RecoveryReport(tasks=tasks, before=dict(before), after=dict(after), actions=sorted(rows.values(), key=lambda r: r.action),
                          changed=changed, calls_total=calls_total, calls_recovery=calls_recovery)
