"""S12 - THE RECOVERY PIPELINE, start to finish, in one place.

A task that was not accepted gets a bounded number of targeted second tries. Read `recover_task` (value and interpretation
tasks) top to bottom; `recover_records` is the same loop for a collection. Each numbered step is one call into its own module.

    an attempt that was not accepted (uncertain or failed)
        |  1. plan     actions.py / records_actions.py   which blocking finding calls for which change (cheapest first)
        |  2. budget   budget.py                         attempts, model calls and seconds left for this task
        |  3. act      (the engine's hands, attempt.py)  gather evidence again if needed, then extraction + validation + acceptance AGAIN
        |  4. keep     keeping.py                        the new result stands only if it is better; two attempts that disagree stay uncertain
        |  5. record   contracts/result.RecoveryAttempt  what was tried, what it cost, what was kept and why
        v
    the best attempt + the trail (shown on the task, in the logs and in `eval --recovery`)

Retry is not recovery: sending the same question again gives the same answer. Every action here CHANGES something named
(smaller calls, more evidence, another depth, another region), and its result is judged by the same policy as a first answer.
"""

from ..config.runtime import ExecutionConfig, RecoverySettings
from ..contracts.common import TaskStatus
from ..contracts.result import RecoveryAttempt
from ..contracts.task import TaskSpecification
from ..events import emit, step
from .actions import Planned, plan_actions, unrecoverable
from .attempt import Attempt, RecordsAttempt, RecordsTools, RecoveryTools
from .budget import Budget
from .keeping import keep_new, keep_new_records
from .records_actions import plan_records_actions

DECIDED = (TaskStatus.SUPPORTED, TaskStatus.NOT_FOUND)


def recover_task(
    task: TaskSpecification, first: Attempt, tools: RecoveryTools, recovery: RecoverySettings, execution: ExecutionConfig,
) -> tuple[Attempt, list[RecoveryAttempt], list[str]]:
    """(the attempt that stands, the trail, notes) for a value or interpretation task."""
    best, trail, tried = first, [], set()
    notes = unrecoverable(first)
    budget = Budget.open(recovery, execution, tools.spent)
    while best.status not in DECIDED:
        # 1. Plan: what could change the outcome of the attempt that stands?
        plans = plan_actions(task, best, best.profile, recovery, tools, tried)
        if not plans:
            break

        # 2. Budget: may another attempt start?
        if stop := _stop(task.id, plans[0], budget, best.status, best.found_value, trail):
            notes.append(stop)
            break

        # 3. Act: the engine extracts and validates again, with the change.
        plan = plans[0]
        new, calls, seconds = _run(task.id, plan, budget)
        tried.add(plan.action)

        # 4. Keep the better of the two.
        keep, why = keep_new(best, new) if new is not None else (False, "the attempt raised an error")

        # 5. Record it.
        trail.append(RecoveryAttempt(
            action=plan.action, reason=plan.reason, change=plan.change, status_before=best.status,
            status_after=new.status if new else TaskStatus.FAILED,
            value_before=None, value_after=new.found_value if new else None, kept=keep, why=why, calls=calls, seconds=seconds))
        _report(task.id, trail[-1])
        best = new if keep and new is not None else best
    return best, trail, notes


def recover_records(
    task: TaskSpecification, first: RecordsAttempt, tools: RecordsTools, recovery: RecoverySettings, execution: ExecutionConfig,
) -> tuple[RecordsAttempt, list[RecoveryAttempt], list[str]]:
    """The same loop for a records task: the collection read so far is never given up for a better-looking status."""
    best, trail, tried, notes = first, [], set(), []
    budget = Budget.open(recovery, execution, tools.spent)
    while best.status not in DECIDED:
        # 1. Plan.
        plans = plan_records_actions(best, recovery, tools, tried)
        if not plans:
            break

        # 2. Budget.
        if stop := _stop(task.id, plans[0], budget, best.status, len(best.outcome.records), trail):
            notes.append(stop)
            break

        # 3. Act: read the named regions (or the weak records) again; the audit and the acceptance run again on the result.
        plan = plans[0]
        new, calls, seconds = _run(task.id, plan, budget)
        tried.add(plan.action)

        # 4. Keep the better of the two.
        keep, why = keep_new_records(best, new) if new is not None else (False, "the attempt raised an error")

        # 5. Record it (for a collection, "value" is the number of records).
        trail.append(RecoveryAttempt(
            action=plan.action, reason=plan.reason, change=plan.change, status_before=best.status,
            status_after=new.status if new else TaskStatus.FAILED,
            value_before=len(best.outcome.records), value_after=len(new.outcome.records) if new else None,
            kept=keep, why=why, calls=calls, seconds=seconds))
        _report(task.id, trail[-1])
        best = new if keep and new is not None else best
    return best, trail, notes


# --------------------------------------------------------------------------------------
# Helpers shared by the two loops
# --------------------------------------------------------------------------------------


def _stop(task_id: str, plan: Planned, budget: Budget, status: TaskStatus, value, trail: list[RecoveryAttempt]) -> str | None:
    """When the budget is used up: a trail entry that says so (an explicit unresolved outcome), and the note for the result."""
    why = budget.exhausted()
    if why is None:
        return None
    emit("S12", f"{task_id}: recovery stops, budget exhausted ({why}); '{plan.action}' was not tried", "warn")
    trail.append(RecoveryAttempt(action="none", reason=plan.reason, change=f"'{plan.action}' was the next action", status_before=status,
                                 status_after=status, value_after=None, kept=False, why=f"budget exhausted: {why}"))
    return f"recovery budget exhausted ({why}); '{plan.action}' was not tried"


def _run(task_id: str, plan: Planned, budget: Budget) -> tuple[object | None, int, float]:
    """One attempt, timed and counted. An exception inside an action costs the attempt, never the task's earlier result."""
    budget.attempts += 1
    before = budget.used()
    new = None
    try:
        with step("S12", f"{task_id}: recovery '{plan.action}' ({plan.change})"):
            new = plan.run()
    except Exception as exc:  # noqa: BLE001 - a broken recovery action must not lose the result the task already has
        emit("S12", f"{task_id}: recovery '{plan.action}' raised {type(exc).__name__}: {exc}", "error")
    after = budget.used()
    return new, after[0] - before[0], round(after[1] - before[1], 2)


def _report(task_id: str, attempt: RecoveryAttempt) -> None:
    emit("S12", f"{task_id}: '{attempt.action}' -> {attempt.status_after.value}; {'kept' if attempt.kept else 'not kept'} ({attempt.why}); "
                f"{attempt.calls} call(s), {attempt.seconds:.1f}s", "ok" if attempt.kept else "warn")

