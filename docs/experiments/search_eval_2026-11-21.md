# Search evaluation, 21 Nov 2026

First run of the search built from `SEARCH_DESIGN.md` sections B and C, against the real library.

- Library at the time: 6,398 saved items (6,959 rows with context items), all from X, 6,369 with a vector. No tags, no stars, no lists and no media extracts exist yet, which limits Q13, Q18, Q19 and Q20.
- Postgres 17.4 with pgvector 0.8.0 on this Mac; the server and the database on the same machine.
- Everything went through `GET /api/items?q=…` on the running server, page size 25. "Latency" is the HTTP round trip seen by the client; the first search for a new text includes the Voyage call for the query vector.
- Judgments are mine, from reading the top 5 rows. Nobody else has checked them, and for the known-item queries I do not know whether the item the question describes was ever saved.

## 1. The 20 test queries

Good = the top 5 answer the question. Mixed = partly, or the retrieval is right but a part of the question is not answerable yet. Poor = the top 5 do not help.

| # | Query string used | Results | Latency | Top 5 | Why |
|---|---|---|---|---|---|
| Q1 | `pgvector HNSW tuning is:thread` | 24 | 286 ms | poor | No saved thread is about this. The rows are threads about fine-tuning, pulled in by the word "tuning". Without `is:thread` (61 results, 56 ms cached) the one pgvector item, a video, is first. `saved:` was left off: "last spring" has no matching item. |
| Q2 | `from:simonw SQLite full-text search embeddings` | 2 | 206 ms | mixed | First is his post on search-based RAG with Datasette, the nearest thing held; second is unrelated. No keyword match. |
| Q3 | `type:video agent booking a flight demo` | 33 | 214 ms | good | Four of five are videos of travel or booking agents. Found by meaning alone; no row has all the words. |
| Q4 | `domain:github.com Postgres job queue` | 0 | 201 ms | poor | Nothing passes the similarity floor inside the GitHub set. Without the filter the rows are about Postgres, none about a job queue. Only 551 of 3,850 links have a stored title, so "found through the link title" is weak. |
| Q5 | `from:karpathy evals` | 1 | 183 ms | good | The one post of his about eval benchmarks, by keyword. Reading and citing several is Ask mode, not built. |
| Q6 | `type:video duration:<5m prompt caching` | 17 | 223 ms | mixed | First and fifth are about caching. Second to fourth only contain both words somewhere; for a two-word query the keyword side has twice the weight and wins. |
| Q7 | `domain:github.com RAG` | 94 | 213 ms | mixed | All five are RAG repos or courses. "Most starred first" is not possible: no star counts are held. `sort:likes` works as a stand-in (90 results, 29 ms). |
| Q8 | `type:article lang:en posted:2026 len:>3000 MCP server authentication` | 0 | 187 ms | good | Empty is the right answer: the library's two posts on MCP authentication are short video posts (`MCP server authentication` returns exactly those two). |
| Q9 | `Chrome extension that intercepts fetch requests` | 26 | 214 ms | mixed | Chrome-extension posts, the first a how-to. None is about intercepting fetch. The graded judgment per item needs Jev. |
| Q10 | `building hybrid search on Postgres` | 36 | 231 ms | good | Four of five: BM25 plus vectors, why embeddings alone fall short, keyword versus vector databases. The first is about Postgres for agents, less to the point. No grouping of the answer. |
| Q11 | `job queue Redis alternative for a small server` | 20 | 211 ms | poor | Rows mention Redis; none is a job queue or an alternative. Needs the two Jev facets the design plans. |
| Q12 | suggest `agents from:` | 10 authors | 5 ms | mixed | Authors by count inside the "agents" result. By month is not available: there is no `count by`. |
| Q13 | suggest `rag domain:` | 10 domains | 4 ms | mixed | Domain counts inside a result work (github.com 90, youtube.com 29, …). The tag form `#AI/RAG` could not be run: the library has no tags. |
| Q14 | `fine-tuning versus RAG` | 49 | 214 ms | not judged | The candidate set is good (both "fine-tuning is a bad idea" and "fine-tuning is all you need" are in the top 5). Finding the contradiction is a Jev stage. |
| Q15 | `is:untagged saved:90d` | 827 | 14 ms | mixed | The filter is right. "A lot of" needs `terms` or `groups`. |
| Q16 | `similar:5070` | 39 | 36 ms | good | Seed is a post on prompt caching; all five are about prompt caching or LLM call cost. |
| Q17 | `similar:@five -from:a,b,c,d,e` | 30 | 36 ms | good | Five about prompt caching and inference cost from other authors. The named set had to be inserted into `result_sets` by hand: nothing creates named sets yet. |
| Q18 | `is:starred is:untagged` | 0 | 7 ms | not judged | Runs; nothing is starred yet. |
| Q19 | `tagged-by:ai #ai conf:<0.85` | 422 | 4 ms | not judged | "No tag named #ai" at position 13, which is correct here. The same query passes on the seeded test database. |
| Q20 | `type:video duration:>10m -has:transcript` | 252 | 12 ms | mixed | The set is right. The total length (685,157 s, about 190 hours, by SQL) is not returned: there is no `stats`. |

