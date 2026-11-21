# Kosha: design plan (v3)

Status: draft for review, 21 Nov 2026. Repo and folder: `kosha-knowledge-library`. "Kosha" is Sanskrit for treasury.

Companion documents:

- `SEARCH_DESIGN.md` (this folder): the full search, pipeline-operation and agent-playbook design. Sections 6 to 8 here summarise it.
- `~/SoftwareProjects/constella/agentic_development_docs/project_design_plan/ai_clustering_design_plan_v4.md`: the Constella label-scoring loop. It is now one strategy among several (section 7), not the fixed plan.

## 1. What this is

A self-hosted, open-source library of everything you save: X bookmarks first, then LinkedIn saves, later browser bookmarks, YouTube, Instagram, links from Gmail and blogs. Items are organised by a tag tree, searchable by qualifiers, keywords and meaning, and operable by agents through one MCP server whose main tool chains small operations the way a shell pipes commands.

| Topic | Decision |
|---|---|
| Name | Kosha; repo `kosha-knowledge-library` |
| Stack | Python backend, React web app, Chrome extension |
| Users | Single user now; `user_id` on every owned table for later |
| X history | Kosha's own Chrome extension in your logged-in browser (accepted risk), built on the parsing logic of `twitter-web-exporter` |
| X ongoing | Extension whenever you open your bookmarks; official API daily job as a setting (on or off, with a monthly cap) |
| LinkedIn | Same extension, click to capture; LinkedIn's official export for the full list |
| Tags | A tree with no fixed depth. Per item: one primary, one secondary, unlimited others. Statuses: proposed, approved, rejected. |
| Source folders | Kept as their own data (X bookmark folders, playlists). They are an input to labeling and a search qualifier, not automatically tags. |
| Embeddings | Voyage: `voyage-4-large` for items, `voyage-4-lite` for queries (same vector space) |
| LLM calls | Configurable through LiteLLM; OpenAI by default |
| Summaries | Every item has one. Which recipe (own text, rule-built, LLM) is decided by an experiment on your real bookmarks. |
| Organising | Agent-driven through pipeline operations. Constella becomes a library Kosha imports for embedding groups and the label-scoring loop. |
| Agentic search | Over MCP first; then an in-app "Ask" box using the same operations |
| Media | Storing images is a setting (default on); storing videos is a setting (default off) |
| Media text | Transcripts and image descriptions live per media file; filled on demand with a cost estimate shown first |
| Read state | `read`, toggled by hand only |
| Archive | Means "marked for deletion". The Archive page has a button that removes the bookmarks at their source through the extension, then deletes them from Kosha. |
| Keyboard | Single keys, Gmail/Linear style; every key remappable in Settings |
| Home view | All bookmarks, newest saved first |
| Devices | Desktop first, usable on a phone |
| Address | `kosha.pragnyalabs.com` (DNS and deploy need your OK at the time) |
| Out of scope | Email digests, RSS, reading reminders, AI bulk tag button in the web app (for now) |

## 2. What the research found

### X bookmarks

- **The official API reaches only the newest ~700** (698 returned; Dewey holds 11,255 of yours) at $0.001 per bookmark.
- **Your latest 100 bookmarks:** 61 have video, 17 photos, 41 are long posts whose full text is in a separate field, 11 quote another post, 5 are replies, 4 are X articles, 41 carry external links. The API gives no save time, and does not return a quoted post's media.

### twitter-web-exporter (prinsss, MIT, 2,824 stars, last release Aug 2026)

| Aspect | Finding | For Kosha |
|---|---|---|
| Form | A userscript, not an extension | Kosha writes its own Manifest V3 shell |
| Capture | Patches `XMLHttpRequest` in the page and reads the bookmark responses X sends as you scroll | Same approach, patching `fetch` as well |
| Parsing | About 300 lines of types and extractors: long text, quoted posts in full, media variants, visibility wrappers | Port these (MIT: keep the notice) |
| Quoted posts | Arrive complete, with their media | Solves the API's gap |
| Bookmark folders | Not supported (open issue: folders use a separate `BookmarkFolderTimeline` request) | New code |
| Scrolling | None; the user scrolls | New: paced auto-scroll, end detection, pause and resume |
| Unavailable posts | Dropped silently | Kosha keeps the raw payload so nothing is lost |
| Articles, link cards | Not parsed | New code, server side |
| Removing a bookmark | Not present | New code |
| Upkeep | X changes break it a few times a year; the maintainer patches within days | Send raw payloads to the server and parse there, so a fix needs no re-capture |

