"""S05 - THE PLAN, in one place: which value tasks are answered by the same model call.

Read `plan_extraction` top to bottom. It runs after retrieval (S06) has gathered the evidence of every task and
before any model is asked. It only decides GROUPING: every task is still extracted (its own part of the answer),
verified and validated on its own afterwards, and a task is asked again alone whenever a shared call cannot be trusted.

    evidence package of every value task
        |  1. overlap    overlap.py    how many passages (tokens) each pair of tasks shares
        |  2. eligible                 which tasks may share a call at all (off / derivation / no evidence stay alone)
        |  3. cost       capacity.py   what each passage costs in a prompt, what one call can carry
        |  4. group      grouping.py   join tasks that share evidence while it is safe (size, fit, dilution, gain)
        |  5. batches                  one Batch per call, with the reason in plain words
        v
    ExtractionPlan (batches + the overlap table); nothing in it changes a result by itself
"""

from ..adapters.base import ExtractorAdapter
from ..config.runtime import ExtractionSettings, PlanningSettings
from ..contracts.evidence import EvidencePackage
from ..contracts.plan import Batch, ExtractionPlan
from ..contracts.task import TaskSpecification
from ..events import emit
from ..extraction.capacity import evidence_budget, max_fields_per_call, passage_cost
from .grouping import Group, Member, group_members
from .overlap import measure_overlap


def plan_extraction(
    packages: dict[str, EvidencePackage],
    tasks: dict[str, TaskSpecification] | None = None,
    planning: PlanningSettings | None = None,
    extraction: ExtractionSettings | None = None,
    extractor: ExtractorAdapter | None = None,
) -> ExtractionPlan:
    """`packages`: task id -> its evidence, in the order the tasks will run. Without `tasks` and `extractor` nothing is grouped."""
    planning, extraction = planning or PlanningSettings(), extraction or ExtractionSettings()

    # 1. Overlap: how much evidence is shared between tasks.
    overlaps = measure_overlap(packages)
    total = sum(p.token_count for pkg in packages.values() for p in pkg.passages)
    distinct = sum({p.passage_id: p.token_count for pkg in packages.values() for p in pkg.passages}.values())
    emit("S05", f"{len(packages)} task(s) read {total} evidence tokens in total, {distinct} of them distinct "
                f"({len(overlaps)} pair(s) of tasks share passages)")
    for row in overlaps[:5]:
        emit("S05", f"{row.task_a} / {row.task_b}: {row.shared_passages} shared passage(s), "
                    f"{round(100 * row.containment)}% of the smaller evidence", "debug")

    # 2. Eligible: which tasks may share a call.
    alone: dict[str, str] = {}  # task id -> why it is asked alone
    for task_id, package in packages.items():
        task = tasks.get(task_id) if tasks else None
        if planning.grouping == "off" or task is None or extractor is None:
            alone[task_id] = "grouping is off (planning.grouping)"
        elif task.rules.derivation:
            alone[task_id] = "the task allows derivation, which is never done inside a shared call"
        elif not package.passages:
            alone[task_id] = "no evidence was retrieved for it"
    eligible = [t for t in packages if t not in alone]

    # 3. Cost: what each passage takes in a prompt, and what one call can carry.
    groups: list[Group] = []
    if len(eligible) > 1:
        cost = passage_cost(extractor, False)
        members = [Member(t, {p.passage_id: cost(p) for p in packages[t].passages}) for t in eligible]
        by_id = {p.passage_id: p for t in eligible for p in packages[t].passages}

        def fits(task_ids: list[str], passage_ids: list[str]) -> bool:
            union = [by_id[i] for i in passage_ids]
            show_document = len({p.document_id for p in union}) > 1
            spend = passage_cost(extractor, show_document)
            return sum(spend(p) for p in union) <= evidence_budget([tasks[i] for i in task_ids], extraction, extractor, show_document)

        # 4. Group: join tasks that share evidence while it is safe.
        groups = group_members(members, fits, max_fields_per_call(planning, extractor), planning.max_foreign_share)
    else:
        groups = [Group([Member(t, {p.passage_id: p.token_count for p in packages[t].passages})]) for t in eligible]

    # 5. Batches: one per model call, in the order of their first task.
    batches = []
    for task_id, package in packages.items():
        if task_id in alone:
            batches.append(Batch(task_ids=[task_id], reason=alone[task_id], passage_ids=[p.passage_id for p in package.passages],
                                 tokens=sum(p.token_count for p in package.passages)))
    for group in groups:
        if len(group.members) == 1:
            member = group.members[0]
            batches.append(Batch(task_ids=[member.task_id], reason="no other task shares enough of its evidence",
                                 passage_ids=list(member.passages), tokens=member.tokens))
        else:
            saved = group.separate_tokens - group.union_tokens
            reason = (f"{len(group.members)} tasks share {round(100 * saved / group.separate_tokens)}% of their evidence: one call reads "
                      f"{group.union_tokens} tokens instead of {group.separate_tokens} (saves {len(group.members) - 1} call(s))")
            batches.append(Batch(task_ids=group.task_ids, reason=reason, passage_ids=list(group.union), tokens=group.union_tokens))
    position = {t: i for i, t in enumerate(packages)}
    batches.sort(key=lambda b: min(position[t] for t in b.task_ids))
    for batch in batches:
        if len(batch.task_ids) > 1:
            emit("S05", f"one model call for {', '.join(batch.task_ids)}: {batch.reason}", "ok")
        else:
            emit("S05", f"{batch.task_ids[0]} is asked alone: {batch.reason}", "debug")
    calls = len(batches)
    emit("S05", f"{len(packages)} task(s) will be asked in {calls} extraction call(s)" +
                (f" (grouping saves {len(packages) - calls})" if calls < len(packages) else ""), "ok" if calls < len(packages) else "info")
    return ExtractionPlan(batches=batches, overlaps=overlaps,
                          notes=[f"evidence tokens summed over tasks: {total}; distinct: {distinct}"])
