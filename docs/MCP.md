# Kosha MCP server

Six tools over streamable HTTP, served by the same process as the API at `/mcp`. The design is in `SEARCH_DESIGN.md` sections E, G and H; what was measured on the real library is in `experiments/pipeline_smoke_2026-11-21.md`.

## Connect

The server must be running (`cd backend && uv run kosha serve`). The token is `KOSHA_API_TOKEN` from the repo's `.env`, the same one the browser extension uses.

```sh
claude mcp add --transport http kosha http://127.0.0.1:8000/mcp --header "Authorization: Bearer $KOSHA_API_TOKEN"
```

- A request without a valid token gets 401.
- The endpoint is stateless: every call stands alone and answers with JSON, so there is no session to resume.
- The SDK's DNS-rebinding check accepts the hosts `127.0.0.1`, `localhost` and `[::1]` only. Behind a reverse proxy with another host name, pass `transport_security` in `kosha/mcp_server.py`.
- The same pipeline is at `POST /api/query` with `{"q": "...", "confirm": null, "run": null}` for the web app and for tests. It returns `text` (what the MCP tool returns) plus `n`, `stages`, `rows`, `spent_usd`, `confirm`, `batch_id` and `set`. An error is a 422 with `{detail: {message, position}}`.

## Tools

| Tool | Arguments | What it does |
|---|---|---|
| `query` | `pipeline`, `confirm?`, `run?` | Runs a pipeline: filter, search, judge with Jev, count, show, bulk-write with preview. `pipeline: "help"` returns the grammar. |
| `tags` | `operations?`, `run?` | No operations: the tree with counts, status and definitions. Operations, applied in order: `create`, `rename`, `describe`, `move`, `merge`, `approve`, `reject`, `delete`. |
| `edit_item` | `id`, then any of `note`, `summary`, `starred`, `read`, `archived`, `primary_tag`, `secondary_tag` | Changes one item. |
| `add_item` | `url` or `text`, `title?`, `note?`, `tags?` | Saves a new item with source `web`. For a URL, Kosha reads the page's title and description itself (https, public addresses only, 1 MB, redirects checked hop by hop). |
| `jobs` | `action`: `status`, `summarize`, `embed`, `sync` | Counts of items without a summary or an embedding, items waiting for removal at the source, last sync; starts the summary, embedding and X sync jobs. |
| `runs` | `action`: `start`, `status`, `close`, `compare`, `promote`, `undo`, `list` | Label runs: a trial tag tree per experiment, coverage and spend, comparison, promotion into the real tree, and undo of any write batch. |

Not in this build: an `approve` scope on tokens (any valid token can approve a tag), media-text jobs, and running a removal at the source from `jobs` (it reports how many are waiting; the extension does the removal).

## Pipeline grammar

```
pipeline := source ( " | " stage )*
source   := where <filter>  |  @name  |  all
```

Three rules:

1. A stage only sees the set that came in. `semantic` in the middle ranks the incoming set; to widen, `union` another set.
2. The reply holds counts and only what `show` asks for. Without `show` it ends with five compact rows (`id | @author | date | 60 characters`).
3. A write is the last stage.

