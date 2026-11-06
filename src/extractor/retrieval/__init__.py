"""S06 - Evidence retrieval.

    retrieval_pipeline.py   THE retrieval pipeline in one readable file (start here)
    types.py                request, corpus and tools
    queries.py              task -> search phrasings (+ QueryExpander hook)
    candidates.py           lexical / vector / structural / qualifier candidate lists
    fusion.py               reciprocal rank fusion
    rerank.py               cross-encoder scoring
    selection.py            cut, dedupe, diversity, qualifier slots
    definitions.py          defined terms
    expansion.py            references, definitions, neighbours
    packaging.py            budget, order, roles, gaps
    tools.py                build the tools from the runtime config

Kept import-light: nothing here loads a model until a function is called.
"""
