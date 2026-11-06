"""Evaluation corpus: loading and validating the human-labeled cases.

You add data under `evaluation/corpus/` (see its README.md); this module only
reads and checks it. A case is one YAML file in `cases/` that points at
documents in `documents/` and a spec in `specs/`, and holds the expected
answers written by a human.
"""

from pathlib import Path

import yaml
from pydantic import ValidationError, model_validator

from ..contracts.common import Completeness, StrictModel, TaskKind, TaskStatus
from ..spec import SpecificationError, compile_specification_file


class ExpectedEvidence(StrictModel):
    """Where a human found the answer. Page + quote, because block ids don't exist yet."""

    document: str  # filename inside documents/
    page: int | None = None
    quote: str | None = None
    role: str = "supports"


class ExpectedRecord(StrictModel):
    fields: dict[str, object]
    evidence: list[ExpectedEvidence] = []


class ExpectedResult(StrictModel):
    status: TaskStatus
    value: object = None  # scalar tasks; only with status 'supported'
    records: list[ExpectedRecord] = []  # records tasks
    completeness: Completeness | None = None
    evidence: list[ExpectedEvidence] = []
    notes: str = ""

    @model_validator(mode="after")
    def _consistent(self):
        if self.status != TaskStatus.SUPPORTED and self.value is not None:
            raise ValueError("`value` is only allowed with status 'supported'")
        return self


class EvalCase(StrictModel):
    case_id: str
    description: str = ""
    documents: list[str]  # filenames inside documents/
    spec: str  # path relative to the corpus dir, e.g. specs/loan.yaml
    tags: list[str] = []  # e.g. scan, long, amendment, contradiction
    expected: dict[str, ExpectedResult]  # task id -> expected result


class Corpus(StrictModel):
    root: Path
    cases: list[EvalCase]


class CorpusError(ValueError):
    pass


def _check_case(case: EvalCase, root: Path) -> list[str]:
    problems: list[str] = []
    for name in case.documents:
        if not (root / "documents" / name).is_file():
            problems.append(f"{case.case_id}: document 'documents/{name}' not found")
    spec_path = root / case.spec
    if not spec_path.is_file():
        return problems + [f"{case.case_id}: spec '{case.spec}' not found"]
    try:
        spec = compile_specification_file(spec_path)
    except SpecificationError as exc:
        return problems + [f"{case.case_id}: spec does not compile: {exc}"]

    task_ids = {t.id: t for t in spec.tasks}
    for task_id, expected in case.expected.items():
        task = task_ids.get(task_id)
        if task is None:
            problems.append(f"{case.case_id}: expected result for unknown task '{task_id}'")
            continue
        if task.kind == TaskKind.RECORDS:
            if expected.completeness is None:
                problems.append(f"{case.case_id}/{task_id}: records tasks need `completeness`")
            if expected.value is not None:
                problems.append(f"{case.case_id}/{task_id}: records tasks use `records`, not `value`")
        elif expected.records or expected.completeness is not None:
            problems.append(f"{case.case_id}/{task_id}: only records tasks have records/completeness")
        for ev in expected.evidence:
            if ev.document not in case.documents:
                problems.append(f"{case.case_id}/{task_id}: evidence document '{ev.document}' not in case")
    for task_id in task_ids:
        if task_id not in case.expected:
            problems.append(f"{case.case_id}: no expected result for task '{task_id}'")
    return problems


def load_corpus(root: Path) -> Corpus:
    """Load every `cases/*.yaml`; raises CorpusError listing ALL problems found."""
    root = Path(root)
    problems: list[str] = []
    cases: list[EvalCase] = []
    for path in sorted((root / "cases").glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            case = EvalCase.model_validate(data)
        except (yaml.YAMLError, ValidationError) as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        problems += _check_case(case, root)
        cases.append(case)
    ids = [c.case_id for c in cases]
    problems += [f"duplicate case_id '{i}'" for i in sorted({i for i in ids if ids.count(i) > 1})]
    if problems:
        raise CorpusError("\n".join(problems))
    return Corpus(root=root, cases=cases)
