"""S08 - THE INTERPRETATION PIPELINE, start to finish, in one place.

An interpretation task asks for a DECISION under criteria ("may the CRO subcontract?" -> permitted / conditional /
prohibited), not for a value copied from the text. Read `interpret_task` top to bottom: each numbered step is one
call, and each call lives in its own small module (named in the comment).

    interpretation task + evidence package (S06)
        |  1. frame      prompts.py        the question, every label with its meaning, the decision criteria
        |  2. evidence   (below)           the passages that fit one call (a decision needs ALL the evidence together)
        |  3. ask        calling.py        one call: quotes by label (supports / supersedes), the label, a justification
        |                tools.py          arithmetic the model asks for is run by the engine, then one follow-up call
        |  4. read       reading.py        quotes are searched in the evidence: an unlocated quote never counts
        |  5. check      checks.py         is the label backed by a located quote? do other labels have located quotes too?
        |  6. judge      judging.py        one call that can only DOWNGRADE (exception ignored, request read as approval...)
        |  7. conclude   (below)           found | absent | uncertain | failed, with the reason
        v
    ValueProposal (a PROPOSAL: acceptance is S10/S11, unchanged) carrying an `Interpretation` (stances + justification)

The model only reports what the text says and which label the criteria give. It never decides alone: no located quote
means no answer, an unresolved conflict is `uncertain`, a technical failure or missing evidence is never "not found".
"""

import json

from ..adapters.base import ExtractorAdapter
from ..config.runtime import InterpretationSettings
from ..contracts.candidate import Candidate, Interpretation, ModelCall, ProposalOutcome, ValueProposal
from ..contracts.common import TaskKind
from ..contracts.coverage import CoverageEntry, CoverageLedger, CoverageState
from ..contracts.evidence import EvidencePackage, EvidencePassage
from ..contracts.passage import IndexedDocument
from ..contracts.task import TaskSpecification
from ..events import emit
from ..extraction.calling import call_model
from ..extraction.prompts import passage_tags, render_passage
from ..extraction.value_pipeline import blocks_absence
from .checks import Assessment, assess
from .judging import judge_label
from .prompts import decision_section, follow_up_prompt, interpretation_prompt
from .reading import Reading, read_reply
from .schema import NO_DECISION
from .tools import Tool, available_tools, run_tool_calls


class InterpretationError(ValueError):
    """The task cannot be served by interpretation."""


