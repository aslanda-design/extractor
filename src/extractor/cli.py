"""Command line entry point:  python -m extractor <command>

    compile   validate a YAML spec and show its tasks / execution order
    manifest  hash documents and show the S01 manifest
    run       run the pipeline (stubs for stages not implemented yet)
    eval      validate the evaluation corpus and (optionally) score the engine
"""

import argparse
import sys
from pathlib import Path

from .artifacts import ArtifactStore
from .config import load_runtime_config
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


def cmd_run(args: argparse.Namespace) -> int:
    config = load_runtime_config(args.config)
    results = run_files(args.spec, _request(args), config)
    print("[" + ",\n".join(r.model_dump_json(indent=2) for r in results) + "]")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus)
    print(f"corpus OK: {len(corpus.cases)} case(s) in {corpus.root}")
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

    p = sub.add_parser("eval", help="validate (and score) the evaluation corpus")
    p.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    p.add_argument("--score", action="store_true", help="also run the engine and score it")
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
