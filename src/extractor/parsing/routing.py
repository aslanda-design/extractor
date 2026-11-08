"""S03 - Decide, per page, whether native text is usable or OCR is needed.

Signals are cheap and deterministic. Thresholds come from `ParsingConfig`.
Region fallback: on a page that stays native, a large picture with no text
over it is OCR'd on its own (e.g. a pasted scan or a stamp with writing).
"""

from enum import StrEnum

from ..config.runtime import ParsingConfig
from ..contracts.common import StrictModel
from .geometry import area, intersection
from .raw import BBox, RawPage

# Characters that signal a broken text layer.
_REPLACEMENT = "�"


def is_garbage(ch: str) -> bool:
    code = ord(ch)
    return (
        ch == _REPLACEMENT
        or 0xE000 <= code <= 0xF8FF  # private use area
        or (code < 32 and ch not in "\t\n\r")  # control characters
    )


class Route(StrEnum):
    NATIVE = "native"
    OCR = "ocr"


class PageSignals(StrictModel):
    chars: int  # non-whitespace characters in the native text layer
    garbage_ratio: float
    image_coverage: float  # share of the page covered by pictures (0..1)


class RouteDecision(StrictModel):
    route: Route
    reason: str
    signals: PageSignals


def compute_signals(page: RawPage) -> PageSignals:
    text = "".join(ch for b in page.blocks for ch in b.text if not ch.isspace())
    garbage = sum(is_garbage(ch) for ch in text)
    page_area = page.width * page.height
    covered = sum(area(b) for b in page.image_boxes)
    return PageSignals(
        chars=len(text),
        garbage_ratio=garbage / len(text) if text else 0.0,
        image_coverage=min(1.0, covered / page_area) if page_area > 0 else 0.0,
    )


def decide_route(page: RawPage, config: ParsingConfig) -> RouteDecision:
    s = compute_signals(page)
    if s.chars < config.min_native_chars:
        return RouteDecision(route=Route.OCR, signals=s,
                             reason=f"no usable text layer ({s.chars} chars < {config.min_native_chars})")
    if s.garbage_ratio > config.max_garbage_ratio:
        return RouteDecision(route=Route.OCR, signals=s,
                             reason=f"corrupted text layer (garbage ratio {s.garbage_ratio:.2f})")
    if s.image_coverage >= config.scan_image_coverage and s.chars <= config.scan_max_chars:
        return RouteDecision(route=Route.OCR, signals=s,
                             reason=f"scanned page (image covers {s.image_coverage:.0%}, {s.chars} chars)")
    return RouteDecision(route=Route.NATIVE, signals=s, reason="usable native text layer")


def regions_needing_ocr(page: RawPage, config: ParsingConfig) -> list[BBox]:
    """Pictures on a native page that are big enough and not already covered by text."""
    page_area = page.width * page.height
    text_boxes = [b.bbox for b in page.blocks if b.bbox and b.text.strip()]
    regions: list[BBox] = []
    for image in page.image_boxes:
        image_area = area(image)
        if page_area <= 0 or image_area / page_area < config.region_min_image_area:
            continue
        covered = sum(intersection(image, t) for t in text_boxes)
        if covered / image_area <= config.region_text_overlap:
            regions.append(image)
    return regions
