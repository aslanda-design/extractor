"""The scheduler: independent pieces of work run side by side, never more than the backend can serve.

The work of the engine is waiting for a model server, so threads are enough (no asyncio rewrite of the adapters). The limit
is the SMALLER of `execution.max_concurrency` (what you allow) and the model's own `max_parallel` (what the backend can do:
one local GPU serves one call at a time, a hosted API serves several). With a limit of 1 nothing is threaded at all: the
work runs in order, exactly as before, which keeps a local run and every test deterministic.

Dependencies are respected by the caller: it hands over one dependency layer at a time (`spec.graph.layers`), and the tasks
inside a layer do not depend on each other.
"""

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Sequence, TypeVar

from ..adapters.base import ExtractorAdapter
from ..config.runtime import ExecutionConfig

T = TypeVar("T")
R = TypeVar("R")


def parallel_limit(settings: ExecutionConfig, *models: ExtractorAdapter) -> int:
    """How many pieces of work may run at once: the setting, capped by the least parallel model involved."""
    return max(1, min([settings.max_concurrency, *(m.max_parallel for m in models)]))


def run_parallel(items: Sequence[T], work: Callable[[T], R], limit: int) -> list[R]:
    """`work(item)` for every item, results in the order of `items`. An exception in one item is raised after the others finish."""
    if limit <= 1 or len(items) <= 1:
        return [work(item) for item in items]
    with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="extractor") as pool:
        # each worker gets a copy of the caller's context, so the run id (events) and the execution scope travel with it
        futures = [pool.submit(contextvars.copy_context().run, work, item) for item in items]
        return [f.result() for f in futures]
