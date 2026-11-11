"""S12 - The recovery actions for records tasks.

  rediscover   the audit says a record may be missing (a numbering gap, numbered text no record covers, a failed or cut-off
               discovery window) -> ONLY the named block regions are read again, in small windows; what is found is merged with
               the records already read, their spans are rebuilt and the audit runs again. A record whose span did not change
               keeps its fields, so the cost is the new windows plus the fields of new or changed records.
  refield      a required field of a record was not accepted -> the fields of THAT record are read again with candidate
               verification on (off by default for records because it triples the calls).

`rediscover` completes an inspection the audit proved incomplete: on by default. `refield` changes how a field is decided: off
until measured.
"""

import re
from dataclasses import replace

from ..config.runtime import RecoverySettings
from ..contracts.validation import Category as C
from ..records.discovery import Discovery
from ..records.records_pipeline import DocumentRead, FoundRecord, assemble, make_record
from ..records.scope import Scope
from .actions import Planned
from .attempt import RecordsAttempt, RecordsTools

MISSING = {C.NUMBERING_GAP, C.POSSIBLE_MISSED_RECORD, C.DISCOVERY_FAILED, C.OUTPUT_TRUNCATED}
REGION = re.compile(r"\bB(\d+)(?:\s*-\s*B(\d+))?")
MAX_REGION_BLOCKS = 60  # a region larger than this is the scope again: reading it once more would be a second full discovery


def plan_records_actions(attempt: RecordsAttempt, settings: RecoverySettings, tools: RecordsTools, tried: set[str]) -> list[Planned]:
    blocking = {f.category for f in attempt.blocking}
    plans: list[Planned] = []
    regions = {i: gap_regions(read, blocking, settings.rediscover_margin_blocks) for i, read in enumerate(attempt.outcome.documents)}
    if any(regions.values()):
        count = sum(len(r) for r in regions.values())
        reason = next(f.message for f in attempt.blocking if f.category in MISSING)
        plans.append(Planned("rediscover", reason, f"{count} block region(s) the audit named were read again and merged with the records already found",
                             lambda: rediscover(attempt, regions, tools)))
    weak = [(i, r) for i, read in enumerate(attempt.outcome.documents) for r in read.records if not r.accepted]
    if C.RECORD_FIELD_NOT_ACCEPTED in blocking and weak:
        plans.append(Planned("refield", f"{len(weak)} record(s) have a required field that was not accepted",
                             f"the fields of {len(weak)} record(s) read again with candidate verification on",
                             lambda: refield(attempt, weak, tools, settings.max_calls)))
    return [p for p in plans if p.action in settings.actions and p.action not in tried]


def gap_regions(read: DocumentRead, blocking: set[str], margin: int) -> list[tuple[int, int]]:
    """Block ranges (first, last) the audit findings of this document point at, with a margin, merged when they touch."""
    last = len(read.tagged) - 1
    spans = []
    for f in read.findings:
        if f.category not in MISSING or f.category not in blocking or f.recovery is None:
            continue
        m = REGION.search(f.recovery.reason)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        a, b = max(0, min(a, b) - margin), min(last, max(a, b) + margin)
        if a <= b and b - a < MAX_REGION_BLOCKS:
            spans.append((a, b))
    merged: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def rediscover(attempt: RecordsAttempt, regions: dict[int, list[tuple[int, int]]], tools: RecordsTools) -> RecordsAttempt:
    reads = []
    for i, read in enumerate(attempt.outcome.documents):
        if not regions.get(i):
            reads.append(read)
            continue
        again = [tools.discover(read.tagged, Scope(a, b, f"recovery: B{a}-B{b} read again")) for a, b in regions[i]]
        reads.append(tools.reread(read, merge_discovery(read.discovery, again, regions[i])))
    return tools.decide(assemble(reads, [line for line in attempt.outcome.log if "no blocks" in line]))


def merge_discovery(old: Discovery, again: list[Discovery], regions: list[tuple[int, int]]) -> Discovery:
    """The first discovery plus what the re-read regions reported. A problem of the first discovery (a failed or cut-off window)
    is cleared only when the region that covers it was read again without that problem."""
    clean = [region for region, d in zip(regions, again) if not d.failed_windows and not d.truncated]

    def reread_ok(text: str) -> bool:
        m = REGION.search(text)
        if not m:
            return False
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        return any(first <= a and b <= last for first, last in clean)

    return Discovery(
        found=[*old.found, *(f for d in again for f in d.found)],
        calls=[*old.calls, *(c for d in again for c in d.calls)],
        windows=old.windows + sum(d.windows for d in again),
        failed_windows=[w for w in old.failed_windows if not reread_ok(w)] + [w for d in again for w in d.failed_windows],
        truncated=(old.truncated and not clean) or any(d.truncated for d in again),
        notes=[*old.notes, *(n for d in again for n in d.notes)],
        wandered=list(old.wandered),  # entries a small window lists beyond the list's own numbering are not reported again
    )


def refield(attempt: RecordsAttempt, weak: list[tuple[int, FoundRecord]], tools: RecordsTools, max_calls: int) -> RecordsAttempt:
    """Read the fields of the weak records again, verification on, stopping when the call budget of the round is used."""
    start = tools.spent().calls
    better: dict[tuple[int, str], FoundRecord] = {}
    for i, record in weak:
        if tools.spent().calls - start >= max_calls:
            break
        outcomes = tools.refield(attempt.outcome.documents[i], record)
        if sum(o.accepted for o in outcomes) > sum(o.accepted for o in record.outcomes):
            better[(i, record.span.record_id)] = FoundRecord(make_record(record.span, outcomes), record.span, outcomes)
    reads = [replace(read, records=[better.get((i, r.span.record_id), r) for r in read.records])
             for i, read in enumerate(attempt.outcome.documents)]
    return tools.decide(assemble(reads, [line for line in attempt.outcome.log if "no blocks" in line]))
