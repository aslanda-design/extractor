"""Command line entry point:  python -m extractor <command>

    compile   validate a YAML spec and show its tasks / execution order
    manifest  hash documents and show the S01 manifest
    parse     parse one document (S03) and write JSON + an HTML inspection report
    index     chunk + index one document (S04); print sections/passages, write an HTML view
    run       run the pipeline (stubs for stages not implemented yet)
    eval      validate the evaluation corpus and (optionally) score the engine
"""

import argparse
import sys
from pathlib import Path

from .adapters import build_adapters
from .artifacts import ArtifactStore
from .config import RuntimeConfig, load_runtime_config
from .evaluation import CorpusError, load_corpus, run_corpus
from .pipeline import run_files
from .scope import (
    DocumentRequest,
    ManifestError,
    build_manifest,
    load_document_request,
)
from .spec import SpecificationError, compile_specification_file

DEFAULT_CORPUS = Path("evaluation/corpus")


def _request(args: argparse.Namespace) -> DocumentRequest:
    if args.request:
        return load_document_request(args.request)
    if not args.docs:
        raise ManifestError("give --docs <files...> or --request <request.yaml>")
    return DocumentRequest.from_paths([Path(d) for d in args.docs])


def cmd_compile(args: argparse.Namespace) -> int:
    spec = compile_specification_file(args.spec)
    print(f"OK  schema_hash={spec.schema_hash[:12]}  tasks={len(spec.tasks)}")
    for n, layer in enumerate(spec.graph.layers, 1):
        print(f"  layer {n}: {', '.join(layer)}")
    for issue in spec.review_issues:
        print(f"  REVIEW [{issue.code}] {issue.path}: {issue.message}")
    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    config = load_runtime_config(args.config)
    manifest = build_manifest(_request(args), ArtifactStore(config.storage.artifact_dir))
    print(manifest.model_dump_json(indent=2))
    return 0


def _real_config(path: Path | None) -> RuntimeConfig:
    """Runtime config for commands that need the real stack.

    With a config file, it is used as-is. Without one: layered parser +
    sentence-transformers embeddings (default model), instead of the fakes.
    """
    config = load_runtime_config(path)
    if path is None:
        models = config.models.model_copy(update={
            "parsing": config.models.parsing.model_copy(update={"name": "layered"}),
            "embedding": config.models.embedding.model_copy(update={"name": "sentence_transformers"})})
        config = config.model_copy(update={"models": models})
    return config


