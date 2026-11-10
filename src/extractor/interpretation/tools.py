"""S08 - Tools the engine runs FOR the model (exact work a language model does badly).

A model cannot be trusted with arithmetic, so when it needs a calculation it asks for one and the engine
computes it. A tool is a plain definition: a name, a description and a JSON schema for its arguments, the
same shape an MCP server publishes (`Tool.as_mcp()`), plus the function that runs it.

    in-process tools (this file)        ->  `register_tool(Tool(...))`
    tools served by an MCP server       ->  an adapter that lists the server's tools and registers each one as a
                                            `Tool` whose `run` calls the server (not built yet: see PROGRESS.md)

Which tools a task may use is the runtime config (`interpretation.tools`), never the YAML. A tool never sees
the document and never decides anything: it returns facts, and the model (or the engine) uses them.
"""

import ast
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict  # JSON schema of the arguments (as an MCP tool publishes it)
    run: Callable[[dict], dict]  # raises ToolError on bad arguments

    def as_mcp(self) -> dict:
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


class ToolError(ValueError):
    """The arguments were unusable (the model gets this back as the tool's result)."""


# --- numbers and arithmetic ------------------------------------------------------------------------------------------


def _number(text) -> Decimal:
    cleaned = re.sub(r"[,$€£\s]", "", str(text))
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise ToolError(f"'{text}' is not a number") from None


_OPERATORS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}


def _evaluate(node) -> Decimal:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _evaluate(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        try:
            return _OPERATORS[type(node.op)](_evaluate(node.left), _evaluate(node.right))
        except ArithmeticError as e:
            raise ToolError(f"cannot compute: {e}") from None
    raise ToolError("only numbers and + - * / ( ) are allowed")


def _calculate(arguments: dict) -> dict:
    expression = re.sub(r"(?<=\d),(?=\d{3}\b)", "", str(arguments.get("expression", ""))).replace("$", "").replace("x", "*").replace("×", "*")
    try:
        value = _evaluate(ast.parse(expression, mode="eval"))
    except SyntaxError:
        raise ToolError(f"'{arguments.get('expression')}' is not an arithmetic expression") from None
    return {"expression": expression.strip(), "value": format(value.normalize(), "f")}


def _compare(arguments: dict) -> dict:
    a, b = _number(arguments.get("a")), _number(arguments.get("b"))
    return {"a": format(a.normalize(), "f"), "b": format(b.normalize(), "f"), "equal": a == b, "difference": format((a - b).normalize(), "f")}


TOOLS: dict[str, Tool] = {}


def register_tool(tool: Tool) -> None:
    TOOLS[tool.name] = tool


register_tool(Tool(
    "calculate", "Exact arithmetic on numbers printed in the document: + - * / and parentheses (for example '1990000 * 0.0001').",
    {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}, _calculate))
register_tool(Tool(
    "compare_numbers", "Say whether two numbers are exactly equal, and by how much they differ.",
    {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]}, _compare))


# --- running what the model asked for -------------------------------------------------------------------------------------


def available_tools(names: list[str]) -> list[Tool]:
    unknown = [n for n in names if n not in TOOLS]
    if unknown:
        raise ValueError(f"unknown interpretation tool(s) {unknown} (registered: {sorted(TOOLS)})")
    return [TOOLS[n] for n in names]


def run_tool_calls(requested: list, tools: list[Tool], limit: int) -> list[dict]:
    """Run what the model asked for (at most `limit` calls). Every call returns a record, errors included."""
    by_name = {t.name: t for t in tools}
    results = []
    for item in requested[:limit]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool") or "")
        raw = item.get("arguments")
        record: dict = {"tool": name, "arguments": raw}
        try:
            if name not in by_name:
                raise ToolError(f"no tool named '{name}' is available")
            arguments = raw if isinstance(raw, dict) else json.loads(raw or "{}")
            if not isinstance(arguments, dict):
                raise ToolError("the arguments must be a JSON object")
            record["result"] = by_name[name].run(arguments)
        except (ToolError, ValueError) as e:  # json errors are ValueErrors
            record["error"] = str(e)
        results.append(record)
    return results
