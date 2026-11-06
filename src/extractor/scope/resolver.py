"""S01 - Resolve a task's document references against the manifest.

A task's `scope.documents` may list filenames or document ids. An empty list
means the task sees every document in the request scope.
"""

from ..contracts.manifest import DocumentManifest
from ..contracts.task import TaskSpecification


class ScopeResolutionError(ValueError):
    pass


def resolve_task_documents(task: TaskSpecification, manifest: DocumentManifest) -> list[str]:
    """Return the document ids a task may read, in manifest order."""
    in_scope = set(manifest.scope.document_ids)
    if not task.scope.documents:
        return list(manifest.scope.document_ids)

    by_ref = {}
    for doc in manifest.documents:
        if doc.document_id in in_scope:
            by_ref[doc.document_id] = doc.document_id
            by_ref[doc.filename] = doc.document_id

    wanted: set[str] = set()
    for ref in task.scope.documents:
        if ref not in by_ref:
            raise ScopeResolutionError(
                f"task '{task.id}' refers to document '{ref}', which is not in the request scope"
            )
        wanted.add(by_ref[ref])
    return [d for d in manifest.scope.document_ids if d in wanted]
