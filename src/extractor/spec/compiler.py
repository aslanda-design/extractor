"""S02 - Compile a specification into normalized tasks and a dependency graph.

    YAML --loader--> SpecificationFile --checks--> issues
                                       --graph---> CompiledSpecification

ERROR issues reject the whole specification (all of them are reported at once).
REVIEW issues are kept on the result so later stages can flag the task.
"""

from pathlib import Path

from ..artifacts.fingerprint import fingerprint
from ..contracts.specification import (
    CompiledSpecification,
    DependencyGraph,
    IssueSeverity,
    SpecificationFile,
    SpecIssue,
)
from .checks import check_dependencies, check_ids, check_task, effective_dependencies
from .dependency_graph import CycleError, build_layers
from .loader import SpecificationError, load_specification

SUPPORTED_VERSION = 1


def compile_specification(spec: SpecificationFile) -> CompiledSpecification:
    issues: list[SpecIssue] = []
    if spec.version != SUPPORTED_VERSION:
        issues.append(SpecIssue(
            severity=IssueSeverity.ERROR, code="unsupported_version", path="version",
            message=f"version {spec.version} is not supported (expected {SUPPORTED_VERSION})",
        ))
    if not spec.tasks:
        issues.append(SpecIssue(
            severity=IssueSeverity.ERROR, code="no_tasks", path="tasks",
            message="the specification defines no tasks",
        ))

    issues += check_ids(spec)
    for i, task in enumerate(spec.tasks):
        issues += check_task(task, i)
    issues += check_dependencies(spec)

    # Graph: only built from tasks whose references resolve (otherwise errors are already listed).
    dependencies = {t.id: effective_dependencies(t) for t in spec.tasks}
    graph: DependencyGraph | None = None
    if not any(i.severity == IssueSeverity.ERROR for i in issues):
        try:
            layers = build_layers(dependencies)
            graph = DependencyGraph(
                dependencies=dependencies,
                order=[t for layer in layers for t in layer],
                layers=layers,
            )
        except CycleError as exc:
            issues.append(SpecIssue(
                severity=IssueSeverity.ERROR, code="dependency_cycle", path="tasks",
                message=str(exc),
            ))

    errors = [i for i in issues if i.severity == IssueSeverity.ERROR]
    if errors or graph is None:
        raise SpecificationError(errors)

    return CompiledSpecification(
        schema_hash=fingerprint(spec),
        name=spec.name,
        tasks=list(spec.tasks),
        graph=graph,
        review_issues=[i for i in issues if i.severity == IssueSeverity.REVIEW],
    )


def compile_specification_file(path: Path) -> CompiledSpecification:
    """Load + validate + compile in one call."""
    return compile_specification(load_specification(path))
