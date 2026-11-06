"""S04 - THE CHUNKING PIPELINE, start to finish, in one place.

Read `chunk_document` top to bottom: each numbered step is one call, and each
call lives in its own small module (named in the comment). To change how
documents are cut, edit a step here or the helper it points to.

    DocumentRepresentation (blocks from the parser)
        |  1. hierarchy      structure.py     headings -> heading_path / parent_id
        |  2. continuations  continuation.py  paragraphs / tables spanning a page break
        |  3. units          units.py         blocks (+ joined continuations) -> units
        |  4. search view    units.py         normalized text + map back to the source
        |  5. sections       sizing.py        group units under their section
        |  6. size rules     sizing.py        merge tiny groups, cut big ones
        |  7. passages       passages.py      chunks -> Passage objects, neighbours linked
        |  8. references     references.py    "see Section 5.2" -> heading block
        v
    IndexedDocument (passages + links; stores/embeddings are added by builder.py)

Everything is deterministic and uses no model except `count_tokens`, the
embedding model's tokenizer, which is how passage size is measured.
"""

from typing import Callable

from ..config.runtime import ChunkingConfig
from ..contracts.document import DocumentRepresentation
from ..contracts.passage import IndexedDocument
from .continuation import find_continuations
from .passages import link_neighbours, make_passage
from .references import find_references
from .sizing import cut_into_chunks, group_into_sections, merge_small_groups
from .structure import assign_hierarchy
from .units import build_units, normalize_units


def chunk_document(
    representation: DocumentRepresentation,
    config: ChunkingConfig,
    count_tokens: Callable[[str], int],
) -> IndexedDocument:
    document_id = representation.document_id

    # 1. Headings -> sections. Every block gets heading_path and parent_id.
    blocks = assign_hierarchy(representation.blocks, config)
    representation = representation.model_copy(update={"blocks": blocks})
    blocks_by_id = {b.block_id: b for b in blocks}

    # 2. Find paragraphs and tables that continue on the next page.
    continuations = find_continuations(blocks, representation.tables) if config.link_continuations else []

    # 3. Blocks (with continuations joined) -> units: headings, paragraphs, tables.
    units = build_units(blocks, representation.tables, continuations, config)

    # 4. Search view: normalized text for every piece, with a map back to the source.
    normalize_units(units)

    # 5. Group units by section; every table is its own group.
    groups = group_into_sections(units, count_tokens)

    # 6. Size rules: tiny groups merge with a neighbour, big ones are cut
    #    at paragraph / sentence boundaries, long tables split by rows.
    groups = merge_small_groups(groups, config, count_tokens)
    chunks = cut_into_chunks(groups, config, count_tokens)

    # 7. Chunks -> passages (text + exact source ranges), neighbours linked.
    passages = [make_passage(document_id, n, chunk, blocks_by_id, count_tokens)
                for n, chunk in enumerate(chunks, start=1)]
    passages = link_neighbours(passages)

    # 8. Explicit references ("see Section 5.2") -> the heading they point to.
    references = find_references(blocks) if config.resolve_references else []

    return IndexedDocument(
        document_id=document_id,
        representation=representation,
        passages=passages,
        continuations=continuations,
        references=references,
    )
