"""Helpers shared by the chat-model adapters (HTTP call, reading the model's JSON)."""

import json
import re
import urllib.error
import urllib.request

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def post_json(url: str, body: dict, headers: dict[str, str], timeout: float) -> dict:
    """POST JSON, return the JSON answer. Raises ChatError with the server's message on failure."""
    request = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise ChatError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:500]}", status=e.code) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ChatError(f"cannot reach {url}: {e}") from e


class ChatError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def read_json_object(text: str) -> dict:
    """The JSON object in a model's text (ignores <think> blocks and ``` fences). Raises ValueError."""
    text = _THINK.sub("", text).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("the reply contains no JSON object")
        data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("the reply is JSON but not an object")
    return data


def schema_instruction(schema: dict) -> str:
    """Used when the server cannot enforce a schema: ask for it in words."""
    return ("Reply with ONE JSON object and nothing else, following exactly this JSON schema:\n"
            + json.dumps(schema, ensure_ascii=False))
