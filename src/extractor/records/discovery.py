"""S09 steps 2-4 - Discover the record boundaries over the WHOLE scope, window by window.

The scope is cut into windows that each fit the model's context (consecutive windows overlap by a few blocks, so a record that crosses
the cut is seen whole in one of them). In each window the model lists every record: its key, the block it starts in, its first words and
the last block it occupies. Code then turns that into exact positions: the first words are searched in the start block (several records
may share a block). Records found in two windows are merged in `spans.reconcile`. Nothing here decides what a record is worth: that is
the audit and acceptance.
"""

import json
from dataclasses import dataclass, field

from ..adapters.base import ExtractorAdapter
from ..config.runtime import RecordSettings
from ..contracts.candidate import ModelCall
from ..contracts.task import TaskSpecification
from ..events import emit
from ..execution import fan_out, width
from ..extraction.calling import call_model
from .keys import keep_family_chain
from .outline import Tagged, include_marker, locate_start, tag_index
from .prompts import discover_prompt
from .schema import discover_schema
from .scope import Scope


@dataclass
class Found:
    """One record the model reported, with positions the engine verified."""

    key: str
    start: int  # block index
    offset: int  # character offset of the record's first character inside the start block
    end: int  # last block index the model said it occupies
    window: int
    flags: list[str] = field(default_factory=list)


@dataclass
class Discovery:
    found: list[Found] = field(default_factory=list)
    calls: list[ModelCall] = field(default_factory=list)
    windows: int = 0
    failed_windows: list[str] = field(default_factory=list)
    truncated: bool = False
    notes: list[str] = field(default_factory=list)
    wandered: list[str] = field(default_factory=list)  # entries dropped because their numbering left the family of the list


def discover(
    task: TaskSpecification, tagged: list[Tagged], scope: Scope, model: ExtractorAdapter, settings: RecordSettings,
) -> Discovery:
    result = Discovery()
    in_scope = tagged[scope.first:scope.last + 1]
    windows = split_windows(task, in_scope, model, settings)
    result.windows = len(windows)
    emit("S09", f"{task.id}: {len(in_scope)} block(s) in scope, read in {len(windows)} window(s)")
    cap = answer_cap(model, settings)

    def read_one(item: tuple[int, list[Tagged]]) -> Discovery:
        n, window = item
        part = Discovery()  # each window fills its own record: the windows may run side by side (`execution.fan_out.record_windows`)
        ask_window(task, window, f"{n}/{len(windows)}", tagged, model, settings, part, n, cap)  # pagination and halving inside stay in order
        return part

    for part in fan_out(list(enumerate(windows, start=1)), read_one, width("record_windows")):  # merged in window order: the same result as a loop
        result.found += part.found
        result.calls += part.calls
        result.windows += part.windows
        result.failed_windows += part.failed_windows
        result.truncated = result.truncated or part.truncated
        result.notes += part.notes
        result.wandered += part.wandered
    return result


