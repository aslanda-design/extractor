"""Stable hashes used for document identity and cache keys.

A fingerprint is the SHA-256 of a canonical JSON rendering, so it only changes
when the content or settings that matter change (not key order, not comments).
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _to_plain(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    raise TypeError(f"cannot fingerprint {type(obj).__name__}")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_to_plain)


def fingerprint(*parts: Any) -> str:
    """Hash any mix of strings, numbers, dicts, lists and pydantic models."""
    return hashlib.sha256(canonical_json(list(parts)).encode("utf-8")).hexdigest()
