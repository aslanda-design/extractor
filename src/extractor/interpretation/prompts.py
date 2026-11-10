"""S08 - The prompts: what the model is told when it must DECIDE a label, in one readable place.

Nothing here depends on which model answers. The wording follows the architecture's rules for interpretation:
a request is not an approval, absence of approval is not rejection, a condition must not be dropped.
"""

from ..contracts.evidence import EvidencePassage
from ..contracts.generation import ModelPrompt
from ..contracts.task import TaskSpecification
from ..extraction.prompts import PASSAGES_HEADER, render_passage
from .schema import NO_DECISION, interpretation_schema, judge_schema
from .tools import Tool

INTERPRET_SYSTEM = f"""You decide ONE question about a contract, using only the passages you are given and the decision \
criteria. You do not know anything else about the contract.

How to answer:
1. List in "evidence" every quote that bears on the question: for each, the label it supports ("label") and its role. \
Use role "supersedes" for a quote that replaces or overrides earlier wording (an amendment); otherwise "supports". \
A quote is copied exactly from ONE passage: one sentence or clause, 40 words at most, never text from two passages. \
List only quotes that bear directly on THIS question; do not list generic clauses (signatures, amendments in general, \
confidentiality) that do not decide it. The "label" of a quote is the label that quote supports on its own. Include the \
quotes that point to a DIFFERENT label than the one you finally choose when they concern the same question: an exception, \
a condition, a limit, a later amendment. Never leave out an exception or a condition that applies to the rule you rely on.
2. Choose "label": the allowed label whose meaning the evidence satisfies, applying the decision criteria. \
Use "{NO_DECISION}" when no passage states a decision on this question (silence is not "no"): a request is not an \
approval, a payment milestone named after an event is not proof the event happened, a rule is not evidence of what \
happened, and the absence of a decision is not a rejection.
3. A condition or exception that attaches to the answer is part of the answer: if the label set has a label for it \
(for example "conditional"), that label is the answer; never drop the condition to give the simpler label.
4. "justification": two sentences at most, naming the quote(s) the decision rests on and why the criteria lead to that label. \
"conditions": what attaches to the decision (empty if nothing). "unresolved": what you could not settle (empty if nothing).
5. If two passages disagree and the criteria give no rule that decides between them, say so in "unresolved" and choose \
"{NO_DECISION}".
6. Never guess. Never rely on what a quote "probably" means beyond its words. A label whose meaning says "expressly", \
"explicitly" or "actual" needs a quote that says exactly that; a quote that is silent, redacted or only names a topic does not \
satisfy it (then the answer is "{NO_DECISION}")."""

TOOLS_RULES = f"""

Exact calculations: do not do arithmetic yourself. When the decision depends on a calculation with numbers printed in \
the passages, list the calculation in "tool_calls" (tool name and "arguments" as a JSON object written as a string) and \
the engine will run it and give you the result. Report the figures you use as quotes in "evidence". Never state the result \
of a calculation yourself: in that first answer ask for the calculation and put "{NO_DECISION}" as the label."""

FOLLOW_UP = """

TOOL RESULTS (computed by the engine; they are exact):
{results}

Now give your final answer: the same format, with the calculation results used as facts and no new "tool_calls"."""

JUDGE_SYSTEM = f"""You check a decision about a contract. You get the question, the allowed labels, the decision criteria, the \
quotes found in the contract (with the label each supports) and the label that was chosen.

Answer label_correct=false when any of these is true:
- the quotes do not satisfy the meaning of the chosen label;
- a quote states an exception, condition, limit or later amendment that the chosen label ignores;
- the chosen label says an event happened, but the quotes only describe a request, a plan, a duty or a payment trigger;
- the decision criteria point at a different label.
Quotes listed under another label are not automatically contradictions: they count only if they concern the same question and \
situation as asked. A label whose meaning says "expressly" or "explicitly" needs a quote that says so; silence or a redacted text \
does not satisfy it. A permission that needs consent or approval IS the conditional label when the label set has one.
If false, give the label you think is right in "better_label" (or "{NO_DECISION}" if the quotes decide nothing). \
If the chosen label is "{NO_DECISION}" (no quote decides the question), answer label_correct=true only when NO quote satisfies \
the meaning of any allowed label; if one does, answer false and name that label in "better_label".
Judge only from the quotes, the calculations and the criteria. Give one short reason."""


def decision_section(task: TaskSpecification) -> str:
    lines = [f"QUESTION: {task.description}"]
    if task.scope.entity:
        lines.append(f"About: {task.scope.entity}")
    if task.scope.time:
        lines.append(f"Time / period: {task.scope.time}")
    lines.append("ALLOWED LABELS:")
    lines += [f"- {label.name}: {label.meaning}" for label in task.output.labels]
    if task.rules.decision_criteria:
        lines.append("DECISION CRITERIA:")
        lines += [f"- {c}" for c in task.rules.decision_criteria]
    for hint in (task.rules.ambiguity, task.rules.normalization):
        if hint:
            lines.append(f"Note: {hint}")
    lines += [f"Evidence must: {r}" for r in task.rules.evidence_requirements]
    return "\n".join(lines)


def interpretation_prompt(
    task: TaskSpecification, tagged: dict[str, EvidencePassage], max_evidence: int, show_document: bool, tools: list[Tool],
) -> ModelPrompt:
    evidence = "\n\n".join(render_passage(tag, p, show_document) for tag, p in tagged.items())
    system = INTERPRET_SYSTEM + (TOOLS_RULES if tools else "")
    return ModelPrompt(
        system=system, user=f"{PASSAGES_HEADER}\n\n{evidence}\n\n{decision_section(task)}", schema_name="interpretation",
        json_schema=interpretation_schema(task, list(tagged), max_evidence, tools))


def follow_up_prompt(first: ModelPrompt, tool_results: list[dict]) -> ModelPrompt:
    """The same question again with the engine's tool results appended, so the model decides with exact numbers."""
    lines = []
    for r in tool_results:
        outcome = r.get("result") if "result" in r else f"error: {r.get('error')}"
        lines.append(f"- {r['tool']}({r.get('arguments')}) = {outcome}")
    return first.model_copy(update={"user": first.user + FOLLOW_UP.format(results="\n".join(lines))})


def judge_prompt(task: TaskSpecification, label: str, stances: list, headings: dict[str, list[str]], tool_results: list[dict]) -> ModelPrompt:
    listing = []
    for s in stances:
        where = " > ".join(headings.get(s.passage_id or "", [])) or "no section"
        listing.append(f"- supports '{s.label}'{' (supersedes earlier wording)' if s.role == 'supersedes' else ''}: "
                       f"\"{s.quote}\" [{where}]")
    calculations = ""
    if tool_results:
        calculations = "\n\nCALCULATIONS (exact, computed by the engine: trust them over your own arithmetic):\n" + "\n".join(
            f"- {r['tool']}({r.get('arguments')}) = {r.get('result', 'error: ' + str(r.get('error')))}" for r in tool_results)
    return ModelPrompt(
        system=JUDGE_SYSTEM, schema_name="judgement", json_schema=judge_schema([x.name for x in task.output.labels]),
        user=f"{decision_section(task)}\n\nQUOTES FOUND IN THE CONTRACT:\n" + "\n".join(listing) + calculations + f"\n\nCHOSEN LABEL: {label}")
