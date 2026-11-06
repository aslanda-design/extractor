"""Evaluation: load the human-labeled corpus and score engine output against it."""

from .corpus import Corpus, CorpusError, EvalCase, load_corpus
from .harness import EvalReport, run_corpus

__all__ = ["Corpus", "CorpusError", "EvalCase", "EvalReport", "load_corpus", "run_corpus"]
