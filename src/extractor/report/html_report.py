"""S13 - THE RUN REPORT: one self-contained HTML file that says what the engine answered and why.

No scripts, no external files, no network: it can be emailed, archived or opened offline. Every string that comes from a document, a
model or a person goes through `escape`, so a document cannot inject markup into the report. Reviews are shown NEXT TO the engine's
answer (the answer a person ends up with, and what the engine said), never instead of it.

    render_report(report, reviewed=None) -> str     reviewed = `review.reviewed_view(...)` items, when there are reviews
"""

import json
import re
from html import escape

from ..contracts.result import RunReport, TaskResult
from ..contracts.review import ReviewedItem

BLOCK = re.compile(r"^(?P<doc>.+?):p(?P<page>\d+):b\d+$")
STYLE = """
:root{--bg:#fff;--fg:#1d1f23;--muted:#5d6470;--line:#d8dce2;--ok:#14633f;--okbg:#e5f4ec;--warn:#7a4b00;--warnbg:#fdf0d8;--bad:#8a1c1c;--badbg:#fbe4e4;--card:#f6f7f9}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--fg:#e8eaee;--muted:#9aa2af;--line:#333943;--ok:#7fd1a5;--okbg:#17372a;--warn:#e9bf73;--warnbg:#3b2d12;--bad:#f1a1a1;--badbg:#431e1e;--card:#1e2126}}
*{box-sizing:border-box}body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,sans-serif}
main{max-width:960px;margin:0 auto}h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.05rem;margin:0}code,.mono{font-family:ui-monospace,Consolas,monospace;font-size:.85em}
.muted{color:var(--muted)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}
.pill{display:inline-block;border-radius:99px;padding:1px 10px;font-size:.8rem;font-weight:600}.supported{background:var(--okbg);color:var(--ok)}
.not_found,.uncertain{background:var(--warnbg);color:var(--warn)}.failed{background:var(--badbg);color:var(--bad)}.human{background:var(--okbg);color:var(--ok)}
table{border-collapse:collapse;width:100%;font-size:.88rem;margin:8px 0}th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600}.kpis{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0}.kpi{border:1px solid var(--line);border-radius:8px;padding:8px 14px;min-width:120px}
.kpi b{display:block;font-size:1.4rem}blockquote{margin:6px 0;padding:4px 12px;border-left:3px solid var(--line);color:var(--muted)}
.head{display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}
"""
ORDER = {"failed": 0, "uncertain": 1, "not_found": 2, "supported": 3}


def esc(value: object) -> str:
    return escape("" if value is None else value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str), quote=True)


def pill(status: str, text: str | None = None) -> str:
    return f'<span class="pill {esc(status)}">{esc((text or status).replace("_", " "))}</span>'


def render_report(report: RunReport, reviewed: list[ReviewedItem] | None = None) -> str:
    by_task = {i.target.label(): i for i in reviewed or [] if i.target.record_key is None}
    extra = [i for i in reviewed or [] if i.target.record_key is not None]
    s, e = report.summary, report.execution
    cards = "\n".join(_task(report, t, by_task.get(t.task_id), [i for i in extra if i.target.task_id == t.task_id])
                      for t in sorted(report.results, key=lambda r: ORDER.get(r.status.value, 9)))
    where = ", ".join(e.remote) if e.remote else "every model ran on this machine"
    models = "".join(f"<tr><td>{esc(k)}</td><td class='mono'>{esc(v)}</td></tr>" for k, v in e.models.items())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Extraction report {esc(report.run_id)}</title><style>{STYLE}</style></head><body><main>
<h1>Extraction report</h1>
<p class="muted">Run <code>{esc(report.run_id)}</code> · {esc(report.created_at)} · {esc(report.specification or 'unnamed specification')} · engine {esc(e.engine_version)}</p>
<div class="kpis">{"".join(f'<div class="kpi"><b>{n}</b>{pill(k)}</div>' for k, n in sorted(s.statuses.items(), key=lambda kv: ORDER.get(kv[0], 9)))}
<div class="kpi"><b>{s.total.calls}</b>model calls ({s.total.cached} cached)</div><div class="kpi"><b>{s.seconds:.0f}s</b>wall clock</div>
<div class="kpi"><b>{s.recovery_attempts}</b>recovery attempts ({s.recovered} improved)</div></div>
<div class="card"><h2>Where your documents went</h2><p>{esc(where)}</p>
<details><summary class="muted">Models, versions and fingerprints</summary><table>{models}
<tr><td>documents</td><td class="mono">{esc(", ".join(f"{e.document_names.get(d, d)} {h[:12]}" for d, h in e.document_hashes.items()))}</td></tr>
<tr><td>specification</td><td class="mono">{esc(e.schema_hash[:16])}</td></tr><tr><td>settings</td><td class="mono">{esc(e.runtime_fingerprint[:16])}</td></tr>
<tr><td>stage versions</td><td class="mono">{esc(e.stage_versions)}</td></tr></table></details></div>
{cards}
<p class="muted">An answer is <b>supported</b> only when the engine's acceptance policy accepted it: its quote was found in the source and no blocking
finding remained. Reviews are shown next to the engine's answer and never replace it in the run.</p></main></body></html>"""


