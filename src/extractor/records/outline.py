"""S09 steps 1-2 helpers - The document as tagged blocks: what the model is shown and how a tag maps back to a block.

Every block of a document gets a stable tag `B<index>` (its position in reading order). The model answers with tags and
the first words of a record; the engine turns them back into blocks and offsets, never trusting a position it cannot verify.
"""

import re
from dataclasses import dataclass

from ..contracts.document import BlockKind, SourceBlock
from ..extraction.quotes import _fold

# text that starts like a numbered / lettered / roman item, a Part, Stage, Article or Section heading
NUMBERED = re.compile(
    r"^\s*(?:\(?\d+(?:\.\d+)*[.)]?\s|\(?[a-zA-Z][.)]\s|\([a-zA-Z]\)|\(?[ivxlcdmIVXLCDM]+[.)]\s|\([ivxlcdmIVXLCDM]+\)|"
    r"(?:Part|Stage|Article|Section|Clause|Schedule)\s+[0-9IVXivx]+)")
PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?\d+(?:\s+of\s+\d+)?\s*$|confidential\s+page\s+\d+", re.I)


@dataclass
class Tagged:
    index: int
    block: SourceBlock

    @property
    def tag(self) -> str:
        return f"B{self.index}"

    def render(self, limit: int | None = None) -> str:
        text = " ".join(self.block.text.split()) if limit else self.block.text
        if limit and len(text) > limit:
            text = text[:limit] + "…"
        page = f"p{self.block.page}" if self.block.page else "p?"
        return f"[{self.tag}] ({page}, {self.block.kind.value}) {text}"


def tag_blocks(blocks: list[SourceBlock]) -> list[Tagged]:
    return [Tagged(i, b) for i, b in enumerate(blocks)]


def tag_index(tag: str, count: int) -> int | None:
    """`B12` -> 12, only when that block exists."""
    m = re.fullmatch(r"B(\d+)", str(tag).strip())
    return int(m.group(1)) if m and int(m.group(1)) < count else None


def is_noise(block: SourceBlock) -> bool:
    """A printed page number or running footer: never part of a record."""
    return bool(PAGE_NUMBER.search(block.text.strip())) and len(block.text) < 80


def looks_like_item(block: SourceBlock) -> bool:
    return block.kind in (BlockKind.HEADING, BlockKind.LIST_ITEM, BlockKind.PARAGRAPH) and bool(NUMBERED.match(block.text)) \
        and len(block.text.strip()) > 12 and not is_noise(block)


def find_offset(text: str, phrase: str) -> int | None:
    """Where `phrase` (the first words of a record, as the model copied them) starts in `text`; None if it cannot be found."""
    haystack, offsets = _fold(text)
    words = phrase.split()
    for n in (len(words), 8, 5, 3):
        needle = _fold(" ".join(words[:n]))[0].strip()
        if len(needle) < 3:
            continue
        at = haystack.find(needle)
        if at >= 0:
            return offsets[at]
    return None


_MARKER_BEFORE = re.compile(r"(?:^|\s)(\(?[0-9A-Za-z]{1,4}[.)]|\([0-9A-Za-z]{1,4}\))\s*(?:\[[^\]]{0,8}\]\s*)?[\"“'‘]?$")


def include_marker(text: str, offset: int) -> int:
    """A record starts at its marker ("b.", "(c)", "4.1"). The model often gives the words AFTER it: move the start back over a marker that
    sits right before the offset (a short numbering token, optionally followed by a redaction mark like [***], never a word)."""
    before = text[max(0, offset - 24):offset]
    m = _MARKER_BEFORE.search(before)
    return offset - (len(before) - m.start(1)) if m else offset


def running_blocks(tagged: list[Tagged]) -> set[int]:
    """Indexes of blocks that are a running header or footer: short text that is printed on several pages ("CONSULTING AGREEMENT",
    "Confidential Page 3 of 7"). When a record crosses a page break these would otherwise end up inside it (and become its "title")."""
    pages_of: dict[str, set] = {}
    for t in tagged:
        text = re.sub(r"\d+", "#", " ".join(t.block.text.split()).casefold())
        if 3 <= len(text) <= 90 and t.block.page:
            pages_of.setdefault(text, set()).add(t.block.page)
    repeated = {text for text, pages in pages_of.items() if len(pages) >= 3}
    return {t.index for t in tagged if re.sub(r"\d+", "#", " ".join(t.block.text.split()).casefold()) in repeated}


def locate_start(text: str, phrase: str, key: str = "") -> tuple[int | None, str]:
    """Where a record starts inside its block, trying in order: the words the model copied, the record's own marker when it occurs once
    in the block, then the stretch of the block that overlaps those words best (OCR, redaction marks and dropped words break exact
    matching). Returns (offset or None, how)."""
    at = find_offset(text, phrase)
    if at is not None:
        return at, "exact"
    marker = re.fullmatch(r"\(?[0-9A-Za-z]{1,4}[.)]|\([0-9A-Za-z]{1,4}\)", key.strip())
    if marker:
        hits = [m.start() for m in re.finditer(r"(?:^|(?<=\s))" + re.escape(key.strip()) + r"(?=\s|$)", text)]
        if len(hits) == 1:
            return hits[0], "marker"
    wanted = re.findall(r"\w+", phrase.casefold())
    if len(wanted) < 3:
        return None, "none"
    words = [(m.start(), m.group(0).casefold()) for m in re.finditer(r"\w+", text)]
    size, best, best_at = len(wanted), 0.0, None
    for i in range(len(words)):
        window = {w for _, w in words[i:i + size + 2]}
        score = sum(w in window for w in wanted) / size
        if score > best and words[i][1] in wanted[:2]:  # the window must open with one of the first words
            best, best_at = score, words[i][0]
    return (best_at, "fuzzy") if best >= 0.6 else (None, "none")