`bookmark-debt` (new, 1 star) contributes two ideas: an estimate of the save time decoded from each entry's `sortIndex` (to be checked against your data), and "stop after two fetches that add nothing" as the end test.

### LinkedIn

No official API returns saved posts. The official export lists URL and save date. The extension reads your saved-posts page once it has loaded. LinkedIn's terms forbid automation tools in general, so this is low risk, not no risk.

### Tweetsmash and Dewey, second pass

| Copy | From |
|---|---|
| Cmd+K palette: Ask first, then views, tags with counts, actions | Tweetsmash |
| Single-key triage (j/k, t, a) and a `?` help overlay | Tweetsmash |
| Tag chips with counts as an in-page filter row, including "Untagged N" | Tweetsmash |
| Sticky bulk bar: "N selected", Select all, Tag, Untag, Archive, with Delete set apart | Tweetsmash |
| Group-by (type, date, author, tag) and sort by saved or posted date | Tweetsmash |
| Grid, row and list views with direct keys | Dewey |
| Quoted post as a nested bordered card; link preview with image, title, two-line description | Dewey |
| Right-hand reader drawer that keeps the list visible | Both |
| Sidebar filters with counts: untagged, video, image, source, year | Dewey |

| Avoid | Seen in |
|---|---|
| One key bound to three actions | Tweetsmash (`r`) |
| A 440-pixel feed on a wide screen | Tweetsmash |
| Hover-only checkboxes; one-click Select All with no confirmation | Dewey |
| A flat list of 30+ folders with no nesting or search | Dewey |
| Drawers that ignore Esc; layout jumping when a drawer opens; scroll position lost on reload | Both |
| Search that runs only on submit, with no operators | Both |

## 3. Architecture

```
Chrome extension ──(raw captures)──┐
React web app ─────────────────────┤
Agent ── Kosha MCP ────────────────┼─> FastAPI (REST + MCP) ─> Postgres + pgvector
                                   │        │  imports: constella (library)
                                    worker ─┴─> Voyage, Jev, LLM, X API, media store
```

| Part | Choice | Why |
|---|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy, Alembic, `uv` | One language for API, worker, MCP and Constella |
| Database | Postgres 16 with pgvector, `pg_trgm`, `ltree` | Vectors, fuzzy match and tag paths in one engine |
| Jobs | Procrastinate (Postgres-backed) | No Redis on the shared server |
| MCP | Official Python MCP SDK, streamable HTTP, bearer token | Same service layer as REST |
| Constella | A small library Kosha imports: embedding groups, nearest-label shortlist, assignment rule | One server to connect; you said any split is fine |
| Web app | React, Vite, TanStack Query and Router, Tailwind, `pnpm` | Static build served by the backend |
| Extension | Chrome Manifest V3, TypeScript, loaded unpacked for now | X capture, LinkedIn capture, removal at source |
| LLM calls | LiteLLM, model names in settings | Your choice: configurable, OpenAI default |
| Deploy | Docker Compose behind the existing reverse proxy on Hetzner | Each deploy and DNS change needs your OK |
| Licence | MIT | |

## 4. Data model

All tables carry `user_id` unless noted.

### `items`

One row per post, article, video or page, whether you saved it or it is only context for something you saved.

