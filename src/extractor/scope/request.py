"""DocumentRequest: the user-facing description of which documents to process.

Either built from CLI paths or loaded from a small YAML file:

    documents:
      - path: loan.pdf
        sections: ["Article 5"]            # optional restriction
      - path: amendment_1.pdf
        relationships:
          - {kind: amendment, target: loan.pdf}
    as_of: 2026-01-01                      # optional time scope
    precedence_rules:                      # optional, copied verbatim
      - "Amendments override the base agreement."
"""

from datetime import date
from pathlib import Path

import yaml

from ..contracts.common import StrictModel
from ..contracts.manifest import RelationshipKind


class RelationshipRequest(StrictModel):
    kind: RelationshipKind
    target: str  # filename (or path) of another document in this request


class DocumentRequestItem(StrictModel):
    path: Path
    sections: list[str] = []
    relationships: list[RelationshipRequest] = []


class DocumentRequest(StrictModel):
    documents: list[DocumentRequestItem]
    as_of: date | None = None
    precedence_rules: list[str] = []

    @classmethod
    def from_paths(cls, paths: list[Path]) -> "DocumentRequest":
        return cls(documents=[DocumentRequestItem(path=p) for p in paths])


def load_document_request(path: Path) -> DocumentRequest:
    """Load a request YAML. Relative document paths resolve against the YAML's folder."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    request = DocumentRequest.model_validate(data)
    resolved = [
        item.model_copy(update={"path": item.path if item.path.is_absolute() else path.parent / item.path})
        for item in request.documents
    ]
    return request.model_copy(update={"documents": resolved})
