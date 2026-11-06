"""Search view text: normalization that keeps a map back to the source characters.

Normalizing (for retrieval) changes characters and therefore offsets. We never
throw the mapping away: `normalize_with_map` returns, for every character of
the normalized text, the offset of the source character it came from.

Operations (in one pass):
  - Unicode NFKC per character (ligatures "ﬁ" -> "fi", non-breaking spaces, ...)
  - de-hyphenation of words broken at a line end ("agree-\\nment" -> "agreement")
  - runs of whitespace collapse to one space, ends are trimmed
"""

import unicodedata


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Return (normalized, char_map); char_map[i] = source offset of normalized[i]."""
    out: list[str] = []
    char_map: list[int] = []
    pending_space: int | None = None  # source offset of a collapsed whitespace run
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        # word broken by a hyphen at the end of a line: "agree-\nment"
        if ch == "-" and i + 2 < n and text[i + 1] == "\n" and text[i + 2].islower() \
                and out and out[-1].isalpha():
            i += 2
            continue
        for piece in unicodedata.normalize("NFKC", ch):
            if piece.isspace():
                if out and pending_space is None:
                    pending_space = i
            else:
                if pending_space is not None:
                    out.append(" ")
                    char_map.append(pending_space)
                    pending_space = None
                out.append(piece)
                char_map.append(i)
        i += 1
    return "".join(out), char_map


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]
