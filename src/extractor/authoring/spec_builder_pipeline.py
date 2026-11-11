"""S02 (authoring) - Draft a specification from a plain-language request. The whole feature in one readable file.

    request (+ the current YAML, when refining)
      1. ask       ONE model call, JSON constrained by the engine's task schema (the model never writes YAML syntax)
      2. clean     drop empty values, so the YAML only carries what the model decided
      3. compile   the real S02 compiler is the judge (same checks as a run)
      4. repair    if it does not compile: ONE targeted call that lists the exact errors, thinking on; at most `max_repairs`
      5. write     Python writes the YAML; REVIEW findings and the model's assumptions come back as warnings

Common case = one call, no document, no thinking. Every call goes through `call_model` (cache, retry by failure kind, meter, guard).
Nothing here runs an extraction: the draft is text the user reads and edits before anything runs.
"""

import json
from dataclasses import dataclass, field

import yaml

from ..adapters.base import ExtractorAdapter
from ..contracts.generation import ModelPrompt
from ..contracts.specification import IssueSeverity, SpecIssue
from ..events import emit, step
from ..extraction.calling import call_model
from ..spec import SpecificationError, compile_specification, parse_specification
from .spec_guide import SYSTEM, response_schema

STAGE = "S02"
MAX_REQUEST_CHARS = 4000


@dataclass
class SpecDraft:
    yaml: str  # the specification text ("" when the model produced nothing usable)
    valid: bool  # it compiles: it can be run as it is
    errors: list[dict] = field(default_factory=list)  # compile errors still present (when not valid)
    warnings: list[str] = field(default_factory=list)  # the model's assumptions and tasks flagged for review
    calls: int = 0  # model calls made (1 = no repair needed)
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return {"yaml": self.yaml, "valid": self.valid, "errors": self.errors, "warnings": self.warnings, "calls": self.calls, "seconds": round(self.seconds, 2)}


def draft_specification(request: str, model: ExtractorAdapter, current_yaml: str | None = None, retries: int = 1, max_repairs: int = 1) -> SpecDraft:
    request = (request or "").strip()
    if not request:
        raise ValueError("describe what you want to extract")
    if len(request) > MAX_REQUEST_CHARS:
        raise ValueError(f"the description is too long ({len(request)} characters; the limit is {MAX_REQUEST_CHARS})")
    schema = response_schema()
    draft = SpecDraft(yaml="", valid=False)
    refining = bool((current_yaml or "").strip())

    with step(STAGE, "drafting the specification from your description" + (" (editing the current one)" if refining else "")):
        # 1. ask
        user = _ask_prompt(request, current_yaml if refining else None)
        first = call_model(model, ModelPrompt(system=SYSTEM, user=user, schema_name="specification", json_schema=schema),
                           "draft the specification", retries, think=False, stage=STAGE)
        draft.calls, draft.seconds = 1, first.reply.seconds
        data = first.reply.data
        if data is None:
            emit(STAGE, f"the model gave no usable draft ({first.reply.error})", "error")
            draft.errors = [{"code": "model", "path": "", "message": first.reply.error or "the model gave no answer"}]
            return draft

        repairs = 0
        while True:
            # 2. clean   3. compile
            data = _prune(data)
            text, issues, review = _compile(data)
            emit(STAGE, f"compiled the draft: {len(_tasks(data))} task(s), " + ("no errors" if not issues else f"{len(issues)} error(s)"),
                 "ok" if not issues else "warn")
            if not issues or repairs >= max_repairs:
                break
            # 4. repair
            repairs += 1
            fix = call_model(model, ModelPrompt(system=SYSTEM, user=_repair_prompt(request, data, issues), schema_name="specification", json_schema=schema),
                             f"repair the draft ({len(issues)} error(s))", retries, think=True, stage=STAGE)
            draft.calls += 1
            draft.seconds += fix.reply.seconds
            if fix.reply.data is None:
                emit(STAGE, f"the repair gave no usable answer ({fix.reply.error})", "warn")
                break
            data = fix.reply.data

    # 5. write
    draft.yaml = text
    draft.valid = not issues
    draft.errors = [i.model_dump(mode="json") for i in issues]
    notes = str(data.get("notes") or "").strip()
    draft.warnings = ([notes] if notes else []) + [f"{i.task_id or i.path}: {i.message}" for i in review]
    emit(STAGE, f"draft ready in {draft.calls} model call(s)" + ("" if draft.valid else "; it still has errors you can fix by hand"), "ok" if draft.valid else "warn")
    return draft


def _ask_prompt(request: str, current_yaml: str | None) -> str:
    if current_yaml:
        return (f"CURRENT SPECIFICATION (YAML):\n{current_yaml.strip()}\n\nCHANGE REQUESTED: {request}\n\n"
                "Return the COMPLETE updated specification: keep every task the change does not touch exactly as it is.")
    return f"REQUEST: {request}"


def _repair_prompt(request: str, data: dict, issues: list[SpecIssue]) -> str:
    listed = "\n".join(f"- {i.path}: {i.message}" for i in issues)
    return (f"REQUEST: {request}\n\nYour draft was rejected by the validator:\n{listed}\n\nYOUR DRAFT:\n{json.dumps(data, ensure_ascii=False)}\n\n"
            "Return the corrected COMPLETE specification. Change only what the errors require.")


def _tasks(data: dict) -> list:
    return data.get("tasks") if isinstance(data.get("tasks"), list) else []


def _prune(node):
    """Remove None, empty strings, empty lists and empty objects (False and 0 are answers, so they stay)."""
    if isinstance(node, dict):
        cleaned = {k: _prune(v) for k, v in node.items()}
        return {k: v for k, v in cleaned.items() if v is not None and v != "" and v != [] and v != {}}
    if isinstance(node, list):
        return [_prune(v) for v in node]
    return node


def _compile(data: dict) -> tuple[str, list[SpecIssue], list[SpecIssue]]:
    """(yaml text, error issues, review issues). The YAML is written even when it has errors, so the user can fix it."""
    body = {"version": 1, **{k: data[k] for k in ("name", "tasks") if k in data}}
    try:
        file = parse_specification(body)
        compiled = compile_specification(file)
    except SpecificationError as exc:
        return _dump(body), exc.issues, []
    tasks = [t.model_dump(mode="json", exclude_defaults=True) for t in file.tasks]
    return _dump({"version": 1, "name": file.name, "tasks": tasks}), [], compiled.review_issues


def _dump(body: dict) -> str:
    body = {k: v for k, v in body.items() if v not in (None, "")}
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=110, default_flow_style=False)
