"""S02 (step 2) - Meaning checks on a structurally valid specification.

Each function returns SpecIssues and never raises. Severity rules:
  ERROR   the declaration is contradictory or impossible -> spec rejected.
  REVIEW  the declaration is legal but too vague to trust -> task flagged for
          review. The compiler never invents the missing rule.
"""

import re

from ..contracts.common import TaskKind
from ..contracts.specification import IssueSeverity, SpecificationFile, SpecIssue
from ..contracts.task import FieldSpec, OutputSpec, OutputType, Rules, TaskSpecification
from .dependency_graph import find_cycle

ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _err(code: str, path: str, message: str, task_id: str | None) -> SpecIssue:
    return SpecIssue(
        severity=IssueSeverity.ERROR, code=code, path=path, message=message, task_id=task_id
    )


def _review(code: str, path: str, message: str, task_id: str | None) -> SpecIssue:
    return SpecIssue(
        severity=IssueSeverity.REVIEW, code=code, path=path, message=message, task_id=task_id
    )


def effective_dependencies(task: TaskSpecification) -> list[str]:
    """Explicit `depends_on` plus the parent record task, if any."""
    deps = list(task.depends_on)
    if task.record and task.record.parent and task.record.parent not in deps:
        deps.append(task.record.parent)
    return deps


# --- identifiers -------------------------------------------------------------


def check_ids(spec: SpecificationFile) -> list[SpecIssue]:
    issues: list[SpecIssue] = []
    seen: set[str] = set()
    for i, task in enumerate(spec.tasks):
        path = f"tasks[{i}].id"
        if not ID_PATTERN.match(task.id):
            issues.append(_err("invalid_id", path, f"'{task.id}' must match {ID_PATTERN.pattern}", task.id))
        if task.id in seen:
            issues.append(_err("duplicate_id", path, f"task id '{task.id}' is used more than once", task.id))
        seen.add(task.id)
    return issues


# --- per-task declarations ---------------------------------------------------


def _check_output(output: OutputSpec, path: str, task_id: str) -> list[SpecIssue]:
    issues: list[SpecIssue] = []
    names = [label.name for label in output.labels]
    if output.labels and output.type != OutputType.LABEL:
        issues.append(_err("labels_need_label_type", f"{path}.labels",
                           "labels are only valid when output type is 'label'", task_id))
    if output.type == OutputType.LABEL:
        if len(names) < 2:
            issues.append(_err("label_needs_choices", f"{path}.labels",
                               "a 'label' output needs at least two labels", task_id))
        if len(set(names)) != len(names):
            issues.append(_err("duplicate_label", f"{path}.labels",
                               "label names must be unique", task_id))
    return issues


def _check_rules(rules: Rules, kind: TaskKind, path: str, task_id: str) -> list[SpecIssue]:
    issues: list[SpecIssue] = []
    if kind == TaskKind.INTERPRETATION:
        if not rules.decision_criteria:
            issues.append(_review("no_decision_criteria", f"{path}.decision_criteria",
                                  "interpretation task has no decision criteria; the engine will "
                                  "not invent any", task_id))
    elif rules.decision_criteria:
        issues.append(_err("criteria_on_non_interpretation", f"{path}.decision_criteria",
                           f"decision criteria belong to 'interpretation' tasks, not '{kind.value}'",
                           task_id))
    return issues


def _check_description(text: str, path: str, task_id: str) -> list[SpecIssue]:
    if not text.strip():
        return [_err("empty_description", path, "description must not be empty", task_id)]
    return []


def check_task(task: TaskSpecification, index: int) -> list[SpecIssue]:
    path = f"tasks[{index}]"
    issues = _check_description(task.description, f"{path}.description", task.id)
    issues += _check_output(task.output, f"{path}.output", task.id)
    issues += _check_rules(task.rules, task.kind, f"{path}.rules", task.id)

    if task.id in task.depends_on:
        issues.append(_err("self_dependency", f"{path}.depends_on", "a task cannot depend on itself", task.id))

    if task.kind == TaskKind.RECORDS:
        issues += _check_record(task, path)
    elif task.record is not None:
        issues.append(_err("record_on_non_records", f"{path}.record",
                           f"'record' is only valid for kind 'records', not '{task.kind.value}'", task.id))
    return issues