def ask_window(
    task: TaskSpecification, window: list[Tagged], label: str, tagged: list[Tagged], model: ExtractorAdapter, settings: RecordSettings,
    result: Discovery, number: int, cap: int, after: str | None = None, depth: int = 0,
) -> None:
    """One discovery call, for a window that may hold more records than one ANSWER can list.

    * The answer lists at most `cap` records. When it lists exactly `cap` there may be more: the window is asked again from the last record
      found, told which record it already has (pagination by key, like a paged API), until a call lists fewer than `cap`.
    * When the answer does not fit the model's output limit (the call is cut off) the cap is halved and the same window is asked again;
      if even a small cap fails and the window has many blocks, the window is split in two halves.
    So a long list degrades into more, smaller calls instead of a failed task."""
    call = call_model(model, discover_prompt(task, window, cap, after), f"find the records ({label})", settings.retries,
                      settings.think_discovery, stage="S09")
    result.calls.append(call)
    if call.reply.data is None:
        if "cut off" in str(call.reply.error) and depth < 8:
            if cap > 4:
                emit("S09", f"{task.id}: window {label} did not fit the answer limit; asking for at most {max(4, cap // 2)} records at a time", "warn")
                ask_window(task, window, label, tagged, model, settings, result, number, max(4, cap // 2), after, depth + 1)
                return
            if len(window) > 2 * settings.window_overlap_blocks + 2:
                mid = len(window) // 2
                emit("S09", f"{task.id}: window {label} is too long for one answer; asking its two halves", "warn")
                result.windows += 1
                ask_window(task, window[:mid + settings.window_overlap_blocks], f"{label}a", tagged, model, settings, result, number, cap, after, depth + 1)
                ask_window(task, window[mid:], f"{label}b", tagged, model, settings, result, number, cap, None, depth + 1)
                return
        result.failed_windows.append(f"window {label} (blocks {window[0].tag}-{window[-1].tag}) failed: {call.reply.error}")
        return
    items = call.reply.data.get("records") or []
    if str(call.reply.data.get("notes") or "").strip():
        result.notes.append(str(call.reply.data["notes"]).strip())
    found = read_window(items, tagged, number)
    keep = keep_family_chain([f.key for f in found], after)
    if keep < len(found):  # the list wandered into another kind of numbering (the next section): stop here
        for f in found[keep:]:
            result.wandered.append(f"'{f.key}' follows '{found[keep - 1].key if keep else after}' but is numbered differently: the list left its section")
        found = found[:keep]
    result.found += found
    emit("S09", f"{task.id}: window {label}: {len(found)} record(s)" + (f" ({len(items) - len(found)} left the list)" if len(found) < len(items) else ""))
    if keep < len(items):
        return
    if len(items) >= cap and found:  # a full answer: there may be more records after the last one
        last = found[-1]
        if depth >= 12:
            result.truncated = True
            return
        resume = [t for t in window if t.index >= last.start]
        ask_window(task, resume, f"{label}+", tagged, model, settings, result, number, cap, last.key, depth + 1)


def answer_cap(model: ExtractorAdapter, settings: RecordSettings) -> int:
    """Records one answer may list: what the model's output limit holds (about 70 tokens each), never more than the setting."""
    return max(4, min(settings.max_records_per_window, model.output_tokens // 70))


def split_windows(task: TaskSpecification, blocks: list[Tagged], model: ExtractorAdapter, settings: RecordSettings) -> list[list[Tagged]]:
    """Consecutive windows that each fit one call, overlapping by `window_overlap_blocks`."""
    probe = discover_prompt(task, [], settings.max_records_per_window, None)
    schema = discover_schema([t.tag for t in blocks[:1]], settings.max_records_per_window)
    overhead = model.count_tokens(probe.system + probe.user + json.dumps(schema))
    budget = max(200, int(model.context_tokens * (1 - settings.prompt_margin)) - model.output_tokens - overhead)
    cost = [model.count_tokens(t.render()) + 8 for t in blocks]
    windows, start = [], 0
    while start < len(blocks):
        end, used = start, 0
        while end < len(blocks) and (used + cost[end] <= budget or end == start):
            used += cost[end]
            end += 1
        windows.append(blocks[start:end])
        if end >= len(blocks):
            break
        start = max(start + 1, end - settings.window_overlap_blocks)
    return windows


def read_window(items: list, tagged: list[Tagged], window: int) -> list[Found]:
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        start = tag_index(item.get("start_block"), len(tagged))
        if start is None:
            continue
        end = tag_index(item.get("end_block"), len(tagged))
        end = start if end is None or end < start else end
        text = tagged[start].block.text
        offset, how = locate_start(text, str(item.get("starts_with") or ""), str(item.get("key") or ""))
        if how not in ("exact", "marker"):  # the model's block tag is often off by one or two: look for the record in the blocks around the one it named
            for near in range(max(0, start - 1), min(len(tagged), start + 4)):
                found_at, how_near = locate_start(tagged[near].block.text, str(item.get("starts_with") or ""), str(item.get("key") or ""))
                if found_at is not None and how_near in ("exact", "marker"):
                    start, text, offset = near, tagged[near].block.text, found_at
                    end = max(end, start)
                    break
        flags = [] if offset is not None else ["boundary_not_located"]
        offset = include_marker(text, offset or 0)
        out.append(Found(key=str(item.get("key") or "").strip(), start=start, offset=offset, end=end, window=window, flags=flags))
    return out
