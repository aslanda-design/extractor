"""S04 step 2 - Detect content that continues across a page break.

Only the LAST content block of a page and the FIRST content block of the next
page are compared (page headers / footers, kind OTHER, are skipped).

  paragraph  the last one has no sentence-ending punctuation and the next one
             starts in lowercase (or the last one ends with a hyphen)
  table      both are tables with the same number of columns

Source blocks are never merged; the link is recorded and passages are built
over the chain later.
"""

from ..contracts.document import BlockKind, SourceBlock, SourceTable
from ..contracts.passage import Continuation, ContinuationKind

_SENTENCE_END = ".!?:;\"'”’)]"
_CONTENT = {BlockKind.PARAGRAPH, BlockKind.LIST_ITEM, BlockKind.TABLE, BlockKind.HEADING}


def _columns(table: SourceTable | None) -> int:
    return max((c.col for c in table.cells), default=-1) + 1 if table else 0


def _is_paragraph_continuation(last: SourceBlock, first: SourceBlock) -> bool:
    if last.kind not in (BlockKind.PARAGRAPH, BlockKind.LIST_ITEM) or first.kind != BlockKind.PARAGRAPH:
        return False
    tail, head = last.text.rstrip(), first.text.lstrip()
    if not tail or not head:
        return False
    return tail.endswith("-") or (tail[-1] not in _SENTENCE_END and head[0].islower())


def find_continuations(blocks: list[SourceBlock], tables: list[SourceTable]) -> list[Continuation]:
    by_id = {t.block_id: t for t in tables}
    pages: dict[int, list[SourceBlock]] = {}
    for b in blocks:
        if b.kind in _CONTENT and b.page is not None:
            pages.setdefault(b.page, []).append(b)

    links: list[Continuation] = []
    for page in sorted(pages):
        following = pages.get(page + 1)
        if not following:
            continue
        last, first = pages[page][-1], following[0]
        if last.kind == BlockKind.TABLE and first.kind == BlockKind.TABLE:
            if _columns(by_id.get(last.block_id)) == _columns(by_id.get(first.block_id)) > 0:
                links.append(Continuation(from_block_id=last.block_id, to_block_id=first.block_id,
                                          kind=ContinuationKind.TABLE))
        elif _is_paragraph_continuation(last, first):
            links.append(Continuation(from_block_id=last.block_id, to_block_id=first.block_id,
                                      kind=ContinuationKind.PARAGRAPH))
    return links
