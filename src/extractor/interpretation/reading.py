"""S08 step 4 - Turn the model's JSON into stances the engine can check.

Every quote is searched in the evidence (the same locator S07 uses), so a stance is either tied to real source
blocks or flagged. Nothing the model says about WHERE a quote is found is taken on trust.
"""

from dataclasses import dataclass, field

from ..contracts.candidate import Interpretation, Stance
from ..contracts.evidence import EvidencePassage
from ..contracts.passage import Passage
from ..contracts.task import TaskSpecification
from ..extraction.quotes import QuoteMatch, locate_quote
from .schema import NO_DECISION


@dataclass
class Reading:
    label: str | None  # None = the model says no passage decides
    interpretation: Interpretation
    tool_calls: list = field(default_factory=list)  # what the model asked the engine to compute
    notes: list[str] = field(default_factory=list)


def read_reply(
    data: dict, task: TaskSpecification, tagged: dict[str, EvidencePassage], passages: dict[str, Passage],
) -> Reading:
    """`tagged`: the passages the call showed (tag -> passage); `passages`: full passages by id, for locating quotes."""
    allowed = {label.name for label in task.output.labels}
    shown = [passages[p.passage_id] for p in tagged.values() if p.passage_id in passages]
    notes: list[str] = []
    stances: list[Stance] = []
    for n, item in enumerate(data.get("evidence") or [], start=1):
        if not isinstance(item, dict) or str(item.get("label")) not in allowed:
            notes.append(f"ignored evidence {n}: not an object with an allowed label")
            continue
        cited = tagged.get(str(item.get("passage") or ""))
        cited_passage = passages.get(cited.passage_id) if cited else None
        others = [p for p in shown if p is not cited_passage]
        match = locate_quote(item.get("quote"), cited_passage, others)
        if "quote_not_located" in match.flags:
            match = _locate_fragment(str(item.get("quote") or ""), cited_passage, others) or match
        stances.append(Stance(
            stance_id=str(item.get("id") or f"e{n}"), label=str(item["label"]), role=str(item.get("role") or "supports"),
            quote=str(item.get("quote") or "") or None, passage_id=match.passage_id, evidence=match.refs, flags=list(match.flags)))
    label = str(data.get("label") or "").strip()
    chosen = label if label in allowed else None
    if label and label != NO_DECISION and label not in allowed:
        notes.append(f"the model answered '{label}', which is not an allowed label")
    interpretation = Interpretation(
        label=chosen, justification=str(data.get("justification") or "").strip(),
        conditions=[str(c).strip() for c in data.get("conditions") or [] if str(c).strip()],
        unresolved=[str(u).strip() for u in data.get("unresolved") or [] if str(u).strip()], stances=stances)
    return Reading(label=chosen, interpretation=interpretation, tool_calls=list(data.get("tool_calls") or []), notes=notes)


def _locate_fragment(quote: str, cited: Passage | None, others: list[Passage]) -> QuoteMatch | None:
    """A long quote often runs across two passages or adds words at one end. Try its longest prefix / suffix found as written.

    The fragment keeps at least half of the quote's words; the match is flagged `quote_partial` (information)."""
    words = quote.split()
    if len(words) < 8:
        return None
    for keep in (0.8, 0.65, 0.5):
        n = max(6, int(len(words) * keep))
        for fragment in (" ".join(words[-n:]), " ".join(words[:n])):
            match = locate_quote(fragment, cited, others)
            if match.located:
                return QuoteMatch(match.passage_id, match.refs, [*match.flags, "quote_partial"])
    return None
