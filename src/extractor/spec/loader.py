"""S02 (step 1) - Read the YAML and check its shape.

Only structure is checked here (known keys, right types). Meaning is checked
by `checks.py`. Every problem is reported as a SpecIssue with its YAML path.
"""

from pathlib import Path

import yaml
from pydantic import ValidationError

from ..contracts.specification import IssueSeverity, SpecificationFile, SpecIssue


class SpecificationError(ValueError):
    """The specification is invalid. `issues` lists every problem found."""

    def __init__(self, issues: list[SpecIssue]):
        self.issues = issues
        lines = [f"  [{i.code}] {i.path}: {i.message}" for i in issues]
        super().__init__("invalid specification:\n" + "\n".join(lines))


def _path(loc: tuple) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _error(code: str, path: str, message: str, task_id: str | None = None) -> SpecIssue:
    return SpecIssue(
        severity=IssueSeverity.ERROR, code=code, path=path, message=message, task_id=task_id
    )


def parse_specification(data: object) -> SpecificationFile:
    """Validate already-parsed YAML data (a dict)."""
    if not isinstance(data, dict):
        raise SpecificationError([_error("schema", "<root>", "the YAML must be a mapping")])
    try:
        return SpecificationFile.model_validate(data)
    except ValidationError as exc:
        raw_tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
        issues = []
        for err in exc.errors():
            loc = err["loc"]
            task_id = None
            if len(loc) >= 2 and loc[0] == "tasks" and isinstance(loc[1], int):
                raw = raw_tasks[loc[1]] if loc[1] < len(raw_tasks) else None
                task_id = raw.get("id") if isinstance(raw, dict) else None
            issues.append(_error("schema", _path(loc), err["msg"], task_id))
        raise SpecificationError(issues) from None


def load_specification(path: Path) -> SpecificationFile:
    """Read a YAML file from disk and validate its shape."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecificationError([_error("yaml_syntax", str(path), str(exc))]) from None
    return parse_specification(data)