# --- records -----------------------------------------------------------------


def _check_record(task: TaskSpecification, path: str) -> list[SpecIssue]:
    if task.record is None:
        return [_err("missing_record", f"{path}.record",
                     "kind 'records' requires a 'record' section", task.id)]
    record = task.record
    rpath = f"{path}.record"
    issues: list[SpecIssue] = []
    if task.output.labels:
        issues.append(_err("labels_on_records", f"{path}.output.labels",
                           "a records task has no labels; put them on its fields", task.id))
    if not record.fields:
        issues.append(_err("no_fields", f"{rpath}.fields", "a records task needs at least one field", task.id))
    if record.boundaries is None:
        issues.append(_review("no_boundaries", f"{rpath}.boundaries",
                              "record boundaries are not described", task.id))
    if record.identity is None:
        issues.append(_review("no_identity", f"{rpath}.identity",
                              "record identity is not described", task.id))

    siblings = [f.id for f in record.fields]
    for j, field in enumerate(record.fields):
        issues += _check_field(field, f"{rpath}.fields[{j}]", siblings, task.id)
    if len(set(siblings)) != len(siblings):
        issues.append(_err("duplicate_id", f"{rpath}.fields", "field ids must be unique within a record", task.id))
    else:
        cycle = find_cycle({f.id: f.depends_on for f in record.fields})
        if cycle:
            issues.append(_err("dependency_cycle", f"{rpath}.fields",
                               "field dependency cycle: " + " -> ".join(cycle), task.id))
    return issues


def _check_field(field: FieldSpec, path: str, siblings: list[str], task_id: str) -> list[SpecIssue]:
    issues: list[SpecIssue] = []
    if not ID_PATTERN.match(field.id):
        issues.append(_err("invalid_id", f"{path}.id", f"'{field.id}' must match {ID_PATTERN.pattern}", task_id))
    if field.kind == TaskKind.RECORDS:
        issues.append(_err("nested_records_unsupported", f"{path}.kind",
                           "fields must be 'value' or 'interpretation'; express nesting with record.parent",
                           task_id))
    issues += _check_description(field.description, f"{path}.description", task_id)
    issues += _check_output(field.output, f"{path}.output", task_id)
    issues += _check_rules(field.rules, field.kind, f"{path}.rules", task_id)
    for dep in field.depends_on:
        if dep == field.id:
            issues.append(_err("self_dependency", f"{path}.depends_on", "a field cannot depend on itself", task_id))
        elif dep not in siblings:
            issues.append(_err("unknown_dependency", f"{path}.depends_on",
                               f"'{dep}' is not a field of this record", task_id))
    return issues


# --- cross-task --------------------------------------------------------------


def check_dependencies(spec: SpecificationFile) -> list[SpecIssue]:
    """Missing targets and parent-kind rules. Cycles are found by the compiler."""
    issues: list[SpecIssue] = []
    kinds = {t.id: t.kind for t in spec.tasks}
    for i, task in enumerate(spec.tasks):
        for dep in task.depends_on:
            if dep != task.id and dep not in kinds:
                issues.append(_err("unknown_dependency", f"tasks[{i}].depends_on",
                                   f"depends on '{dep}', which is not defined", task.id))
        parent = task.record.parent if task.record else None
        if parent is not None:
            if parent == task.id:
                issues.append(_err("self_dependency", f"tasks[{i}].record.parent",
                                   "a records task cannot be its own parent", task.id))
            elif parent not in kinds:
                issues.append(_err("unknown_parent", f"tasks[{i}].record.parent",
                                   f"parent '{parent}' is not defined", task.id))
            elif kinds[parent] != TaskKind.RECORDS:
                issues.append(_err("parent_not_records", f"tasks[{i}].record.parent",
                                   f"parent '{parent}' must be a records task", task.id))
    return issues
