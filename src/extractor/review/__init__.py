"""S13 - Review: corrections kept apart from predictions.

    reviewing.py     `make_review` (snapshot of the prediction, a decision that makes sense for it), `reviewed_view` (what a person ends up with)
    store.py         `ReviewStore`: append-only, next to the runs in the artifact store, never inside them
    labels.py        `corrections_as_labels`: reviewed decisions as the `expected:` section of a corpus case
    predictions.py   `locate` / `prediction_of`: what the engine said about a task, a record or a field, fingerprinted
"""

from .labels import corrections_as_labels
from .predictions import UnknownTarget, locate, prediction_of
from .reviewing import make_review, reviewed_view
from .store import ReviewStore

__all__ = ["ReviewStore", "UnknownTarget", "corrections_as_labels", "locate", "make_review", "prediction_of", "reviewed_view"]
