"""Command line entry point:  python -m extractor <command>

    compile   validate a YAML spec and show its tasks / execution order
    manifest  hash documents and show the S01 manifest
    parse     parse one document (S03) and write JSON + an HTML inspection report
    index     chunk + index one document (S04); print sections/passages, write an HTML view
    retrieve  show every step of evidence retrieval (S06) for one task
    extract   extract the value of one task (S07): candidates, reconciliation, model calls
    run       run the pipeline (stubs for stages not implemented yet)
    eval      validate the evaluation corpus and (optionally) score the engine
"""

import argparse
import sys
from pathlib import Path

from .adapters import build_adapters
from .artifacts import ArtifactStore
from .config import RuntimeConfig, load_runtime_config
from .contracts.common import TaskKind
from .evaluation import CorpusError, load_corpus, run_corpus
from .pipeline import index_documents, result_schema, run_report
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
    sentence-transformers embeddings and cross-encoder reranker (default models), instead of the fakes.
    """
    config = load_runtime_config(path)
    if path is None:
        models = config.models.model_copy(update={
            "parsing": config.models.parsing.model_copy(update={"name": "layered"}),
            "embedding": config.models.embedding.model_copy(update={"name": "sentence_transformers"}),
            "reranking": config.models.reranking.model_copy(update={"name": "cross_encoder"})})
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
    restored = sum(p.recovered_words for p in representation.pages)
    print(f"{len(representation.pages)} page(s) ({ocr_pages} via OCR), {len(representation.blocks)} block(s), "
          f"{restored} word(s) restored from the text layer, "
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


def cmd_retrieve(args: argparse.Namespace) -> int:
    from .retrieval.report import format_package
    from .retrieval.retrieval_pipeline import gather_evidence
    from .retrieval.tools import build_retrieval_tools
    from .retrieval.types import request_from_task
    from .scope import resolve_task_documents

    config = _real_config(args.config)
    store = ArtifactStore(config.storage.artifact_dir)
    manifest = build_manifest(_request(args), store)
    spec = compile_specification_file(args.spec)
    task = spec.task(args.task)
    adapters = build_adapters(config, store)
    indexed = index_documents(manifest, adapters, config, store)
    document_ids = resolve_task_documents(task, manifest)
    profile = args.profile or config.retrieval.profile
    package = gather_evidence(
        request_from_task(task, document_ids), [indexed[d] for d in document_ids], build_retrieval_tools(config, adapters, store),
        config.retrieval.resolve(profile), manifest=manifest, profile=profile)

    texts = {p.passage_id: (p.heading_path, p.text) for d in indexed.values() for p in d.passages}
    print(format_package(package, texts))
    out_dir = args.out or Path(config.storage.artifact_dir) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{task.id}.evidence.json"
    out.write_text(package.model_dump_json(indent=2), encoding="utf-8")
    print()
    print(f"json: {out}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    from .extraction import extract_value
    from .extraction.report import format_proposal
    from .retrieval.retrieval_pipeline import gather_evidence
    from .retrieval.tools import build_retrieval_tools
    from .retrieval.types import request_from_task
    from .scope import resolve_task_documents

    config = _real_config(args.config)
    store = ArtifactStore(config.storage.artifact_dir)
    manifest = build_manifest(_request(args), store)
    task = compile_specification_file(args.spec).task(args.task)
    adapters = build_adapters(config, store)
    indexed = index_documents(manifest, adapters, config, store)
    document_ids = resolve_task_documents(task, manifest)
    documents = [indexed[d] for d in document_ids]
    profile = args.profile or config.retrieval.profile
    package = gather_evidence(request_from_task(task, document_ids), documents, build_retrieval_tools(config, adapters, store),
                              config.retrieval.resolve(profile), manifest=manifest, profile=profile)
    proposal = extract_value(task, package, documents, adapters.extractor, config.extraction_for(profile))
    print(format_proposal(proposal))
    out_dir = args.out or Path(config.storage.artifact_dir) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{task.id}.proposal.json"
    out.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
    print()
    print(f"json (with the full prompts and raw model answers): {out}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = _real_config(args.config)
    if args.no_cache:
        config = config.model_copy(update={"execution": config.execution.model_copy(update={"cache_model_calls": False})})
    report = run_report(args.spec, _request(args), config, args.profile)
    if args.out:  # the stable output file: results + what the run cost (schema: `python -m extractor schema`)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        s = report.summary
        print(f"wrote {args.out}: " + ", ".join(f"{n} {k}" for k, n in s.statuses.items())
              + f"; {s.total.calls} model call(s) ({s.total.cached} cached), {s.total.seconds:.0f}s of model time, {s.seconds:.0f}s in all; "
                f"{s.recovery_attempts} recovery attempt(s), {s.recovered} task(s) improved")
        return 0
    print("[" + ",\n".join(r.model_dump_json(indent=2) for r in report.results) + "]")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    """The JSON Schema of the file `run --out` writes."""
    import json

    print(json.dumps(result_schema(), indent=2))
    return 0


def _repeated(args: argparse.Namespace, evaluate) -> None:
    """Run a measurement once (with the call cache, unless --no-cache) or `--repeat N` times without it, and print the spread."""
    from .evaluation.scoped import spread

    if args.repeat and args.repeat > 1:
        reports = []
        for n in range(1, args.repeat + 1):
            print(f"--- run {n}/{args.repeat} ---")
            reports.append(evaluate(False))
            print(reports[-1].format())
        print(spread([r.headline() for r in reports]))
        return
    print(evaluate(False if args.no_cache else None).format())


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
    if args.retrieval and corpus.cases:
        from .evaluation.retrieval_eval import VARIANTS, evaluate_retrieval

        config = _real_config(args.config)
        store = ArtifactStore(config.storage.artifact_dir)
        profiles = args.profile.split(",") if args.profile else [config.retrieval.profile]
        variants = args.variants.split(",") if args.variants else None
        unknown = [v for v in variants or [] if v not in VARIANTS]
        if unknown:
            raise CorpusError(f"unknown variant(s) {unknown}; available: {list(VARIANTS)}")
        report = evaluate_retrieval(corpus, config, build_adapters(config, store), store, profiles, variants)
        print(report.format())
        print("cand% = gold passage reached the reranker, sel% = survived selection, pkg% = in the final package, "
              "exc% = same for contradicting/qualifying evidence")
    if args.overlap and corpus.cases:
        from .evaluation.overlap_eval import evaluate_overlap

        config = _real_config(args.config)
        store = ArtifactStore(config.storage.artifact_dir)
        profiles = args.profile.split(",") if args.profile else [config.retrieval.profile]
        case_ids = args.cases.split(",") if args.cases else None
        print(evaluate_overlap(corpus, config, build_adapters(config, store), store, profiles, case_ids).format())
    if args.extraction and corpus.cases:
        from .evaluation.extraction_eval import evaluate_extraction

        config = _real_config(args.config)
        store = ArtifactStore(config.storage.artifact_dir)
        profiles = args.profile.split(",") if args.profile else [config.retrieval.profile]
        kinds = [TaskKind(k) for k in args.kinds.split(",")] if args.kinds else None
        tags = args.tags.split(",") if args.tags else None
        case_ids = args.cases.split(",") if args.cases else None
        groupings = args.grouping.split(",") if args.grouping else None
        print(evaluate_extraction(corpus, config, build_adapters(config, store), store, profiles, kinds, tags, case_ids, groupings,
                                  save_dir=args.save, cache=False if args.no_cache else None).format())
    if args.attribute:
        from .evaluation.attribution import attribute

        print(attribute(args.attribute).format())
    if args.attribute_records:
        from .evaluation.records_eval import attribute_records

        causes = attribute_records(args.attribute_records)
        from collections import Counter

        print(f"{len(causes)} problem(s): " + ", ".join(f"{k} {n}" for k, n in Counter(c.cause for c in causes).most_common()))
        for c in causes:
            print(f"  {c.cause:<13}{c.case_id}/{c.task_id}: {c.what}")
    if args.records and corpus.cases:
        from .evaluation.records_eval import evaluate_records

        config = _real_config(args.config)
        store = ArtifactStore(config.storage.artifact_dir)
        profiles = args.profile.split(",") if args.profile else [config.retrieval.profile]
        tags = args.tags.split(",") if args.tags else None
        case_ids = args.cases.split(",") if args.cases else None
        task_ids = args.tasks.split(",") if args.tasks else None
        adapters = build_adapters(config, store)
        _repeated(args, lambda cache: evaluate_records(corpus, config, adapters, store, profiles, tags, case_ids, task_ids, save_dir=args.save, cache=cache))
    if args.acceptance and corpus.cases:
        from .evaluation.acceptance_eval import evaluate_acceptance

        config = _real_config(args.config)
        store = ArtifactStore(config.storage.artifact_dir)
        profiles = args.profile.split(",") if args.profile else [config.retrieval.profile]
        kinds = [TaskKind(k) for k in args.kinds.split(",")] if args.kinds else None
        tags = args.tags.split(",") if args.tags else None
        case_ids = args.cases.split(",") if args.cases else None
        adapters = build_adapters(config, store)
        _repeated(args, lambda cache: evaluate_acceptance(corpus, config, adapters, store, profiles, kinds, tags, case_ids,
                                                          save_dir=args.save, from_dir=args.from_dir, qualify=args.qualify, cache=cache))
    if args.recovery and corpus.cases:
        from .evaluation.recovery_eval import evaluate_recovery

        config = _real_config(args.config)
        store = ArtifactStore(config.storage.artifact_dir)
        kinds = [TaskKind(k) for k in args.kinds.split(",")] if args.kinds else None
        tags = args.tags.split(",") if args.tags else None
        case_ids = args.cases.split(",") if args.cases else None
        print(evaluate_recovery(corpus, config, build_adapters(config, store), store, args.profile, kinds, tags, case_ids, save_dir=args.save,
                                cache=False if args.no_cache else None).format())
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
        if name == "run":
            p.add_argument("--profile", help="retrieval depth: light | standard | deep | one of your own")
            p.add_argument("--out", type=Path, help="write the whole run (results + cost summary) to this JSON file; schema: `extractor schema`")
            p.add_argument("--no-cache", action="store_true", help="make every model call again instead of resuming from the call cache")
        p.set_defaults(func=func)

    p = sub.add_parser("schema", help="print the JSON Schema of the file `run --out` writes")
    p.set_defaults(func=cmd_schema)

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

    p = sub.add_parser("retrieve", help="show every step of evidence retrieval for one task")
    p.add_argument("spec", type=Path)
    p.add_argument("task", help="task id in the spec (value or interpretation task)")
    p.add_argument("--docs", nargs="*", help="document files")
    p.add_argument("--request", type=Path, help="request YAML (documents, relationships, ...)")
    p.add_argument("--profile", help="retrieval depth: light | standard | deep | one of your own")
    p.add_argument("--out", type=Path, help="output folder (default: <artifact_dir>/reports)")
    p.add_argument("--config", type=Path, help="runtime config YAML")
    p.set_defaults(func=cmd_retrieve)

    p = sub.add_parser("extract", help="extract the value of one task and show candidates, reconciliation and model calls")
    p.add_argument("spec", type=Path)
    p.add_argument("task", help="id of a value task in the spec")
    p.add_argument("--docs", nargs="*", help="document files")
    p.add_argument("--request", type=Path, help="request YAML (documents, relationships, ...)")
    p.add_argument("--profile", help="retrieval depth: light | standard | deep | one of your own")
    p.add_argument("--out", type=Path, help="output folder (default: <artifact_dir>/reports)")
    p.add_argument("--config", type=Path, help="runtime config YAML (models.extraction selects the model)")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("eval", help="validate (and score) the evaluation corpus")
    p.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    p.add_argument("--score", action="store_true", help="also run the engine and score it")
    p.add_argument("--parsing", action="store_true", help="measure parsing quality on the corpus documents")
    p.add_argument("--indexing", action="store_true", help="measure chunk/quote containment on the corpus documents")
    p.add_argument("--retrieval", action="store_true", help="measure evidence recall of retrieval, with ablations")
    p.add_argument("--overlap", action="store_true", help="measure how much evidence the value tasks of a case share (S05; no chat model needed)")
    p.add_argument("--extraction", action="store_true", help="measure value extraction (correct / wrong / false absence...)")
    p.add_argument("--acceptance", action="store_true", help="measure what the engine ACCEPTS (S10/S11): automation, precision, false accepts")
    p.add_argument("--save", type=Path, help="--extraction / --acceptance: store every evidence package, proposal and what the labeler expected in this folder (for --attribute and --from)")
    p.add_argument("--from", dest="from_dir", type=Path, help="--acceptance only: re-run validation on proposals saved with --save (no model calls)")
    p.add_argument("--attribute", type=Path, help="read the folder written by --save and say, for every miss, which stage caused it and whether it can be fixed (no model calls)")
    p.add_argument("--records", action="store_true", help="measure records extraction (S09): record recall/precision, field accuracy, completeness")
    p.add_argument("--tasks", help="--records only: only these task ids, comma separated")
    p.add_argument("--attribute-records", type=Path, help="read the folder written by --records --save and say why each missing / extra / wrong record happened")
    p.add_argument("--recovery", action="store_true", help="measure S12 (the whole engine, recovery as configured): rescues, harm and calls per action")
    p.add_argument("--no-cache", action="store_true", help="do not read finished model calls from the call cache (every call is made again)")
    p.add_argument("--repeat", type=int, help="--acceptance / --records only: run N times without the call cache and print the spread of each metric")
    p.add_argument("--qualify", action="store_true", help="--acceptance only: write a qualification record when the thresholds in acceptance.qualification are met")
    p.add_argument("--profile", help="retrieval profile(s) to evaluate, comma separated (e.g. light,standard,deep)")
    p.add_argument("--kinds", help="--extraction / --acceptance only: task kinds to score, comma separated (value,interpretation,records; default: all implemented)")
    p.add_argument("--tags", help="--extraction / --acceptance only: only cases having ALL these tags, comma separated (e.g. split:test,digital)")
    p.add_argument("--cases", help="--extraction / --acceptance only: only these case ids, comma separated (e.g. business_01,business_05)")
    p.add_argument("--grouping", help="--extraction only: S05 grouping settings to compare, comma separated (e.g. off,auto: one call per task vs tasks that share a call)")
    p.add_argument("--variants", help="comma separated ablations (default: all): full,stage_one,no_rerank,...")
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
