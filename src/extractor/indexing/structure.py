"""S04 step 1 - Heading hierarchy: fill `heading_path` and `parent_id` on every block.

Depth of a heading comes from, in order:
  1. its numbering   "1." -> 1, "1.2" -> 2, "1.2.3" -> 3, "Article 5" / "Schedule A" -> 1
     (only when the parser's levels are flat or missing; see `levels_are_flat`)
  2. the parser's heading level
  3. 1

A heading's own `heading_path` includes itself and its `parent_id` is the
enclosing heading. Content blocks get the path of the section they sit in and
that heading as `parent_id`. Blocks before the first heading get an empty path.
"""

import re

from ..config.runtime import ChunkingConfig
from ..contracts.document import BlockKind, SourceBlock

_NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s")
_TOP_LEVEL_WORDS = re.compile(r"^\s*(article|part|chapter|schedule|appendix|annex|exhibit)\b", re.IGNORECASE)
_SUB_LEVEL_WORDS = re.compile(r"^\s*(section|clause)\s+\d+(?:\.\d+)*", re.IGNORECASE)


def numbering_depth(title: str) -> int | None:
    """Depth implied by the heading text, or None when it has no recognizable numbering."""
    m = _NUMBERED.match(title)
    if m:
        return m.group(1).count(".") + 1
    if _TOP_LEVEL_WORDS.match(title):
        return 1
    if _SUB_LEVEL_WORDS.match(title):
        return 2
    return None


def levels_are_flat(blocks: list[SourceBlock]) -> bool:
    """True when the parser gives (almost) no usable hierarchy: one level or none."""
    levels = {b.heading_level for b in blocks if b.kind == BlockKind.HEADING}
    levels.discard(None)
    return len(levels) <= 1


def heading_depth(block: SourceBlock, flat: bool, config: ChunkingConfig) -> int:
    if config.infer_depth_from_numbering and flat:
        depth = numbering_depth(block.text)
        if depth is not None:
            return depth
    return block.heading_level or 1


def assign_hierarchy(blocks: list[SourceBlock], config: ChunkingConfig) -> list[SourceBlock]:
    """Return copies of `blocks` (same order) with heading_path / parent_id filled in."""
    flat = levels_are_flat(blocks)
    stack: list[tuple[int, str, str]] = []  # (depth, heading block id, title)
    result: list[SourceBlock] = []
    for block in blocks:
        if block.kind == BlockKind.HEADING:
            depth = heading_depth(block, flat, config)
            while stack and stack[-1][0] >= depth:
                stack.pop()
            parent = stack[-1][1] if stack else None
            stack.append((depth, block.block_id, " ".join(block.text.split())))
            result.append(block.model_copy(update={
                "heading_path": [title for _, _, title in stack], "parent_id": parent}))
        else:
            result.append(block.model_copy(update={
                "heading_path": [title for _, _, title in stack],
                "parent_id": stack[-1][1] if stack else None}))
    return result