| Column | Notes |
|---|---|
| `id`, `seq` | `seq` is a stable number used for pagination |
| `source`, `source_id` | x, linkedin, youtube, chrome, instagram, gmail, web. Unique together. |
| `is_saved` | False for a quoted post or thread parent held only as context. It becomes true if you later bookmark that post too; no second row. |
| `saved_order`, `saved_at`, `saved_at_est` | Order from the source; real save time when the source gives one; otherwise an estimate, shown as approximate |
| `captured_at` | When Kosha first received it |
| `content_types` | Ordered set of `text`, `image`, `video`, `audio`, `document`, `article`, `link` |
| `url`, `title`, `body` | `body` is the author's full text only. It never holds text extracted from images or video. |
| `author_name`, `author_handle`, `author_avatar`, `posted_at`, `lang` | |
| `likes`, `reposts`, `replies`, `views` | From the source, for sorting and `likes:>1000` |
| `duration_s` | Longest video or audio in the item |
| `summary`, `summary_method`, `summary_status` | Method: own text, rule-built, or LLM (section 6) |
| `starred`, `note`, `read_at` | Your star, your note, and when you marked it read |
| `archived_at` | Set means marked for deletion |
| `content_hash` | Changes when text or extracts change, so embeddings, summaries and cached Jev answers refresh |
| `raw` | Original payload |

### `item_media`

One row per file attached to an item.

| Column | Notes |
|---|---|
| `type`, `mime_type` | photo, video, animated_gif, audio, document (for example a LinkedIn PDF carousel) |
| `position` | Order within the item |
| `original_url`, `preview_url`, `variants` | Remote addresses; `variants` lists a video's mp4 files and bit rates |
| `local_path`, `local_preview_path`, `bytes` | Filled when stored. Images follow the `store_images` setting (default on), videos and audio follow `store_videos` (default off). Previews are always stored; they are small. |
| `duration_ms`, `width`, `height`, `alt_text` | |
| `extract_text` | Text taken from this file: a transcript for video and audio, a description or read-out text for an image or document |
| `extract_kind`, `extract_format` | transcript, description, ocr; plain or timestamped |
| `extract_status`, `extract_model`, `extract_lang` | none, queued, done, failed; which model wrote it |

Why extracts sit here and not on `items`: an item can have several files of different types, each needing its own extract and status. A YouTube item is one item with one video row. Search and summaries read the extracts of all of an item's media.

With a store setting off, the card loads the remote address; that can stop working if the source removes the file.

### `item_relations`

| Column | Notes |
|---|---|
| `from_item_id` | The item that points |
| `relation` | Enum: `quotes`, `replies_to`, `thread_member` |
| `to_item_id` | Nullable until the target is held |
| `to_source_id` | Always kept, so a missing target can be fetched later |
| `position` | Order within a thread |
| `target_state` | held, missing, unavailable (deleted or private) |

Unique on (`from_item_id`, `relation`, `to_source_id`).

| Case | Rows | What the card shows |
|---|---|---|
| Bookmark A quotes B | A `quotes` B. B is an item with `is_saved` false and its own media rows. | A on top with A's media; B nested below with B's media |
| B itself quotes C | B `quotes` C, usually `missing` | A "quotes another post" link inside B; fetched only when opened |
| A replies to P | A `replies_to` P | "Replying to @p" with P above when held |
| A is post 3 of the author's own thread | Each post `thread_member` of the root, with `position` | A, plus "Read thread" showing all posts held |
| B is deleted | `target_state` unavailable | A, with "quoted post unavailable" |
| LinkedIn reshare with a comment | Same as a quote | Same as a quote |
| You also bookmark B later | B's `is_saved` becomes true | B appears in lists too; nothing is duplicated |

Because the quoted post is a normal item, its images and video load the same way as the bookmark's own. The extension receives quoted posts complete; the official API does not return their media, so those wait for the extension or an enrichment fetch.

### Tags

| Table | Column | Notes |
|---|---|---|
| `tags` | `id`, `parent_id`, `path` | No depth limit. `path` (ltree) is maintained from the parents for fast subtree filters. |
| | `name`, `slug`, `description` | Rename and move are one-row changes; items point at the `id`. The description is the definition Jev checks against. |
| | `status` | proposed, approved, rejected (kept so it is not proposed again) |
| | `origin`, `namespace` | human, agent, import. `namespace` lets a labeling experiment build a trial tree without touching the real one. |
| | `color`, `sort` | |
| `item_tags` | `item_id`, `tag_id` | Many per item |
| | `role` | primary, secondary, other. At most one primary and one secondary per item. |
| | `attached_by`, `confidence` | human, agent, import; Jev's probability when an agent attached it |
| | `removed_by_user` | A tag you removed stays recorded, so an agent does not put it back |