Every reply starts with two lines: `n=<items left> spent=$<this call>` (plus the run's spend and budget, and the saved set name), then one entry per stage, `stage in→out`, with dollars and a note where there are any. A reply is capped at about 1,500 tokens; when `show` rows are cut it ends with `showing 37 of 412; more: add "| skip 37" before show`.

### Filter (`where`)

The search bar's qualifier language (`SEARCH_DESIGN.md` B): `from:a,b`, `#tag/path`, `is:untagged|starred|unread|thin|long|archived`, `type:video`, `has:link`, `domain:github.com`, `folder:"Name"`, `list:"Name"`, `saved:2026-03..`, `posted:30d..`, `likes:>100`, `len:>1000`, `lang:en`, `tags:0`, `id:12,13`, words, `"a phrase"`, `prefix*`, `~fuzzy`, `/regex/`, `in:title,body word`, `-negated`, `(a OR b)`, `@set`.

After a Jev stage, the attached answer can be tested by its name: `where p:>=0.9`, `where rag:0.5..0.8`, `where -p:>=0.8`. `score:>0.4` tests the last ranking score.

### Stages

| Stage | Effect |
|---|---|
| `where <filter>` | Keeps the items that match. Order is kept. |
| `grep [-v] word ~fuzzy /re/ [in:title,body]` | Text matching only; `-v` inverts. A regex may contain `\|` (leave a space after its closing slash). |
| `semantic "text" [top N]` | Orders the set by meaning (cosine similarity, exact). Items without an embedding drop out and are counted in the note. |
| `hybrid "text" [top N]` | Keyword and meaning fused, as the search bar does it. Returns at most about 400 items; for wider recall use `semantic ... top N` and `union` a `grep`. |
| `similar <id\|@set> [top N]` | Orders the set by similarity to one item or to the average of a set. |
| `sort <key> [asc\|desc]` | Keys: `saved`, `posted`, `captured`, `likes`, `views`, `reposts`, `replies`, `duration`, `len`, `conf`, `random`, `score`, or an answer name. |
| `head N`, `tail N`, `skip N`, `sample N [seed]` | Slices. `sample` without a seed repeats, so a confirmed write sees the set it previewed. |
| `jev "question" [>= p] [as name]` | One yes/no question per item. Attaches a probability under `name` (default `p`); `>= p` keeps only items at or above it. Prints a five-band histogram. |
| `jev score "question" ["l0;l1;l2;l3"] [>= p] [as name]` | A score instead of yes/no: by default the four usefulness levels of `SEARCH_DESIGN.md` D.4; the value is level / top level, so 0 to 1. |
| `score_labels [#subtree] [>= p] [apply]` | Each item is asked about its 8 nearest tags (approved and proposed, by embedding of path and definition); Constella's rule picks primary, secondary and others. Reports placed, pending, label sizes, too-broad and overlapping labels. `apply` writes the tags (a write: preview and confirm). |
| `union @a`, `intersect @a`, `except @a` | Set algebra with a saved set. |
| `count`, `count by <dim> [top N]` | `author`, `domain`, `tag`, `folder`, `month`, `year`, `source`, `type`, `lang`. |
| `stats [field]` | Without a field: n, authors, date range, median length, share with a primary tag, untagged, embedded. With `likes`, `views`, `reposts`, `replies`, `len`, `duration`, `score` or an answer name: n, min, median, max, sum. |
| `terms [N]` | Stems distinctive to this set against the rest of the library (log-odds z-score over document counts, body and quoted text): `stem  z  in_set  in_library`. Feed one back as `grep stem*`. |
| `groups [k] [as prefix]` | Embedding groups (Constella K-Means, `k` up to 40, default at most 12), each saved as `@prefix1`…, with size, cohesion and three 60-character samples. Run it again on one group for a second level: `@g3 \| groups 6 as g3.` |
| `show field[:chars] ...` | `id`, `author`, `date`, `source`, `summary`, `body`, `text`, `title`, `tags`, `folder`, `domain`, `url`, `score`, and any answer name. |
| `as @name` | Saves the set with its scores and answers. A set lives 7 days from its last use. |
| `tag "A/B" [role primary\|secondary\|other\|auto]` | Attaches the tag as an agent, with the last Jev answer as confidence. A missing tag (and its parents) is created as proposed. |
| `untag "A/B"` | Removes the tag where an agent attached it. |
| `roles auto` | Among agent-attached tags of each item: highest confidence primary, next secondary, rest other; a tag and its ancestor never both count. |
| `star`, `unstar`, `read`, `unread`, `archive`, `unarchive` | Item state. |
| `list "name"` | Appends the set to a hand-made list (created if missing). A list does not expire. |
| `save view "name"` | Saves `where <filter> [\| sort key]` as a saved view. |

### Jev

- Jev reads the first 2,000 characters of the item's text (title, body, quoted post, link titles, media extracts), not the summary: a rule-built summary lost up to 21 of 33 true yeses in Constella's measurements.
- Jev reads literally. Ask a full question that says what counts. On the real library the fragment `"about RAG / retrieval-augmented generation?"` put 91 of 106 items between 0.5 and 0.8 and none at 0.9; the spelled-out question put 52 at 0.9 or above.
- Answers are cached by (question, item, text). The same question again costs nothing; a reworded question or a changed text is asked again.
- Before spending, the stage estimates. More than 2,000 new questions returns `needs_confirm est=$…` and a confirm token. A call that would pass the per-call budget (`jev_run_budget_usd`, $0.50), the run's budget or the monthly budget (`jev_monthly_budget_usd`, $5) is refused with nothing spent.
- Price: $0.042 per million input tokens; about $0.007 per 1,000 items for one question, about $0.05 per 1,000 items for `score_labels` (estimated from Constella's measurement, not run on the real library).

### Writes

| Size | What happens |
|---|---|
| 25 items or fewer | Applied at once. The reply gives counts and a `batch` id. |
| More than 25 | Nothing is written. The reply is `PREVIEW`, with counts (`new`, `already`, `by_hand`, `removed_by_you`), five samples and `confirm="c_…"`. Send the same pipeline again with that token within 10 minutes. The token is bound to the stage as written and to the exact set; if either changed you get a new preview. |
| A new tag, or more than 500 items onto an agent-made tag | The tag is `proposed` until the user approves it. |

- Rows written by a pipeline carry `attached_by='agent'` and Jev's p as `confidence`.
- A tag the user removed from an item is never put back. A row attached by hand is never changed or removed.
- Every write is one undo batch: `runs` with `action: "undo", batch: <id>` (or `POST /api/undo/<id>`).

## Playbook for a labeling agent

Hand this section to the agent.

You are labeling a bookmark library through Kosha's MCP tools. Item text stays on the server. Read counts, terms and short samples, never whole items. Every `query` reply shows what the call spent; never pass the budget.

1. **Start a run.** `runs {action: "start", name, strategy, budget_usd}`. Pass the returned `run` id to every `query` and `tags` call. Your tags then go to a trial tree and the real one is untouched.
2. **Get the map** (about 3,000 tokens in all).
   - `all | count by source`, `all | count by type`, `all | count by folder top 30`
   - `all | count by author top 40`, `all | count by domain top 30`, `all | count by month`
   - For each folder and the top authors: `where folder:"<name>" | terms 12`. (`terms` on `all` compares the library with itself and says so.)
   - `all | groups 12 as g`, then `@g<n> | groups 6 as g<n>.` and `@g<n> | terms 12` on the large or loose ones (low cohesion).
   - `tags` with no operations, to read the current tree.
3. **Propose the tree** with the current tree in front of you. Keep the user's folder and tag names. Write each tag as a path and a one-sentence definition that says what counts and what does not; the definition is the Jev question. Send one `tags` call with `create` operations. Start with 15 to 25 top-level tags.
4. **Dry run.** `all | sample 300 1 | as @dry`. Do step 5 on `@dry` only, then `@dry | where is:untagged | count`. If more than 30% is untagged, read `@dry | where is:untagged | groups 8 as d` and fix the tree. This costs about a cent.
5. **Per tag.**
   - a. Candidates, by cheap stages, saved as a set: `all | semantic "<definition>" top N | as @c_x` (N about three times the size you expect), then widen with `where from:<authors> | union @c_x | as @c_x` and `all | grep stem* | union @c_x | as @c_x`.
   - b. Sanity check, about 300 tokens: `@c_x | terms 12`, `@c_x | count by author top 8`, `@c_x | sample 8 | show id text:60`. Fix the filters before paying.
   - c. Ask Jev: `@c_x | jev "<full question from the definition>" as x | as @s_x`. Read the histogram.
   - d. Tag the confident ones: `@s_x | where x:>=0.8 | tag "<path>"`, read the preview, repeat with `confirm`.
   - e. Review the uncertain band: `@s_x | where x:0.5..0.8 | sample 12 | show id summary:90 x`. If most belong, the definition is too narrow: rewrite it (`tags` `describe`) and ask again.
   - f. Check recall: `where #<path> | terms 10`. For a stem you did not search for: `all | grep stem* | except @c_x | sample 10 | show id text:60`. If those belong, add them and repeat c and d on the new items only (the rest is cached).
6. **Go deeper where the counts say so.** Read `tags` again. A tag holding more than 25% of the library is too broad: `where #<path> | groups 8 as b`. A tag with more than about 150 items and visible sub-themes gets children; do step 5 with `where #<path>` as the source. A tag with fewer than 5 items merges into its parent (`tags` `merge`).
7. **Assign roles.** `all | roles auto`, then confirm.
8. **Leftovers**, at most three rounds: `where is:untagged | count`, then `where is:untagged | groups 12 as u`. For a group with a clear theme, add a tag and do step 5. Report `where is:thin | count`: those need a media extract, not a label.
9. **Keep totals.** After each tag and each round: `runs {action: "status", run}` gives coverage, untagged, tags and dollars.
10. **Stop** when coverage reaches the target (default 85%), or a leftover round tagged under 10% of what was untagged, or three leftover rounds are done, or the next call would pass the budget. Then `runs {action: "close", run}` and report coverage, spend, the tree and what is left. The user decides whether to `promote`.

The other route is item-centred: once the tree has definitions, `where is:untagged | score_labels` asks each item about its 8 nearest tags in one call (about $0.05 per 1,000 items, estimated), and `score_labels apply` writes the result. It is the way to label new items against an existing tree.

Cut-off: tag at 0.8. Treat 0.8 to 0.9 as the band worth a spot check (`where x:0.8..0.9 | sample 12 | show ...`).
