# Kosha: search and elemental operations (design)

Status: design only, 21 Nov 2026. No code accompanies this file. It extends `DESIGN.md` sections 4, 6, 7 and 8 and says where they must change (section B.6 and H).

How numbers are marked:

- **docs** = read today from the vendor's documentation; the URL follows.
- **measured** = taken from `DESIGN.md` or from Constella's `ai_clustering_design_plan_v4.md` (called "v4" below), which measured them.
- **estimated** = my arithmetic from the above. Nothing in this file was measured today and no paid call was made.

## 0. Decisions in one table

| Topic | Decision |
|---|---|
| Query language | One filter grammar with AND, OR, NOT, grouping, ranges and text operators. It parses to one filter AST. The web app, REST and MCP all use it. |
| Retrieval at 30k rows | Filter first, then an **exact** vector scan inside the filtered set. No ANN index. |
| Fusion | Weighted reciprocal rank fusion, k = 60, both weights 1.0, top 200 from each ranker. |
| Vectors | One vector per item, 1,024 dimensions stored as `halfvec`. Chunk vectors for long transcripts come later. |
| Reranking | None in the search bar. Jev for yes/no and graded judgments. Voyage reranker is a later, optional stage. |
| Jev search | Runs on the item summary only, inside the set the query selected. Answers are cached by (question, summary hash). |
| Summaries | Every item gets one. Short items use their own text. Long items get an LLM summary from a small model. |
| Agent operations | **Both forms**: one `query` tool that takes a pipeline expression, and named result sets (handles) that the pipeline creates and combines. |
| Agentic search | A fixed loop over the `query` tool, read-only, with a token and money budget per question. |
| Constella | Becomes a small Python library that Kosha imports. One MCP server, not two. |
| Kosha MCP tools | Six, down from about thirty. |

---

## A. Real search needs (the test set)

Twenty queries a builder would ask of their bookmarks. Sections B to F are checked against them. The plan for each is in F.4.

| # | Type | Query | What it stresses |
|---|---|---|---|
| Q1 | Known item | "that thread about pgvector HNSW tuning I saved last spring" | Thread, exact terms, save time that the source does not give |
| Q2 | Known item | "the post where @simonw compared SQLite full-text search to embeddings" | Author plus meaning |
| Q3 | Known item | "a video where someone demos an agent booking a flight; I forget who" | Video with little text; summary and transcript |
| Q4 | Known item | "the GitHub repo for a Postgres job queue that someone linked" | Link domain and link title, not post text |
| Q5 | Topical | "what did @karpathy say about evals" | Author filter, then read and cite several |
| Q6 | Topical | "videos under 5 minutes about prompt caching" | Type and duration filters with meaning |
| Q7 | Topical | "GitHub repos I bookmarked for RAG, most starred first" | Link kind, a metric of the link target, sort |
| Q8 | Topical | "long articles on MCP server authentication, in English, from this year" | Type, language, date, length |
| Q9 | Project relevance | "everything useful for designing a Chrome extension that intercepts fetch" | Broad recall, then a graded judgment per item |
| Q10 | Project relevance | "what should I read before building hybrid search on Postgres" | Several sub-topics; grouping the answer |
| Q11 | Project relevance | "which of my saved tools could replace Redis for a job queue on a small server" | Two facets: is it a tool, does it fit the need |
| Q12 | Aggregate | "who do I bookmark most about agents, and how did that change by month" | Counts by author and month inside a topical set |
| Q13 | Aggregate | "which domains do the links under #AI/RAG come from" | Counts by domain inside a tag |
| Q14 | Aggregate | "which bookmarks contradict each other on whether fine-tuning beats RAG" | Stance per item, then reading both sides |
| Q15 | Aggregate | "what did I save a lot of last quarter that has no tag yet" | Untagged, grouping, distinctive terms |
| Q16 | Similarity | "things similar to this one" | Vector of one item |
| Q17 | Similarity | "more like these five, but from other authors" | Vector of a set, negated author filter |
| Q18 | Maintenance | "posts I starred but never tagged" | Two state filters |
| Q19 | Maintenance | "AI-attached tags under #AI with confidence below 0.85" | Tag provenance and confidence |
| Q20 | Maintenance | "videos over 10 minutes with no transcript, and their total length" | Missing-part filter and a sum |

---

## B. Qualifier language

### B.1 Grammar

```
query     := or_expr [ sort ]
or_expr   := and_expr ( "OR" and_expr )*
and_expr  := unary ( [ "AND" ] unary )*            whitespace means AND
unary     := [ "-" | "NOT" ] atom
atom      := "(" or_expr ")" | qualifier | tagref | handle | text
qualifier := KEY ":" value ( "," value )*           a comma means OR inside one key
value     := WORD | QUOTED | cmp | range
cmp       := ( ">" | ">=" | "<" | "<=" ) bound      likes:>1000   duration:<5m
range     := [bound] ".." [bound]                   posted:2026-03..2026-05   conf:..0.85
bound     := NUMBER | DATE | DURATION | RELATIVE    2026-03-14 | 2026-03 | 2026 | 5m | 90s | 30d
tagref    := "#" [ "=" ] path [ "/*" ]              path := name ( "/" name )*, quoted when it has spaces
handle    := "@" NAME                               a saved result set (section E)
text      := WORD | QUOTED | WORD "*" | "~" WORD | "/" REGEX "/"
sort      := "sort:" KEY [ "-asc" | "-desc" ]
```

Rules:

- `OR` and `NOT` are upper case, so the words "or" and "not" stay searchable.
- AND binds tighter than OR. Parentheses group.
- An unknown key is an error that names the nearest valid key. It is never silently treated as text. To search for the literal `foo:bar`, quote it.
- By default a query sees saved, non-archived items. `is:archived` or `include:archived` changes that. Context-only items (quoted posts, thread parents) appear only with `include:context`.

### B.2 Text operators