### Source folders

- **`collections`**: `source`, `source_id`, `name`, `kind` (x_folder, youtube_playlist, linkedin, dewey_folder, browser_folder).
- **`item_collections`**: which items are in which.

They are searchable (`folder:"Coding with AI"`) and visible to the labeling agent. Whether any become tags is decided after the first organising runs on real data.

### Other tables

- **`item_links`**: expanded URL, `domain`, title, description, preview image.
- **`item_embeddings`**: `item_id`, `model`, `content_hash`, `vector`.
- **`result_sets`**: named, short-lived sets of item ids produced by pipelines (section 7).
- **`jev_questions`, `jev_answers`**: every Jev answer, keyed by question, item and summary hash, so a repeated check costs nothing.
- **`label_runs`**: one row per organising run: strategy, namespace, coverage, spend.
- **`spend_ledger`**: every paid call (X, Voyage, Jev, LLM, speech-to-text).
- **`lists`, `list_items`, `saved_views`**.
- **`sync_state`**, **`captures`** (raw batches from the extension), **`deleted_items`** (so a later sync does not bring a deleted item back).
- **`users`, `api_tokens`, `settings`, `keybindings`**.

## 5. Sync, capture and removal

### The extension

- A script in the page records the bookmark responses X sends (`Bookmarks`, `BookmarkFolderTimeline` and the folder list) and hands them to the extension, which posts the raw JSON to Kosha in batches. Kosha parses on the server.
- **Backfill:** click Backfill on your bookmarks page. The extension scrolls at an uneven, human pace, backs off if X answers "too many requests", stops after two loads that add nothing, and can be paused and resumed. Then it walks each bookmark folder.
- **Passive:** any time you open your bookmarks, new ones are captured. Running Backfill again is a full re-sync without an API key; it stops early once it reaches known posts.
- **LinkedIn:** a Capture button on your saved-posts page.

### Official X API

A daily job, newest first, stopping at the first known post or after three pages with nothing new. A setting turns it on or off; a monthly cap (default $3) stops it. Both routes produce the same `source_id`, and the richer payload wins field by field.

### Removal at the source

The Archive page has a "Remove from sources" button. It hands the archived items to the extension, shows progress, and ends with a report (removed, already gone, failed). Kosha deletes its own copy only for items the extension confirms.

| Source | How | My evaluation |
|---|---|---|
| X | The extension un-bookmarks each post in your session | Same kind of action as clicking the bookmark icon. Paced a few seconds apart. Low risk. |
| LinkedIn | The extension opens your saved-posts page, uses each post's menu to Unsave, and accepts the confirmation if one appears | Workable. LinkedIn does restrict accounts for automated activity, so: 8 to 20 seconds between posts, at most about 40 per run, and stop at once on any unexpected dialog. At that pace I judge the risk low, but I cannot verify LinkedIn's thresholds. |

An agent can trigger the same job through MCP.

## 6. Search

Full detail, including 20 test queries and a plan for each, is in `SEARCH_DESIGN.md`. I have read its summary, not every line.

### Qualifier language

One grammar for the search bar, REST and MCP. Space means AND; `OR`, `-` and parentheses work; a key takes a comma list; numbers and dates take comparisons and ranges. Every search type below runs inside the set the qualifiers select, as in Gmail.

| Group | Qualifiers |
|---|---|
| Text | word, `"exact phrase"`, prefix `retriev*`, fuzzy `~word`, `/regex/`, and `in:title,body,quote,link,note,summary,transcript,image` to choose fields |
| Tags | `#a/b` (with everything beneath), `#=a/b` (exact), `-#tag`, `primary:`, `secondary:`, `tags:0`, `tagged-by:ai` or `human`, `conf:<0.85` |
| People and structure | `from:a,b,c`, `quotes:`, `replies-to:`, `is:thread`, `folder:`, `list:` |
| Content | `source:`, `type:video`, `has:image` (also video, audio, link, quote, note, transcript, summary), `lang:`, `duration:<5m`, `len:>1000`, `domain:github.com`, `link:` |
| Time | `posted:2026-03..2026-05`, `after:30d`, `before:`, `saved:` (approximate where estimated), `recent:200` |
| State | `is:starred`, `is:read`, `is:unread`, `is:untagged`, `is:archived` |
| Engagement and order | `likes:>1000`, `views:`, `sort:saved`, `sort:posted`, `sort:likes` |
| Sets | `similar:<id>`, `@name` (a saved result set) |

