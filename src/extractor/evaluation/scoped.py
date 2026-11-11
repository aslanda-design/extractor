"""Helpers every corpus measurement shares: the execution scope, the split of a case, and the spread of repeated runs.

  measured     decorator: the measurement runs inside ONE execution scope, so finished model calls are cached (an interrupted
               35-minute measurement resumes instead of starting again) and `cache=False` measures real run-to-run variation.
  split_of     the `split:train|validation|test` tag of a case. Fixes are designed on train and validation; the test split is
               reported separately and its errors are not read when designing a fix (otherwise the number stops meaning anything).
  spread       min / mean / max of a metric over repeated runs: a difference smaller than the spread is noise, not a result.
"""

from functools import wraps
from statistics import mean

from ..execution import execution_scope
from .corpus import EvalCase

NO_SPLIT = "no split"


def measured(evaluate):
    """`evaluate(corpus, config, adapters, store, ..., cache=None)`: the same function, run inside one execution scope."""

    @wraps(evaluate)
    def run(corpus, config, adapters, store, *args, cache: bool | None = None, **kwargs):
        with execution_scope(store, config.execution, cache=cache):
            return evaluate(corpus, config, adapters, store, *args, **kwargs)

    return run


def split_of(case: EvalCase) -> str:
    return next((t.split(":", 1)[1] for t in case.tags if t.startswith("split:")), NO_SPLIT)


def spread(runs: list[dict[str, float | None]]) -> str:
    """One line per metric: every run's value, then min / mean / max. `runs` is one dict of metrics per repeated run."""
    lines = [f"{len(runs)} run(s) without the call cache (real run-to-run variation):"]
    for name in runs[0]:
        values = [r[name] for r in runs if r.get(name) is not None]
        if not values:
            continue
        shown = "  ".join(f"{v:.1%}" if 0 <= v <= 1 else f"{v:g}" for v in values)
        lines.append(f"  {name:<22}{shown}   min {min(values):.3g}  mean {mean(values):.3g}  max {max(values):.3g}  spread {max(values) - min(values):.3g}")
    lines.append("  A change smaller than the spread of a metric is noise: do not count it as an improvement.")
    return "\n".join(lines)