Totals: 6 good, 8 mixed, 3 poor, 3 not judged.

### Main weaknesses

1. **No relevance judgment after retrieval.** Q9 and Q11 return the right neighbourhood and the wrong items. The design expects Jev here; nothing in this build replaces it.
2. **A weak semantic match still fills the page.** When nothing is really about the query, the nearest items pass (Q1, Q11). The floor and margin below cut the worst of it, but a similarity of 0.33 means "related" for one query and "noise" for another.
3. **Short queries lean on keywords.** With one or two words the keyword ranker has weight 2.0, and `ts_rank_cd` has no idea that "prompt caching" as a pair is rarer than each word (Q6). The design's 2.0 may be too high; I did not change it.
4. **Keyword AND is strict.** In hybrid mode a row counts as a keyword hit only when it has every top-level word, so most long questions get `no_exact_matches` and are ranked by meaning alone (Q1, Q2, Q3, Q4, Q9, Q11).
5. **Missing data.** No GitHub stars, few link titles, no transcripts or image descriptions, no tags. Q4, Q7, Q13, Q19 and the video queries depend on them.
6. **No aggregates.** `count by`, `stats`, `terms` and `groups` belong to the pipeline (section E), which is not built. Suggest covers the count-by-author and count-by-domain cases only.

## 2. Latency at 6,398 saved items

Ten text queries and twelve filter queries, three passes each (one pass for the first-time rows). "Engine" is the time inside `search()`, "HTTP" adds serialising 25 rows.

| Kind | Engine median | Engine p95 | HTTP median | HTTP p95 |
|---|---|---|---|---|
| Filter only | 5 ms | 7 ms | 12 ms | 15 ms |
| Keyword | 3 ms | 9 ms | 8 ms | 19 ms |
| Semantic, query vector cached | 18 ms | 20 ms | 25 ms | 28 ms |
| Hybrid, query vector cached | 19 ms | 23 ms | 27 ms | 30 ms |
| Hybrid with a qualifier, cached | 19 ms | 22 ms | 26 ms | 30 ms |
| Semantic, first time (Voyage call) | 184 ms | 207 ms | 194 ms | 215 ms |
| Hybrid, first time (Voyage call) | 206 ms | 366 ms | 216 ms | 377 ms |
| Suggest `from:` | – | – | 6 ms | – |
| Suggest `is:` (nine counts) | – | – | 22 ms | – |

- The design estimated 150 to 400 ms for a first hybrid search and 50 to 120 ms cached. Measured: about 210 ms and about 27 ms. The Voyage round trip (about 170 ms) is nearly all of a first search.
- The exact vector scan costs about 15 ms at 6,400 vectors. If it grows in proportion, 30,000 items would be about 70 ms; that is not measured.

## 3. Embeddings

| | |
|---|---|
| Documents | `voyage-4-large`, `input_type=document`, 1,024 dimensions, stored as `halfvec` |
| Queries | `voyage-4-lite`, `input_type=query` |
| Items embedded | 6,369 of 6,398 saved items (the rest have no text) |
| Tokens | 816,184 for documents, 406 for queries |
| Cost at list price | $0.098 for documents; queries round to $0.00 (all in `spend_ledger`). The account's free allowance may make the real charge zero. |
| Requests | 51, at most 128 items or 200,000 characters each |