def cmd_parse(args: argparse.Namespace) -> int:
    from .parsing.backends.pdfium_renderer import PdfiumRenderer
    from .parsing.report import write_report

    config = _real_config(args.config)
    store = ArtifactStore(config.storage.artifact_dir)
    entry = build_manifest(DocumentRequest.from_paths([args.document]), store).documents[0]
    representation = build_adapters(config, store).parser.parse(entry, args.document)

    out_dir = args.out or Path(config.storage.artifact_dir) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{args.document.stem}.parsed.json"
    json_path.write_text(representation.model_dump_json(indent=2), encoding="utf-8")
    html_path = write_report(representation, args.document, PdfiumRenderer(), out_dir / f"{args.document.stem}.report.html")

    ocr_pages = sum(p.route == "ocr" for p in representation.pages)
    open_issues = [i for i in representation.issues if not i.resolved]
    print(f"{len(representation.pages)} page(s) ({ocr_pages} via OCR), {len(representation.blocks)} block(s), "
          f"{len(representation.issues)} issue(s) ({len(open_issues)} unresolved)")
    print(f"json:   {json_path}")
    print(f"report: {html_path}")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    from .indexing.builder import IndexBuilder
    from .indexing.report import write_index_report
    from .parsing.backends.pdfium_renderer import PdfiumRenderer

    config = _real_config(args.config)
    store = ArtifactStore(config.storage.artifact_dir)
    adapters = build_adapters(config, store)
    entry = build_manifest(DocumentRequest.from_paths([args.document]), store).documents[0]
    builder = IndexBuilder(config, adapters, store)
    doc = builder.index(entry, args.document)

    out_dir = args.out or Path(config.storage.artifact_dir) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = write_index_report(doc, args.document, PdfiumRenderer(), out_dir / f"{args.document.stem}.index.html")

    print(f"{len(doc.passages)} passage(s), {len(doc.references)} reference(s) "
          f"({sum(not r.resolved for r in doc.references)} unresolved), {len(doc.continuations)} continuation(s); "
          f"embedding model: {doc.embedding_model}")
    for p in doc.passages:
        print(f"  {p.passage_id:<28} {p.token_count:>4} tok  p{p.pages}  {' > '.join(p.heading_path) or '(no section)'}")
    print(f"view: {html_path}")
    if args.query:
        include = config.indexing.chunking.include_heading_in_index
        print()
        print(f"query: {args.query!r}")
        for label, hits in (("lexical", builder.lexical.search(doc.index_key, args.query, 3)),
                            ("vector", builder.vectors.query(doc.index_key, adapters.embedder.embed_query(args.query), 3))):
            for h in hits:
                print(f"  [{label}] {h.score:7.3f}  {h.id}  {doc.passage(h.id).text[:70]!r}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = _real_config(args.config)
    results = run_files(args.spec, _request(args), config)
    print("[" + ",\n".join(r.model_dump_json(indent=2) for r in results) + "]")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus)
    print(f"corpus OK: {len(corpus.cases)} case(s) in {corpus.root}")
    if args.parsing and corpus.cases:
        from .evaluation.parsing_eval import evaluate_parsing

        config = _real_config(args.config)
        store = ArtifactStore(config.storage.artifact_dir)
        metrics, checks = evaluate_parsing(corpus, build_adapters(config, store).parser)
        print(metrics.model_dump_json(indent=2))
        print(f"quote recall: {metrics.quote_recall}  page accuracy: {metrics.page_accuracy}")
        for c in (c for c in checks if not c.found or c.on_expected_page is False):
            print(f"  MISS {c.document} p{c.page}: {c.quote!r}")
    if args.indexing and corpus.cases:
        from .evaluation.indexing_eval import evaluate_indexing
        from .indexing.builder import IndexBuilder

        config = _real_config(args.config)
        store = ArtifactStore(config.storage.artifact_dir)
        metrics = evaluate_indexing(corpus, IndexBuilder(config, build_adapters(config, store), store))
        print(metrics.model_dump_json(indent=2))
        print(f"quote containment (single passage): {metrics.containment}")
    if args.score and corpus.cases:
        report = run_corpus(corpus, load_runtime_config(args.config))
        print(f"status accuracy: {report.status_accuracy:.0%}  value accuracy: {report.value_accuracy}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="extractor", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("compile", help="validate and compile a YAML spec")
    p.add_argument("spec", type=Path)
    p.set_defaults(func=cmd_compile)

    for name, func, help_ in (
        ("manifest", cmd_manifest, "build the document manifest"),
        ("run", cmd_run, "run the pipeline"),
    ):
        p = sub.add_parser(name, help=help_)
        if name == "run":
            p.add_argument("spec", type=Path)
        p.add_argument("--docs", nargs="*", help="document files")
        p.add_argument("--request", type=Path, help="request YAML (documents, relationships, ...)")
        p.add_argument("--config", type=Path, help="runtime config YAML")
        p.set_defaults(func=func)

    p = sub.add_parser("parse", help="parse one document and write an inspection report")
    p.add_argument("document", type=Path)
    p.add_argument("--out", type=Path, help="output folder (default: <artifact_dir>/reports)")
    p.add_argument("--config", type=Path, help="runtime config YAML (default: layered parser)")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("index", help="chunk and index one document, show passages")
    p.add_argument("document", type=Path)
    p.add_argument("--out", type=Path, help="output folder (default: <artifact_dir>/reports)")
    p.add_argument("--query", help="also run this query against the lexical and vector indexes")
    p.add_argument("--config", type=Path, help="runtime config YAML (default: real parser + default embedding model)")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("eval", help="validate (and score) the evaluation corpus")
    p.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    p.add_argument("--score", action="store_true", help="also run the engine and score it")
    p.add_argument("--parsing", action="store_true", help="measure parsing quality on the corpus documents")
    p.add_argument("--indexing", action="store_true", help="measure chunk/quote containment on the corpus documents")
    p.add_argument("--config", type=Path, help="runtime config YAML")
    p.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (SpecificationError, ManifestError, CorpusError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
