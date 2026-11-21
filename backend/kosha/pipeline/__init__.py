"""The pipeline `query` engine (SEARCH_DESIGN.md section E): one text
expression of stages separated by `|`, run on the server."""
from kosha.pipeline.executor import GRAMMAR, run_query

__all__ = ["GRAMMAR", "run_query"]
