"""Measure records extraction (S09): did the engine find the right records, with the right fields, and say so honestly?

Scored per task, against the labeled records:
  recall      share of the labeled records the engine found          precision   share of the engine's records that are labeled
  field exact normalized equality of a field value (case, spacing)    field near  token overlap >= 0.9 (the meaning is the same, the edges differ)
  completeness / status   what the engine claimed about the collection, against the label

Records are matched BY CONTENT, not by order: every labeled/found pair is scored on its fields (token overlap) and the best pairs are
taken first (a pair needs 0.5). A labeled record nobody matched is a MISS, a found record nobody matched is a FALSE record.
`--save DIR` stores what is needed to ask, offline, WHY each miss happened (`attribute_records`).
"""

import json
import re
import time
from collections import Counter
from pathlib import Path

from ..adapters.registry import AdapterSet
from ..artifacts import ArtifactStore
from ..config import RuntimeConfig
from ..contracts.common import Completeness, StrictModel, TaskKind, TaskStatus
from ..events import emit, progress
from ..indexing.builder import IndexBuilder
from ..parsing.metrics import normalize_text
from ..records.records_pipeline import extract_records
from ..retrieval.tools import build_retrieval_tools
from ..scope import DocumentRequest, build_manifest, resolve_task_documents
from ..spec import compile_specification_file
from ..validation.records import decide_records
from .corpus import Corpus
from .scoped import measured, split_of

THRESHOLD = 0.5


def _tokens(value) -> list[str]:
    return re.findall(r"\w+", normalize_text(str(value)))


