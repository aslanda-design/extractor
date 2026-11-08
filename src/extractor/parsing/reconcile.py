"""S03 - Completeness check: compare the layout parse with the PDF's own text layer.

The layout parser (OpenDataLoader) gives structure but is the only thing that cannot
notice what it dropped. The PDF's text layer (read independently, see
backends/pdfium_text.py) is ground truth for digital pages: if a word is in the PDF, it
must be in some block. This module (pure functions, no I/O) does three things:

    1. measure_coverage   which reference words are NOT inside any parsed block?
    2. group_orphans      glue those orphan words back into lines and paragraphs
    3. insert_in_reading_order  put the recovered blocks where a reader would meet them

`reconcile_page` runs all three. Recovered blocks are flagged `recovered=True` so every
later stage and the inspection report can tell them from layout-parser output.
"""

import re
from collections import Counter
from statistics import median

from ..config.runtime import ParsingConfig
from ..contracts.common import StrictModel
from ..contracts.document import BlockKind
from .geometry import horizontal_overlap
from .raw import BBox, RawBlock, RawPage, RefWord
from .routing import is_garbage

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokens(text: str) -> list[str]:
    """Case/punctuation-insensitive words, the unit coverage is counted in."""
    return _TOKEN.findall(text.casefold())


class Coverage(StrictModel):
    total_words: int  # reference words that contain at least one letter/digit
    covered_words: int
    orphans: list[RefWord] = []  # reference words found in no parsed block

    @property
    def ratio(self) -> float:
        return self.covered_words / self.total_words if self.total_words else 1.0


class ReconcileResult(StrictModel):
    page: RawPage  # the page with recovered blocks inserted (unchanged when nothing was missing)
    coverage: Coverage
    recovered_blocks: int = 0


def reference_is_trusted(words: list[RefWord], config: ParsingConfig) -> bool:
    """A text layer with almost no text or mostly garbage cannot be used as a yardstick."""
    chars = "".join(w.text for w in words)
    if len(chars) < config.min_native_chars:
        return False
    return sum(is_garbage(c) for c in chars) / len(chars) <= config.max_garbage_ratio


# --- 1. coverage ----------------------------------------------------------------

def _contains(bbox: BBox, x: float, y: float, tol: float) -> bool:
    return bbox[0] - tol <= x <= bbox[2] + tol and bbox[1] - tol <= y <= bbox[3] + tol


def _take(counter: Counter, wanted: Counter) -> bool:
    """Consume `wanted` from `counter` if all of it is there."""
    if all(counter[t] >= n for t, n in wanted.items()):
        counter.subtract(wanted)
        return True
    return False


def measure_coverage(page: RawPage, reference: list[RefWord], tolerance: float = 3.0) -> Coverage:
    """Match every reference word to the parsed text, counting each parsed word at most once.

    Pass 1 is geometric: a word is covered when its centre lies inside a block that still
    holds that word. Pass 2 forgives sloppy boxes: a leftover word is covered when ANY
    block still has an unused copy. What remains after both passes is truly missing.
    """
    blocks = [(b.bbox, Counter(tokens(b.text))) for b in page.blocks if b.text.strip()]
    pending: list[tuple[RefWord, Counter]] = []
    total = covered = 0
    for word in reference:
        wanted = Counter(tokens(word.text))
        if not wanted:
            continue  # punctuation-only: nothing a reader could cite
        total += 1
        cx, cy = (word.bbox[0] + word.bbox[2]) / 2, (word.bbox[1] + word.bbox[3]) / 2
        if any(bbox and _contains(bbox, cx, cy, tolerance) and _take(counts, wanted) for bbox, counts in blocks):
            covered += 1
        else:
            pending.append((word, wanted))
    pool: Counter = Counter()
    for _, counts in blocks:
        pool.update({t: n for t, n in counts.items() if n > 0})
    orphans: list[RefWord] = []
    for word, wanted in pending:
        if _take(pool, wanted):
            covered += 1
        else:
            orphans.append(word)
    return Coverage(total_words=total, covered_words=covered, orphans=orphans)


# --- 2. orphan words -> blocks ----------------------------------------------------

