"""S03 - Parsing: native text + OCR routing, issue detection, retries.

    routing.py       page signals -> native or OCR (+ picture regions to OCR)
    detectors.py     empty / corrupted / reading-order / table problems
    parsing_pipeline.py  THE PIPELINE in one file: numbered steps per document and per page
    reconcile.py     completeness check: layout parse vs the PDF text layer, restores dropped words
    page_ocr.py      OCR of a page/crop and the OCR retry ladder
    normalize.py     block ids and public SourceBlocks
    layered_parser.py  the ParserAdapter wiring real backends (registered as "layered")
    backends/        OpenDataLoader (layout), Docling (OCR), pdfium (rendering + text-layer reference)
    report.py        HTML inspection report
    metrics.py       proxy quality metrics against the labeled corpus

Kept import-light on purpose: heavy libraries load only when a backend runs.
"""
