"""S09 - What a record key looks like: the family of numbering it belongs to ((a) (b); 4.1(iii); Stage 4; Part II).

Used by the audit (a sequence must not skip a member; numbered text of the records' own family must not be left uncovered) and by the
discovery clean-up (a key of another family than the rest is probably not a record of this task).
"""

import re

ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _roman(text: str) -> int | None:
    if not text or any(c not in ROMAN for c in text):
        return None
    total = 0
    for a, b in zip(text, text[1:] + " "):
        total += -ROMAN[a] if b in ROMAN and ROMAN[a] < ROMAN[b] else ROMAN[a]
    return total


def sequence_part(key: str) -> tuple[str, str, int | None] | None:
    """(prefix, token, integer value if the token is a number) for the LAST component of a key such as '4.1(iii)', 'Stage 4', '(c)'."""
    m = re.search(r"\(?([A-Za-z]+|\d+)[).]?\s*$", key.strip())
    if not m:
        return None
    prefix = re.sub(r"[\s(),:;]+", "", key[:m.start()].casefold()).rstrip(".")
    token = m.group(1).casefold()
    return (prefix, token, int(token)) if token.isdigit() else (prefix, token, None)


def _to_roman(value: int) -> str:
    out = ""
    for sym, n in (("m", 1000), ("cm", 900), ("d", 500), ("cd", 400), ("c", 100), ("xc", 90), ("l", 50), ("xl", 40), ("x", 10), ("ix", 9), ("v", 5), ("iv", 4), ("i", 1)):
        while value >= n:
            out += sym
            value -= n
    return out


def _shape(key: str) -> tuple[str, str] | None:
    """(prefix, kind) of a key's sequence part: the family of numbering it belongs to."""
    part = sequence_part(key)
    if not part:
        return None
    prefix, token, number = part
    if number is not None:
        return prefix, "number"
    if _roman(token) and (len(token) > 1 or token in "ivx"):
        return prefix, "roman"
    return (prefix, "letter") if len(token) == 1 else None


_KIND_GROUP = {"number": "number", "letter": "letter", "roman": "letter"}  # a single i, v, x is as likely a letter as a roman numeral


def family(key: str) -> str | None:
    """The kind of numbering a key uses, ignoring its prefix: '(c)' and '2.1(a)' are both 'letter', 'Stage 4' and '5.' are 'number'.
    None for keys that are names (a country, a party): no numbering rule applies to them."""
    shape = _shape(key)
    return _KIND_GROUP[shape[1]] if shape else None


def keep_family_chain(keys: list[str], anchor: str | None = None) -> int:
    """How many of the listed keys, from the start, belong to ONE numbering family (the family of `anchor`, or of the first key that has one).

    A list of lettered definitions that is followed by '2.0', '3.0'... is not one list: the model wandered into the next section. Names
    (family None) never break a chain."""
    wanted = family(anchor) if anchor else None
    for n, key in enumerate(keys):
        fam = family(key)
        if fam is None:
            continue
        if wanted is None:
            wanted = fam
        elif fam != wanted:
            return n
    return len(keys)


def _dotted(key: str) -> tuple[int, ...] | None:
    m = re.match(r"\s*(\d+(?:\.\d+)*)\.?(?![\d(])", key)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def nested_keys(keys: list[str]) -> set[int]:
    """Positions of keys that are sub-items of another listed key ('3.1' under '3', '9.2' under '9') when most keys are at the shallower
    level. A list of top-level clauses is not a list of every numbered sub-clause: the nested ones belong to their parent."""
    parsed = [_dotted(k) for k in keys]
    depths = [len(p) for p in parsed if p]
    if len(depths) < 4:
        return set()
    shallow = min(depths)
    if depths.count(shallow) < 0.5 * len(depths):
        return set()
    tops = {p for p in parsed if p and len(p) == shallow}
    return {i for i, p in enumerate(parsed) if p and len(p) > shallow and p[:shallow] in tops}
