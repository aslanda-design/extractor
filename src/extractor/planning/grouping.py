"""S05 step 4 - Which tasks can share ONE model call without hurting each other?

Pure arithmetic over the passages each task was given; no model, no prompt. It starts with every task alone and
repeatedly joins the two groups that would save the most evidence tokens, as long as the joined group is SAFE:

    size       at most `max_fields` tasks
    fit        the shared evidence fits in one call (the caller's `fits` check: context, instructions, answer size)
    dilution   no task would be shown more than `max_foreign_share` of passages that its own retrieval did not pick
    gain       the join actually saves evidence tokens (the tasks really do share passages)

The dilution rule is the quality guard: a task only ever shares a call with tasks whose evidence is mostly ITS evidence.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass
class Member:
    task_id: str
    passages: dict[str, int]  # passage id -> tokens that passage takes in a prompt

    @property
    def tokens(self) -> int:
        return sum(self.passages.values())


@dataclass
class Group:
    members: list[Member]

    @property
    def task_ids(self) -> list[str]:
        return [m.task_id for m in self.members]

    @property
    def union(self) -> dict[str, int]:
        return {pid: cost for m in self.members for pid, cost in m.passages.items()}

    @property
    def union_tokens(self) -> int:
        return sum(self.union.values())

    @property
    def separate_tokens(self) -> int:
        return sum(m.tokens for m in self.members)


def group_members(
    members: list[Member], fits: Callable[[list[str], list[str]], bool], max_fields: int, max_foreign_share: float,
) -> list[Group]:
    """Groups in the order of their first task. `fits(task_ids, passage_ids)` says whether one call can carry them."""
    groups = [Group([m]) for m in members]
    order = {m.task_id: i for i, m in enumerate(members)}
    while True:
        best, best_saved = None, 0
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                joined = Group(sorted(groups[i].members + groups[j].members, key=lambda m: order[m.task_id]))
                saved = groups[i].union_tokens + groups[j].union_tokens - joined.union_tokens
                if saved > best_saved and _safe(joined, fits, max_fields, max_foreign_share):
                    best, best_saved = (i, j, joined), saved
        if best is None:
            return groups
        i, j, joined = best
        groups = [g for k, g in enumerate(groups) if k not in (i, j)] + [joined]
        groups.sort(key=lambda g: order[g.members[0].task_id])


def _safe(group: Group, fits, max_fields: int, max_foreign_share: float) -> bool:
    if len(group.members) > max_fields:
        return False
    total = group.union_tokens
    if any(1 - m.tokens / total > max_foreign_share for m in group.members):  # share of the call this task did not retrieve
        return False
    return fits(group.task_ids, list(group.union))
