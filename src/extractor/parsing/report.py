"""Inspection report: each original page next to its parsed blocks (static HTML).

Boxes are drawn from the blocks' own bounding boxes, so what you see is exactly
the page mapping that evidence will use. Hover a block (either side) to
highlight its counterpart. Dashed outlines = text that came from OCR.
"""

import base64
import html
import tempfile
from pathlib import Path

from ..contracts.document import DocumentRepresentation, PageInfo
from .backends.base import PageRenderer

PREVIEW_DPI = 100

_STYLE = """
body{font:14px system-ui,sans-serif;margin:0;background:#f4f4f5;color:#18181b}
header{padding:12px 20px;background:#fff;border-bottom:1px solid #d4d4d8}
.issue{margin:2px 0;padding:4px 8px;border-radius:4px}
.issue.open{background:#fee2e2}.issue.fixed{background:#dcfce7}
/* align-items:flex-start matters: if the sheet stretched to the taller blocks column, the
   percentage-positioned boxes would be scaled against a taller box than the image. */
.page{display:flex;align-items:flex-start;gap:16px;padding:16px 20px;border-bottom:1px solid #d4d4d8}
.sheet{position:sticky;top:8px;flex:0 0 560px}.sheet img{width:100%;display:block;border:1px solid #a1a1aa}
.box{position:absolute;border:1.5px solid #2563eb;background:rgba(37,99,235,.08)}
.box.ocr{border-style:dashed;border-color:#d97706;background:rgba(217,119,6,.08)}
.box.hl{background:rgba(220,38,38,.35);border-color:#dc2626}
.blocks{flex:1;min-width:0}.blocks h3{margin:0 0 6px}
.blk{padding:4px 6px;margin:2px 0;background:#fff;border-left:3px solid #2563eb;cursor:default}
.blk.ocr{border-left-color:#d97706}.blk.hl{background:#fee2e2}
.blk small{color:#71717a}.blk pre{margin:2px 0 0;white-space:pre-wrap;font:inherit}
"""

_SCRIPT = """
document.querySelectorAll('[data-b]').forEach(el=>{
  el.addEventListener('mouseenter',()=>document.querySelectorAll('[data-b="'+el.dataset.b+'"]').forEach(x=>x.classList.add('hl')));
  el.addEventListener('mouseleave',()=>document.querySelectorAll('[data-b="'+el.dataset.b+'"]').forEach(x=>x.classList.remove('hl')));
});
"""


def _page_image(renderer: PageRenderer, source: Path, number: int) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "p.png"
        renderer.render(source, number, PREVIEW_DPI, png)
        return base64.b64encode(png.read_bytes()).decode()


def _page_html(rep: DocumentRepresentation, info: PageInfo, image_b64: str) -> str:
    boxes, rows = [], []
    for b in (b for b in rep.blocks if b.page == info.page):
        ocr = " ocr" if b.from_ocr else ""
        key = html.escape(b.block_id, quote=True)
        if b.bbox:
            l, t, r, bt = b.bbox
            style = (f"left:{l / info.width:.4%};top:{t / info.height:.4%};"
                     f"width:{(r - l) / info.width:.4%};height:{(bt - t) / info.height:.4%}")
            boxes.append(f'<div class="box{ocr}" data-b="{key}" style="{style}"></div>')
        level = f" h{b.heading_level}" if b.heading_level else ""
        rows.append(f'<div class="blk{ocr}" data-b="{key}"><small>{html.escape(b.block_id)} · '
                    f'{b.kind.value}{level}{" · OCR" if b.from_ocr else ""}</small>'
                    f'<pre>{html.escape(b.text)}</pre></div>')
    return (f'<section class="page"><div class="sheet"><img src="data:image/png;base64,{image_b64}">'
            f'{"".join(boxes)}</div><div class="blocks"><h3>Page {info.page} — route: {info.route}'
            f' <small>({html.escape(info.route_reason)})</small></h3>{"".join(rows)}</div></section>')


def build_report(rep: DocumentRepresentation, source: Path, renderer: PageRenderer, title: str = "") -> str:
    issues = "".join(
        f'<div class="issue {"fixed" if i.resolved else "open"}">'
        f'{"resolved" if i.resolved else "UNRESOLVED"} · {i.kind.value} · page {i.page} · '
        f'{html.escape(i.message)}{" · tried " + " → ".join(i.attempts) if i.attempts else ""}</div>'
        for i in rep.issues
    ) or "<div>No parsing issues.</div>"
    pages = "".join(_page_html(rep, p, _page_image(renderer, source, p.page)) for p in rep.pages)
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title or rep.document_id)}</title>"
            f"<style>{_STYLE}</style><header><h2>{html.escape(title or rep.document_id)}</h2>"
            f"<div>{len(rep.pages)} page(s), {len(rep.blocks)} block(s), {len(rep.tables)} table(s)</div>"
            f"{issues}</header>{pages}<script>{_SCRIPT}</script>")


def write_report(rep: DocumentRepresentation, source: Path, renderer: PageRenderer, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_report(rep, source, renderer, source.name), encoding="utf-8")
    return out
