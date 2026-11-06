"""Small bbox helpers (pure functions)."""

from .raw import BBox


def area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def intersection(a: BBox, b: BBox) -> float:
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def horizontal_overlap(a: BBox, b: BBox) -> float:
    """Shared width as a fraction of the narrower box (0 when they don't overlap)."""
    shared = min(a[2], b[2]) - max(a[0], b[0])
    narrow = min(a[2] - a[0], b[2] - b[0])
    return max(0.0, shared) / narrow if narrow > 0 else 0.0


def scale(b: BBox, factor: float, dx: float = 0.0, dy: float = 0.0) -> BBox:
    """Scale then translate; used to map pixel coordinates back to points."""
    return (b[0] * factor + dx, b[1] * factor + dy, b[2] * factor + dx, b[3] * factor + dy)