def overlap(a, b) -> float:
    """Token F1 of two field values (1.0 = same words)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    common = sum((Counter(ta) & Counter(tb)).values())
    if not common:
        return 0.0
    p, r = common / len(tb), common / len(ta)
    return 2 * p * r / (p + r)


def exact(a, b) -> bool:
    return " ".join(_tokens(a)) == " ".join(_tokens(b))


def pair_score(gold: dict, found: dict) -> float:
    return sum(overlap(v, found.get(k)) for k, v in gold.items()) / max(len(gold), 1)


def match(gold: list[dict], found: list[dict]) -> dict[int, int]:
    """gold index -> found index, best pairs first, each used once, a pair needs `THRESHOLD`."""
    pairs = sorted(((pair_score(g, f), i, j) for i, g in enumerate(gold) for j, f in enumerate(found)), reverse=True)
    taken_g, taken_f, out = set(), set(), {}
    for score, i, j in pairs:
        if score >= THRESHOLD and i not in taken_g and j not in taken_f:
            out[i] = j
            taken_g.add(i)
            taken_f.add(j)
    return out


class TaskRecordsScore(StrictModel):
    case_id: str
    task_id: str
    profile: str
    split: str = "no split"  # the case's `split:` tag
    gold: int
    found: int
    matched: int
    fields_total: int
    fields_exact: int
    fields_near: int
    status: str
    completeness: str
    expected_completeness: str
    seconds: float
    misses: list[str] = []  # labeled records nobody matched
    false_records: list[str] = []
    wrong_fields: list[str] = []

    @property
    def recall(self) -> float:
        return self.matched / self.gold if self.gold else 1.0

    @property
    def precision(self) -> float:
        return self.matched / self.found if self.found else (1.0 if not self.gold else 0.0)


class RecordsReport(StrictModel):
    scores: list[TaskRecordsScore]

    def totals(self, split: str | None = None) -> dict:
        scores = [s for s in self.scores if split is None or s.split == split]
        t = lambda f: sum(f(s) for s in scores)  # noqa: E731
        gold, found, matched = t(lambda s: s.gold), t(lambda s: s.found), t(lambda s: s.matched)
        fields = t(lambda s: s.fields_total)
        return {"tasks": len(scores), "gold": gold, "found": found, "matched": matched,
                "recall": matched / gold if gold else None, "precision": matched / found if found else None,
                "field_exact": t(lambda s: s.fields_exact) / fields if fields else None,
                "field_near": t(lambda s: s.fields_near) / fields if fields else None,
                "supported": sum(s.status == "supported" for s in scores),
                "complete_checked": sum(s.completeness == "checked" for s in scores),
                "seconds": sum(s.seconds for s in scores)}

    def headline(self) -> dict[str, float | None]:
        """The numbers compared between repeated runs (`eval --repeat`)."""
        t = self.totals()
        return {"record recall": t["recall"], "record precision": t["precision"], "field exact": t["field_exact"], "tasks supported": float(t["supported"])}

    def format(self) -> str:
        head = f"{'case/task':<34}{'gold':>5}{'found':>6}{'recall':>8}{'prec.':>7}{'exact':>7}{'near':>6}  {'status':<10}{'complete':<9}{'sec':>6}"
        lines = [head]
        for s in self.scores:
            exact_ = f"{s.fields_exact / s.fields_total:.0%}" if s.fields_total else "n/a"
            near = f"{s.fields_near / s.fields_total:.0%}" if s.fields_total else "n/a"
            lines.append(f"{s.case_id + '/' + s.task_id:<34}{s.gold:>5}{s.found:>6}{s.recall:>8.0%}{s.precision:>7.0%}{exact_:>7}{near:>6}  "
                         f"{s.status:<10}{s.completeness:<9}{s.seconds:>6.0f}")
        t = self.totals()
        pct = lambda v: "n/a" if v is None else f"{v:.0%}"  # noqa: E731
        lines += ["", f"TOTAL {t['tasks']} task(s): recall {pct(t['recall'])} ({t['matched']}/{t['gold']}), precision {pct(t['precision'])} "
                      f"({t['matched']}/{t['found']}), field exact {pct(t['field_exact'])}, near {pct(t['field_near'])}, "
                      f"supported {t['supported']}, completeness checked {t['complete_checked']}"]
        splits = sorted({s.split for s in self.scores})
        if splits not in ([], ["no split"]):  # design fixes on train / validation; the test split is the number to report
            for split in splits:
                t = self.totals(split)
                lines.append(f"  split {split:<11}{t['tasks']} task(s): recall {pct(t['recall'])} ({t['matched']}/{t['gold']}), precision {pct(t['precision'])}, "
                             f"field exact {pct(t['field_exact'])}, supported {t['supported']}")
        for s in self.scores:
            lines += [f"  MISS   {s.case_id}/{s.task_id}: {m}" for m in s.misses]
            lines += [f"  FALSE  {s.case_id}/{s.task_id}: {m}" for m in s.false_records]
            lines += [f"  FIELD  {s.case_id}/{s.task_id}: {m}" for m in s.wrong_fields[:6]]
        return "\n".join(lines)


@measured
def evaluate_records(
    corpus: Corpus, config: RuntimeConfig, adapters: AdapterSet, store: ArtifactStore, profiles: list[str],
    tags: list[str] | None = None, case_ids: list[str] | None = None, tasks: list[str] | None = None, save_dir: Path | None = None,
) -> RecordsReport:
    cases = [c for c in corpus.cases if all(t in c.tags for t in tags or []) and (not case_ids or c.case_id in case_ids)]
    if not cases:
        raise ValueError("no corpus case matches the given tags / case ids")
    builder = IndexBuilder(config, adapters, store)
    tools = build_retrieval_tools(config, adapters, store)
    scores: list[TaskRecordsScore] = []
    for profile in profiles:
        for number, case in enumerate(cases, start=1):
            spec = compile_specification_file(corpus.root / case.spec)
            wanted = [t for t in spec.tasks if t.kind == TaskKind.RECORDS and case.expected.get(t.id) is not None and (not tasks or t.id in tasks)]
            if not wanted:
                continue
            progress("eval", number, len(cases), f"cases ({profile})")
            paths = [corpus.root / "documents" / n for n in case.documents]
            manifest = build_manifest(DocumentRequest.from_paths(paths))
            by_name = {e.filename: e for e in manifest.documents}
            indexed = {e.document_id: builder.index(e, p) for e, p in zip((by_name[p.name] for p in paths), paths)}
            for task in wanted:
                started = time.time()
                documents = [indexed[d] for d in resolve_task_documents(task, manifest) if d in indexed]
                outcome = extract_records(task, documents, adapters, config, profile, scorer=lambda q, t: tools.reranker.score(q, t))
                report, decision, completeness = decide_records(task, outcome, config.acceptance)
                expected = case.expected[task.id]
                gold = [r.fields for r in expected.records]
                found = [{o.result.field_id: o.proposal.value for o in r.outcomes} for r in outcome.records]
                pairs = match(gold, found)
                total = exact_n = near_n = 0
                wrong = []
                for gi, fj in pairs.items():
                    for key, value in gold[gi].items():
                        total += 1
                        got = found[fj].get(key)
                        exact_n += exact(value, got)
                        near_n += overlap(value, got) >= 0.9
                        if overlap(value, got) < 0.9:
                            wrong.append(f"{_short(next(iter(gold[gi].values())))}.{key}: expected {_short(value)!r} got {_short(got)!r}")
                scores.append(TaskRecordsScore(
                    case_id=case.case_id, task_id=task.id, profile=profile, split=split_of(case), gold=len(gold), found=len(found), matched=len(pairs),
                    fields_total=total, fields_exact=exact_n, fields_near=near_n, status=decision.status.value, completeness=completeness.value,
                    expected_completeness=(expected.completeness or Completeness.CHECKED).value, seconds=time.time() - started,
                    misses=[_short(" | ".join(str(v) for v in g.values())) for i, g in enumerate(gold) if i not in pairs],
                    false_records=[_short(r.record.key + ": " + r.record.text) for j, r in enumerate(outcome.records) if j not in pairs.values()],
                    wrong_fields=wrong))
                emit("eval", f"{case.case_id}/{task.id}: {len(pairs)}/{len(gold)} records, {decision.status.value}", "ok")
                if save_dir:
                    _save(save_dir, case.case_id, task.id, profile, expected, gold, outcome, documents, decision.status.value, completeness.value,
                          [f.model_dump(mode="json") for f in report.findings])
    return RecordsReport(scores=scores)


def _short(value, n: int = 90) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= n else text[:n] + "…"


# --- why did each miss happen? (offline, from what --save stored) ---------------------------------------------------------------


def _save(save_dir, case_id, task_id, profile, expected, gold, outcome, documents, status, completeness, findings) -> None:
    Path(save_dir).mkdir(parents=True, exist_ok=True)
    record = {
        "case": case_id, "task_id": task_id, "status": status, "completeness": completeness, "findings": findings, "scopes": outcome.scopes,
        "document_text": "\n".join(b.text for d in documents for b in d.representation.blocks), "scope_text": "\n".join(outcome.scope_texts),
        "gold": gold, "found": [{"key": r.record.key, "text": r.record.text, "fields": {o.result.field_id: o.proposal.value for o in r.outcomes},
                                 "statuses": {o.result.field_id: o.result.status.value for o in r.outcomes}} for r in outcome.records]}
    (Path(save_dir) / f"{case_id}__{task_id}__{profile}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def _has(text: str, needle) -> bool:
    n = " ".join(_tokens(needle))
    return bool(n) and n in " ".join(_tokens(text))


class RecordCause(StrictModel):
    case_id: str
    task_id: str
    what: str  # the miss, the false record or the wrong field
    cause: str
    fixable: str


CAUSES = {
    "parse": "yes if a re-parse recovers the text; otherwise a parsing limit",
    "scope": "yes: the region the model named does not contain the record (scope prompt, outline, or fall back to the whole document)",
    "boundary": "yes: discovery prompt, window size, how the first words are located (the record is in the scope but no record covers it, or covers it wrongly)",
    "false_record": "yes: boundary definition in the YAML or discovery prompt (something that is not a record was listed)",
    "field": "partly: the record text is right and the field value is wrong (field description in the YAML, extraction prompt, thinking)",
    "span": "yes: the record's span does not contain the labeled value (boundary / continuation problem)",
}


def attribute_records(save_dir: Path) -> list[RecordCause]:
    out: list[RecordCause] = []
    for f in sorted(Path(save_dir).glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        gold, found = d["gold"], [{**r["fields"]} for r in d["found"]]
        pairs = match(gold, found)
        for i, g in enumerate(gold):
            label = _short(" | ".join(str(v) for v in g.values()), 70)
            longest = max((str(v) for v in g.values()), key=len)
            if i in pairs:
                span = d["found"][pairs[i]]["text"]
                for key, value in g.items():
                    if overlap(value, found[pairs[i]].get(key)) < 0.9:
                        cause = "field" if _has(span, value) or overlap(value, span) > 0.9 else "span"
                        out.append(RecordCause(case_id=d["case"], task_id=d["task_id"], what=f"{label} / {key}", cause=cause, fixable=CAUSES[cause]))
                continue
            if not _has(d["document_text"], longest[:80]):
                cause = "parse"
            elif not _has(d["scope_text"], longest[:80]):
                cause = "scope"
            else:
                cause = "boundary"
            out.append(RecordCause(case_id=d["case"], task_id=d["task_id"], what=f"missing: {label}", cause=cause, fixable=CAUSES[cause]))
        for j, r in enumerate(d["found"]):
            if j not in pairs.values():
                out.append(RecordCause(case_id=d["case"], task_id=d["task_id"], what=f"extra: {_short(r['key'] + ': ' + r['text'], 70)}",
                                       cause="false_record", fixable=CAUSES["false_record"]))
    return out