| Form | Meaning | Compiles to |
|---|---|---|
| `retrieval` | Stemmed word match. Also matches "retrieve", "retrieving". Feeds ranking. | `tsv @@ to_tsquery('english','retrieval')` |
| `"hybrid search"` | The words next to each other, stemmed. | `phraseto_tsquery` |
| `retriev*` | Prefix. This is the "stem" search from the user's example. | `to_tsquery('english','retriev:*')` |
| `~pgvectr` | Fuzzy: tolerates typos. | pg_trgm `'pgvectr' <% search_text` (word similarity, default threshold 0.6; docs: https://www.postgresql.org/docs/16/pgtrgm.html) |
| `/hnsw\.ef_\w+/` | Regular expression, case-insensitive. | `search_text ~* '...'`, helped by the trigram index |
| `-term`, `-"phrase"` | Must not contain. | `NOT (...)` |
| `in:transcript (prompt caching)` | Scope the text atoms of the same group to named fields. Fields: `title`, `body`, `quote`, `link`, `note`, `summary`, `transcript`, `image`. Several are allowed: `in:title,note`. | Weight labels on the tsquery (B.5) |
| `note:todo`, `title:hnsw` | Shorthand for `(in:note todo)`, `(in:title hnsw)`. | same |

Bare words and phrases do two jobs. They filter, and they are the text the keyword and semantic rankers use. Prefix, fuzzy, regex and negated terms only filter.

### B.3 Qualifiers

"Existing" means listed in `DESIGN.md` section 6. "Needs" names the column or index, detailed in B.6.

| Qualifier | Meaning | Status | Needs |
|---|---|---|---|
| `#ai/rag` | Has this tag **or any tag below it** | existing, now with paths | `tags.path` |
| `#=ai/rag` | Has exactly this tag | new | |
| `#ai/rag/*` | Has a tag below it, not the tag itself | new | `tags.path` |
| `#rag` | Leaf name alone is allowed when it is unique. If not, the error lists the candidates. | new | |
| `-#tag` | Does not have it | existing | |
| `primary:#x`, `secondary:#x` | The tag holds that role on the item | new | `item_tags.role` |
| `tags:0`, `tags:>3` | Number of tags on the item. `is:untagged` = `tags:0`. | new | |
| `tagged-by:ai`, `tagged-by:human`, `tagged-by:import` | Who attached at least one tag. With a tag: `tagged-by:ai #ai` means an AI-attached tag under `#ai`. | new | `item_tags.attached_by` |
| `conf:<0.85`, `conf:0.5..0.8` | Confidence of an AI-attached tag, combined with the tag in the same group | new | `item_tags.confidence` |
| `tag-status:proposed` | Tag is proposed, approved or rejected | new | |
| `from:@handle`, `from:"Name"` | Author. Comma list allowed. | existing, now multi-value | |
| `mentions:@h` | The text mentions the handle | new, later | `items.mentions[]` |
| `quotes:@h`, `quotes:<id>` | Quotes a post by that author, or that post | new | `item_relations` |
| `replies-to:@h` | Is a reply to that author | new | `item_relations` |
| `thread:<id>` | Every item in the same thread as the item | new | `item_relations` |
| `source:x` | x, linkedin, youtube, web, gmail, chrome, instagram | existing | |
| `folder:"name"` | The source folder or playlist | new | `items.source_folder` (exists) |
| `list:"name"` | Member of a hand-made list | new | |
| `type:video` | Content type: text, image, video, audio, article, link. Comma list allowed. | new name; `has:video` stays as an alias | `content_types` adds `audio` |
| `has:link`, `has:quote`, `has:note`, `has:summary`, `has:transcript`, `has:image-text` | A part is present | `has:link` existing, rest new | |
| `kind:tutorial` | Genre written with the summary: tutorial, tool, paper, opinion, news, demo, thread, other | new | `items.kind` |
| `domain:github.com` (alias `site:`) | A link in the item points at this domain; subdomains included | new | `item_links.domain` |
| `link:pgvector` | The URL or link title contains the text | new | trigram on `item_links` |
| `linkkind:repo` | repo, paper, video, doc, article | new, later | `item_links.kind` |
| `stars:>1000` | GitHub stars of a linked repo | new, later | `item_links.repo_stars` |
| `lang:en` | Language of the item | new | `items.lang` (exists) |
| `duration:<5m`, `duration:10m..30m` | Longest video or audio in the item | new | `items.duration_s` |
| `len:>1000`, `is:long` | Characters of body | new | `items.body_len` |
| `after:`, `before:`, `posted:2026-03..2026-05`, `posted:2025`, `after:30d` | Posting time. Relative values count back from today. | existing, extended | |
| `saved:2026-03..2026-05` | Save time. Uses the real time when the source gives one, otherwise an estimate (B.6). Results built on estimates are labelled "approx." | new | `saved_at_est` |
| `captured:1d..` | When Kosha first stored the item. Always known. | new | `captured_at` |
| `recent:500` | The 500 most recently saved | new | |
| `is:starred`, `is:read`, `is:unread`, `is:archived`, `is:thread`, `is:reply`, `is:thin` | State flags. `is:thin` = under 40 characters of text and no media extract. | starred, archived, untagged existing; rest new | `read_at` |
| `likes:>1000`, `reposts:`, `replies:`, `views:`, `bookmarks:` | Engagement numbers from the source | new | metric columns |
| `id:18234,9912` | These items | new | |
| `similar:18234` | Not a filter. Adds a semantic ranker that uses this item's vector. `similar:@name` uses the average vector of a set. | new | |
| `@name` | Member of a saved result set. This is also how an embedding group is named: `@h7.g3`. | new | `result_sets` |
| `sort:` | relevance, saved, posted, likes, views, stars, duration, conf, random | new | |

Left out on purpose:

- **A `cluster:` qualifier.** Groups are made on demand by the `groups` operation and come back as handles. Storing cluster ids would bring back the cluster tables `DESIGN.md` removed.
- **A Jev qualifier.** Jev costs money and its answers are not stored attributes, so it is a pipeline stage (D, E), not a filter. In the search bar it is a mode with its own question box.

Check against section A: Q1 `is:thread saved:2026-03..2026-05`, Q4 `domain:github.com`, Q6 `type:video duration:<5m`, Q7 `domain:github.com sort:stars`, Q8 `type:article lang:en posted:2026 len:>3000`, Q17 `similar:@five -from:...`, Q18 `is:starred is:untagged`, Q19 `tagged-by:ai #ai conf:<0.85`, Q20 `type:video duration:>10m -has:transcript`.

### B.4 Filter AST

The parser produces one tree. Everything else consumes it.

```
And(children) | Or(children) | Not(child)
Pred(key, op, value)             from = "simonw";  duration < 300;  posted in [a, b)
Tag(path_id, mode, role?)        mode: subtree | exact | below
Text(kind, value, fields)        kind: word | phrase | prefix | fuzzy | regex
Set(handle)
plus, at query level:  rank_text (the bare words and phrases, not negated),  similar_to,  sort
```

- The compiler turns the tree into one SQL `WHERE` clause and a list of rankers. There is no second code path for agents.
- `print(parse(q))` returns a canonical string. Saved views, recent searches and handles store that string.
- One test covers the grammar: for a few hundred generated trees, `parse(print(tree)) == tree`.

### B.5 Autocomplete

One endpoint: `GET /api/search/suggest?q=<bar text>&cursor=<position>` returns `{replace_range, suggestions: [{insert, label, count, kind}]}`. The app renders them as a dropdown and turns finished qualifiers into chips, as Gmail does.

| What is being typed | Suggestions |
|---|---|
| Empty bar | The last 10 searches, then saved views |
| Start of a word that prefixes a key (`fr`, `du`) | The key with a one-line hint and an example: `from:` "author", `duration:` "e.g. <5m" |
| `from:` | Authors by count **inside the current result set**, then type-ahead on handle and name (prefix first, then trigram) |
| `#` | Tags as breadcrumb paths with counts, matched on any segment. A parent offers `#a/b` and `#a/b/*`. |
| `domain:` | Top domains with counts, then type-ahead |
| `source:`, `type:`, `has:`, `is:`, `kind:`, `lang:`, `tagged-by:` | The fixed values, each with its count in the current set. Zero-count values are greyed. |
| `folder:`, `list:` | Names with counts |
| `after:`, `before:`, `posted:`, `saved:` | Presets (7d, 30d, this year, last year, each month that has items) and a date picker |
| `duration:`, `likes:`, `conf:`, `len:` | Buckets with counts, for example `<1m`, `1m..5m`, `5m..20m`, `>20m` |
| `similar:` | "Paste an item link, or use *Find similar* on a card" |
| `@` | Live handles, newest first |
| A bare word that equals a tag or author name | A hint row: "Search tag #rag (412)" or "from:@rag_guy (38)" |
| Unknown key | "Did you mean `domain:`?" |

The counts are a `count by` operation (section E) run with the rest of the bar as its filter, so they always mean "if you add this, you get N". Recent searches come from a small `search_history` table.

### B.6 Data-model changes `DESIGN.md` needs

| Where | Change | Why |
|---|---|---|
| `tags` | Remove the four-level cap. Add `path ltree` built from tag ids (for example `12.88.340`) with a GiST index. Rename changes no path; move rewrites the subtree's paths. (docs: https://www.postgresql.org/docs/16/ltree.html) | Unlimited depth; `#a/b` and `#a/b/*` become one indexed test |
| `tags` | Add `ns` (namespace, default `main`) and `embedding halfvec(1024)` of "path: description" | Experiments (G.3); nearest-tag hints |
| `item_tags` | Replace `is_primary` with `role` (primary, secondary, other). One primary and one secondary per item and namespace, by partial unique index. `attached_by` values become human, ai, import. Add `agent` (which agent), `batch_id`, `ns`. Keep `confidence` and `removed_by_user`. | The facts in the brief; undo; experiments |
| `items` | Add `read_at`. `DESIGN.md` lists read tracking as out of scope; the brief lists `has_read`. | `is:read` |
| `items` | Add `like_count`, `repost_count`, `reply_count`, `view_count`, `bookmark_count` (nullable) | `likes:>`, sort |
| `items` | Add `duration_s`, `body_len`, `captured_at`, `kind` | `duration:`, `len:`, `kind:` |
| `items` | Add `saved_at_est` | `saved:` when the source gives no time. Estimate: walk the items in save order and keep the running maximum of `posted_at`. A bookmark cannot be older than the newest post saved before it. It is a lower bound, and I expect it to be close for someone who mostly bookmarks fresh posts. **Not verified**; check it against LinkedIn's export, which has real save dates. |
| `items` | Add `summary_source` (verbatim, llm, human, agent) and `summary_input_hash` | Section D |
| `items` | Add `search_text` (title, body, quoted text, link titles, note), `tsv`, `tsv_extra`, all maintained by the worker | Search |
| `items`, `item_media` | Move `transcript` and `transcript_status` to `item_media` as `extract_text`, `extract_kind` (transcript, image description), `extract_status`. Add `audio` to `content_types`. | The brief says media extracts cover video, audio and images |
| `item_links` | Add `domain` (registrable domain, indexed). Later: `kind`, `repo_full_name`, `repo_stars` filled by a free GitHub API call. | `domain:`, Q7 |
| `item_embeddings` | `vector` becomes `halfvec(1024)`, storage `PLAIN`. Add `input_hash`. No ANN index. | Section C |
| New | `result_sets`, `jev_questions`, `jev_answers`, `spend_ledger`, `label_runs`, `search_history`, `query_embeddings` (cache), `term_df` (materialized view) | D, E, G |
| Remove | `relevance_runs`, `relevance_scores` | Replaced by `jev_answers` plus a saved result set |
| Remove | The seven `/api/constella/v1` endpoints and the Constella workspace | Section H |
| Change | Section 6 item 1 said full-text covers tag names. It no longer does. | A tag rename would force re-indexing many items. The bar suggests the tag instead (B.5). |

Text-search columns:

| Column | Weight A | Weight B | Weight C | Weight D |
|---|---|---|---|---|
| `tsv` | title, author name and handle | body | quoted text, link titles and descriptions | summary |
| `tsv_extra` | your note | transcript | image descriptions and alt text | – |

`in:` scoping maps a field to a column and a weight label, for example `in:transcript` to `tsv_extra` with label B. The configuration is `english` when `lang` is `en`, otherwise `simple`.

Indexes, and no more than these to start:

- GIN on `tsv` and on `tsv_extra`.
- GIN with `gin_trgm_ops` on `search_text`, `author_handle`, `author_name`, `item_links.url`.
- GiST on `tags.path`; btree on `item_tags (tag_id, item_id)` and `item_links (domain)`.
- No btree indexes on scalar columns such as `posted_at` or `duration_s` yet. At 30,000 narrow rows a sequential scan is a few milliseconds (estimated). Add one when `EXPLAIN` shows the need.

---

## C. Retrieval pipeline

### C.1 How filters, keyword and semantic combine

**Filter first, then exact search inside the filtered set.**

```sql
WITH f AS (                       -- the set the qualifiers select
  SELECT id FROM items WHERE <compiled filter>
), kw AS (                        -- keyword ranker, inside f
  SELECT i.id, row_number() OVER (ORDER BY ts_rank_cd(<weights>, i.tsv, q, 1) DESC) AS r
  FROM items i JOIN f USING (id), to_tsquery('english', :tsq) q
  WHERE i.tsv @@ q OR i.tsv_extra @@ q
  LIMIT 200
), sem AS (                       -- semantic ranker, inside f, exact
  SELECT e.item_id AS id, row_number() OVER (ORDER BY e.vector <=> :qvec) AS r
  FROM item_embeddings e JOIN f ON f.id = e.item_id
  ORDER BY e.vector <=> :qvec
  LIMIT 200
)
SELECT id, coalesce(:w_kw / (60 + kw.r), 0) + coalesce(:w_sem / (60 + sem.r), 0) AS score
FROM kw FULL JOIN sem USING (id)
ORDER BY score DESC, id
LIMIT 500;
```

Why exact and not ANN:

- pgvector searches exactly when there is no vector index, with perfect recall (docs: https://github.com/pgvector/pgvector#indexing).
- With an HNSW index a filter is applied **after** the index scan. A filter that matches 10% of rows leaves about 4 of the default 40 candidates (docs, same page, "Filtering"). pgvector 0.8 added iterative scans to work around this, with a default limit of 20,000 visited tuples (docs, "Iterative Index Scans"). At 30,000 rows that workaround approaches a full scan anyway.
- The user's requirement is that semantic search runs *within* the qualifier set. Exact search inside the set meets it with no tuning and no missed results.
- Size: a `halfvec(1024)` is 2 × 1,024 + 8 = 2,056 bytes (docs, "Halfvec Type"). 30,000 of them are 62 MB. As `vector` they would be 123 MB.
- Speed: 20 to 60 ms for a scan of all 30,000 on the shared server (**estimated**; measured in build step 3 before anything depends on it). A filtered set is faster in proportion.
- When to change: above about 200,000 items, or if the measured p95 passes 150 ms. Then add HNSW with `hnsw.iterative_scan = relaxed_order`. The query shape above stays the same.

### C.2 Fusion and weights

Reciprocal rank fusion: `score = Σ w / (60 + rank)` over the two rankers, top 200 from each, top 500 kept.

| Case | `w_kw` | `w_sem` |
|---|---|---|
| Default | 1.0 | 1.0 |
| The text is one or two tokens, or looks like an identifier (digits, `_`, `.`, camelCase) | 2.0 | 1.0 |
| The text is six words or more with no quotes | 1.0 | 1.5 |
| Only prefix, fuzzy or regex terms (nothing to embed) | 1.0 | off |
| Keyword ranker returns nothing | off | 1.0, and the app says "no exact matches, showing related items" |
| `similar:` given and no text | off | 1.0 |

The semantic ranker also drops results under a minimum similarity. All of these are constants, tuned once against section A in build step 3.

Mode switch in the bar: keyword (`w_sem` off), semantic (`w_kw` off), hybrid (default).

Keyword ranking uses the built-in `ts_rank_cd` with field weights and length normalisation (docs: https://www.postgresql.org/docs/16/textsearch-controls.html).

**Is a BM25 extension worth it? No, not now.**

- `ts_rank_cd` has no inverse document frequency. That hurts when many common words are OR-ed together. Here words are AND-ed, the sets are small, and the semantic ranker covers the vague queries.
- `pg_textsearch` needs Postgres 17 or 18; Kosha is on 16. ParadeDB `pg_search` is AGPL-3.0, which sits badly with an MIT project. (docs: https://neon.com/faqs/pg-search-vs-pg-textsearch-vs-lakebase-search-bm25)
- Their advantage is top-k speed on millions of rows. Ranking a few hundred matching rows out of 30,000 takes milliseconds either way (estimated).
- Revisit if build step 3 shows keyword misses on section A that fusion does not fix.

### C.3 What is embedded

One vector per item. The text, in this order:

1. title
2. body, first 6,000 characters
3. `Quoted:` the quoted post's text
4. `Links:` title and description of each external link
5. `Summary:` the item summary, once it exists

Not included: author name and handle (they are qualifiers; embedding them makes "similar" mean "same author"), tags, your note.

| Fact | Value | Status |
|---|---|---|
| Model | Voyage 4 family, 1,024 dimensions (256, 512, 2,048 also offered), 32,000-token context | docs: https://docs.voyageai.com/docs/embeddings.md |
| `input_type` | `document` for items, `query` for searches. Voyage prepends a different instruction to each. | docs, same page |
| Shared space | All Voyage 4 models produce compatible vectors. Voyage recommends embedding documents with `voyage-4-large` and queries with a smaller model. | docs: https://blog.voyageai.com/2026/01/15/voyage-4/ |
| Price | `voyage-4-lite` $0.02, `voyage-4` $0.06, `voyage-4-large` $0.12 per million tokens. First 200 million tokens free per account. | docs: https://docs.voyageai.com/docs/pricing.md |
| Limits | 1,000 texts and 1 million tokens per request for `voyage-4-lite`; 16M tokens and 2,000 requests per minute | docs: embeddings.md, https://docs.voyageai.com/docs/rate-limits.md |
| Tokens for 30,000 items | About 9 million, at about 300 tokens an item | estimated |
| Cost for 30,000 items | $0.18 with lite, $1.08 with large. $0 while inside the free 200 million. | estimated |

Decisions:

- **One vector per item in v1.** A post of up to 4,400 characters fits whole. For an article or transcript longer than the cut, the summary in the embedded text still represents the whole.
- **Chunk vectors later**, when transcripts are common: a table of about 400-token windows per long item, and the item's score is its best chunk. The cost is small (3,000 transcripts of 3,000 tokens is 9 million tokens, $0.18, estimated). The reason to wait is complexity, not money.
- Keyword search covers full transcripts from the first day (`tsv_extra`), so "find the video where they said X" works on exact words before chunk vectors exist.
- `input_hash` is a hash of the embedded text. When a summary or quoted post arrives, the hash changes and the item is embedded again.
- Query vectors are cached by query text, which removes the Voyage round trip for repeated searches.
- Open question 1: use `voyage-4-large` for documents. The user chose `voyage-4-lite`. Because the space is shared, this is a re-embedding job later, not a schema change.

### C.4 Reranking

| Option | What it is | Price | When it pays |
|---|---|---|---|
| None | The fused order | – | The search bar. A person scans 25 results faster than a reranker returns. |
| Voyage `rerank-3-lite` / `rerank-3` | Cross-encoder: one request, up to 1,000 documents, 32,000-token context | $0.02 / $0.05 per million tokens; tokens = query tokens × documents + document tokens (docs: pricing.md, https://docs.voyageai.com/docs/reranker.md). The price table lists 200 million free tokens for both; the paragraph above it names only the 2.x models. | When an agent will read only the top 10 to 20 of 100 to 200 candidates and the query is a plain "relevant to this text" |
| Jev | One typed question per item, with a probability | $0.042 per million input tokens (docs: https://docs.typesafe.ai/models.md) | When the judgment is a criterion: "is this a tool", "is this about RAG", "how useful for project X". The answer can be thresholded and is cached for reuse. |

- Reranking 50 summaries of about 80 tokens with a 15-token query is about 4,750 tokens: $0.0001 with `rerank-3-lite` (estimated). Jev on the same 50 costs about $0.0002 (estimated). Money does not decide this.
- Decision: v1 ships `jev` only, because organising needs it anyway. `rerank` is an optional pipeline stage added later if section A shows a gap. A rerank score cannot be reused for another query; a Jev answer to a label question is reused forever.

### C.5 Snippets and highlights

- Computed only for the rows on the page (25 to 100), never for the whole result.
- Keyword hit: `ts_headline` on the field that matched, with `MaxFragments=2, MaxWords=18, MinWords=8`, and the field named when it is not the body ("in transcript", "in note"). `ts_headline` re-parses the original text, so it is given at most the first 5,000 characters around the match (docs: textsearch-controls, "Highlighting Results").
- Semantic-only hit: the summary if there is one, otherwise the first 160 characters of the body. No highlight, and a small "related" mark.
- Agents get the same thing through `show snippet:120`.

### C.6 Sort orders

relevance (default when there is text), saved (default with no text; save order, newest first), posted, likes, views, stars, duration, conf, random with a seed, and any Jev answer by its alias.

### C.7 Stable pagination of a ranked result

- A ranked search stores its ordered ids (top 500) and scores as a result set, the same object as an agent's handle (section E). The URL carries `rs=<handle>&page=3`. Every page is a slice of that snapshot, so page 3 does not shift while you read.
- The snapshot lives 30 minutes from last use. After that the search runs again and the app shows "results refreshed".
- A filter with a plain sort (saved, posted) does not need a snapshot. It pages by keyset on (sort key, `seq`), as `DESIGN.md` section 9 already says.
- Beyond 500 ranked results the app says "refine the search".

### C.8 Expected latency (all estimated; measured in build step 3)

| Step | Time |
|---|---|
| Parse and compile | under 1 ms |
| Filter plus keyword ranker | 10 to 30 ms |
| Query embedding (Voyage round trip) | 80 to 250 ms; 0 when cached |
| Exact vector scan, whole library | 20 to 60 ms |
| Fusion, snapshot write | under 5 ms |
| Snippets for one page | 10 to 40 ms |
| **Hybrid search, first time** | **150 to 400 ms** |
| **Hybrid search, cached query; or keyword only** | **50 to 120 ms** |
| Autocomplete counts | 10 to 40 ms |
| Jev on 300 summaries | 1 to 4 s |
| Jev on 30,000 summaries | 40 to 110 s, as a background job |

---

## D. Jev search

### D.1 What it is

A stage that asks Jev one typed question about each item in a set, **reading only the item's summary**, and attaches the answer to the set. The set comes from qualifiers and, usually, a hybrid search. Nothing else about the item is sent.

Jev facts used here:

| Fact | Value | Status |
|---|---|---|
| Price | $0.042 per million input tokens; output free | docs: https://docs.typesafe.ai/models.md |
| Limits | 64,000 tokens per request, 32,000 for the state plus any one question. No limit on the number of questions in a request. | docs, same page |
| State | Read once per request; every question is evaluated against it in parallel and in isolation | docs, same page and https://docs.typesafe.ai/introduction.md |
| Batch API or discount | None found. "Batching" means many questions in one request. | docs (v4 found the same) |
| Rate limits | Per organisation, shown in the developer console. Not published. | docs |
| Types | Choice, Score (2 to 10 ordered levels), Noul (probability of yes), Box | docs: https://docs.typesafe.ai/primitives/score.md, .../noul.md |
| Model | `jev-1.14.0`, pinned. An alias moves and answers can change. | docs |
| Tokens for 8 Nouls on a 15-word state | 942 per request, so about 118 per question including a path, a definition and criteria | measured (v4) |
| Whether the state is billed once per request or once per question | Not confirmed | open since v4 |

### D.2 Request shape

Kosha packs many items into one request, the way TypeSafe's own search cookbook does (docs: https://docs.typesafe.ai/cookbooks/infinite_cardinality_choice.md, 300 one-item questions per request):

- **State**: the question's context, sent once. For example the project description and what counts as useful.
- **Questions**: one per item. The item's summary is inside the question's instructions.
- 100 items per request, 8 requests in flight. That is about 10,000 tokens a request, far under the limit. The cookbook notes that very large requests sometimes draw an overload response, so the client retries with backoff.

A second shape exists for labeling many labels at once: state = one item's summary, questions = one Noul per candidate label. That is the v4 shape and is used only by `score_labels` (E.2). Both go through the same client and the same cache.

### D.3 How candidates are narrowed first

1. Qualifiers select the set.
2. Optionally `hybrid "<text>" top N` keeps the N best. For a project-relevance search N is 300 to 500.
3. `jev` runs on what is left.

Guards: the stage prints its estimate before spending. A set over 2,000 items, or an estimate over $0.50, returns `needs_confirm` with the number. A full-library scan is allowed and cheap (D.6); it is just never the default.

### D.4 Question design

| Use | Type | Why |
|---|---|---|
| Filter or tag fit: "is this about X" | **Noul**, with `true` and `false` criteria taken from the tag's definition | One number that can be thresholded. v4 measured labels accepted at 0.8 as about 97% correct on account names (20 audited items per band). |
| Ranking by usefulness for a project | **Score**, four levels | The docs say a Noul is the probability of yes, not a degree; for a degree, use a Score. |
| Several facets at once | Several questions in the same stage: `jev "q1" as a, "q2" as b` | Each facet is its own answer; code combines them |

The four Score levels for project relevance:

0. Unrelated to the project.
1. Same broad area, nothing usable.
2. Contains a usable idea, technique or reference.
3. Directly addresses the need: a how-to, a tool or a worked example.

The Score comes back as a number from 0 to 3 with a confidence. Kosha stores both and exposes the score divided by 3, so every Jev answer is on a 0 to 1 scale.

Rules for wording, from the TypeSafe docs and v4:

- One specific thing per question. Split "useful and recent and a tool" into facets.
- Jev reads literally. Spell the abbreviation out and say what counts: "retrieval-augmented generation (RAG): retrieving documents to ground a language model's answer; includes chunking, rerankers, vector search for LLM apps".
- Do not ask Jev whether the tag tree is good. In v4 it said yes to all 10 proposed splits.

### D.5 Caching

```
jev_questions (q_hash, type, instructions, criteria, model, first_asked_at)
jev_answers   (q_hash, item_id, input_hash, value, detail, asked_at)      key: all three
```

- `q_hash` = hash of the model id, the type, the instructions and the criteria.
- `input_hash` = hash of the exact summary text sent.
- Asking the same question of the same summary again costs nothing and returns at once.
- A reworded question is a new `q_hash` and is asked again. A changed summary is a new `input_hash` and is asked again. Nothing has to be invalidated by hand.
- This table also replaces Constella's score store (section H).

### D.6 Cost

Assumptions (all estimated): a summary is about 45 tokens; the question frame around it 15; per-question overhead 30; so **about 90 tokens per item**, plus a 150-token state per request of 100 items. Worst case: the state is billed with every question, which adds 150 tokens per item.

| Items scanned | Expected | Worst case |
|---|---|---|
| 300 (a typical narrowed set) | $0.001 | $0.003 |
| 1,000 | $0.004 | $0.010 |
| 10,000 | $0.04 | $0.10 |
| 30,000 | $0.12 | $0.30 |

Each extra facet adds the same again. A repeated scan costs $0. `DESIGN.md` estimated $0.25 for 30,000 items reading 200 tokens of text each; reading summaries is cheaper and more even.

One measurement settles the assumptions before anything is built on them (build step 0): 100 real summaries, one question, both shapes, comparing billed tokens, answers and latency. Cost under $0.05.

### D.7 Items with no summary yet

- Jev always has something to read. If `summary_status` is not `done`, the stage sends a **provisional summary**: the first 300 characters of the item's composed text (title, body, quoted text, link titles). It is free and deterministic.
- The answer is cached under the provisional text's hash. When the real summary arrives the hash differs, so the item is asked again the next time the question runs.
- The response always says how many items used provisional text: `provisional=212`.
- `is:thin` items (almost no text and no media extract) are skipped and counted: `skipped_thin=57`. They need a media extract first.

### D.8 How and when summaries get written

Jev depends on summaries, so they are not on demand. They are written for every item by a background job, right after capture.

| Item | Summary | Cost |
|---|---|---|
| Composed text of 400 characters or less, no article, no transcript | The text itself. `summary_source = verbatim`. | $0 |
| Longer posts, articles, quote posts with a long quote, anything with a transcript | Written by a small LLM. `summary_source = llm`. | below |
| A video with almost no text and no transcript | None. `is:thin`. Written when a media extract arrives. | – |
| Any item | You or an agent can overwrite it. `summary_source = human` or `agent`. | – |

Prompt shape:

- 20 items per call. Input: a JSON list of `{id, source, author, text}`, where `text` is the composed text, cut at 6,000 characters (transcripts at 3,000). An item that was cut is flagged `summary_truncated` so it is never mistaken for a full reading.
- Instruction: one summary per item, 40 words at most, written so that a yes/no topical question can be answered from it alone. Say what it is, the specific subject, the named tools, libraries or people, and the concrete claim or offer. No filler, no "this post". Return `null` when the text is too thin.
- Output: structured JSON `[{id, summary, kind}]`. `kind` is the genre used by the `kind:` qualifier and costs nothing extra.

Model: the small, fast class. The id lives in one constant and is called through LiteLLM, as Constella planned. Priced example: `claude-haiku-5-5` at $0.10 input and $0.50 output per million tokens, with a 50% discount on the Batch API (docs: the Claude API reference bundled with Claude Code, price table cached 3 Nov 2026).

Cost for 30,000 items (estimated):

- About 55% need an LLM summary: 16,500 items. The rest are verbatim.
- Input: 16,500 × 580 tokens = 9.6 million = $0.96.
- Output: 16,500 × 60 tokens = 1.0 million = $0.50, plus an allowance of half as much again for the model's reasoning tokens = $0.25.
- **About $1.70, or $0.85 through the Batch API** when a wait of up to a day is acceptable. Ongoing: about 20 new items a day, under $0.01 a week.
- For comparison, v4 measured $0.36 per 1,000 one-line descriptions with `grok-4.7`: $5.94 for the same 16,500.

When a transcript arrives later, the summary's input hash changes and the summary is rewritten; the embedding and the cached Jev answers then refresh on their own.

---

## E. Elemental operations for agents

### E.1 Form: both

**One tool, `query`, that takes a pipeline expression. A pipeline can read from and write to named result sets (handles).**

- A pipeline alone covers a chain: filter, narrow, check, tag. One tool call, one small response.
- Handles cover everything a chain cannot: branching, coming back to a set later, set algebra, and reviewing a slice of a set that was already paid for.
- An agent already knows this shape from the shell: pipes, `grep`, `head`, `sort`, redirect to a name.
- One tool with a grammar costs about 300 tokens of tool description. Twenty separate tools would cost several thousand in every session. The full grammar is returned by `query "help"` only when asked (about 900 tokens).

```
pipeline := source ( "|" stage )* [ ">" "@" NAME ]
source   := filter-expression (section B)  |  "@" NAME  |  "all"
```

Three rules make it predictable:

1. **A stage only ever sees the set that came in.** `semantic` in the middle of a pipeline ranks the incoming set; it does not go back to the library. To widen, use `union`.
2. **The response never contains item text unless a `show` stage asks for it**, and then only the named fields at the named lengths.
3. **A write is the last stage** and follows the preview rules in E.4.

### E.2 Operations

Costs are per call. "UI" marks operations the web app also uses, through the same service function behind `POST /api/query`.

| Stage | Input → output | Money | UI |
|---|---|---|---|
| *filter-expression* as source | Library → set | – | search bar, sidebar |
| `where <filter>` | Set → smaller set. May also test an attached answer: `where rag:>=0.9`, `where rag:0.5..0.9`. | – | bulk filters |
| `grep [-v] <text atoms>` | Same as `where` with only text operators. `-v` inverts. | – | |
| `semantic "<text>" [top N]` | Set → the N nearest by meaning, in order, with `score` | one query embedding, about $0 | semantic mode |
| `hybrid "<text>" [top N]` | Set → the N best by fused keyword and meaning | same | search bar default |
| `similar <id \| @name> [top N]` | Set → nearest to an item, or to the average vector of a set | – | "Find similar" |
| `rerank "<text>" [top N]` (later) | Set → reordered by the Voyage reranker over summaries | C.4 | |
| `jev "<question>" [as NAME] [>= p] [levels "a;b;c;d"]` | Set → same set with an answer attached per item; `>= p` keeps only those at or above p. `levels` makes it a Score. Several questions separated by commas. | D.6 | Relevance scan panel |
| `score_labels [#subtree] [cutoff p] [dry]` | Set → for each item, one Noul for each of its 8 nearest labels, and v4's assignment rule. This is Constella's `score`. With `sample 300 \|` in front and `dry`, it is v4's dry run. | about 1,070 tokens an item (v4, estimated) | later |
| `sort <key> [asc\|desc]` | Set → ordered. Keys as in C.6, plus any answer alias. | – | sort menu |
| `head N`, `tail N`, `skip N` | Set → slice of the current order | – | pagination |
| `sample N [seed S]` | Set → N random items, repeatable | – | |
| `union <src>`, `intersect <src>`, `except <src>` | Two sets → one. `<src>` is a handle or a parenthesised pipeline. | – | |
| `count [by <dim>] [top N]` | Set → a count, or counts by author, domain, tag (`tag:1` for top level), source, folder, type, kind, lang, list, month, year | – | autocomplete counts, sidebar counts |
| `stats <field>` | Set → n, sum, min, median, max of duration, likes, views, len, or an answer alias | – | cost estimate for transcripts |
| `hist <alias>` | Set → ten-bucket histogram of an attached answer | – | |
| `terms [top N] [vs all \| @name]` | Set → its most distinctive stems against the library or another set: `stem, lift, n_in_set, n_in_library` | – | tag review, later |
| `groups K [samples M]` | Set → K embedding groups (K ≤ 40), each with a count, M samples of 80 characters, three distinctive stems, the two nearest groups and the nearest tag. Each group becomes a handle `@h7.g3`. This is Constella's `overview`. | – | tag review, later |
| `show <field[:chars]>, ...` | Set → rows. Fields: id, author, date, source, type, kind, title, text, body, summary, snippet, quote, transcript, note, url, domain, links, tags, primary, score, likes, duration, and any answer alias. | – | result rows |
| `tag "<path>" [as primary\|secondary\|other\|auto] [conf ALIAS]` | Set → tag rows. `conf` stores the named answer as the confidence. | – | bulk tag |
| `untag "<path>"` | Set → removes the tag where an AI or this agent attached it; rows attached by a human are left alone unless the call comes from the UI | – | bulk untag |
| `roles auto` | Set → for each item, among AI-attached tags: highest confidence becomes primary, next secondary, the rest other. A tag and its own ancestor never both count; the deeper one wins. Human-set roles are kept. | – | |
| `star`, `unstar`, `read`, `unread`, `archive`, `unarchive` | Set → state change | – | bulk mode |
| `list "<name>"` | Set → appended to a hand-made list (created if missing). A list does not expire. | – | add to list |
| `save view "<name>"` | A pure filter and sort → a saved view | – | saved views |
| `> @name` | Names the result set | – | |

How `terms` works: Postgres `ts_stat` counts, for each stem, the documents of the set that contain it (docs: https://www.postgresql.org/docs/16/textsearch-features.html). The same counts for the whole library sit in the `term_df` materialized view, refreshed nightly. `lift = (n_in_set / set size) / (n_in_library / library size)`, for stems in at least 3 items of the set. The stems are Snowball stems, so "retrieve", "retrieval" and "retrieving" all count as `retriev`. That is the rare-term idea in the user's example, and its output can be fed straight back as `grep retriev*`.

### E.3 What a response looks like, and its token footprint

Every response starts with one header line and is capped at 1,500 tokens by default (a parameter raises it to 4,000). Item ids are the short integer `seq`, two tokens each, not a UUID.

| Ending stage | Response | Tokens |
|---|---|---|
| None, or `> @name` | Header plus 3 samples of 40 characters | 50 to 70 |
| `count` | Header only | about 25 |
| `count by … top N` | N lines of `value  n` | 25 + about 8 per line |
| `terms top N` | N lines of `stem  lift  n  n_lib` | 25 + about 9 per line |
| `hist`, `stats` | One or two lines | 40 to 60 |
| `groups K samples 3` | K blocks | about 90 per group; 40 groups is the 4,000 cap |
| `jev …` | Header, cost, provisional and skipped counts, histogram | 90 to 130 |
| `show id,text:40` | One line per row | about 17 per row |
| `show id,author,summary:120,score` | One line per row | about 45 per row |
| A write, preview or result | Count, breakdown, 5 samples, confirm token or batch id | 120 to 180 |

Header, in full:

```
n=412 @h7 (24h) cost=$0.0000 run=$0.0031/$1.00
```

When rows would pass the cap, the response stops at a whole row and ends with `showing 37 of 412; use head/skip`.

### E.4 Writes: preview and confirmation

| Size of the write | What happens |
|---|---|
| 25 items or fewer, onto an approved tag | Applied at once. The response still gives the count, 5 samples and a `batch` id. |
| More than 25 items | Nothing is written. The response is a preview: the count, how many already have the tag, how many are skipped because you removed that tag before, 5 samples, and `confirm=c_9f2a`. Calling again with that token applies it. |
| More than 500 items, or the tag is new | Applied after confirmation, but the tag stays `proposed`. It does not show in normal views until you approve it in the tag review screen, which shows the count and samples. |

- The confirm token is bound to the exact set that was previewed and lasts 10 minutes. What is confirmed is what was shown.
- A tag you removed from an item is never put back. Rows a human attached are never changed by an agent.
- Every write records a `batch_id`. `runs undo <batch>` reverses it.
- An MCP token can approve tags only if it was created with the `approve` scope, which is off by default.
- The thresholds 25 and 500 are constants.

### E.5 Handles

```
result_sets (id, name, pipeline, item_ids int[], answers jsonb, created_at, expires_at)
```

- Every `query` call saves its result as a handle and returns the name (`@h7`). `> @name` gives it your own name.
- A handle is a snapshot: ordered ids, the last ranking score, and any attached answers by alias.
- Cheap: 30,000 ids are 120 KB.
- Unnamed handles expire after 24 hours, named ones after 7 days from last use. At most 200 live handles; the oldest unnamed go first. `list "<name>"` is how a set is kept for good.
- An item deleted after the snapshot is skipped when the handle is read.

### E.6 Spend, budgets and long runs

- `query` takes `run` (a label-run id, G.3) and `budget_usd`. The header shows this call's cost and the run's total against its budget.
- A paid stage estimates first. If the estimate is over $0.50, or over what is left of the budget, the response is `needs_confirm est=$0.61` and nothing is spent.
- Every paid call is a row in `spend_ledger` (time, run, stage, provider, tokens, dollars, and how many answers came from the cache). Cache hits are recorded with what they would have cost, so two strategies can be compared fairly even when the second one benefits from the first one's answers.
- A stage that runs longer than 20 seconds returns `running job=j12 done=8400/30000`. Calling the same query again returns progress or the result. Answers are stored as they arrive, so a killed run resumes without paying twice.

### E.7 The user's RAG example, end to end

Goal: tag the bookmarks about retrieval-augmented generation. Library of 30,000. Tokens are what the agent reads back; dollars are estimated.

| # | Call | Agent reads | Tokens | Money |
|---|---|---|---|---|
| 1 | `all \| hybrid "retrieval-augmented generation RAG" top 300 \| count by author top 12` | 12 authors with counts | 120 | $0 |
| 2 | `from:@a,@b,@c,@d,@e,@f,@g,@h \| grep retriev* > @rag_authors` | `n=412`, 3 samples | 70 | $0 |
| 3 | `@rag_authors \| head 20 \| show id,text:40` | 20 rows of 40 characters | 360 | $0 |
| 4 | `all \| hybrid "RAG: retrieval-augmented generation, chunking, rerankers, vector search for LLM answers" top 400 \| union @rag_authors > @rag_cand` | `n=706`, 3 samples | 70 | $0 |
| 5 | `@rag_cand \| jev "Is this item about retrieval-augmented generation (RAG): retrieving documents to ground a language model's answer, including chunking, rerankers and vector search for LLM apps?" as rag > @rag_scored` | Histogram, `provisional=31 skipped_thin=9` | 110 | $0.003 (worst case $0.007) |
| 6 | `@rag_scored \| where rag:>=0.9 \| tag "AI/RAG" as other conf rag` | Preview: 388 items, 12 already tagged, 5 samples, `confirm=c_9f2a` | 160 | $0 |
| 7 | Same call with `confirm: "c_9f2a"` | `tagged 376 batch=b_31` | 40 | $0 |
| 8 | `@rag_scored \| where rag:0.5..0.9 \| sample 12 \| show id,summary:90,rag` | 12 rows from the uncertain band | 380 | $0 |
| | **Total** | | **about 1,300 read, about 400 written** | **about $0.003** |

- The same thing as one line, when no review is wanted: `from:@a,@b,@c | grep retriev* | semantic "RAG" top 200 | jev "about retrieval-augmented generation?" >= 0.9 | tag "AI/RAG"`.
- Without these operations the agent would read 706 summaries itself: about 35,000 tokens, and still have to write 388 ids back.
- Step 4 is what stops the result depending on the author list: authors give precision, the hybrid search gives recall, Jev decides.
- The role is `other` on purpose. Primary and secondary are decided once, by `roles auto`, after all labels are scored (G.1 step 7).

---

## F. Agentic search

### F.1 The loop

The same five steps whether the driver is Kosha's built-in "Ask" or an external agent on MCP.

1. **Understand.** Name the query type (the six in section A). Pull out anything that is a qualifier: author, time, type, domain, tag, state. What is left is the topic text.
2. **Plan.** Write one pipeline: qualifiers, then `hybrid` on the topic text, ending in `show id,author,date,snippet:100,score | head 15`. Add a second line, `count by tag:1 top 3`, only when a refinement is likely.
3. **Inspect.** Read the compact rows. Decide: found, too few, too many, or needs a judgment.
4. **Refine**, at most three times:
   - Too few: drop the weakest qualifier, add synonyms with OR, or switch `hybrid` to `semantic`.
   - Too many, and the need is a criterion or "useful for": add `jev` on the top 300, with a Score for usefulness or a Noul for a yes/no criterion.
   - Too many, and a qualifier would cut it: add the qualifier.
5. **Answer.** Read `summary:300` of at most 12 items, and `body:1500` of at most 3 when the answer needs detail. Reply briefly and cite item ids. The app shows the cited items as cards and offers the whole result set ("all 47 matches") through its handle.

### F.2 Tools, stop rules, budget

| | Built-in Ask | External agent on MCP |
|---|---|---|
| Tools | `query` with read stages and `jev` only. No write stages. The top two levels of the tag tree are in the system prompt (about 300 to 600 tokens). | All six tools (section H) |
| Tool calls per question | 6 at most | The agent's own limit |
| Tokens per question | Each tool result at most 1,200 tokens. About 26,000 input tokens summed over the loop and 1,300 output (estimated). Hard stop at 40,000 input. | Kosha only controls what it returns: the per-response cap |
| Jev money per question | $0.02 without asking; above that the app shows the estimate and a button | The `budget_usd` the agent passes |
| Stop when | A known item is found with a clear margin; or the top 10 did not change after a refinement; or the limits are reached | same |
| If nothing fits | Say so, and show the nearest results marked "related, not a match" | same |

Model class for Ask:

- **Mid-tier model at low or medium effort** as the default. The planning step turns loose language into exact qualifiers, and the user called this the most important feature. Priced example: `claude-sonnet-5-5` at $2 input and $10 output per million tokens (docs: the bundled Claude API reference, cached 3 Nov 2026). About **$0.03 to $0.07 a question** (estimated; the lower figure with prompt caching of the system prompt and tool description).
- The small class (`claude-haiku-5-5`, $0.10 and $0.50) would be about **$0.003 to $0.01 a question** (estimated). Build step 10 runs all of section A through both. If the small one gets the same answers, it becomes the default.
- The model id is one constant. The loop is an ordinary tool-use loop in Kosha's worker with one tool.
- An external agent costs Kosha nothing in LLM tokens. Claude Code or Grokbot pays for its own reasoning; Kosha's part is the Jev and embedding cents.

### F.3 v1 and later

| v1 | Later |
|---|---|
| The loop above with the `query` tool | Remembering which rewrites worked for you |
| Known-item, topical, project-relevance, aggregate, similarity and maintenance queries | Automatic contradiction finding with no claim named |
| Contradictions on a claim the user names (Q14) | Voyage `rerank` stage; chunk vectors for transcripts |
| Answers that cite item ids | Saved Ask threads; learning from which results you open |

### F.4 Behaviour on each query in section A

| # | Plan |
|---|---|
| Q1 | `is:thread saved:2026-03..2026-05 \| hybrid "pgvector HNSW tuning" top 20`. If empty, drop `saved:` (it may be an estimate) and say so. |
| Q2 | `from:@simonw \| hybrid "SQLite full-text search compared to embeddings" top 10` |
| Q3 | `type:video \| semantic "demo of an agent booking a flight" top 30`. If the top results are weak: tell the user how many videos have no transcript and offer to extract them. |
| Q4 | `domain:github.com \| hybrid "Postgres job queue" top 15 \| show id,links,snippet:80` |
| Q5 | `from:@karpathy \| hybrid "evals evaluation benchmarks" top 40 \| show id,date,summary:200`. Read, then answer by theme with citations. |
| Q6 | `type:video duration:<5m \| hybrid "prompt caching" top 20` |
| Q7 | `domain:github.com (#ai/rag OR retriev* OR RAG) \| sort stars desc \| head 20 \| show id,links,stars`. Needs the star enrichment; until then it sorts by likes and says so. |
| Q8 | `type:article lang:en posted:2026 len:>3000 \| hybrid "MCP server authentication OAuth" top 20` |
| Q9 | `all \| hybrid "Chrome extension intercept fetch XHR service worker MV3 content script" top 400 \| jev "How useful is this for designing a Chrome extension that intercepts fetch requests?" levels "…" as use \| sort use desc \| head 25 \| show id,summary:150,use` |
| Q10 | As Q9, then `count by tag` and `groups 6` on the top 60, and answer in a reading order by group |
| Q11 | `all \| hybrid "job queue background jobs without Redis" top 300 \| jev "Is this a tool or library, not an opinion?" as tool, "Could it run a job queue on one small server without Redis?" as fit \| where tool:>=0.7 fit:>=0.7` |
| Q12 | `all \| hybrid "AI agents" top 800 \| jev "Is this about AI agents?" >= 0.8 > @agents`, then `@agents \| count by author top 10` and `@agents \| count by month` |
| Q13 | `#ai/rag has:link \| count by domain top 15` |
| Q14 | `all \| hybrid "fine-tuning versus RAG" top 150 \| jev "Does this argue fine-tuning is the better way to add knowledge?" as ft, "Does this argue RAG is the better way?" as rag`. Read the top 5 summaries on each side and cite opposing pairs. |
| Q15 | `is:untagged saved:90d.. \| groups 12`, then `terms` on the three largest groups |
| Q16 | `all \| similar 18234 top 20` |
| Q17 | `id:1,2,3,4,5 > @five`, then `-from:@a,@b,@c \| similar @five top 25` |
| Q18 | `is:starred is:untagged \| count`, then `show` the first page |
| Q19 | `(tagged-by:ai #ai conf:<0.85) \| sort conf asc \| show id,primary,conf,summary:80` |
| Q20 | `type:video duration:>10m -has:transcript \| stats duration` |

---

## G. Agentic organising

### G.1 Playbook (written to be handed to Claude Code or Grokbot)

> You are labeling a bookmark library through Kosha's MCP tools. Item text stays on the server. Read counts, terms and short samples, not whole items. Every `query` response shows this run's spend; never pass the budget.
>
> **1. Start a run.** `runs start name=<name> strategy=<name> budget_usd=<n>`. Use the returned `run` id and its namespace in every call.
>
> **2. Get the map (about 6,000 tokens in all).**
> - `all | count by source`, `all | count by type`, `all | count by folder top 30`
> - `all | count by author top 40`, `all | count by domain top 30`, `all | count by month`
> - Distinctive terms of each folder and of the top 10 authors: `folder:"<name>" | terms top 12`. (`terms` on `all` compares the library with itself and says nothing.)
> - `all | groups 40 samples 3`
> - `tags` with no operations, to read the current tree with counts.
>
> **3. Propose or revise the tree, with the current tree in front of you.**
> - Seeds come first: source folders, tags the user made, playlist names. Keep their names.
> - Write each label as a path, a one-sentence definition, and what it excludes. The definition becomes the Jev question, and Jev reads literally.
> - Send the changes as one `tags` call with a list of operations (add, rename, redefine, move, merge, delete). New tags arrive as proposed.
> - Start with 15 to 25 top-level labels. Go deeper only where step 6 says so.
>
> **4. Dry run.** `all | sample 300 seed 1 > @dry`. For each top-level label, run step 5a to 5c on `@dry` only. Read coverage: `@dry | where tags:0 | count`. If more than 30% is untagged, look at `@dry | where tags:0 | groups 8` and fix the tree. Repeat once or twice. This costs about a cent.
>
> **5. Per label.**
> - **a. Candidates, by cheap filters.** Union of: `all | hybrid "<definition>" top N` (N about three times the size you expect, at most 3,000); `grep` on two or three distinctive stems from `terms`; `from:` the authors whose share of the first set is high; the seed folder. Save as `@c_<label>`.
> - **b. Sanity check, about 300 tokens.** `@c_<label> | terms top 12`, `| count by author top 8`, `| sample 8 | show text:60`. If it looks wrong, fix the filters before paying.
> - **c. Ask Jev.** `@c_<label> | jev "<question from the definition>" as p > @s_<label>`. Read the histogram.
> - **d. Tag the confident ones.** `@s_<label> | where p:>=<cutoff> | tag "<path>" as other conf p`, then confirm.
> - **e. Review the uncertain band.** `@s_<label> | where p:0.5..<cutoff> | sample 12 | show id,summary:90,p`. If most belong, the definition is too narrow: rewrite it and ask again. If most do not, leave them.
> - **f. Check recall.** `#<path> | terms top 10`. For a stem you did not search for: `all | grep <stem>* | except @c_<label> | sample 10 | show text:60`. If those belong, add the stem to step a and run c and d on the new items only.
>
> **6. Go deeper where the counts say so.** Read the tree again with `tags`. A label holding more than 25% of the library is too broad: split it using `#<path> | groups 12`. A label with more than about 150 items and visible sub-themes gets children; run step 5 on `#<path>` instead of `all`, with the full path in the question. A label with fewer than 5 items merges into its parent. Two siblings sharing more than half of the smaller one's items: merge them or sharpen both definitions.
>
> **7. Assign roles.** `all | roles auto`, then confirm.
>
> **8. Leftovers, at most three rounds.** `is:untagged | count`. Then `is:untagged | groups 30`. For a group with a clear theme, add a label and run step 5. Report `is:thin | count` to the user: those need a media extract, not a label.
>
> **9. Keep totals.** After each label and each round, call `runs status`. It returns coverage (share of items with a primary tag), untagged count, labels, dollars spent, and tokens Kosha has served you.
>
> **10. Stop when** any of these holds: coverage reached the target (default 85%); a leftover round tagged under 10% of what was untagged; three leftover rounds are done; or the next call would pass the budget. Then `runs finish` and report coverage, spend, the tree, and what is left.

Notes on the playbook:

- **Cut-off.** The user's example used 0.9. v4 measured 0.8 as about 97% correct, and at 0.9 a third of the items came back for about 3 more points (measured, on account names). Default: tag at 0.8, and show 0.8 to 0.9 as a "doubtful" band in the review screen. The 200-item hand-labelled sample (G.3) decides.
- **Approval.** New tags stay proposed until the user approves them (E.4). On a first full pass the agent stops after step 4 and asks for approval of the top level.
- **New items.** Each day, with no agent: `is:untagged captured:1d.. | score_labels`. That walks each new item down the existing tree at about $0.002 a day (v4, estimated).

### G.2 Strategies worth testing against each other

| # | Strategy | How it labels | Expected strength | Expected weakness |
|---|---|---|---|---|
| S1 | **Constella v4 loop** (baseline) | Item-centred. Every item is asked about its 8 nearest labels (`score_labels`); pending items are regrouped; three rounds. | High coverage; multi-label for free | Highest Jev cost: $3 to $6 for 30,000 (v4, estimated) |
| S2 | **Label-by-label retrieval** (the playbook above) | Label-centred. Cheap filters build a candidate set per label; one Jev question per candidate. | Lowest Jev cost; every decision is inspectable | Recall depends on the candidate filters; items no label retrieved are never asked |
| S3 | **Bottom-up from embedding groups** | `groups 40`, name each group, zoom into big groups, then Jev verifies membership per named group | Finds topics nobody thought to seed | Group boundaries are arbitrary; two runs gave 19 and 14 top labels in v4 (measured) |
| S4 | **Author, folder and domain first** | Sample 10 items per frequent author or folder; give the author a likely label; Jev checks only that author's items against it | Very cheap candidates; uses the strongest free signal in bookmarks | Authors who post on many topics; items from rare authors |
| S5 | **LLM reads summaries in batches** (control) | A small LLM is given the tree and 30 summaries per call and returns labels. No Jev. | Simple; about $0.50 to $0.80 for 30,000 (estimated) | No probability to threshold; drifts across batches |
| S6 | **S2 for the head, S1 for the tail** | Label-by-label for the largest labels, then `score_labels` on what is still untagged | Likely the best cost for the coverage | Two mechanisms to explain |

### G.3 Making the comparison easy

- **A run** (`label_runs`: id, name, strategy, namespace, budget, start and end) owns its spend ledger rows and its write batches.
- **A namespace** is a separate tag tree and separate tag rows over the same items. `main` is what the app shows. Each experimental run writes to its own namespace, so strategies never overwrite each other and none of them touches real tags. `runs promote <run>` copies a namespace into `main` as proposed tags.
- **Jev answers are shared across runs** (the cache does not care who asked). The ledger records cache hits at what they would have cost, so the second strategy is not flattered.
- **Two stages**, because strategies that invent their own tree cannot be scored against one answer key:
  1. *Tree stage.* Run the tree-building part of S1, S3 and S4 on the dry-run sample. Compare by coverage of the 300 sample at the cut-off, number of labels, and the too-broad and overlap flags. The user picks or merges a tree.
  2. *Assignment stage.* Freeze that tree. The user hand-labels about 200 items in a `gold` namespace (a stratified sample: source, short and long, video, quote, link). Each strategy then assigns the full library under the frozen tree.

| Metric | How it is computed |
|---|---|
| Coverage | Share of items with a primary tag at or above the cut-off |
| Agreement with the gold sample | Primary matches exactly; primary matches at the top level; gold primary is among the item's tags; per-label precision and recall |
| Jev and LLM cost | From the ledger, both billed and "as if nothing were cached" |
| Agent tokens | Tokens Kosha served in tool results (counted by Kosha) plus the agent's own reported total |
| Time | Run start to finish, and Jev wall time |
| Tags per item, leftovers, thin items | `count` queries on the namespace |
| Stability | Run the same strategy twice; adjusted Rand index between the two |

`runs compare <a> <b> …` prints this table.

---

## H. Kosha and Constella

### H.1 Where the line goes

**Recommendation: Constella becomes a small Python library that Kosha imports. There is one MCP server, Kosha's.**

Why the line moved: once Kosha owns filtering, grouping by handle, Jev calls and the Jev cache, the things v4 gave Constella its own process for are already in Kosha.

| v4 Constella part | With Kosha's operations |
|---|---|
| `sync`, the SQLite workspace, `vectors.npy`, the seven REST endpoints, `RestSource` | Not needed. The data is already in the same process. |
| `overview` | The `groups` stage. The grouping and sample-picking code comes from the library. |
| `apply_labels` | The `tags` tool |
| `score` | The `score_labels` stage. The shortlist of 8 nearest labels and the assignment rule come from the library. |
| Jev client and stored answers | One client and one `jev_answers` table, in Kosha, shared with Jev search |
| Spend ledger, approval rule, resumable runs | Kosha's `spend_ledger`, write previews and jobs |

What stays in the Constella library, as pure functions with no I/O:

- `group(vectors, k, seed)` and `pick_samples(vectors, groups, m)`
- `shortlist(item_vectors, label_vectors, n=8)`
- `assign(scores, cutoff, extra_cutoff)`: the primary, secondary and other rule, including "a label and its ancestor never both count"
- `tree_hints(assignments)`: the too-broad and overlap flags
- A file runner for other datasets (a JSONL file in, labels out), so Constella still works without Kosha

What this costs:

- It reverses the earlier choice of two MCP servers.
- Constella is no longer a separate product an agent can point at any REST source. Other datasets go through the file runner or through Kosha.
- Kosha's schema gains the Jev answer cache. v4 section 4.3 argued against that; the argument no longer holds because Jev search needs the same table.
- Kosha depends on Constella's releases, and takes on numpy and a k-means implementation.
- About 40% of v4's planned 1,300 lines are never written (estimated): the store, both sources, sync, the contract tests and the fake Kosha server.
- Constella's two pre-build measurements still apply and move into Kosha's build step 0.

Options not chosen:

| Option | Why not |
|---|---|
| Keep two MCP servers, Constella doing only grouping and label scoring | Constella would need item vectors and summaries, so the sync, the cache and the contract all stay, for two tools |
| Drop Constella for bookmarks entirely | The grouping and assignment rules were measured in Constella's experiments and are worth keeping as tested code; and Constella still has other datasets |

### H.2 Kosha MCP tools

Six tools. `DESIGN.md` section 8 listed about thirty; the table says where each group went.

| Tool | Purpose | Replaces |
|---|---|---|
| `query` | Run a pipeline: filter, search, check, count, show, and bulk writes with preview. `query "help"` returns the grammar. | `search`, `find_similar`, `relevance_scan_*`, `list_recent`, `list_by_tag`, `list_untagged`, `get_item`, `get_items`, `get_thread`, `tag_items`, `untag_items`, `set_primary_tag`, `star_items`, `archive_items`, `list_archived`, `add_to_list`, `create_list`, `run_saved_view`, `save_view`, `list_saved_views`, `stats`; Constella's `overview` and `score` |
| `tags` | With no arguments: the tree with counts and flags, about 10 tokens a label. With a list of operations: add, rename, redefine, move, merge, delete, approve, reject. | `list_tags`, `create_tag`, `update_tag`, `move_tag`, `merge_tags`, `delete_tag`; Constella's `apply_labels` |
| `edit_item` | Set one item's note, summary, transcript or list position | `set_note`, `set_summary`, `set_transcript`, `reorder_list` |
| `add_item` | Save a URL, with an optional note and tags | `add_item` |
| `jobs` | Start, estimate or check background work over a set: sync, summaries, media extracts, embeddings, delete at source | `sync_status`, `sync_now`, `video_text_estimate`, `video_text_start`, `delete_at_source`; Constella's `sync` |
| `runs` | Start, finish, status, compare, promote and undo for label runs | new |

Reading a single item is `id:18234 | show body:2000,links,tags`. Reading a thread is `thread:18234 | sort posted asc | show author,text:400`.

---

## I. Build order and open questions

### I.1 Build order

Each step ends in something that can be run and checked.

| # | Build | Test that it works |
|---|---|---|
| 0 | Three measurements, about $0.10 in total, each needing a key: (a) Jev on 100 real summaries in both request shapes: billed tokens, agreement, latency; (b) 100 summaries from the small model: cost and a read-through; (c) an exact scan of 30,000 synthetic `halfvec(1024)` rows on the Hetzner box (free) | The numbers in C.8, D.6 and D.8 are replaced by measured ones |
| 1 | Schema changes of B.6. Parser, AST, printer, SQL compiler for every qualifier that needs no enrichment. | `parse(print(tree)) == tree` on generated trees. Each qualifier in B.3 has one test against a fixture of 200 items. Q13, Q18, Q19, Q20 run. |
| 2 | Keyword search: `tsv`, `tsv_extra`, trigram index, text operators, `in:`, snippets, sort, keyset pagination | Q4 runs. `retriev*` matches "retrieval" and "retrieve". `~pgvectr` matches "pgvector". |
| 3 | Embeddings job, query-vector cache, exact semantic search, hybrid fusion, `similar`, ranked snapshots | Section A's known-item and topical queries, on the real library, judged by hand. Fusion constants set. Latency table measured. Page 3 of a ranked result is identical on reload. |
| 4 | Suggest endpoint and the search bar with chips | Each row of B.5 by hand; counts equal the result counts after selecting |
| 5 | The `query` tool: pipeline parser, handles, set algebra, `show`, `head`, `tail`, `skip`, `sample`, `sort`, `count`, `stats`, `terms`, response caps; MCP and REST | Every response under its cap at 1,000 and 30,000 items. `a union b`, `intersect`, `except` against Python sets. Steps 1 to 4 of E.7 run. |
| 6 | Summaries job (verbatim rule, LLM batches, `kind`, hashes) | Every item is `done`, `verbatim` or `thin`. Changing a body rewrites its summary and re-embeds it. Spend matches the estimate within a factor of two. |
| 7 | Jev client, `jev_questions`, `jev_answers`, the `jev` and `hist` stages, ledger, budgets, background jobs | A second identical call sends nothing. A reworded question is asked again. A killed 30,000-item scan resumes. Q9 and Q11 run. |
| 8 | Write stages with preview, confirm and undo; `tags` tool; `roles auto`; namespaces and `runs` | E.7 end to end. A confirm token fails after the set changes. A tag you removed is not re-attached. `runs undo` restores the previous state. |
| 9 | Constella library: `group`, `shortlist`, `assign`, `tree_hints`; the `groups` and `score_labels` stages | `groups 40` stays under 4,000 tokens at 30,000 items and gives the same groups on a second call. `sample 300 \| score_labels dry` reproduces v4's dry run. |
| 10 | Ask mode | All 20 queries of section A through the mid-tier and the small model; tokens and cost per question recorded; the default model chosen |
| 11 | Experiments: gold sample of 200, the strategies of G.2, `runs compare` | The comparison table for at least S1, S2 and S5 |
| Later | `rerank` stage, chunk vectors, GitHub star enrichment, `mentions:`, automatic contradiction finding | |

Steps 1 to 4 are `DESIGN.md` phase 2. Steps 5 to 8 replace phases 3 and 5. Step 9 replaces phase 4.

### I.2 Open questions

| # | Question | Recommended default |
|---|---|---|
| 1 | Embed documents with `voyage-4-large` instead of `voyage-4-lite`? Queries stay on lite. | Yes. About $1 once for 30,000 items, probably $0 inside the free 200 million tokens. Voyage's launch post describes exactly this split. Changing later is only a re-embedding job. |
| 2 | Which provider and key for summaries and for Ask? | The small class for summaries, the mid-tier class for Ask, ids in constants. Priced here with `claude-haiku-5-5` and `claude-sonnet-5-5`, which needs an Anthropic key. `grok-4.7` works too at about seven times the summary cost (v4, measured). |
| 3 | Write summaries for every item up front, or on demand? | Up front. About $0.85 to $1.70 for 30,000. Jev search and labeling both read them. |
| 4 | Constella as an imported library, one MCP server? | Yes (section H). |
| 5 | Jev cut-off for writing a tag: 0.9 as in your example, or 0.8 as v4 measured? | 0.8, with 0.8 to 0.9 shown as doubtful. Settle it on the 200-item gold sample. |
| 6 | Write thresholds: apply directly up to 25 items; confirm above; keep the tag proposed above 500 or when it is new | Keep. Lower them if the first run feels unsupervised. |
| 7 | May `saved:` use the estimated save time when the source gives none? | Yes, labelled "approx." Check the estimate against LinkedIn's real dates first. |
| 8 | Bring read tracking back into scope? `DESIGN.md` excluded it; the brief lists `has_read`. | Add the `read_at` column and `is:read` only. No reminders, no unread counts. |
| 9 | How much may Ask spend on Jev without asking? | $0.02 a question, $2 a month, both settings. |
| 10 | Add the `kind` genre field with the summary? | Yes. It is free with the summary call and gives `kind:tutorial` and similar. |
| 11 | Ship the Voyage reranker in v1? | No. Add it if section A shows a ranking gap. |
| 12 | Experiments in namespaces inside the one database, or in a copy of the database? | Namespaces. One column on two tables, and runs can be compared with ordinary queries. |
| 13 | Unlimited tag depth is now allowed. Should the playbook still stop at a depth? | Soft limit of 4, as before; go deeper only for a label with more than 150 items. |

### I.3 Needed from you

1. A Voyage API key and a TypeSafe API key (already listed in `DESIGN.md` section 12).
2. A key for the LLM that writes summaries and runs Ask (question 2).
3. Your OK for the three measurements in build step 0, about $0.10 in total.

## Sources

- Voyage: `embeddings.md`, `pricing.md`, `reranker.md`, `rate-limits.md` under https://docs.voyageai.com/docs/, and https://blog.voyageai.com/2026/01/15/voyage-4/. Read 21 Nov 2026.
- TypeSafe: `introduction.md`, `models.md`, `api/system-one.md`, `primitives/score.md`, `primitives/noul.md`, `patterns/fan-out.md`, and the cookbooks `infinite_cardinality_choice.md`, `rerank_typesafe.md`, `semantic_find.md` under https://docs.typesafe.ai/. Read 21 Nov 2026.
- pgvector README, version 0.8.7: https://github.com/pgvector/pgvector. Read 21 Nov 2026.
- Postgres 16 manual: `pgtrgm`, `textsearch-controls`, `textsearch-features`, `ltree`.
- BM25 extensions: https://neon.com/faqs/pg-search-vs-pg-textsearch-vs-lakebase-search-bm25 and https://www.tigerdata.com/blog/pg-textsearch-bm25-full-text-search-postgres.
- LLM prices: the Claude API reference bundled with Claude Code, model table cached 3 Nov 2026.
- Measured figures: `DESIGN.md` section 2 (100 sampled X bookmarks) and Constella `ai_clustering_design_plan_v4.md` (1,322 account names, 11 Nov 2026).
