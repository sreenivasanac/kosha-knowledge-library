"""Capture parsers. `parse_capture` maps a contract (source, kind) to a parser;
every parser returns the same `ParseResult` shape (see `common.py`)."""
from typing import Any

from kosha.parsers import linkedin, x_api, x_graphql
from kosha.parsers.common import ParsedItem, ParseResult

__all__ = ["ParsedItem", "ParseResult", "parse_capture", "UnknownCapture"]


class UnknownCapture(ValueError):
    pass


def parse_capture(source: str, kind: str, payload: Any, meta: dict | None = None) -> ParseResult:
    match (source, kind):
        case ("x", "bookmarks_page"):
            return x_graphql.parse_timeline(payload, meta, in_folder=False)
        case ("x", "folder_page"):
            return x_graphql.parse_timeline(payload, meta, in_folder=True)
        case ("x", "folder_list"):
            return x_graphql.parse_folder_list(payload)
        case ("x", "api_page"):
            return x_api.parse_api_page(payload)
        case ("linkedin", "saved_posts"):
            return linkedin.parse_saved_posts(payload)
        case ("linkedin", "saves_csv"):
            return linkedin.parse_saves_csv(payload)
    raise UnknownCapture(f"no parser for source={source!r} kind={kind!r}")
