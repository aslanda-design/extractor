"""S04 - Structure, passages, indexes and provenance.

    chunking_pipeline.py   THE chunking pipeline in one readable file (start here)
    structure.py           heading hierarchy
    continuation.py        cross-page continuations
    units.py               blocks -> units, search-view text
    sizing.py              grouping and size rules
    passages.py            chunks -> Passage objects
    references.py          "Section 5.2" -> heading block
    search_text.py         normalization with a map to the source
    provenance.py          map passage text back to source blocks; invariants
    stores/                lexical + vector index interfaces and implementations
    builder.py             chunking + indexes + cache + coverage for one document

Kept import-light: nothing here loads a model or a database until used.
"""
