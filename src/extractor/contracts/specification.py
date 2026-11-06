"""The YAML file as a whole, and the compiler's output (stage S02)."""

from enum import StrEnum

from .common import StrictModel
from .task import TaskSpecification


class SpecificationFile(StrictModel):
    """Exact shape of the extraction YAML."""

    version: int = 1
    name: str | None = None
    tasks: list[TaskSpecification]


class IssueSeverity(StrEnum):
    ERROR = "error"  # specification is rejected
    REVIEW = "review"  # accepted, but the affected task needs human review


class SpecIssue(StrictModel):
    severity: IssueSeverity
    code: str  # short machine-readable id, e.g. "duplicate_id"
    path: str  # where in the YAML, e.g. "tasks[2].output"
    message: str
    task_id: str | None = None


class DependencyGraph(StrictModel):
    dependencies: dict[str, list[str]]  # task id -> ids it waits for
    order: list[str]  # one valid execution order
    layers: list[list[str]]  # tasks in a layer are independent of each other


class CompiledSpecification(StrictModel):
    schema_hash: str
    name: str | None
    tasks: list[TaskSpecification]  # declaration order
    graph: DependencyGraph
    review_issues: list[SpecIssue] = []  # REVIEW-severity findings

    def task(self, task_id: str) -> TaskSpecification:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise KeyError(task_id)
