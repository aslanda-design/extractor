"""The run meter: how many model calls, tokens and seconds each stage and each task used.

Every model call goes through `extraction/calling.py::call_model`, which reports here. The meter only counts; it never
changes a result. Recovery (S12) reads it to know what an attempt cost, and the run ends with a `RunSummary` made from it.
"""

import threading

from ..contracts.generation import ModelReply
from ..contracts.result import StageCost


class RunMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stages: dict[str, dict] = {}
        self._tasks: dict[str, dict] = {}

    def add(self, stage: str, task_id: str, reply: ModelReply) -> None:
        with self._lock:
            for bucket in (self._stages.setdefault(stage, _empty()), self._tasks.setdefault(task_id, _empty()) if task_id else None):
                if bucket is None:
                    continue
                if reply.cached:
                    bucket["cached"] += 1
                    continue
                bucket["calls"] += 1
                bucket["failed"] += reply.data is None
                bucket["prompt_tokens"] += reply.prompt_tokens or 0
                bucket["completion_tokens"] += reply.completion_tokens or 0
                bucket["seconds"] += reply.seconds

    def task(self, task_id: str) -> StageCost:
        with self._lock:
            return _cost(self._tasks.get(task_id, _empty()))

    def stages(self) -> dict[str, StageCost]:
        with self._lock:
            return {name: _cost(b) for name, b in sorted(self._stages.items())}

    def tasks(self) -> dict[str, StageCost]:
        with self._lock:
            return {name: _cost(b) for name, b in self._tasks.items()}

    def total(self) -> StageCost:
        with self._lock:
            buckets = list(self._stages.values())
        sums = _empty()
        for b in buckets:
            for k in sums:
                sums[k] += b[k]
        return _cost(sums)


def _empty() -> dict:
    return {"calls": 0, "cached": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}


def _cost(bucket: dict) -> StageCost:
    return StageCost(**{**bucket, "seconds": round(bucket["seconds"], 2)})