**Autocomplete:** typing a key suggests its values with counts computed inside the current result; an empty bar shows recent searches and saved views; an unknown key is flagged, not ignored.

### How results are ranked

1. **Filter first.** The qualifiers select the candidate set in SQL.
2. **Keyword** rank inside that set (Postgres full-text, with `pg_trgm` for fuzzy and prefix).
3. **Semantic** rank inside that set by exact vector comparison. At 30,000 items no approximate index is needed, and an approximate index would apply filters too late.
4. **Hybrid (default)** merges the two by reciprocal rank fusion.
5. A ranked result is stored as a snapshot, so page 3 stays page 3 while you read.

Each item gets one vector built from title, body, quoted text, link titles and summary. Estimated latency is 150 to 400 ms for a first search; this will be measured.

### Summaries

Jev reads summaries, and so does every compact result row, so every item has one. Per your answer, the recipe is chosen by experiment on your real bookmarks. Candidates:

| Recipe | Applies to |
|---|---|
| Own text | Short posts |
| Rule-built: title, first two or three sentences, and a metadata line (author, source, length, content types) | Long posts, articles |
| Rule-built from media: video title plus the first sentences of the transcript | Video-heavy items, once extracts exist |
| LLM summary of the first part | Compared against the rule-built ones |

The experiment scores each recipe on a hand-checked sample by Jev accuracy, search quality and cost. An LLM summary for all 30,000 items is estimated at about $1.70.

### Jev search

- Runs only on summaries, inside the set that qualifiers and retrieval produced.
- Yes/no questions for criteria ("is this about retrieval-augmented generation?"); a four-level score for "how useful is this for building this project".
- Answers are cached, so re-running a question costs nothing.
- Estimated cost: $0.004 per 1,000 items, $0.12 for 30,000; $0.30 worst case if Jev bills the question context per item.

### Agentic search

An LLM turns a request into a pipeline (section 7), reads compact rows, refines at most three times, and answers with cited items.

- **First:** over MCP, with Claude Code or Grokbot as the agent.
- **Then:** an Ask box in the web app with read-only operations, at most six calls and about $0.02 of Jev per question. Estimated LLM cost is $0.003 to $0.07 a question depending on model; the 20 test queries decide which model is good enough.

## 7. Pipeline operations and organising

### One tool, chained operations

The MCP tool `query` takes a pipeline. Each stage narrows, ranks, judges, summarises or writes, and the work happens on the server. The agent sees counts and only the fields it asks for, cut to the length it asks for. Any result can be named (`@rag`) and reused or combined later.

