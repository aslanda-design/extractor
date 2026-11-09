"""History of everything the UI ran (parse, chunk/index, search, retrieve, extract, run, corpus measures).

Each run is one JSON file in `<workspace>/history/`, written by `Workspace` through the `recorded` decorator, so
usage can be tracked and any earlier result opened again after a reload or restart. Logging never changes a result:
this module only stores what the engine already returned.
"""

import json
from datetime import datetime
from pathlib import Path

# kind -> label shown in the History tab, and the "group" the Corpus tab filters on
KINDS = {
    "parse": "Parse",
    "index": "Chunk & index",
    "search": "Index search",
    "retrieve": "Retrieve evidence",
    "extract": "Extract value",
    "run": "Run spec",
    "corpus.parsing": "Corpus · parsing",
    "corpus.indexing": "Corpus · chunking",
    "corpus.extraction": "Corpus · extraction",
    "corpus.acceptance": "Corpus · acceptance",
}


def _pct(v) -> str:
    return "n/a" if v is None else f"{round(v * 100)}%"


def _short(text, n: int = 90) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def subject(kind: str, params: dict) -> str:
    """What the run was about, in one line (document, query, spec...)."""
    if kind in ("parse", "index"):
        return params.get("name", "")
    if kind == "search":
        return f"{params.get('name', '')} · “{_short(params.get('query'), 60)}”"
    if kind == "retrieve":
        return f"“{_short(params.get('text'), 60)}” · {', '.join(params.get('documents') or [])}"
    if kind == "extract":
        return f"“{_short((params.get('form') or {}).get('text'), 60)}” · {', '.join(params.get('documents') or [])}"
    if kind == "run":
        return f"{params.get('spec_name', '')} · {', '.join(params.get('documents') or [])}"
    if kind in ("corpus.extraction", "corpus.acceptance"):
        cases = params.get("cases") or []
        kinds = params.get("kinds") or ["value"]
        tags = params.get("tags") or []
        return f"{len(cases) if cases else 'all'} project(s) · {'+'.join(kinds)}" + (f" · {'+'.join(tags)}" if tags else "")
    return "all cases" if kind.startswith("corpus.") else ""


def headline(kind: str, result: dict | None) -> str:
    """The one-line outcome shown in the list."""
    if not result:
        return ""
    r = result
    try:
        if kind == "parse":
            ocr = sum(1 for p in r["pages"] if p["route"] == "ocr")
            open_issues = sum(1 for i in r["issues"] if not i["resolved"])
            return f"{len(r['pages'])} page(s), {ocr} via OCR, {open_issues} unresolved issue(s)"
        if kind == "index":
            return f"{len(r['passages'])} passage(s), {len(r['references'])} reference(s)"
        if kind == "search":
            return f"{len(r['lexical'])} lexical / {len(r['vector'])} vector hit(s)"
        if kind == "retrieve":
            return f"{len(r['package'])} passage(s) in the package, {len(r['gaps'])} gap(s), profile {r['profile']}"
        if kind == "extract":
            value = f" = {json.dumps(r['value'], ensure_ascii=False)}" if r.get("value") is not None else ""
            return _short(f"{r['outcome']}{value}")
        if kind == "run":
            counts: dict[str, int] = {}
            for x in r["results"]:
                counts[x["status"]] = counts.get(x["status"], 0) + 1
            return ", ".join(f"{n} {s}" for s, n in counts.items()) or "no tasks"
        if kind == "corpus.parsing":
            return f"quote recall {_pct(r['quote_recall'])}, right page {_pct(r['page_accuracy'])}"
        if kind == "corpus.indexing":
            return f"quotes in one passage {_pct(r['containment'])}"
        if kind == "corpus.acceptance":
            row = (r.get("rows") or [None])[0]
            if not row:
                return "no tasks"
            return (f"{row['tasks']} tasks: automation {_pct(row['automation_rate'])}, accepted precision {_pct(row['accepted_precision'])}, "
                    f"false accepts {row['counts'].get('false_accept', 0)}, false absences {row['counts'].get('false_absence', 0)}")
        if kind == "corpus.extraction":
            row = (r.get("rows") or [None])[0]
            if not row:
                return "no tasks"
            share = lambda k: _pct(row["counts"].get(k, 0) / row["tasks"]) if row["tasks"] else "n/a"
            return f"{row['tasks']} tasks: correct {share('correct')}, false absence {share('false_absence')}, false value {share('false_value')}"
    except (KeyError, TypeError, IndexError):
        pass
    return ""


