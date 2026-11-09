"""S07 step 6 - Screening: deterministic gates a candidate must pass before it may compete.

The model reports what it thinks is a value; code checks the things code can check.
A rejected candidate stays in the proposal (flag `rejected:<why>`) so a person can see
it, but it cannot be chosen. Gates, in order:

  redacted       "[***]": the value exists but the document hides it      -> the task is `uncertain`
  not_in_quote   the value does not appear inside its own quote           -> invented or mis-copied
  blank          only a field label / placeholder ("Contract No:", "____") -> the field is empty
  unreadable     a typed answer (date, number...) that cannot be read as that type -> `uncertain`
"""

import re
from dataclasses import dataclass, field

from ..contracts.candidate import Candidate
from ..contracts.task import OutputType, TaskSpecification

TYPED = {OutputType.NUMBER, OutputType.INTEGER, OutputType.DATE, OutputType.AMOUNT, OutputType.BOOLEAN, OutputType.LABEL}
_LABEL_ONLY = re.compile(r"^[\W_]*[A-Za-z][\w .#/&'()-]{0,50}[:：]\W*$")  # "Contract No:" "Agreement number:"
_PLACEHOLDER = re.compile(r"^[\s_.\-–—*#\[\]()]*$|^(n/?a|none|tbd|blank)\W*$", re.I)


@dataclass
class Screened:
    kept: list[Candidate] = field(default_factory=list)
    rejected: list[tuple[Candidate, str, str]] = field(default_factory=list)  # (candidate, kind, why)


def screen(candidates: list[Candidate], task: TaskSpecification) -> Screened:
    out = Screened()
    typed = task.output.type in TYPED
    for c in candidates:
        verdict = _verdict(c, typed, task.output.type)
        if verdict is None:
            out.kept.append(c)
        else:
            kind, why = verdict
            out.rejected.append((c.model_copy(update={"flags": [*c.flags, f"rejected:{kind}"]}), kind, why))
    return out


def _verdict(c: Candidate, typed: bool, output_type: OutputType) -> tuple[str, str] | None:
    if c.derived:
        return None
    literal = (c.source_value or "").strip()
    if "redacted" in c.flags:
        return "redacted", f"'{literal}' is redacted in the document"
    if "value_not_in_quote" in c.flags:
        return "not_in_quote", f"'{literal}' does not appear in the quote the model gave for it"
    if _LABEL_ONLY.match(literal) or _PLACEHOLDER.match(literal):
        return "blank", f"'{literal}' is only a field label or placeholder: the field is empty"
    if typed and "not_normalized" in c.flags:
        return "unreadable", f"'{literal}' cannot be read as a {output_type.value}"
    return None
