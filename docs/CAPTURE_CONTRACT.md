# Capture contract

Everything that enters Kosha from a browser (the extension, or a script run in the page) uses one envelope. The server stores the envelope untouched in `captures`, then parses it. Parsing is always server-side so a change at X or LinkedIn is fixed without re-capturing.

## Envelope

```json
{
  "source": "x",
  "kind": "bookmarks_page",
  "meta": {
    "url": "https://x.com/i/api/graphql/<hash>/Bookmarks?...",
    "operation": "Bookmarks",
    "folder_id": null,
    "folder_name": null,
    "captured_at": "2026-11-21T10:00:00Z",
    "client": "extension/0.1.0"
  },
  "payload": { }
}
```

| `source` | `kind` | `payload` |
|---|---|---|
| `x` | `bookmarks_page` | Raw JSON body of X's `Bookmarks` GraphQL response |
| `x` | `folder_page` | Raw JSON body of `BookmarkFolderTimeline`; `meta.folder_id` and `meta.folder_name` set |
| `x` | `folder_list` | Raw JSON body of the bookmark-folder list response |
| `x` | `api_page` | Raw JSON body of the official API `GET /2/users/{id}/bookmarks` |
| `linkedin` | `saved_posts` | `{ "posts": [ { "urn", "url", "author_name", "author_url", "author_headline", "text", "posted_label", "media": [ { "type", "url", "poster" } ], "reshared": { ...same shape... } } ], "page_url" }` read from the saved-posts page |
| `linkedin` | `saves_csv` | `{ "rows": [ { "url", "saved_at" } ] }` from LinkedIn's data export |

## Endpoint

`POST /api/captures` with `Authorization: Bearer <api token>` and the envelope as the JSON body.
Response: `{ "capture_id": 1, "items_new": 18, "items_updated": 2, "items_unchanged": 0 }`.

## Files

The same envelope, one per file, can be ingested from disk: `uv run kosha ingest-dir data/captures/x`.
File names sort in capture order (`000123-bookmarks_page.json`). Ingest is idempotent.
