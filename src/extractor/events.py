"""Observability - the engine's activity log, readable by people (the UI Logs tab) and tests.

Not a pipeline stage: every stage reports into it. The engine never reads events back to
make decisions, and nothing here changes a result; it only records what happened.

    emit(stage, message)                 one line ("S06", "reranked 40 passages")
    with step(stage, message): ...       a timed step: a start line, then "done in 1.2s" (or "failed")
    progress(stage, done, total, what)   a counter the UI draws as a bar
    with run_scope("Extract", ...): ...  groups everything inside under one run (a job) with its own timeline

Events go to a process-wide ring buffer (`LOG`). Readers poll `LOG.since(seq)`. The buffer is
bounded, thread-safe, and costs nothing when nobody reads it.

Convention (see CLAUDE.md): every new stage or step emits through this module in the same change
that adds it, using its stage code (S01-S13) and plain-language messages.
"""

import threading
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Iterator

MAX_EVENTS = 5000

_run_id: ContextVar[str] = ContextVar("run_id", default="")


@dataclass
class Event:
    seq: int
    time: float
    level: str  # debug | info | ok | warn | error
    stage: str  # "S03", "S06"... or "run" / "ui"
    message: str
    run: str = ""  # run_scope id this belongs to
    kind: str = "log"  # log | start | end | progress | run_start | run_end
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


class EventLog:
    def __init__(self, capacity: int = MAX_EVENTS):
        self._events: deque[Event] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._seq = 0

    def add(self, level: str, stage: str, message: str, kind: str = "log", data: dict | None = None) -> Event:
        with self._lock:
            self._seq += 1
            event = Event(self._seq, time.time(), level, stage, message, _run_id.get(), kind, data or {})
            self._events.append(event)
        return event

    def since(self, seq: int = 0, run: str | None = None) -> list[Event]:
        with self._lock:
            events = [e for e in self._events if e.seq > seq]
        return [e for e in events if run is None or e.run == run]

    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


LOG = EventLog()


def emit(stage: str, message: str, level: str = "info", **data) -> None:
    LOG.add(level, stage, message, data=data)


def progress(stage: str, done: int, total: int, what: str = "") -> None:
    """A counter ("embedded 3/12 batches"); the UI shows the latest one per run as a bar."""
    LOG.add("info", stage, f"{what} {done}/{total}".strip(), "progress", {"done": done, "total": total, "what": what})


@contextmanager
def step(stage: str, message: str, **data) -> Iterator[dict]:
    """A timed step. Put results in the yielded dict and they appear on the closing line."""
    LOG.add("info", stage, message, "start", data)
    started = time.time()
    result: dict = {}
    try:
        yield result
    except BaseException as exc:
        LOG.add("error", stage, f"{message} failed after {time.time() - started:.1f}s: {exc}", "end",
                {**data, "seconds": round(time.time() - started, 2), "failed": True})
        raise
    seconds = round(time.time() - started, 2)
    detail = ", ".join(f"{k}={v}" for k, v in result.items())
    LOG.add("ok", stage, f"{message} done in {seconds:.1f}s" + (f" ({detail})" if detail else ""), "end",
            {**data, **result, "seconds": seconds})


@contextmanager
def run_scope(title: str, **data) -> Iterator[str]:
    """Everything emitted inside belongs to one run (job), with a start and an end event."""
    run = f"r{int(time.time() * 1000) % 10**9:x}{threading.get_ident() % 997:x}"
    token = _run_id.set(run)
    LOG.add("info", "run", title, "run_start", {"title": title, **data})
    started = time.time()
    try:
        yield run
        LOG.add("ok", "run", f"{title} finished in {time.time() - started:.1f}s", "run_end",
                {"title": title, "seconds": round(time.time() - started, 2)})
    except BaseException as exc:
        LOG.add("error", "run", f"{title} failed: {exc}", "run_end",
                {"title": title, "seconds": round(time.time() - started, 2), "failed": True})
        raise
    finally:
        _run_id.reset(token)