def _union(boxes: list[BBox]) -> BBox:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _lines(words: list[RefWord]) -> list[list[RefWord]]:
    """Words on the same baseline, left to right; columns apart on one row become separate lines."""
    # Phase 1: rows by vertical centre only (words of one row arrive in any x order).
    rows: list[list[RefWord]] = []
    for w in sorted(words, key=lambda w: (w.bbox[1] + w.bbox[3]) / 2):
        cy, h = (w.bbox[1] + w.bbox[3]) / 2, w.bbox[3] - w.bbox[1]
        for row in rows:
            ry = median((x.bbox[1] + x.bbox[3]) / 2 for x in row)
            rh = median(x.bbox[3] - x.bbox[1] for x in row)
            if abs(cy - ry) <= 0.5 * min(h, rh):
                row.append(w)
                break
        else:
            rows.append([w])
    # Phase 2: inside a row, left to right, a wide horizontal gap starts a new line (next column).
    lines: list[list[RefWord]] = []
    for row in rows:
        row.sort(key=lambda w: w.bbox[0])
        limit = 3 * median(x.bbox[3] - x.bbox[1] for x in row)
        lines.append([row[0]])
        for prev, w in zip(row, row[1:]):
            if w.bbox[0] - prev.bbox[2] > limit:
                lines.append([w])
            else:
                lines[-1].append(w)
    return lines


def group_orphans(orphans: list[RefWord]) -> list[RawBlock]:
    """Orphan words -> lines -> paragraphs (stacked lines of one column with a small gap)."""
    paragraphs: list[list[list[RefWord]]] = []
    for line in sorted(_lines(orphans), key=lambda ln: min(w.bbox[1] for w in ln)):
        box = _union([w.bbox for w in line])
        height = median(w.bbox[3] - w.bbox[1] for w in line)
        for para in paragraphs:
            pbox = _union([w.bbox for ln in para for w in ln])
            # Character boxes of consecutive lines often overlap vertically, so compare the
            # distance between line centres (the pitch) instead of the gap between boxes.
            pitch = (box[1] + box[3]) / 2 - median((w.bbox[1] + w.bbox[3]) / 2 for w in para[-1])
            if horizontal_overlap(pbox, box) > 0.5 and 0.5 * height <= pitch <= 2.0 * height:
                para.append(line)
                break
        else:
            paragraphs.append([line])
    return [
        RawBlock(kind=BlockKind.PARAGRAPH, recovered=True, parser_id="reference",
                 text=" ".join(w.text for ln in para for w in ln),
                 bbox=_union([w.bbox for ln in para for w in ln]))
        for para in paragraphs
    ]


# --- 3. reading order ---------------------------------------------------------------

def insert_in_reading_order(page: RawPage, new_blocks: list[RawBlock]) -> RawPage:
    """Insert blocks into the page's order: above the next block of their own column.

    Falls back to "after the last block of the column", then to plain vertical position.
    """
    merged = list(page.blocks)
    for block in sorted(new_blocks, key=lambda b: b.bbox[1] if b.bbox else 0):
        if not block.bbox:
            merged.append(block)
            continue
        top = block.bbox[1]
        column = [i for i, b in enumerate(merged) if b.bbox and horizontal_overlap(b.bbox, block.bbox) > 0.5]
        below = [i for i in column if merged[i].bbox[1] > top]
        if below:
            pos = below[0]
        elif column:
            pos = column[-1] + 1
        else:
            pos = next((i for i, b in enumerate(merged) if b.bbox and b.bbox[1] > top), len(merged))
        merged.insert(pos, block)
    return page.model_copy(update={"blocks": merged})


# --- the whole check ------------------------------------------------------------------

def reconcile_page(page: RawPage, reference: list[RefWord], config: ParsingConfig) -> ReconcileResult:
    coverage = measure_coverage(page, reference, config.reconcile_box_tolerance)
    if len(coverage.orphans) < config.reconcile_min_missing_words:
        return ReconcileResult(page=page, coverage=coverage)
    recovered = group_orphans(coverage.orphans)
    return ReconcileResult(page=insert_in_reading_order(page, recovered), coverage=coverage,
                           recovered_blocks=len(recovered))
