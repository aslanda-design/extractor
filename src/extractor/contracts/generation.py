"""What goes into and comes out of a generative model call (extraction, interpretation, verification).

The engine writes the prompt and the JSON schema; the adapter only turns
"prompt + schema" into "JSON" for whatever model or server it wraps.
"""

from .common import StrictModel


class ModelPrompt(StrictModel):
    system: str
    user: str
    schema_name: str = "answer"
    json_schema: dict | None = None  # the reply must follow this JSON schema
    think: bool | None = None  # let the model reason before answering (slower, usually more accurate); None = adapter default


class ModelReply(StrictModel):
    data: dict | None = None  # the parsed JSON object; None when the call failed
    raw: str = ""  # the model's text exactly as returned (kept for inspection)
    error: str | None = None  # why `data` is missing: transport, truncation, invalid JSON...
    seconds: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