def interpret_task(
    task: TaskSpecification,
    package: EvidencePackage,
    documents: list[IndexedDocument],
    model: ExtractorAdapter,
    settings: InterpretationSettings | None = None,
    *,
    ledger: CoverageLedger | None = None,
) -> ValueProposal:
    settings = settings or InterpretationSettings()
    if task.kind != TaskKind.INTERPRETATION or not task.output.labels:
        raise InterpretationError(f"task '{task.id}' is not an interpretation task with labels")
    log: list[str] = []
    proposal = ValueProposal(task_id=task.id, outcome=ProposalOutcome.UNCERTAIN, gaps=list(package.gaps))
    if not package.passages:
        return proposal.model_copy(update={"outcome": ProposalOutcome.FAILED, "steps": log,
                                           "reason": "no indexed passages to read (nothing was parsed or indexed for this task)"})
    passages = {p.passage_id: p for d in documents for p in d.passages}
    headings = {p.passage_id: p.heading_path for p in package.passages}
    tools = available_tools(settings.tools) if settings.max_tool_calls else []

    # 1 + 2. Frame the question and keep the passages that fit ONE call (a decision weighs all the evidence together).
    shown, omitted = _fit_one_call(task, package.passages, model, settings, tools)
    proposal = proposal.model_copy(update={"gaps": [*proposal.gaps, *omitted]})
    log.append(f"evidence: {len(shown)} of {len(package.passages)} passage(s) in one call (context {model.context_tokens})")
    emit("S08", f"{task.id}: {len(shown)} passage(s) go to the model" + (f", {len(omitted)} left out for the budget" if omitted else ""),
         "warn" if omitted else "info")
    tagged = passage_tags(shown)
    show_document = len({p.document_id for p in shown}) > 1

    # 3. Ask. If the model asked for calculations the engine runs them and the model answers again with the results.
    prompt = interpretation_prompt(task, tagged, settings.max_evidence, show_document, tools)
    calls: list[ModelCall] = [call_model(model, prompt, "interpret", settings.retries, settings.think, stage="S08")]
    first = calls[-1].reply
    tool_results: list[dict] = []
    if first.data is not None and tools and first.data.get("tool_calls"):
        tool_results = run_tool_calls(first.data["tool_calls"], tools, settings.max_tool_calls)
        log += [f"tool {r['tool']}: {r.get('result', r.get('error'))}" for r in tool_results]
        emit("S08", f"{task.id}: the engine ran {len(tool_results)} calculation(s) for the model")
        calls.append(call_model(model, follow_up_prompt(prompt, tool_results), "interpret with tool results", settings.retries,
                                settings.think, stage="S08"))
    final = calls[-1].reply
    if final.data is None:
        return proposal.model_copy(update={"outcome": ProposalOutcome.FAILED, "calls": calls, "steps": log,
                                           "reason": f"the model returned no usable answer: {final.error}"})

    # 4. Read: quotes located in the evidence by code.
    reading = read_reply(final.data, task, tagged, passages)
    reading.interpretation = reading.interpretation.model_copy(update={"tool_results": tool_results})
    log += reading.notes
    _mark_inspected(ledger, task.id, shown)
    interpretation = reading.interpretation
    log.append(f"model chose {reading.label or 'no decision'}; {len(interpretation.stances)} quote(s) listed")

    # 5. Check by code.
    assessment = assess(reading.label, interpretation.stances)
    if assessment.dropped:
        log.append(f"ignored {len(assessment.dropped)} quote(s) not found in the evidence")
    proposal = proposal.model_copy(update={"calls": calls, "interpretation": interpretation})

    if reading.label is None:
        # "no decision" is also checked by the judge when the model quoted something: it may only downgrade this to uncertain
        confirmed = False
        if assessment.located and not interpretation.unresolved and settings.judge:
            verdict = judge_label(task, NO_DECISION, assessment.located, headings, model, settings.retries, settings.think_judge, tool_results)
            proposal = proposal.model_copy(update={"calls": [*proposal.calls, *([verdict.call] if verdict.call else [])]})
            confirmed = verdict.ran and verdict.approved
            log.append("judge confirms that no quote decides the question" if confirmed else
                       f"judge does not confirm 'no decision': {verdict.reason}")
        return _conclude_empty(proposal, reading, assessment, log, settings, confirmed)
    if not assessment.backing:
        return _uncertain(proposal, [reading.label], f"the model chose '{reading.label}' but no quote supporting it was found in the evidence", log)

    # 6. Judge: can only downgrade.
    if settings.judge:
        verdict = judge_label(task, reading.label, assessment.located, headings, model, settings.retries, settings.think_judge, tool_results)
        if verdict.call:
            proposal = proposal.model_copy(update={"calls": [*proposal.calls, verdict.call]})
        if not verdict.ran:
            log.append(verdict.reason)
        elif not verdict.approved:
            emit("S08", f"{task.id}: the judge disagrees with '{reading.label}': {verdict.reason}", "warn")
            return _uncertain(proposal, [reading.label, *([verdict.better_label] if verdict.better_label else [])],
                              f"the judge disagrees with '{reading.label}': {verdict.reason}", log)
        if verdict.ran and verdict.approved and assessment.competing:
            log.append(f"judge kept '{reading.label}' although quotes also support {', '.join(assessment.competing_labels)}")
    elif assessment.competing:
        return _uncertain(proposal, [reading.label, *assessment.competing_labels],
                          f"quotes support '{reading.label}' and also {', '.join(assessment.competing_labels)}, and the judge is off", log)

    # 7. Conclude.
    return _found(proposal, reading, assessment, log)


# --------------------------------------------------------------------------------------
# 2. Evidence that fits one call
# --------------------------------------------------------------------------------------


def _fit_one_call(
    task: TaskSpecification, passages: list[EvidencePassage], model: ExtractorAdapter, settings: InterpretationSettings, tools: list[Tool],
) -> tuple[list[EvidencePassage], list[str]]:
    """The passages that fit one call, in source order; the best-ranked are kept first. The rest become gaps (never silent)."""
    show_document = len({p.document_id for p in passages}) > 1
    empty = interpretation_prompt(task, {}, settings.max_evidence, show_document, tools)
    overhead = model.count_tokens(empty.system + decision_section(task) + json.dumps(empty.json_schema)) + 200 * bool(tools)
    budget = int(model.context_tokens * (1 - settings.prompt_margin)) - model.output_tokens - overhead
    cost = {p.passage_id: model.count_tokens(render_passage("P00", p, show_document)) for p in passages}
    if sum(cost.values()) <= budget:
        return list(passages), []
    kept: set[str] = set()
    used = 0
    for p in sorted(passages, key=lambda p: -(p.rerank_score if p.rerank_score is not None else 0.0)):
        if used + cost[p.passage_id] <= budget or not kept:  # the best passage is always shown, even if it alone is big
            kept.add(p.passage_id)
            used += cost[p.passage_id]
    omitted = [f"omitted {p.passage_id}: left out of the interpretation call (~{cost[p.passage_id]} tokens, budget {budget})"
               for p in passages if p.passage_id not in kept]
    return [p for p in passages if p.passage_id in kept], omitted