**Shared-space check.** Ten queries were embedded with both models and run against the large-model document vectors (4,300 items at the time). The lite and large query vectors agreed on 8.1 of the top 10 items on average (range 6 to 10), and the lite results read as sensible for all ten: for example the pgvector talk first for "pgvector HNSW index tuning", five eval posts for "how to evaluate LLM outputs", three travel-agent demos for "demo of an AI agent booking a flight". Queries stay on `voyage-4-lite`.

**Similarity scale.** On this library the best hit for a query that has real matches scores 0.50 to 0.70, the tenth 0.40 to 0.60, and unrelated items sit near 0.20 to 0.30. A query with no real match tops out near 0.35. Hence the two constants: a hit is kept when it scores at least 0.30 and within 0.15 of the best hit.

## 4. What changed from the design

| Design | Built | Why |
|---|---|---|
| B.2: bare words both filter and rank | In hybrid and semantic mode the top-level bare words rank only. In keyword mode, or with an explicit sort, they filter. | C.1's own SQL does this, and otherwise a question in plain words returns nothing. Words inside groups, negated words, prefix, fuzzy and regex terms always filter. |
| C.1: fusion in one SQL statement | Each ranker is one query; fusion (`rrf`) is a Python function | Simpler to test. Adds no measurable time. |
| C.2: one minimum similarity | A floor (0.30) and a margin below the best hit (0.15) | One fixed cut was either too loose for strong queries or emptied weak ones. |
| C.2: rank with `ts_rank_cd` on the query | `ts_rank_cd` of all top-level words together, plus 0.1 of "any word", tried stemmed and unstemmed | Scoring with OR alone ignored how close the words are and put weak rows first. |
| C.5: snippet names the field that matched | One `ts_headline` over the combined search text (title, body, quoted text, link titles, note). No field name, and transcripts are not highlighted. | Kept small. The snippet is HTML-escaped with `<mark>` only. |
| C.7: keyset paging for plain sorts | Offset paging, as the list already did | Not asked for here. |
| C.7: the URL carries a handle name | `snapshot_id` (the `result_sets` row id). The notice (`no_exact_matches`) is only on the response that made the snapshot. | |
| B.6: trigram indexes on author and link URL | Only `search_text` has one (it replaces the ones on body and title) | Sequential scans are a few milliseconds at this size. |
| B.6: `tsv` weight A is title and author | Same, so `in:title` also matches the author's name | One weight label holds both. |
| B.2: `in:` scopes every text atom | It scopes words, phrases and prefixes. Fuzzy and regex always read the whole search text. | They run on text, not on the weighted vectors. |
| B.2: fuzzy threshold 0.6 as a constant | pg_trgm's own default (0.6) | It is a database setting, not a query argument. |
| B.3: `mentions:`, `kind:`, `linkkind:`, `stars:`, `bookmarks:`, `sort:stars` | Each answers "not available yet" | Kosha does not hold that data. |
| B.3: `replies-to:@h` | Matches only when the parent post is held | The parent's author is not stored on the reply. |
| B.3: `tagged-by:ai` | Accepted; the stored value is `agent` | The schema says `agent`. |
| B.3: `saved:` labelled "approx." | The filter uses the estimate where there is no real time; rows already carry `saved_at_approximate` | |
| C.3: summary is part of the embedded text | Only a summary that adds something (not own-text, not rule-built) | Rule-built summaries repeat the body and add the author, which the design keeps out of vectors. |
| D.8: `summary_source` verbatim / llm | `summary_method` own_text / rule (the column the schema has) | |
| Date bounds | Every date qualifier is a half-open range in UTC: `posted:2026-03` is the month, `>2026-03` starts in April, `saved:7d` is the last seven days | The design did not say. |
| List order | `coalesce(saved_at, saved_at_est, captured_at)` newest first, then `saved_order` | Items with no order from the source no longer sit on top. For X the estimate is decoded from `saved_order`, so the order of existing items is unchanged. |

Not built from sections B and C: the generated-tree round-trip test (ten hand-written queries are checked instead), date-picker presets by month in suggest, and the `term_df` view.
