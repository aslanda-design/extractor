"""Builds the preloaded sample: a FICTIONAL service agreement PDF, then one real engine run on it.

Writes to apps/web/public/sample/: service-agreement.pdf, spec.yaml, result.json, blocks.json, logs.json.
Run from the repo root with the engine venv:   .venv\Scripts\python.exe frontend\scripts\build_sample.py
Nothing here is real: the parties, dates and amounts are invented. The result IS a real run (qwen via Ollama).
"""

import json
import sys
import time
from pathlib import Path

import yaml
from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "frontend" / "apps" / "web" / "public" / "sample"
sys.path.insert(0, str(ROOT / "ui"))

TEXT = [
    ("SERVICE AGREEMENT", "title"),
    ("Fictional sample document. All parties, dates and amounts are invented.", "note"),
    ("1. Parties and Date", "h"),
    ("This Service Agreement (the \"Agreement\") is entered into on 14 March 2026 by and between Harborline "
     "Analytics Ltd., a company registered in Atlantis (the \"Provider\"), and Mercer & Vale Foods Inc., a company "
     "registered in Elbonia (the \"Client\").", "p"),
    ("2. Services", "h"),
    ("The Provider will deliver demand-forecasting and reporting services to the Client as described in the "
     "Statement of Work attached as Schedule A (the \"Services\").", "p"),
    ("3. Approval of the Statement of Work", "h"),
    ("The Client's procurement committee approves the Statement of Work, provided that the Provider delivers a "
     "signed data-protection addendum before any work starts and that total fees do not exceed the budget set out "
     "in Schedule A.", "p"),
    ("4. Obligations of the Provider", "h"),
    ("4.1 The Provider shall deliver a written forecast report within five (5) business days after the end of each "
     "calendar month.", "p"),
    ("4.2 The Provider shall maintain professional liability insurance for the whole term of this Agreement.", "p"),
    ("5. Obligations of the Client", "h"),
    ("5.1 The Client shall pay each undisputed invoice within thirty (30) days after receiving it.", "p"),
    ("5.2 The Client shall give the Provider read-only access to the sales data systems named in Schedule A.", "p"),
    ("6. Confidentiality", "h"),
    ("Each party shall keep the other party's Confidential Information secret and shall use it only to perform this "
     "Agreement.", "p"),
    ("7. Term and Termination", "h"),
    ("This Agreement starts on the date in clause 1 and continues until terminated. Either party may terminate it "
     "for material breach that remains uncured after written notice.", "p"),
    ("8. General", "h"),
    ("This Agreement is governed by the laws of Atlantis. Amendments must be in writing and signed by both parties.", "p"),
]

SPEC = """version: 1
name: sample service agreement
tasks:
- id: agreement_date
  kind: value
  description: The date on which the agreement is entered into.
  output: {type: date, format: ISO-8601}
  rules:
    normalization: Convert to YYYY-MM-DD.
    on_absent: not_found
- id: sow_approval
  kind: interpretation
  description: Whether the Client approved the Statement of Work.
  output:
    labels:
    - {name: approved, meaning: Approval is granted without conditions.}
    - {name: conditional, meaning: Approval is granted subject to stated conditions.}
    - {name: rejected, meaning: Approval is explicitly refused.}
    - {name: pending, meaning: The decision is explicitly still open.}
  rules:
    decision_criteria:
    - Never drop a condition when approval is conditional.
- id: obligations
  kind: records
  description: Every obligation a party commits to in the agreement.
  record:
    identity: One numbered obligation.
    boundaries: An obligation starts at its number and ends before the next number or heading.
    fields:
    - id: party
      kind: value
      description: The party that carries the obligation (Provider or Client).
- id: liability_cap
  kind: value
  description: The maximum total liability amount the agreement states, as a number.
  output: {type: number}
  rules:
    normalization: Return the number only, without currency symbol.
    on_absent: not_found
"""


def make_pdf(path: Path) -> None:
    pdf = FPDF(format="A4")
    pdf.set_margins(22, 22, 22)
    pdf.add_page()
    for text, kind in TEXT:
        if kind == "title":
            pdf.set_font("Helvetica", "B", 20)
            pdf.cell(0, 12, text, new_x="LMARGIN", new_y="NEXT")
        elif kind == "note":
            pdf.set_font("Helvetica", "I", 9)
            pdf.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)
        elif kind == "h":
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_font("Helvetica", "", 11)
            pdf.multi_cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))


def main() -> None:
    from extractor.events import LOG
    from service import Workspace, public_event

    OUT.mkdir(parents=True, exist_ok=True)
    pdf_path = OUT / "service-agreement.pdf"
    make_pdf(pdf_path)
    (OUT / "spec.yaml").write_text(SPEC, encoding="utf-8")

    ws = Workspace(ROOT / ".extractor_artifacts" / "sample_workspace")
    base = yaml.safe_load((ROOT / ".extractor_artifacts" / "workspace" / "runtime.yaml").read_text(encoding="utf-8"))
    base["models"]["interpretation"] = base["models"]["extraction"]  # a real model, not the fake one
    ws.set_config(base)
    ws.save_upload("service-agreement.pdf", pdf_path.read_bytes())
    ws.save_spec("sample.yaml", SPEC)

    start = LOG.last_seq()
    began = time.time()
    blocks = ws.blocks("service-agreement.pdf")
    result = ws.run("sample.yaml", ["service-agreement.pdf"])
    result.pop("manifest", None)
    result["recorded"] = {"at": time.strftime("%Y-%m-%d"), "seconds": round(time.time() - began, 1),
                          "model": base["models"]["extraction"]["params"].get("model")}
    (OUT / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "blocks.json").write_text(json.dumps(blocks, ensure_ascii=False), encoding="utf-8")
    events = [public_event(e) for e in LOG.since(start)]
    (OUT / "logs.json").write_text(json.dumps(events, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"done: {len(result['results'])} results, {len(events)} log events, {time.time() - began:.0f}s")


if __name__ == "__main__":
    main()
