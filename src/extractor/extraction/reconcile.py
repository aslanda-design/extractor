"""S07 step 7 - Reconcile competing candidates: group the ones that agree, keep the ones that conflict apart.

Pure code, no model. Two candidates agree when their normalized values are equal
(or, when a value could not be normalized, their literals are equal ignoring case
and spacing). The best candidate of a group is the one whose quote was found in the
evidence; the group keeps the evidence of all its members.
Choosing BETWEEN groups needs the task's precedence rules (choosing.py).
"""

import json
import re
from dataclasses import dataclass

from ..contracts.candidate import Candidate

PROBLEM_FLAGS = ("quote_not_located", "no_quote")


@dataclass
class Group:
    value_key: str
    members: list[Candidate]

    @property
    def best(self) -> Candidate:
        ranked = sorted(self.members, key=lambda c: (bool(set(c.flags) & set(PROBLEM_FLAGS)), len(c.flags)))
        return ranked[0]

    def merged(self) -> Candidate:
        """The best candidate carrying the evidence and qualifications of every member that agrees with it."""
        best = self.best
        evidence = list(dict.fromkeys(ref for c in self.members for ref in c.evidence))
        qualifications = list(dict.fromkeys(q for c in self.members for q in c.qualifications))
        return best.model_copy(update={"evidence": evidence, "qualifications": qualifications})

    @property
    def located(self) -> bool:
        return any(not (set(c.flags) & set(PROBLEM_FLAGS)) for c in self.members)


def value_key(candidate: Candidate) -> str:
    value = candidate.normalized_value
    if value is not None:
        return "n:" + json.dumps(value, sort_keys=True, ensure_ascii=False)
    text = re.sub(r"^(the|a|an)\s+", "", (candidate.source_value or "").casefold().strip())
    return "s:" + re.sub(r"[\W_]+", "", text)  # case, spacing, punctuation and hyphenation (OCR) do not make a different value


def group_candidates(candidates: list[Candidate]) -> list[Group]:
    """Groups in order of first appearance."""
    groups: dict[str, Group] = {}
    for candidate in candidates:
        key = value_key(candidate)
        groups.setdefault(key, Group(key, [])).members.append(candidate)
    return list(groups.values())


def describe(candidate: Candidate) -> str:
    """One candidate as a short block of text, for the log and for the 'choose' prompt."""
    lines = [f"value: {candidate.source_value if candidate.source_value is not None else candidate.normalized_value}"]
    for label, value in (("quote", candidate.quote), ("about", candidate.entity), ("date", candidate.date)):
        if value:
            lines.append(f"{label}: {value}")
    if candidate.qualifications:
        lines.append("conditions: " + "; ".join(candidate.qualifications))
    return "\n".join(lines)
