"""Portable concurrency core: named resources with a capacity, and a bounded fan-out. Standard library only.

This file imports nothing from the engine, so it can be copied into another project as it is.

Two separate questions, two separate tools:

  fan_out(items, work, width)   HOW MANY pieces of work are in flight (the shape of the work: tasks, batches, windows...)
  slot("llm_api")               HOW MANY calls a backend serves at once (the hardware or the API: a GPU, a hosted model...)

Two rules keep it safe:

  1. A slot is taken ONLY around one backend call (one `generate`, one `embed`, one `score`), never around a fan-out.
     So nested fan-outs cannot deadlock, and the load on a backend is capped by its resource however wide the fan-outs are.
  2. A width of 1 runs a plain loop on the caller's thread (no thread pool), so a local run and every test stay deterministic.

    resources = Resources({"gpu": 1, "llm_api": 8})
    with use_resources(resources):
        def ask(prompt):
            with slot("llm_api"):                      # waits here when 8 calls are already in flight
                return client.generate(prompt)
        answers = fan_out(prompts, ask, width=16)      # 16 workers, never more than 8 calls at the API
    print(resources.stats())                           # {"llm_api": {"capacity": 8, "peak": 8, "calls": 40, "wait_seconds": 1.2}, ...}

A resource that is not declared has no limit, and outside `use_resources` a slot does nothing.
"""

import contextvars
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable, Iterator, Mapping, Sequence, TypeVar

T = TypeVar("T")
R = TypeVar("R")


class Resources:
    """Named backends, each with a number of calls it serves at once, and what happened at each one."""

    def __init__(self, capacities: Mapping[str, int]):
        self._capacity = {name: max(1, int(n)) for name, n in capacities.items()}
        self._semaphores = {name: threading.BoundedSemaphore(n) for name, n in self._capacity.items()}
        self._lock = threading.Lock()
        self._stats = {name: {"peak": 0, "calls": 0, "wait_seconds": 0.0, "active": 0} for name in self._capacity}

    def capacity(self, name: str) -> int | None:
        return self._capacity.get(name)

    @contextmanager
    def slot(self, name: str) -> Iterator[None]:
        semaphore = self._semaphores.get(name)
        if semaphore is None:  # not declared: no limit
            yield
            return
        asked = time.perf_counter()
        semaphore.acquire()
        waited = time.perf_counter() - asked
        with self._lock:
            s = self._stats[name]
            s["active"] += 1
            s["calls"] += 1
            s["wait_seconds"] += waited
            s["peak"] = max(s["peak"], s["active"])
        try:
            yield
        finally:
            with self._lock:
                self._stats[name]["active"] -= 1
            semaphore.release()

    def stats(self) -> dict[str, dict]:
        with self._lock:
            return {name: {"capacity": self._capacity[name], "peak": s["peak"], "calls": s["calls"],
                           "wait_seconds": round(s["wait_seconds"], 2)} for name, s in self._stats.items()}


_current: contextvars.ContextVar[Resources | None] = contextvars.ContextVar("resources", default=None)


def current_resources() -> Resources | None:
    return _current.get()


@contextmanager
def use_resources(resources: Resources) -> Iterator[Resources]:
    """Makes `resources` the current ones (workers started by `fan_out` inherit them). A scope inside another reuses the outer one."""
    outer = _current.get()
    if outer is not None:
        yield outer
        return
    token = _current.set(resources)
    try:
        yield resources
    finally:
        _current.reset(token)


@contextmanager
def slot(name: str) -> Iterator[None]:
    """Hold one slot of resource `name` for the duration of ONE backend call."""
    resources = _current.get()
    if resources is None:
        yield
        return
    with resources.slot(name):
        yield


def fan_out(items: Sequence[T], work: Callable[[T], R], width: int) -> list[R]:
    """`work(item)` for every item, results in the order of `items`, at most `width` at a time.

    An exception in one item is raised after the others finish. With `width <= 1` (or one item) nothing is threaded."""
    if width <= 1 or len(items) <= 1:
        return [work(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(width, len(items)), thread_name_prefix="fanout") as pool:
        # each worker gets a copy of the caller's context, so whatever is in context vars (resources, run id...) travels with it
        futures = [pool.submit(contextvars.copy_context().run, work, item) for item in items]
        return [f.result() for f in futures]