# --------------------------------------------------------------------------------------
# 7. Conclude
# --------------------------------------------------------------------------------------


def _candidate(label: str, assessment: Assessment, conditions: list[str]) -> Candidate:
    own = [s for s in assessment.located if s.label == label]
    refs = list(dict.fromkeys(r for s in own for r in s.evidence))
    # An approximate quote (OCR typos the model corrected, "..." for omitted words) is still located in the right place and the label
    # is not copied from its wording, so it does not block acceptance; the stance keeps the flag and the UI card shows it.
    flags = sorted({f for s in own for f in s.flags if f != "quote_approximate"})
    return Candidate(
        source_value=label, normalized_value=label, evidence=refs, quote=own[0].quote if own else None,
        passage_id=own[0].passage_id if own else None, qualifications=conditions, flags=flags, candidate_id=f"label:{label}")


def _found(proposal: ValueProposal, reading: Reading, assessment: Assessment, log: list[str]) -> ValueProposal:
    task_id = proposal.task_id
    task_labels = {s.label for s in assessment.located}
    chosen = _candidate(reading.label, assessment, reading.interpretation.conditions)
    log.append(f"chosen: {reading.label} ({len(assessment.backing)} supporting quote(s); labels with quotes: {', '.join(sorted(task_labels))})")
    emit("S08", f"{task_id}: {reading.label}", "ok")
    return proposal.model_copy(update={
        "outcome": ProposalOutcome.FOUND, "value": reading.label, "chosen": [chosen], "candidates": [chosen],
        "reason": reading.interpretation.justification or f"quotes support '{reading.label}'", "steps": log})


def _uncertain(proposal: ValueProposal, labels: list[str], reason: str, log: list[str]) -> ValueProposal:
    """The labels that were in play stay visible as alternatives (they are not an answer)."""
    assessment = assess(None, proposal.interpretation.stances if proposal.interpretation else [])
    candidates = [_candidate(label, assessment, []) for label in dict.fromkeys(labels)]
    return proposal.model_copy(update={"outcome": ProposalOutcome.UNCERTAIN, "candidates": candidates, "reason": reason, "steps": log})


def _conclude_empty(
    proposal: ValueProposal, reading: Reading, assessment: Assessment, log: list[str], settings: InterpretationSettings,
    confirmed: bool = False,
) -> ValueProposal:
    """The model found no decision in the evidence. That is `absent` only if nothing points the other way and nothing was missed."""
    interpretation: Interpretation = reading.interpretation
    labels = sorted({s.label for s in assessment.located})
    if interpretation.unresolved or (len(labels) > 1 and not confirmed):
        why = ("; ".join(interpretation.unresolved) if interpretation.unresolved else f"quotes support {', '.join(labels)} and nothing settles it")
        return _uncertain(proposal, labels, f"no decision could be reached: {why}", log)
    blocking = [g for g in proposal.gaps if blocks_absence(g, settings)]
    if blocking:
        return _uncertain(proposal, labels, "no decision found, but the evidence is incomplete: " + blocking[0], log)
    if labels and not confirmed:  # the model said "no decision" yet listed quotes for one label: the quotes must be explained, not dropped
        return _uncertain(proposal, labels, f"the model found no decision although it quoted evidence for '{labels[0]}'", log)
    return proposal.model_copy(update={"outcome": ProposalOutcome.ABSENT, "steps": log,
                                       "reason": interpretation.justification or "no passage states a decision on this question"})


def _mark_inspected(ledger: CoverageLedger | None, task_id: str, shown: list[EvidencePassage]) -> None:
    if ledger is None:
        return
    for passage in shown:
        for block_id in passage.block_ids:
            ledger.record(CoverageEntry(document_id=passage.document_id, block_id=block_id, page=None,
                                        state=CoverageState.INSPECTED, task_id=task_id))
