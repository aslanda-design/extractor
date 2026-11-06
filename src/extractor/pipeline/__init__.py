"""Pipeline wiring: runs the stages in order (see run.py for stage status)."""

from .run import index_documents, run_files, run_pipeline

__all__ = ["index_documents", "run_files", "run_pipeline"]