def _task(report: RunReport, t: TaskResult, item: ReviewedItem | None, others: list[ReviewedItem]) -> str:
    head = f'<div class="head"><h2><code>{esc(t.task_id)}</code> <span class="muted">{esc(t.kind.value)}</span></h2><span>{pill(t.status.value)}' \
           f'{" " + _human(item) if item and item.source == "human" else ""}</span></div>'
    return f'<section class="card">{head}{_answer(t, item)}{_evidence(report, t)}{_why(t)}{_recovery(t)}{_reviews(item, others)}</section>'


def _human(item: ReviewedItem) -> str:
    return f'<span class="pill human">reviewed: {esc(item.decision.value if item.decision else "")}</span>'


def _answer(t: TaskResult, item: ReviewedItem | None) -> str:
    out = ""
    if t.kind.value == "records":
        fields = sorted({f.field_id for r in t.records for f in r.fields}, key=lambda x: x)
        rows = "".join(f"<tr><td class='mono'>{esc(r.key)}</td>" + "".join(
            f"<td>{pill(f.status.value) if f.status.value != 'supported' else ''} {esc(f.value)}</td>" if (f := next((x for x in r.fields if x.field_id == fid), None)) else "<td></td>"
            for fid in fields) + "</tr>" for r in t.records)
        out += f"<p>{len(t.records)} record(s), completeness <b>{esc(t.completeness.value if t.completeness else '')}</b>. {esc(t.scope_note)}</p>" \
               f"<table><tr><th>Key</th>{''.join(f'<th>{esc(f)}</th>' for f in fields)}</tr>{rows}</table>"
    elif t.value is not None:
        out += f"<p><b>{esc(t.value)}</b></p>"
    elif t.alternatives:
        out += f"<p class='muted'>Proposed, not accepted: {esc(' | '.join(json.dumps(a, ensure_ascii=False, default=str) for a in t.alternatives))}</p>"
    if item is not None and item.source == "human":
        out += f"<p>Reviewer's answer: {pill(item.status.value)} <b>{esc(item.value)}</b> <span class='muted'>(the engine said {esc(item.engine_status.value)}" \
               f"{': ' + json.dumps(item.engine_value, ensure_ascii=False, default=str) if item.engine_value is not None else ''})</span></p>"
    return out


def _evidence(report: RunReport, t: TaskResult) -> str:
    if not t.evidence:
        return ""
    rows = []
    for link in t.evidence[:12]:
        m = BLOCK.match(link.ref.block_id)
        name = report.execution.document_names.get(link.ref.document_id, link.ref.document_id)
        page = f" p.{m.group('page')}" if m else ""
        rows.append(f"<tr><td>{esc(link.role.value)}</td><td>{esc(name)}{esc(page)}</td>"
                    f"<td>{f'<blockquote>{esc(link.quote)}</blockquote>' if link.quote else ''}<span class='mono muted'>{esc(link.ref.block_id)}</span></td></tr>")
    more = f"<p class='muted'>and {len(t.evidence) - 12} more source block(s)</p>" if len(t.evidence) > 12 else ""
    return f"<h3 class='muted'>Evidence</h3><table><tr><th>Role</th><th>Where</th><th>Source</th></tr>{''.join(rows)}</table>{more}"


def _why(t: TaskResult) -> str:
    if t.acceptance is None:
        return ""
    reasons = "".join(f"<li>{esc(r)}</li>" for r in t.acceptance.reasons[:5])
    blocking = "".join(f"<tr><td class='mono'>{esc(f.category)}</td><td>{esc(f.message)}</td></tr>" for f in t.acceptance.blocking[:10])
    return (f"<h3 class='muted'>Why this status ({esc(t.acceptance.policy)} policy)</h3><ul>{reasons}</ul>" if reasons else "") + \
           (f"<table><tr><th>Blocking finding</th><th>Message</th></tr>{blocking}</table>" if blocking else "")


def _recovery(t: TaskResult) -> str:
    if not t.recovery:
        return ""
    rows = "".join(f"<tr><td class='mono'>{esc(a.action)}</td><td>{esc(a.change)}</td><td>{esc(a.status_before.value)} to {esc(a.status_after.value)}</td>"
                   f"<td>{'kept' if a.kept else 'not kept'}: {esc(a.why)}</td><td>{a.calls} / {a.seconds:.0f}s</td></tr>" for a in t.recovery)
    return f"<h3 class='muted'>Second tries</h3><table><tr><th>Action</th><th>What changed</th><th>Status</th><th>Result</th><th>Calls / time</th></tr>{rows}</table>"


def _reviews(item: ReviewedItem | None, others: list[ReviewedItem]) -> str:
    rows = [i for i in [item, *others] if i is not None and i.decision is not None]
    if not rows:
        return ""
    body = "".join(f"<tr><td class='mono'>{esc(i.target.label())}</td><td>{esc(i.decision.value)}{' (stale: not applied)' if i.stale else ''}</td>"
                   f"<td>{esc(i.value if i.decision.value == 'correct' else '')}</td><td>{esc(i.note)}</td></tr>" for i in rows)
    return f"<h3 class='muted'>Reviews</h3><table><tr><th>Target</th><th>Decision</th><th>Value</th><th>Note</th></tr>{body}</table>"
