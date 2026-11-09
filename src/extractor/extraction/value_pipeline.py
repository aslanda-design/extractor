"""S07 - THE VALUE EXTRACTION PIPELINE, start to finish, in one place.

Read `extract_value` top to bottom: each numbered step is one call, and each
call lives in its own small module (named in the comment). To change how a
value is extracted, edit a step here or the helper it points to.

    value task + evidence package
        |  1. tag         prompts.py     passages get short tags P1, P2... the model can copy
        |  2. split       partition.py   evidence that does not fit the model's context is cut into calls
        |  3. ask         calling.py     one call per part: "list EVERY value the passages state" (JSON schema)
        |  4. read        reading.py     JSON -> candidates; each quote is searched in the evidence -> source blocks
        |  5. normalize   normalize.py   canonical value (dates, numbers, amounts...) by code, not by the model
        |  6. screen      screening.py   code gates: redacted, value not in its quote, blank field, unreadable typed value
        |  7. derive      derivation.py  only if the task allows it: the engine does the arithmetic
        |  8. verify      verifying.py   a second, easier model question per candidate: "is this THE value the field asks for?"
        |  9. reconcile   reconcile.py   candidates that agree merge; conflicts go to the task's precedence rules
        |                 choosing.py    (the model applies those rules; it may not invent one)
        | 10. conclude    (below)        found | absent | uncertain | failed, with the reason
        v
    ValueProposal (a PROPOSAL: acceptance is the engine's policy, phase 6)

The model only reports what the text says. It never decides alone, and a
technical failure or missing evidence is never turned into "not found".
"""

import json

from ..adapters.base import ExtractorAdapter
from ..config.runtime import ExtractionSettings
from ..contracts.candidate import Candidate, ModelCall, ProposalOutcome, ValueProposal
from ..contracts.coverage import CoverageEntry, CoverageLedger, CoverageState
from ..contracts.evidence import EvidencePackage
from ..contracts.passage import IndexedDocument, Passage
from ..contracts.task import Cardinality, OutputType, TaskSpecification
from ..contracts.common import TaskKind
from ..events import emit
from .calling import call_model
from .choosing import choose_between
from .derivation import derive_candidates
from .normalize import infer_date_order, normalize_candidate
from .partition import split_evidence
from .prompts import extraction_prompt, passage_tags, render_passage
from .reading import Reading, read_reply
from .reconcile import Group, group_candidates
from .screening import screen
from .verifying import verify_candidates

PROBLEM_FLAGS = {"quote_not_located", "no_quote"}


class ExtractionError(ValueError):
    """The task cannot be served by value extraction."""