class RunHistory:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def record(self, kind: str, params: dict, settings: dict, started: datetime, status: str,
               result: dict | None = None, error: str | None = None) -> str:
        """Write one run; returns its id. `status` is `ok` or `error` (the job started but raised)."""
        finished = datetime.now()
        run_id = f"{started:%Y%m%d-%H%M%S}-{started.microsecond // 1000:03d}-{kind.replace('.', '-')}"
        record = {
            "run_id": run_id, "kind": kind, "label": KINDS.get(kind, kind), "status": status,
            "started_at": started.isoformat(timespec="seconds"), "finished_at": finished.isoformat(timespec="seconds"),
            "seconds": round((finished - started).total_seconds(), 1),
            "params": params, "settings": settings, "subject": subject(kind, params),
            "headline": headline(kind, result) if status == "ok" else _short(error, 120),
            "error": error, "result": result,
        }
        (self.folder / f"{run_id}.json").write_text(json.dumps(record, ensure_ascii=False, default=str), encoding="utf-8")
        return run_id

    def list(self, kind: str | None = None, group: str | None = None) -> list[dict]:
        """Summaries (no result), newest first. `group` is a kind prefix such as `corpus.`."""
        out = []
        for path in sorted(self.folder.glob("*.json"), reverse=True):
            try:
                r = json.loads(path.read_text(encoding="utf-8"))
                if (kind and r["kind"] != kind) or (group and not r["kind"].startswith(group)):
                    continue
                out.append({k: v for k, v in r.items() if k != "result"})
            except (OSError, ValueError, KeyError):
                continue  # a damaged file must not hide the others
        return out

    def get(self, run_id: str) -> dict | None:
        path = self.folder / f"{run_id}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def delete(self, run_id: str) -> None:
        (self.folder / f"{run_id}.json").unlink(missing_ok=True)

    def clear(self, kind: str | None = None, group: str | None = None) -> int:
        runs = self.list(kind, group)
        for r in runs:
            self.delete(r["run_id"])
        return len(runs)

    def import_legacy(self, legacy: Path) -> None:
        """One-time COPY of the first, corpus-only history format (`corpus_history/`) into this one.

        The old files are never deleted or modified, so a restart (or a failed import) cannot lose history; a marker
        file stops the copy from running again, so runs you delete here do not come back."""
        marker = self.folder / ".legacy_imported"
        if not legacy.is_dir() or marker.exists():
            return
        for path in legacy.glob("*.json"):
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
                kind = "corpus." + old["measure"]
                started = datetime.fromisoformat(old["finished_at"])
                run_id = f"{started:%Y%m%d-%H%M%S}-000-{kind.replace('.', '-')}"
                settings = {"retrieval_profile": old["settings"].get("retrieval_profile"),
                            "extraction": old["settings"].get("extraction_model"), "parsing": old["settings"].get("parsing")}
                record = {"run_id": run_id, "kind": kind, "label": KINDS.get(kind, kind), "status": "ok",
                          "started_at": old["finished_at"], "finished_at": old["finished_at"], "seconds": None,
                          "params": old["params"], "settings": settings, "subject": subject(kind, old["params"]),
                          "headline": headline(kind, old["result"]), "error": None, "result": old["result"]}
                target = self.folder / f"{run_id}.json"
                if not target.exists():
                    target.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            except (OSError, ValueError, KeyError):
                continue
        marker.write_text("legacy corpus_history copied; originals left untouched\n", encoding="utf-8")
