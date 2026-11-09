"""S07 step 4 - Turn the model's JSON into candidates the engine can check.

The model's quote is searched in the evidence (quotes.py), so every candidate
ends up either tied to real source blocks or flagged. Nothing the model says
about WHERE a value is found is taken on trust.
"""

from dataclasses import dataclass, field

from ..contracts.candidate import Candidate
from ..contracts.evidence import EvidencePassage
from ..contracts.passage import Passage
from ..contracts.task import OutputType
from .quotes import contains_value, locate_quote, with_legal_suffix, with_scale_word


@dataclass
class DerivationRequest:
    operation: str
    operands: list[str]  # candidate ids
    explanation: str = ""


@dataclass
class Reading:
    """What one model reply contained."""

    part: int  # which call (1-based)
    candidates: list[Candidate] = field(default_factory=list)
    derivations: list[DerivationRequest] = field(default_factory=list)
    absent_reason: str = ""
    notes: list[str] = field(default_factory=list)


def read_reply(
    data: dict, part: int, tagged: dict[str, EvidencePassage], passages: dict[str, Passage],
    output_type: OutputType | None = None,
) -> Reading:
    """`tagged`: the passages this call showed (tag -> passage); `passages`: full passages by id, for locating quotes."""
    reading = Reading(part=part, absent_reason=str(data.get("absent_reason") or "").strip())
    shown = [passages[p.passage_id] for p in tagged.values() if p.passage_id in passages]
    ids: dict[str, str] = {}  # the model's id -> the engine's id
    for n, item in enumerate(data.get("candidates") or [], start=1):
        if not isinstance(item, dict):
            reading.notes.append(f"ignored candidate {n}: not an object")
            continue
        literal = str(item.get("value") or "").strip()
        if not literal:
            reading.notes.append(f"ignored candidate {n}: no value")
            continue
        model_id = str(item.get("id") or f"k{n}")
        engine_id = model_id if part == 1 else f"{model_id}.{part}"
        while engine_id in ids.values():
            engine_id += "_2"
        ids[model_id] = engine_id

        cited = tagged.get(str(item.get("passage") or ""))
        cited_passage = passages.get(cited.passage_id) if cited else None
        match = locate_quote(item.get("quote"), cited_passage, [p for p in shown if p is not cited_passage], literal, output_type)
        flags = list(match.flags)
        literal = with_legal_suffix(with_scale_word(literal, item.get("quote"), output_type), item.get("quote"), output_type)
        if match.located and "quote_relocated" not in flags and not contains_value(item.get("quote"), literal, output_type):
            flags.append("value_not_in_quote")
        reading.candidates.append(Candidate(
            candidate_id=engine_id, source_value=literal, quote=str(item.get("quote") or "") or None,
            passage_id=match.passage_id, evidence=match.refs, entity=str(item.get("entity") or "").strip() or None,
            date=str(item.get("date") or "").strip() or None,
            qualifications=[str(c).strip() for c in item.get("conditions") or [] if str(c).strip()], flags=flags))

    for item in data.get("derived") or []:
        if isinstance(item, dict) and item.get("operation"):
            operands = [ids.get(str(o), str(o)) for o in item.get("operands") or []]
            reading.derivations.append(DerivationRequest(str(item["operation"]), operands, str(item.get("explanation") or "")))
    return reading