def extract_value(
    task: TaskSpecification,
    package: EvidencePackage,
    documents: list[IndexedDocument],
    extractor: ExtractorAdapter,
    settings: ExtractionSettings | None = None,
    *,
    ledger: CoverageLedger | None = None,
) -> ValueProposal:
    settings = settings or ExtractionSettings()
    if task.kind != TaskKind.VALUE:
        raise ExtractionError(f"task '{task.id}' is a {task.kind.value} task: value extraction only serves value tasks")
    proposal = ValueProposal(task_id=task.id, outcome=ProposalOutcome.UNCERTAIN, gaps=list(package.gaps))
    log: list[str] = []
    passages = {p.passage_id: p for d in documents for p in d.passages}  # full passages, to locate quotes
    if not package.passages:
        # retrieval returns passages whenever any exist, so an empty package means nothing was parsed or indexed
        return proposal.model_copy(update={"outcome": ProposalOutcome.FAILED, "steps": log,
                                           "reason": "no indexed passages to read (nothing was parsed or indexed for this task)"})

    # 1. Tag the passages: the model cites "P3", the engine maps it back to the real passage.
    tagged = passage_tags(package.passages)
    show_document = len({p.document_id for p in package.passages}) > 1

    # 2. Split the evidence into as many calls as the model's context needs.
    overhead = _prompt_cost(task, tagged, settings, extractor, show_document)
    budget = int(extractor.context_tokens * (1 - settings.prompt_margin)) - extractor.output_tokens - overhead
    parts, oversize = split_evidence(
        list(tagged.values()), max(budget, 1),
        lambda p: extractor.count_tokens(render_passage("P00", p, show_document)))
    proposal.gaps.extend(oversize)
    if settings.passages_per_call:  # focus: fewer passages per call, each call reconciled afterwards
        n = settings.passages_per_call
        parts = [part[i:i + n] for part in parts for i in range(0, len(part), n)]
    log.append(f"evidence: {len(package.passages)} passage(s) in {len(parts)} call(s) "
               f"(~{budget} evidence tokens per call; context {extractor.context_tokens})")
    emit("S07", f"{task.id}: {len(package.passages)} passage(s) go to the model in {len(parts)} call(s)")

    # 3. Ask the model, once per part.
    part_tags = [{tag: p for tag, p in tagged.items() if p in part} for part in parts]
    calls: list[ModelCall] = []
    for number, tags in enumerate(part_tags, start=1):
        prompt = extraction_prompt(task, tags, settings.max_candidates, show_document)
        calls.append(call_model(extractor, prompt, f"extract {number}/{len(parts)}", settings.retries, settings.think_extraction))

    # 4. Read the answers: candidates with their quotes located in the evidence.
    readings: list[Reading] = []
    for number, (part, call) in enumerate(zip(parts, calls), start=1):
        if call.reply.data is None:
            proposal.gaps.append(f"call {number}/{len(parts)} failed, its {len(part)} passage(s) were not read: {call.reply.error}")
            continue
        readings.append(read_reply(call.reply.data, number, part_tags[number - 1], passages, task.output.type))
        _mark_inspected(ledger, task.id, part)
    candidates = [c for r in readings for c in r.candidates]
    log += [note for r in readings for note in r.notes]
    log.append(f"model reported {len(candidates)} candidate(s)" +
               (f"; {len(calls) - len(readings)} call(s) failed" if len(readings) < len(calls) else ""))
    emit("S07", f"{task.id}: the model reported {len(candidates)} candidate(s)",
         "warn" if len(readings) < len(calls) else "info")

    # 5. Normalize each value by code (the literal text is always kept).
    date_order = infer_date_order(" ".join(p.text for p in package.passages)) if task.output.type == OutputType.DATE else None
    if date_order:
        log.append(f"numeric dates in the evidence prove the document writes them {'month/day/year' if date_order == 'mdy' else 'day/month/year'}")
    candidates = [normalize_candidate(c, task, date_order) for c in candidates]

    # 6. Screen: code gates a candidate must pass to compete (rejected ones stay visible, flagged).
    rejections: list[tuple[str, str]] = []  # (kind, why)
    if settings.screen_candidates:
        screened = screen(candidates, task)
        rejections += [(kind, why) for _, kind, why in screened.rejected]
        candidates = _in_original_order(candidates, screened.kept, [c for c, _, _ in screened.rejected])
        for _, _, why in screened.rejected:
            log.append(f"rejected: {why}")
        if screened.rejected:
            emit("S07", f"{task.id}: {len(screened.rejected)} candidate(s) rejected by the screening gates", "warn")

    # 7. Derive: only if the task allows it; the engine computes, the model only names the operation.
    for reading in readings:
        derived, derivation_log = derive_candidates(reading.derivations, _live(candidates), task.output.type, bool(task.rules.derivation))
        candidates += derived
        log += derivation_log

    # 8. Verify: a separate model question per candidate; it can only remove candidates.
    if settings.verify_candidates and _live(candidates):
        headings = {p.passage_id: p.heading_path for p in package.passages}
        verdicts = verify_candidates(task, [c for c in _live(candidates) if not c.derived], headings, extractor, settings.retries, settings.think_verification)
        if verdicts.call:
            calls.append(verdicts.call)
            if verdicts.call.reply.data is None:
                log.append(f"verification failed, nothing was removed: {verdicts.call.reply.error}")
        for c in list(candidates):
            if c.candidate_id in verdicts.rejected:
                why = verdicts.rejected[c.candidate_id]
                candidates[candidates.index(c)] = c.model_copy(update={"flags": [*c.flags, "rejected:judge"]})
                rejections.append(("judge", why))
                log.append(f"verification rejected {c.candidate_id} ({c.source_value!r}): {why}")
        if verdicts.rejected:
            emit("S07", f"{task.id}: verification rejected {len(verdicts.rejected)} candidate(s)", "warn")
    proposal = proposal.model_copy(update={"candidates": candidates, "calls": calls})

    # 9. Reconcile: merge what agrees, apply the task's precedence rules to what conflicts.
    if not _live(candidates):
        reasons = [r.absent_reason for r in readings if r.absent_reason]
        return _conclude_empty(proposal, readings, reasons, log, settings, rejections)
    usable = _drop_unlocated(_live(candidates), settings, log)
    groups = group_candidates(usable)
    log.append(f"{len(groups)} distinct value(s) after merging candidates that agree")
    if task.output.cardinality == Cardinality.MANY:
        chosen = [g.merged() for g in groups]
        return _found(proposal, chosen, [_value_of(c) for c in chosen], "several values requested", log)
    if len(groups) == 1:
        return _found(proposal, [groups[0].merged()], _value_of(groups[0].merged()), "all candidates agree", log)
    if not task.rules.precedence or not settings.choose_with_model:
        why = "no precedence rule in the task to decide between them" if not task.rules.precedence else "model-based choice is disabled"
        return _uncertain(proposal, f"{len(groups)} conflicting values and {why}", log)
    choice = choose_between(task, groups, extractor, settings.retries, settings.think_choice)
    proposal = proposal.model_copy(update={"calls": [*calls, choice.call]})
    if choice.group is None:
        return _uncertain(proposal, choice.reason, log)
    chosen_one = choice.group.merged()
    return _found(proposal, [chosen_one], _value_of(chosen_one), f"precedence rules chose {chosen_one.candidate_id}: {choice.reason}", log)


