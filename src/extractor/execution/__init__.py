"""Execution support (S05 scheduling side): how the work of a run is carried out, never what it decides.

    scope.py        `execution_scope(...)`: the cache and the meter every model call of a run reports to; `task_scope(id)`
    call_cache.py   a finished model call is never paid twice (resume after a crash, cheap re-measurement)
    meter.py        calls, tokens and seconds per stage and per task -> `RunSummary`
    concurrency.py  portable core (standard library only): named resources with a capacity, and a bounded `fan_out`
    scheduler.py    engine wiring: `execution.fan_out` widths, `execution.resources` / `assign`, `call_slot(model)`
"""

from .call_cache import CallCache
from .concurrency import Resources, current_resources, fan_out, slot, use_resources
from .meter import RunMeter
from .scheduler import Plan, announce, call_slot, parallelism_scope, plan_parallelism, report, run_parallel, width
from .scope import Execution, current, execution_scope, task_scope

__all__ = ["CallCache", "Execution", "Plan", "Resources", "RunMeter", "announce", "call_slot", "current", "current_resources", "execution_scope",
           "fan_out", "parallelism_scope", "plan_parallelism", "report", "run_parallel", "slot", "task_scope", "use_resources", "width"]
