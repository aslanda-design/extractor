"""ArtifactStore: versioned processing artifacts and cached results on disk.

Layout:   <root>/<kind>/<key>.json     e.g. manifests/ab12....json
          <root>/originals/<sha256>.<ext>   retained source documents

The store never decides whether an artifact is *correct*; callers choose keys
(usually a fingerprint of document hash + processing settings) so a changed
input or setting naturally misses the cache.
"""

import json
import os
import shutil
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from .fingerprint import canonical_json

M = TypeVar("M", bound=BaseModel)


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path_for(self, kind: str, key: str) -> Path:
        return self.root / kind / f"{key}.json"

    def has(self, kind: str, key: str) -> bool:
        return self.path_for(kind, key).is_file()

    def put(self, kind: str, key: str, value: BaseModel | dict[str, Any]) -> Path:
        path = self.path_for(kind, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(canonical_json(payload), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a crash never leaves a half-written artifact
        return path

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        path = self.path_for(kind, key)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def get_model(self, kind: str, key: str, model: type[M]) -> M | None:
        data = self.get(kind, key)
        return None if data is None else model.model_validate(data)

    def retain_original(self, source: Path, sha256: str) -> Path:
        """Keep a copy of the original file so evidence can be inspected later."""
        dest = self.root / "originals" / f"{sha256}{source.suffix.lower()}"
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        return dest
