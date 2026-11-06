"""HTML view of the chunking result: passage boundaries drawn on the original pages.

Each passage gets a color. Boxes on the page images show which passage every
block ended up in; hovering a box or a card highlights its counterpart.
A block split across passages (long paragraph, table rows) shows the color of
its first passage. Section tree, continuations and references are listed on top.
"""

import html
from pathlib import Path

from ..contracts.document import BlockKind
from ..contracts.passage import IndexedDocument
from ..parsing.backends.base import PageRenderer
from ..parsing.report import _page_image

_STYLE = """
body{font:14px system-ui,sans-serif;margin:0;background:#f4f4f5;color:#18181b}
header{padding:12px 20px;background:#fff;border-bottom:1px solid #d4d4d8}
header h2{margin:0 0 6px} details{margin:6px 0} summary{cursor:pointer;font-weight:600}
.tree div{padding:1px 0} .unres{color:#b91c1c}
/* align-items:flex-start matters: a stretched sheet makes the %-positioned boxes scale against a taller box than the image. */
.page{display:flex;align-items:flex-start;gap:16px;padding:16px 20px;border-bottom:1px solid #d4d4d8}
.sheet{position:relative;flex:0 0 560px}.sheet img{width:100%;display:block;border:1px solid #a1a1aa}
.box{position:absolute;border:2px solid var(--c);background:color-mix(in srgb,var(--c) 14%,transparent)}
.box.hl,.card.hl{outline:3px solid #dc2626}
.cards{flex:1;min-width:0}.card{padding:6px 8px;margin:6px 0;background:#fff;border-left:6px solid var(--c)}
.card small{color:#52525b}.card pre{margin:4px 0 0;white-space:pre-wrap;font:inherit}
.tag{background:#e4e4e7;border-radius:3px;padding:0 4px;margin-left:4px;font-size:12px}
"""
_SCRIPT = """
document.querySelectorAll('[data-p]').forEach(el=>{
  const all=()=>document.querySelectorAll('[data-p="'+el.dataset.p+'"]');
  el.addEventListener('mouseenter',()=>all().forEach(x=>x.classList.add('hl')));
  el.addEventListener('mouseleave',()=>all().forEach(x=>x.classList.remove('hl')));
});
"""


def _color(index: int) -> str:
    return f"hsl({(index * 47) % 360} 70% 42%)"


def _section_tree(doc: IndexedDocument) -> str:
    rows = []
    for b in doc.representation.blocks:
        if b.kind == BlockKind.HEADING:
            rows.append(f'<div style="margin-left:{(len(b.heading_path) - 1) * 18}px">'
                        f'{html.escape(b.text)} <small>(p{b.page})</small></div>')
    return "".join(rows) or "<div>No headings.</div>"


def build_index_report(doc: IndexedDocument, source: Path, renderer: PageRenderer, title: str = "") -> str:
    rep = doc.representation
    order = {p.passage_id: i for i, p in enumerate(doc.passages)}
    block_passage: dict[str, str] = {}
    for p in doc.passages:
        for seg in p.segments:
            block_passage.setdefault(seg.block_id, p.passage_id)

    links = "".join(f"<div>{html.escape(c.from_block_id)} → {html.escape(c.to_block_id)} "
                    f'<span class="tag">{c.kind.value}</span></div>' for c in doc.continuations) or "<div>None.</div>"
    refs = "".join(
        f'<div class="{"" if r.resolved else "unres"}">{html.escape(r.text)} in {html.escape(r.source_block_id)} → '
        f'{html.escape(r.target_block_id) if r.resolved else "UNRESOLVED"}</div>' for r in doc.references) or "<div>None.</div>"

    pages_html = []
    for info in rep.pages:
        boxes = []
        for b in (b for b in rep.blocks if b.page == info.page and b.bbox and b.block_id in block_passage):
            pid = block_passage[b.block_id]
            l, t, r, bt = b.bbox
            boxes.append(f'<div class="box" data-p="{html.escape(pid)}" style="--c:{_color(order[pid])};'
                         f'left:{l / info.width:.4%};top:{t / info.height:.4%};'
                         f'width:{(r - l) / info.width:.4%};height:{(bt - t) / info.height:.4%}"></div>')
        cards = []
        for p in (p for p in doc.passages if info.page in p.pages):
            tags = "".join(f'<span class="tag">{html.escape(x)}</span>' for x in
                           [p.kind.value, *(["continued across pages"] if p.continued_across_pages else []),
                            *([p.note] if p.note else [])])
            cards.append(f'<div class="card" data-p="{html.escape(p.passage_id)}" style="--c:{_color(order[p.passage_id])}">'
                         f'<small>{html.escape(p.passage_id)} · {p.token_count} tokens · pages {p.pages} · '
                         f'{html.escape(" > ".join(p.heading_path) or "(no section)")}</small>{tags}'
                         f'<pre>{html.escape(p.text)}</pre></div>')
        pages_html.append(
            f'<section class="page"><div class="sheet"><img src="data:image/png;base64,'
            f'{_page_image(renderer, source, info.page)}">{"".join(boxes)}</div>'
            f'<div class="cards"><h3>Page {info.page}</h3>{"".join(cards)}</div></section>')

    name = html.escape(title or rep.document_id)
    sizes = [p.token_count for p in doc.passages] or [0]
    return (f"<!doctype html><meta charset=utf-8><title>{name}</title><style>{_STYLE}</style>"
            f"<header><h2>{name}</h2><div>{len(doc.passages)} passage(s) · tokens min/avg/max "
            f"{min(sizes)}/{sum(sizes) // len(sizes)}/{max(sizes)} · embedding model {html.escape(doc.embedding_model)}</div>"
            f'<details open><summary>Sections</summary><div class="tree">{_section_tree(doc)}</div></details>'
            f"<details><summary>Cross-page continuations ({len(doc.continuations)})</summary>{links}</details>"
            f"<details><summary>References ({len(doc.references)})</summary>{refs}</details></header>"
            f'{"".join(pages_html)}<script>{_SCRIPT}</script>')


def write_index_report(doc: IndexedDocument, source: Path, renderer: PageRenderer, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_index_report(doc, source, renderer, source.name), encoding="utf-8")
    return out
