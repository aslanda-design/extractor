"""The execution scope: what every model call of ONE run shares (the call cache, the meter, the retry pause).

    with execution_scope(store, config.execution) as exe:     # opened by the engine, an evaluation or a UI job
        with task_scope("maturity_date"):                      # calls made inside are counted for this task
            ...

Stages do not receive these objects as arguments: `call_model` asks for the current scope. Outside a scope (a unit test that
calls a stage directly) there is no cache and no meter, and everything works as before. A scope opened inside another one
reuses the outer one, so an evaluation that calls the engine still has ONE meter.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

from ..artifacts.store import ArtifactStore
from ..config.runtime import ExecutionConfig
from .call_cache import CallCache
from .meter import RunMeter


@dataclass
class Execution:
    cache: CallCache | None = None
    meter: RunMeter = field(default_factory=RunMeter)
    retry_pause: float = 0.0  # seconds before re-sending a call that failed in transport (multiplied by the attempt number)


_current: ContextVar[Execution | None] = ContextVar("execution", default=None)
_task: ContextVar[str] = ContextVar("execution_task", default="")


def current() -> Execution | None:
    return _current.get()


def current_task() -> str:
    return _task.get()


@contextmanager
def execution_scope(store: ArtifactStore | None, settings: ExecutionConfig | None = None, *, cache: bool | None = None) -> Iterator[Execution]:
    """`cache=None` follows `execution.cache_model_calls`; True / False force it (an evaluation of run-to-run variation forces False)."""
    outer = _current.get()
    if outer is not None:
        yield outer
        return
    settings = settings or ExecutionConfig()
    use_cache = settings.cache_model_calls if cache is None else cache
    exe = Execution(cache=CallCache(store) if store is not None and use_cache else None, retry_pause=settings.retry_pause_seconds)
    token = _current.set(exe)
    try:
        yield exe
    finally:
        _current.reset(token)


@contextmanager
def task_scope(task_id: str) -> Iterator[None]:
    token = _task.set(task_id)
    try:
        yield
    finally:
        _task.reset(token)
