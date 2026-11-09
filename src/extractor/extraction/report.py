"""S07 - A readable text view of a ValueProposal (used by `python -m extractor extract`)."""

from ..contracts.candidate import ValueProposal


def format_proposal(proposal: ValueProposal) -> str:
    lines = [f"task {proposal.task_id}: {proposal.outcome.value.upper()}  value={proposal.value!r}", f"  why: {proposal.reason}"]
    chosen = {c.candidate_id for c in proposal.chosen}
    lines.append(f"  candidates ({len(proposal.candidates)}):")
    for c in proposal.candidates:
        mark = "*" if c.candidate_id in chosen else " "
        shown = c.source_value if c.source_value is not None else f"(derived: {c.derivation})"
        lines.append(f"   {mark} {c.candidate_id:<6} {shown!r} -> {c.normalized_value!r}"
                     + (f"  about={c.entity!r}" if c.entity else "") + (f"  date={c.date!r}" if c.date else "")
                     + (f"  flags={c.flags}" if c.flags else ""))
        if c.quote:
            lines.append(f"            quote: {c.quote!r}  [{c.passage_id or 'not located'}]")
        for q in c.qualifications:
            lines.append(f"            condition: {q}")
    if proposal.steps:
        lines.append("  steps:")
        lines += [f"    - {s}" for s in proposal.steps]
    for gap in proposal.gaps:
        lines.append(f"  gap: {gap}")
    for call in proposal.calls:
        r = call.reply
        lines.append(f"  call '{call.purpose}': {r.seconds}s, {r.prompt_tokens or '?'} prompt / {r.completion_tokens or '?'} answer tokens"
                     + (f"  ERROR: {r.error}" if r.error else ""))
    return "\n".join(lines)