# --------------------------------------------------------------------------------------
# 8. Conclude
# --------------------------------------------------------------------------------------


def _found(proposal: ValueProposal, chosen: list[Candidate], value, why: str, log: list[str]) -> ValueProposal:
    log.append(f"chosen: {', '.join(c.candidate_id for c in chosen)} ({why})")
    flagged = sorted({f for c in chosen for f in c.flags if f in PROBLEM_FLAGS | {"not_normalized", "value_not_in_quote"}})
    reason = why + (f"; check: {', '.join(flagged)}" if flagged else "")
    return proposal.model_copy(update={"outcome": ProposalOutcome.FOUND, "value": value, "chosen": chosen,
                                       "reason": reason, "steps": log})


def _uncertain(proposal: ValueProposal, reason: str, log: list[str]) -> ValueProposal:
    return proposal.model_copy(update={"outcome": ProposalOutcome.UNCERTAIN, "reason": reason, "steps": log})


_ATTACHMENTS = ("schedule", "exhibit", "appendix", "appendices", "annex")


def _blocks_absence(gap: str, settings: ExtractionSettings) -> bool:
    """Does this retrieval gap stop us from calling an empty answer 'absent'?

    Content that was left out (budget, failed calls) always does. An unresolved reference to a section of the
    document itself may mean a parsing failure, so it does by default. A reference to an attachment (Schedule A,
    Exhibit 10.16) that is not among the supplied files does not: absence is judged against the supplied documents.
    """
    if not gap.startswith("unresolved reference"):
        return True
    attachment = any(f"'{word}" in gap.lower() for word in _ATTACHMENTS)
    return settings.unresolved_attachments_block_absence if attachment else settings.unresolved_references_block_absence


def _live(candidates: list[Candidate]) -> list[Candidate]:
    """Candidates that were not rejected by a gate or by verification."""
    return [c for c in candidates if not any(f.startswith("rejected:") for f in c.flags)]


def _in_original_order(original: list[Candidate], kept: list[Candidate], rejected: list[Candidate]) -> list[Candidate]:
    by_id = {c.candidate_id: c for c in [*kept, *rejected]}
    return [by_id[c.candidate_id] for c in original]


def _conclude_empty(
    proposal: ValueProposal, readings: list[Reading], reasons: list[str], log: list[str], settings: ExtractionSettings,
    rejections: list[tuple[str, str]] = (),
) -> ValueProposal:
    """No usable candidate. That only means 'absent' if every call worked and retrieval left no gap.

    A hidden (redacted) value or a value that cannot be read as the requested type is NOT absence.
    """
    for kind, why in rejections:
        if kind in ("redacted", "unreadable"):
            return _uncertain(proposal, f"a value exists but cannot be used: {why}", log)
    reasons = [*reasons, *(why for _, why in rejections)]
    if not readings:
        errors = {c.reply.error for c in proposal.calls}
        return proposal.model_copy(update={"outcome": ProposalOutcome.FAILED, "steps": log,
                                           "reason": "the model returned no usable answer: " + "; ".join(sorted(map(str, errors)))})
    blocking = [g for g in proposal.gaps if _blocks_absence(g, settings)]
    if blocking:
        return _uncertain(proposal, "no value found, but the evidence is incomplete: " + blocking[0], log)
    return proposal.model_copy(update={"outcome": ProposalOutcome.ABSENT, "steps": log,
                                       "reason": "no passage states the value" + (f" ({'; '.join(reasons)})" if reasons else "")})


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _value_of(candidate: Candidate):
    return candidate.normalized_value if candidate.normalized_value is not None else candidate.source_value


def _drop_unlocated(candidates: list[Candidate], settings: ExtractionSettings, log: list[str]) -> list[Candidate]:
    """A candidate whose quote is not in the evidence is unsupported. Keep it only when nothing better exists."""
    ok = [c for c in candidates if not (set(c.flags) & PROBLEM_FLAGS) or c.derived]
    if len(ok) == len(candidates):
        return candidates
    if ok or not settings.allow_unlocated_quotes:
        log.append(f"ignored {len(candidates) - len(ok)} candidate(s) whose quote is not in the evidence")
        return ok
    log.append("no candidate has a quote found in the evidence: keeping them, flagged")
    return candidates


def _prompt_cost(task, tagged, settings, extractor, show_document) -> int:
    """Tokens of everything in a call except the passages (instructions, task, schema)."""
    prompt = extraction_prompt(task, {}, settings.max_candidates, show_document)
    return extractor.count_tokens(prompt.system + prompt.user + json.dumps(prompt.json_schema))


def _mark_inspected(ledger: CoverageLedger | None, task_id: str, part) -> None:
    if ledger is None:
        return
    for passage in part:
        for block_id in passage.block_ids:
            ledger.record(CoverageEntry(document_id=passage.document_id, block_id=block_id, page=None,
                                        state=CoverageState.INSPECTED, task_id=task_id))