| Kind | Stages |
|---|---|
| Narrow and rank | `where` (any qualifiers), `grep` (word, fuzzy, regex), `semantic`, `hybrid`, `similar`, `sort`, `head`, `tail`, `skip`, `sample` |
| Judge | `jev` (a question and a threshold), `score_labels` (Constella's loop) |
| Combine | `union`, `intersect`, `except` |
| Summarise | `count by` author, domain, tag, folder or month; `stats`; `terms` (words distinctive to this set against the library); `groups` (embedding groups) |
| Show | `show field:chars`, for example `show summary:40 author` |
| Write | `tag`, `untag`, `roles`, `star`, `archive`, `list`, `save view` |

Your example as one pipeline:

```
where from:a,b,c | grep ~retriev | semantic "retrieval augmented generation" | head 200
  | jev "about RAG / retrieval-augmented generation?" >= 0.9 | tag "AI/RAG"
```

Worked through in `SEARCH_DESIGN.md`: about 8 calls, about 1,300 tokens read by the agent, about $0.003.

**Safety on writes:** up to 25 items apply directly; above that the tool returns a count, a sample and a confirm token; above 500 items, or for a brand-new tag, the tag stays `proposed` until you approve it. Every write is undoable as a batch.

### Embedding groups, and your Constella image

`groups` is the operation you described: group any set by embedding, get back named sets with counts and a few samples, pick one, group it again for a second level, or pass it to any other stage.

The image (`~/tasks/constella.png`, K-Means with five clusters on Quora answers) shows why groups are a way to select items and not a source of labels: the colours overlap heavily, and cluster 2 appears in four separate islands. So a group is a starting set that an agent names and then checks item by item with Jev, never a tag applied wholesale.

Constella's part is therefore a library: the grouping, the nearest-labels shortlist and the assignment rule. If the experiments show it adds nothing over the plain stages, it is dropped for Kosha.

### Playbook for the agent

1. **Map the library:** `count by` author, domain and folder; `terms`; `groups` with three samples each. A few thousand tokens.
2. **Propose a tree** with a one-sentence definition per tag, with the current tree and your folders in context.
3. **Per tag:** build a candidate set with cheap stages, check it with `jev`, tag the confident ones, show the doubtful band (0.8 to 0.9) for review.
4. **Assign roles:** the highest-scoring tag is primary, the next is secondary, the rest are others.
5. **Leftovers:** `where is:untagged | groups` and repeat; revise the tree where a tag is too broad or two overlap.
6. **Stop** when a round tags under a set share of what was left, or the spend limit is reached. Coverage and spend are reported after every round.

### Experiments, once all bookmarks are in

Each strategy runs in its own tag namespace and is compared on coverage, agreement with about 200 bookmarks you label by hand, cost, agent tokens and time.

| Strategy | Idea |
|---|---|
| Top-down seeded | Start from your folders and a proposed tree; fill each tag by retrieval and Jev |
| Bottom-up groups | Start from embedding groups; name them; verify with Jev |
| Author-first | Authors who post on one topic seed that topic's candidates |
| Tag-by-tag retrieval | For each tag, hybrid search then Jev, as in your example |
| Constella v4 loop | Jev scores every item against its eight nearest tags |

## 8. Kosha MCP server

Six tools, down from about thirty, because `query` replaces the separate search, list and bulk tools.

| Tool | Purpose |
|---|---|
| `query` | Run a pipeline; read, judge and write |
| `tags` | Read and change the tree: create, rename, describe, move, merge, approve, reject |
| `edit_item` | One item: note, star, read, summary, roles |
| `add_item` | Add a URL or text |
| `jobs` | Start and check sync, media text, removal at source |
| `runs` | Start, compare and close labeling runs; spend and coverage |

This departs from your first request for many tools. The pipeline gives more combinations with less to learn; if agents handle it poorly in practice, common pipelines can be added as named tools.

## 9. Web app

### Layout

- **Sidebar** (collapsible): search, All, Unread, Starred, Untagged, Proposed tags, Archive; the tag tree with counts, collapsible and searchable; saved views; sources; lists.
- **Main area:** list (a reading column of about 680 pixels), grid, or compact rows. A row of tag chips with counts filters the current view.
- **Reader drawer** on the right for one item or a thread; the list stays visible; Esc closes it.
- **Pagination:** numbered pages over the stable `seq`, with page, sort and filters in the URL. Page size 25, 50 or 100. Position is kept on reload.
- **Card:** author, date, text with "more", media, quoted post nested with its own media, link preview, primary tag as a breadcrumb, secondary tag, other tags as chips, source icon, always-visible checkbox in bulk mode.
- **Media text:** a button on each video, image or audio file; the same action in bulk; "all without text" in Settings. Each shows the count, total minutes and estimated cost before you confirm.
- **Search bar:** qualifier autocomplete with counts, mode switch (hybrid, keyword, semantic), active qualifiers shown as removable chips, "Run Jev check" on the current result.

### Keyboard

Every action has a key, shown in the `?` overlay and in tooltips, and remappable in Settings. Keys are ignored while typing in a field.

| Area | Key | Action |
|---|---|---|
| Move | `j` / `k` | Next / previous item |
| | `g g` / `G` | First / last on the page |
| | `[` / `]` | Previous / next page |
| | `Enter` or `o` | Open the reader drawer |
| | `Shift+O` | Open the original in a new tab |
| | `Esc` | Close drawer, clear selection, leave search |
| Go to | `g a`, `g u`, `g r`, `g s`, `g p`, `g x` | All, Untagged, Unread, Starred, Proposed tags, Archive |
| | `g t` | Jump to a tag (type-ahead) |
| | `g l` | Jump to a list |
| Act | `t` | Tag picker: type-ahead over paths, Enter adds, new path creates |
| | `1` / `2` in the picker | Make the highlighted tag primary / secondary |
| | `Shift+T` | Remove a tag |
| | `s` | Star |
| | `Shift+R` | Toggle read |
| | `n` | Edit note |
| | `d` | Mark for deletion (to Archive), with an Undo toast |
| | `z` | Undo the last action |
| | `l` | Add to list |
| | `c` | Copy link; `Shift+C` copies as Markdown |
| | `m` | Get media text for this item |
| | `p` | Play or pause the item's video |
| Select | `x` | Select or unselect |
| | `Shift+j` / `Shift+k` | Extend the selection |
| | `Cmd+A` | Select the page; a second press offers "all N results" |
| Search | `/` | Focus search |
| | `Cmd+K` | Command palette: Ask, views, tags, actions |
| | `/` as the first character in the palette | Slash commands: `/tag`, `/untag`, `/archive`, `/view grid`, `/sort posted`, `/save-view`, `/jev`, `/sync` |
| | `f` | Filter the view by the focused item's author; `Shift+F` finds similar items |
| View | `v l`, `v g`, `v c` | List, grid, compact |
| | `?` | Shortcut help |

All single-item actions apply to the selection when one exists.

## 10. Later (designed for, not built in v1)

- Obsidian export with properties and a `.base` file; later a plugin.
- Extension: "save this page", Instagram capture.
- Workona replacement in its own repo.
- YouTube, Instagram, Gmail links, blogs on the same capture pipeline.
- Multi-user sign-up.
- Transcript chunk vectors, the Voyage reranker, an AI bulk tag button.

## 11. Phases

| Phase | Delivers |
|---|---|
| 0 | Repo, licence, README, Docker Compose, schema, login. Three small measurements (section 12). |
| 1 | Extension: X backfill and passive capture, folders. Capture pipeline and parser. List, grid, reader drawer, numbered pagination, quote cards, media playback, keyboard navigation. |
| 2 | Qualifier language with autocomplete; keyword search; Voyage embeddings; hybrid search |
| 3 | Tags, roles, star, note, read, archive; tag management; bulk mode; full keyboard map and remapping |
| 4 | MCP server with `query` and the pipeline stages; summaries experiment; Jev stage |
| 5 | Organising: `groups`, Constella library, label runs; hand-labelled sample; strategy experiments on your full library |
| 6 | Deploy to Hetzner |
| 7 | LinkedIn capture; removal at source |
| 8 | In-app Ask; media text; lists and saved views; Obsidian export |

## 12. Access and spending

| Item | State |
|---|---|
| Voyage, TypeSafe, OpenAI and X keys | Found in `~/tasks/kosha_api_keys.txt` (the file name has an `s`). At build time they go into the project's `.env`, which is git-ignored; nothing is printed. |
| Measurements before building | About $0.50 in total, needs your OK: how Jev bills a packed request, summary recipes on 100 bookmarks, exact vector scan speed |
| Extension | You load it once in Chrome ("Load unpacked") |
| Public GitHub repo | I create it under your account when you say go |
| Hetzner deploy and DNS | Your OK at the time |

No other keys are needed for phases 0 to 5.

## 13. Defaults I chose (say so if any is wrong)

| Topic | Default |
|---|---|
| Monthly cap for the X API job | $3 |
| Login | One account, email and password |
| Theme | Follows the system light/dark setting |
| Jev cut-off for tagging | 0.8, with 0.8 to 0.9 shown as doubtful; settled on the hand-labelled sample |
| Write thresholds for agents | Direct up to 25 items; confirm above; proposed above 500 |
| Ask's Jev allowance | $0.02 a question, $2 a month |
| Removal pacing | X a few seconds apart; LinkedIn 8 to 20 seconds, 40 per run |
| MCP tools | Six, pipeline-first |
| Constella | Library inside Kosha; dropped if experiments show no gain |
