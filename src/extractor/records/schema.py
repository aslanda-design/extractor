"""S09 - The JSON schemas the model must answer with."""


def scope_schema(block_count: int) -> dict:
    tag = {"type": "string", "pattern": r"^B\d+$"}
    return {"type": "object", "properties": {"first_block": tag, "last_block": tag, "reason": {"type": "string"}},
            "required": ["first_block", "last_block", "reason"]}


def discover_schema(tags: list[str], max_records: int) -> dict:
    record = {"type": "object",
              "properties": {"key": {"type": "string"}, "start_block": {"type": "string", "enum": tags},
                             "starts_with": {"type": "string"}, "end_block": {"type": "string", "enum": tags}},
              "required": ["key", "start_block", "starts_with", "end_block"]}
    return {"type": "object", "properties": {"records": {"type": "array", "items": record, "maxItems": max_records},
                                             "notes": {"type": "string"}},
            "required": ["records", "notes"]}
