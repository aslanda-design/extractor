"""S12 - Which result stands after a recovery attempt: the earlier one or the new one.

The rule is the same idea as everywhere in the engine: an attempt PROPOSES, the engine decides, and a second attempt may only
make the answer safer or more complete.

  * a failed attempt replaces nothing;
  * a result is replaced only by one of a better status (failed < uncertain < not_found < supported);
  * two attempts that found DIFFERENT values disagree: the task stays `uncertain` and both values are shown to a person
    (the newer attempt is not "more right" because it came later);
  * "not found" cannot replace an attempt that saw a value or candidates: something was there.

An accepted (`supported`) result never reaches recovery, so it is never replaced here.
"""

import json

from ..contracts.candidate import ProposalOutcome
from ..contracts.common import TaskStatus
from .attempt import Attempt, RecordsAttempt

RANK = {TaskStatus.FAILED: 0, TaskStatus.UNCERTAIN: 1, TaskStatus.NOT_FOUND: 2, TaskStatus.SUPPORTED: 3}


def same_value(a, b) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def keep_new(old: Attempt, new: Attempt) -> tuple[bool, str]:
    """(replace the old result with the new one?, why) for a value / interpretation task."""
    if new.status == TaskStatus.FAILED:
        return False, f"the attempt failed ({new.proposal.reason})"
    if new.status == TaskStatus.SUPPORTED and old.found_value is not None and not same_value(old.found_value, new.found_value):
        return False, f"two attempts disagree ({old.found_value!r} before, {new.found_value!r} now): a person should decide"
    if new.status == TaskStatus.NOT_FOUND and old.proposal.outcome in (ProposalOutcome.FOUND, ProposalOutcome.UNCERTAIN):
        return False, "an earlier attempt saw a value or candidates, so absence cannot be claimed"
    if RANK[new.status] > RANK[old.status]:
        return True, {TaskStatus.SUPPORTED: "accepted", TaskStatus.NOT_FOUND: "the inspection is now complete and nothing states the value",
                      TaskStatus.UNCERTAIN: "the call now works; the answer still needs review"}[new.status]
    if new.status == old.status == TaskStatus.UNCERTAIN and len(new.blocking) < len(old.blocking) \
            and (old.found_value is None or same_value(old.found_value, new.found_value)):
        return True, f"still uncertain, with fewer blocking findings ({len(old.blocking)} -> {len(new.blocking)})"
    return False, "no improvement"


def keep_new_records(old: RecordsAttempt, new: RecordsAttempt) -> tuple[bool, str]:
    """(replace?, why) for a records task. Records already read are never given up for a better-looking status."""
    if new.status == TaskStatus.FAILED:
        return False, "the attempt failed"
    if len(new.outcome.records) < len(old.outcome.records):
        return False, f"the attempt lost records ({len(old.outcome.records)} -> {len(new.outcome.records)})"
    if RANK[new.status] > RANK[old.status]:
        return True, "accepted" if new.status == TaskStatus.SUPPORTED else "improved"
    if RANK[new.status] == RANK[old.status] and len(new.blocking) < len(old.blocking):
        return True, f"fewer blocking findings ({len(old.blocking)} -> {len(new.blocking)})"
    return False, "no improvement"
