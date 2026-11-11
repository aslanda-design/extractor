"""The scheduler: engine wiring of the portable concurrency core (`concurrency.py`).

Two separate settings of `execution:` decide how much runs side by side:

  fan_out     how many pieces of work of each kind are in flight (tasks, retrieval, batches, evidence parts, record windows,
              records). `max_concurrency` is the default of every level. With 1 nothing is threaded: the work runs in order,
              exactly as before, which keeps a local run and every test deterministic.
  resources   how many calls each BACKEND serves at once (`gpu: 1`, `llm_api: 8`). Every model is assigned to a resource
              (`assign:`, default a resource named after the model). A call to the model holds one slot of its resource, so
              models on the same resource share its capacity (a local GPU: embedder, reranker and LLM take turns), while a
              model on its own resource (a hosted LLM) runs as wide as that resource allows.

The capacity of a resource is the SMALLER of what you declare and what every adapter on it can serve (`max_parallel`): one local
GPU serves one call at a time, a hosted API several. The adapter has the last word. Embedding and reranking adapters have no
`max_parallel` and are assumed to serve one call at a time unless they say otherwise.

Dependencies are respected by the caller: it hands over one dependency layer at a time (`spec.graph.layers`), and the tasks
inside a layer do not depend on each other. Parallelism never changes an answer: it is not part of the run's identity.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

from ..config.runtime import ExecutionConfig
from ..events import emit
from .concurrency import Resources, current_resources, fan_out, slot, use_resources

run_parallel = fan_out  # name used by older callers: `run_parallel(items, work, limit)`

_routes: ContextVar[dict[int, str] | None] = ContextVar("execution_routes", default=None)
_settings: ContextVar[ExecutionConfig | None] = ContextVar("execution_settings", default=None)


@dataclass
class Plan:
    """What `plan_parallelism` decided: the capacity of each resource, which resource each model uses, and what was capped."""

    capacities: dict[str, int] = field(default_factory=dict)
    routes: dict[int, str] = field(default_factory=dict)  # id(adapter) -> resource name
    members: dict[str, list[str]] = field(default_factory=dict)  # resource -> the models that share it
    capped: list[str] = field(default_factory=list)


def plan_parallelism(settings: ExecutionConfig, adapters) -> Plan:
    """Resource capacities from the settings and the adapters of the run (`adapters` is an `AdapterSet`)."""
    chosen = [("embedding", adapters.embedder), ("reranking", adapters.reranker), ("extraction", adapters.extractor),
              ("interpretation", adapters.interpretation_model), ("verification", adapters.verifier)]
    if getattr(adapters, "escalation_model", None) is not None:
        chosen.append(("escalation", adapters.escalation_model))
    plan = Plan()
    limits: dict[str, int] = {}
    for capability, adapter in chosen:
        if id(adapter) in plan.routes:  # the same model serves two capabilities (interpretation falls back to extraction)
            continue
        name = settings.resource_of(capability)
        plan.routes[id(adapter)] = name
        plan.members.setdefault(name, []).append(capability)
        limits[name] = min(limits.get(name, 1 << 30), max(1, int(getattr(adapter, "max_parallel", 1))))
    for name, backend in limits.items():
        declared = settings.resources.get(name)
        plan.capacities[name] = min(declared, backend) if declared is not None else backend
        if declared is not None and declared > backend:
            plan.capped.append(f"{name}: {declared} allowed, {backend} served by {', '.join(plan.members[name])}")
    return plan


@contextmanager
def parallelism_scope(settings: ExecutionConfig, adapters) -> Iterator[Plan | None]:
    """Opens the resources of a run (what `call_slot` takes slots from). A scope inside another one reuses the outer one."""
    if current_resources() is not None:
        yield None
        return
    plan = plan_parallelism(settings, adapters)
    token, widths_token = _routes.set(plan.routes), _settings.set(settings)
    try:
        with use_resources(Resources(plan.capacities)):
            yield plan
    finally:
        _routes.reset(token)
        _settings.reset(widths_token)


def width(level: str) -> int:
    """How many pieces of work of this kind may be in flight, for the run in progress (1 outside a run: in order).
    Stages deep in the pipeline (evidence parts, discovery windows, record fields) ask here instead of receiving a setting."""
    settings = _settings.get()
    return settings.width(level) if settings is not None else 1


def resource_name(adapter) -> str:
    """The resource a model's calls take a slot from ('' = none declared: no limit)."""
    return (_routes.get() or {}).get(id(adapter), "")


@contextmanager
def call_slot(adapter) -> Iterator[None]:
    """Hold a slot of the model's resource around ONE call to it (`generate`, `embed`, `score`)."""
    with slot(resource_name(adapter)):
        yield


def describe(settings: ExecutionConfig, plan: Plan) -> list[str]:
    """Plain-language lines about the parallelism of a run, for the log."""
    widths = ", ".join(f"{level} {settings.width(level)}" for level in ("tasks", "retrieval", "batches", "evidence_parts", "record_windows", "records"))
    resources = "; ".join(f"{name} {n} ({', '.join(plan.members[name])})" for name, n in plan.capacities.items())
    return [f"parallelism: {widths}", f"resources: {resources}"]


def announce(settings: ExecutionConfig, plan: Plan | None) -> None:
    if plan is None:
        return
    for line in describe(settings, plan):
        emit("S05", line)
    for line in plan.capped:
        emit("S05", f"resource capped by the model's own limit: {line}", "warn")


def report(resources: Resources | None, plan: Plan | None) -> dict:
    """Per-resource numbers of a finished run (`capacity`, `peak` in flight, `calls`, `wait_seconds`), plus who shares it."""
    if resources is None:
        return {}
    stats = resources.stats()
    return {name: {**s, "models": (plan.members.get(name, []) if plan else [])} for name, s in stats.items()}
