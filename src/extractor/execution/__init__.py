"""Execution support (S05 scheduling side): how the work of a run is carried out, never what it decides.

    scope.py       `execution_scope(...)`: the cache and the meter every model call of a run reports to; `task_scope(id)`
    call_cache.py  a finished model call is never paid twice (resume after a crash, cheap re-measurement)
    meter.py       calls, tokens and seconds per stage and per task -> `RunSummary`
    scheduler.py   independent work in a bounded thread pool, dependency layer by dependency layer
"""

from .call_cache import CallCache
from .meter import RunMeter
from .scheduler import parallel_limit, run_parallel
from .scope import Execution, current, execution_scope, task_scope

__all__ = ["CallCache", "Execution", "RunMeter", "current", "execution_scope", "parallel_limit", "run_parallel", "task_scope"]
