"""Error attribution: for every wrong answer, WHICH stage went wrong, and can it be fixed?

`eval --extraction --save DIR` (and `--acceptance --save DIR`) writes one file per task with the evidence package, the proposal
and what the labeler expected. `eval --attribute DIR` reads them back, offline, and puts every miss in exactly one cause:

  parse            the labeled quote is not in the parsed text of the document (OCR / layout lost it)
  retrieval        the quote is in the document but not in the evidence package the model read
  evidence         the quote reached the model but the model did not report it (value: not a candidate; interpretation: not a stance)
  judgement        the decisive quote was reported and the final answer is still wrong or withheld (selection, label choice, judge)
  false_decision   the labeler says there is no answer, the engine decided one
  abstention       the labeler says nothing is stated and the engine withheld instead of saying not_found (safe, but not automated)
  failure          the model call or its output was unusable
  acceptance       the proposal was right and the engine did not accept it (or the other way round): see `eval --acceptance`

`fixable` says what could be done about it; the last two columns are a starting point for a person, not a verdict.
"""

import json
import re
from collections import Counter
from pathlib import Path

from ..contracts.candidate import ProposalOutcome, ValueProposal
from ..contracts.common import StrictModel, TaskStatus
from ..contracts.evidence import EvidencePackage
from ..extraction.quotes import _fold
from .matching import same_value

FIXABLE = {
    "parse": "yes if a re-parse (other OCR settings or backend) recovers the text; otherwise a parsing limit",
    "retrieval": "yes: queries, evidence budget, expansion, retrieval profile (`deep`)",
    "evidence": "yes: prompt and schema, passages per call, thinking; or the YAML description names the wrong thing",
    "judgement": "partly: criteria wording in the YAML, judge, thinking; if the evidence and criteria are unambiguous it is a model limit",
    "false_decision": "partly: criteria must say when silence is not a decision; the judge; else a model limit",
    "abstention": "partly: the engine withheld where the labeler found nothing; check what blocked it (judge, damaged text, redaction)",
    "failure": "yes: retries, context size, model server",
    "acceptance": "yes: acceptance profile and checks (see the acceptance report)",
    "ok": "",
}


class Attribution(StrictModel):
    case_id: str
    task_id: str
    kind: str
    cause: str
    expected: object = None
    got: object = None
    detail: str = ""


class AttributionReport(StrictModel):
    rows: list[Attribution]

    def counts(self) -> dict[str, int]:
        return dict(Counter(r.cause for r in self.rows))

    def format(self) -> str:
        counts = self.counts()
        lines = [f"{len(self.rows)} task(s)"]
        for cause, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {n:>3}  {cause:<15}{FIXABLE.get(cause, '')}")
        lines.append("")
        lines += [f"  {r.cause:<15}{r.case_id}/{r.task_id} expected={r.expected!r} got={r.got!r}  {r.detail}"
                  for r in self.rows if r.cause != "ok"]
        return "\n".join(lines)


# --- saving ---------------------------------------------------------------------------------------------------------------


def gold_presence(quotes: list[str], texts: dict[str, str], package: EvidencePackage) -> list[dict]:
    """For each labeled quote: is it in the parsed document at all, and in the evidence the model was shown?"""
    in_package = " ".join(p.text for p in package.passages)
    everything = " ".join(texts.values())
    return [{"quote": q, "in_document": _contains(everything, q), "in_package": _contains(in_package, q)} for q in quotes]


def _contains(text: str, quote: str) -> bool:
    haystack, needle = _fold(text)[0], _fold(quote)[0].strip()
    return bool(needle) and needle in haystack


def save_record(save_dir: Path, name: str, task, expected, package: EvidencePackage, proposal: ValueProposal, texts: dict[str, str], case_id: str) -> None:
    Path(save_dir).mkdir(parents=True, exist_ok=True)
    quotes = [e.quote for e in expected.evidence if e.quote]
    record = {
        "case": case_id, "task_id": task.id, "kind": task.kind.value, "type": task.output.type.value, "description": task.description,
        "expected_status": expected.status.value, "expected_value": expected.value,
        "gold": gold_presence(quotes, texts, package),
        "package": package.model_dump(mode="json"), "proposal": proposal.model_dump(mode="json")}
    (Path(save_dir) / name).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


# --- reading them back ----------------------------------------------------------------------------------------------------------


def attribute(save_dir: Path) -> AttributionReport:
    rows = []
    for f in sorted(Path(save_dir).glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if "expected_status" not in d:
            continue  # a replay file written without labels
        rows.append(_attribute_one(d))
    if not rows:
        raise ValueError(f"no saved task records in '{save_dir}' (create them with `eval --extraction --save`)")
    return AttributionReport(rows=rows)


def _reported_quotes(d: dict) -> list[str]:
    proposal = d["proposal"]
    quotes = [c.get("quote") or "" for c in proposal.get("candidates", [])]
    quotes += [s.get("quote") or "" for s in (proposal.get("interpretation") or {}).get("stances", [])]
    return [q for q in quotes if q]


def _attribute_one(d: dict) -> Attribution:
    proposal = ValueProposal.model_validate(d["proposal"])
    expected_status, expected = TaskStatus(d["expected_status"]), d["expected_value"]
    out = proposal.outcome
    base = dict(case_id=d["case"], task_id=d["task_id"], kind=d["kind"], expected=expected if expected_status == TaskStatus.SUPPORTED else expected_status.value,
                got=proposal.value if out == ProposalOutcome.FOUND else out.value)

    def row(cause: str, detail: str = "") -> Attribution:
        return Attribution(cause=cause, detail=detail or proposal.reason[:140], **base)

    if out == ProposalOutcome.FAILED:
        return row("failure")
    if expected_status != TaskStatus.SUPPORTED:
        if out == ProposalOutcome.FOUND:
            return row("false_decision")
        if expected_status == TaskStatus.NOT_FOUND and out == ProposalOutcome.UNCERTAIN:
            return row("abstention", "the labeler says nothing is stated; the engine withheld instead of saying not_found (safe, not automated)")
        return row("ok" if (expected_status == TaskStatus.NOT_FOUND and out == ProposalOutcome.ABSENT) or (expected_status == TaskStatus.UNCERTAIN and out == ProposalOutcome.UNCERTAIN) else "judgement")
    if out == ProposalOutcome.FOUND and same_value(expected, proposal.value):
        return row("ok")
    gold = d.get("gold") or []
    if gold and not any(g["in_document"] for g in gold):
        return row("parse", "the labeled quote is not in the parsed text")
    if gold and not any(g["in_package"] for g in gold):
        return row("retrieval", "the labeled quote is in the document but was not in the evidence the model read")
    reported = _reported_quotes(d)
    if gold and not any(_contains(q, g["quote"]) or _overlap(g["quote"], q) for g in gold for q in reported):
        return row("evidence", "the labeled quote was in the evidence but the model did not report it")
    return row("judgement", "the decisive quote was reported, the final answer is wrong or withheld")


def _overlap(gold: str, quote: str) -> bool:
    """The model's quote and the labeled quote share a long stretch (they point at the same sentence)."""
    a, b = set(re.findall(r"\w+", gold.casefold())), set(re.findall(r"\w+", quote.casefold()))
    return len(a) >= 3 and len(a & b) / len(a) >= 0.6
