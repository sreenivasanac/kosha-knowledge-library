# Search tuning, 21 Nov 2026

Follows `search_eval_2026-11-21.md`. Every number here is measured on the real library unless it says otherwise.

- **Judgments are LLM-made.** gpt-5.4 graded 3,657 (query, item) pairs 0 to 2 from the first 2,000 characters of each item. Nobody has checked them.
- The constants were tuned on the same 40 queries they are reported on, so the gains are somewhat optimistic. The 4 LinkedIn queries were added afterwards and not tuned on.
- The library grew during the work (7,159 X items, then 833 LinkedIn saves). The before/after table uses the X items only, the corpus the "before" run saw. A whole-library row follows it.
- Test set: `search_testset_2026-11-21.json` (queries and judged ids, no text). Scripts and cached scores: `search_tuning_2026-11-21/` (run from `backend/` with `uv run python ../docs/experiments/search_tuning_2026-11-21/final.py`).

## 1. Before and after (40 queries, X items)

nDCG@10 and recall@30 are means over the 36 queries that have a relevant item. P@10 is the share of the shown top 10 that is relevant (grade 1 or 2). "Shown" is the mean number of results. Latency is the warm median inside the engine (query vector cached).

| Method | nDCG@10 | P@10 | Recall@30 | Shown | Empty | Latency |
|---|---|---|---|---|---|---|
| Before: keyword | 0.416 | 0.965 | 0.198 | 19 | 15 | 2 ms |
| Before: semantic | 0.673 | 0.815 | 0.345 | 39 | 1 | 15 ms |
| **Before: hybrid** | **0.723** | **0.838** | **0.409** | 52 | 0 | 18 ms |
| After: keyword | 0.448 | 0.956 | 0.215 | 22 | 12 | 11 ms |
| After: semantic, weaker shown | 0.715 | 0.774 | 0.530 | 190 | 0 | 16 ms |
| **After: hybrid, default (strong matches only)** | **0.772** | **0.924** | **0.467** | 67 | 1 | 27 ms (p95 67, max 117) |
| After: hybrid, `weak=true` (what a pipeline stage gets) | 0.806 | 0.840 | 0.589 | 250 | 0 | same |
| After: hybrid + `rerank=voyage` (rerank-3, top 50) | 0.858 | 0.874 | 0.623 | 50 | 0 | + 268 ms |
| After: hybrid + `rerank=jev` (top 50) | 0.857 | 0.867 | 0.640 | 50 | 0 | + 205 ms |

- Whole library (X + LinkedIn), adopted defaults: 0.782 / 0.926 / 0.475 by default, 0.819 / 0.840 / 0.622 with `weak=true`.
- The 4 LinkedIn queries (not tuned on): 0.815 / 0.925 / 0.587 by default.
- Negative queries (nothing in the library answers them): before, 3 to 6 results each, all noise. After, 1, 1, 0 and 1 result; 201 are behind `weak=true`.
- Over HTTP a first search takes 180 to 330 ms, of which about 170 ms is the Voyage call for the query vector; a repeated one 30 to 55 ms. A first search with a misspelled word was 314 ms, just over the 300 ms target.

### Per category (nDCG@10 / P@10 / recall@30)

| Category | n | Before hybrid | After default | After `weak=true` | + Jev rerank | + Voyage rerank |
|---|---|---|---|---|---|---|
| Known-item | 6 | 0.661 / 0.81 / 0.33 | 0.717 / 0.88 / 0.36 | 0.833 / 0.63 / 0.75 | 0.886 | 0.859 |
| Topical | 6 | 0.798 / 0.97 / 0.49 | 0.845 / 0.95 / 0.51 | 0.845 / 0.95 / 0.50 | 0.943 | 0.889 |
| Project relevance | 6 | 0.801 / 0.97 / 0.49 | 0.794 / 0.95 / 0.49 | 0.784 / 0.95 / 0.56 | 0.923 | 0.878 |
| Typo / partial | 5 | 0.698 / 0.82 / 0.45 | 0.787 / 0.92 / 0.49 | 0.797 / 0.96 / 0.53 | 0.839 | 0.921 |
| Short two-word | 5 | 0.942 / 1.00 / 0.54 | 0.953 / 1.00 / 0.53 | 0.953 / 1.00 / 0.52 | 0.960 | 0.962 |
| Long question | 5 | 0.759 / 0.92 / 0.23 | 0.719 / 0.92 / 0.45 | 0.728 / 0.96 / 0.48 | 0.882 | 0.835 |
| Qualifier + text | 5 | 0.938 / 0.98 / 0.61 | 0.944 / 1.00 / 0.60 | 0.944 / 1.00 / 0.76 | 0.968 | 0.961 |

All six known items are first in every "after" row; the known-item figures move with the partially relevant items around them.

### The first evaluation's 12 text queries

Verdict by rule from the LLM grades of the top 5 (good: a grade 2, or three relevant; mixed: one or two relevant; poor: none). This rule is kinder than the first evaluation's reading by hand, so compare the two columns, not this table with that one.

| | Before | After (default) |
|---|---|---|
| Good | 9 | 8 |
| Mixed | 0 | 1 (Q11: 2 results shown, 22 before) |
| Poor | 2 (Q1 padded with 27 unrelated threads; Q4 empty) | 1 (Q4 empty) |
| Right to be empty or near it | 1 (Q8) | 2 (Q8; Q1 now shows 1) |

The filter-only and suggest queries (Q12, Q13, Q15 to Q20) run as before. `similar:` now returns up to 200 neighbours (see section 5).

## 2. Adopted defaults (all constants at the top of `kosha/search/engine.py`)

| Constant | Was | Now | Why |
|---|---|---|---|
| `KEYWORD_MIN_COVERAGE` | every word (AND) | 0.35 of the query's word weight | The largest single gain: nDCG 0.741 to 0.795, recall@30 0.49 to 0.63. Each word weighs its rarity (BM25's idf, counted with the word index), so a row with "pgvector" and "HNSW" is a hit without "building". Keyword mode stays AND. |
| Semantic floor 0.30 and margin 0.15 | applied in the ranker | removed | Cost a third of recall@30 (0.49 against 0.68) and emptied searches inside a narrow filter. The ranker returns its nearest 200. |
| `STRONG_COVERAGE` 0.6, `STRONG_SIMILARITY` 0.40, `STRONG_MARGIN` 0.20 | – | new | The "few good results" cut. A result is strong when its keyword coverage is at least 0.6, or its similarity is at least 0.40 and within 0.20 of the best. `GET /api/items` shows the strong ones and returns `weak_hidden`; `weak=true` shows all. Of 60 settings tried this had the best nDCG among those that keep negatives near empty. |
| `ENGAGEMENT_WEIGHT` | – | 0.1 | Score × (1 + 0.1 × log10(1 + likes) / 6). nDCG +0.011, the same at 0.03 and 0.3. |
| Misspellings | none | prefix, then closest indexed words | A word no item contains is tried as a prefix, then replaced by the up to 3 closest indexed words (trigram similarity ≥ 0.4, author names included). Notice `fuzzy_match`. About 85 ms, only when it happens. Typo nDCG 0.70 to 0.79. |
| Fusion weights, `RRF_K`, field weights | 1:1, 2:1 short, 1:1.5 long; 60 | unchanged | See section 3. |
| Embedded text | title, body, quoted, link titles, summary | + first 2,000 characters of media extracts | Asked for by the other agents; 140 items re-embedded. Not measured on the test set. |

New parameters: `rerank=voyage|jev|none` and `weak=true|false` on `GET /api/items`, `weak_hidden` in the reply. Notices are now `no_exact_matches`, `fuzzy_match`, `semantic_unavailable`, `rerank_unavailable`. `engine.rank()` keeps its signature and returns every hit, weak ones included.

## 3. What did not help

All on the adopted retrieval; differences under about 0.01 nDCG are noise at 36 queries.

| Variant | Result |
|---|---|
| Keyword:semantic weight for all queries, 2:1 / 1.5:1 / 1:1 / 1:2 | 0.817 / 0.829 / 0.821 / 0.805 against 0.826 for the shape-based weights |
| Short-query weight 1:1, 1:2, 3:1 instead of 2:1 | 0.824, 0.817, 0.827. The first evaluation's worry that 2:1 is too high for two-word queries does not show: short queries score 0.95 either way. |
| Long-question weight 1:1, 1:2, 1:3 instead of 1:1.5 | 0.823, 0.829, 0.832 |
| "Long" from 5 or 8 words instead of 6 | 0.828, 0.827 |
| RRF k of 10, 30, 100 | 0.822, 0.828, 0.827 |
| Phrase boost (words adjacent) | 0.827 at both strengths; cover density already rewards closeness. Code removed. |
| Title/author and body field weights (five settings) | 0.825 to 0.828. Only 119 items have a title. |
| Share of "any word" in the keyword score; length normalisation | 0.820 to 0.829 |
| Recency as a tie-breaker | 0.828 at 0.03, 0.813 at 0.3. Code removed. |
| Coverage 0.25 / 0.3 / 0.4 / 0.45 | 0.809 / 0.812 / 0.820 / 0.798 against 0.826 at 0.35 |
| Embedded text without the quoted post / link titles / both | Semantic recall@30 0.646 to 0.632 / 0.624 / 0.608; nDCG within 0.01. Both stay in. |
| Summary in the embedded text | Not testable: no item had an LLM-written summary when this ran. |
| One statement with a FILTER per suggest count | 2 to 80 times slower than one indexed count each (tag-status 2 ms to 186 ms). Reverted. |

## 4. Rerankers (top 50, scores for 3,624 pooled pairs)

| | nDCG@10 | P@10 | Recall@30 | Latency per 50, median / p95 | Cost per query |
|---|---|---|---|---|---|
| None (`weak=true` order) | 0.806 | 0.840 | 0.589 | – | – |
| Voyage `rerank-3-lite` | 0.817 | 0.850 | 0.611 | 257 / 292 ms | $0.00019 |
| Voyage `rerank-3` | 0.858 | 0.874 | 0.623 | 268 / 301 ms | $0.00049 |
| Jev yes/no, query as a full question | 0.857 | 0.867 | 0.640 | 205 / 312 ms | $0.00052 |

- Voyage's current models are `rerank-3` ($0.05 per million tokens) and `rerank-3-lite` ($0.02); the 2.5 family is listed as older (docs.voyageai.com/docs/reranker.md and pricing.md, read today). An item is about 195 tokens.
- Jev and `rerank-3` are level overall. Jev is better on project, topical and long questions (0.92, 0.94, 0.88 against 0.88, 0.89, 0.84); `rerank-3` on misspelled queries (0.92 against 0.84), because Jev reads the misspelled question literally.
- `rerank-3-lite` gains little. `VOYAGE_RERANK_MODEL` is `rerank-3`.
- As a cut, with the scores as thresholds (`STRONG_SCORE`): Jev at 0.2 gives P@10 0.94, nDCG 0.815, 29 results, negatives 0.5 results; `rerank-3` at 0.4 gives 0.95, 0.800, 33, negatives 1.25. Jev at 0.5 is too strict (5 real queries come back empty).
- Jev answers are cached per (question, item), so the same search again costs nothing; a Voyage score is not kept.
- The search bar stays without a reranker: it would triple the time of a first search.

## 5. Remaining weak spots

- **Long questions and project needs without a reranker.** nDCG 0.72 and 0.79, no better than before; the gain there is recall (0.23 to 0.45 for long questions). Filler words ("which bookmarks explain") still take part in the keyword side.
- **Negatives still show one result** in 3 of 4 cases: one item holding most of the query's rare words (a recipe post for the bread query, an infrastructure post for the Terraform one).
- **A word no item contains has the highest weight.** "x y zebra" makes rows with x and y weak (coverage about 0.57), so a query with one off-topic word can show nothing by default.
- **Q4 (a GitHub repo for a Postgres job queue): nothing shown.** No item is squarely about it, and most link titles are still missing.
- **`similar:` returns up to 200.** Item-to-item similarities are higher than query-to-item ones, so the 0.40 / 0.20 rule cuts nothing there.
- **Inside a narrow filter the semantic ranker ranks everything**, so unrelated rows get a little rank credit and can sit above a keyword-only hit.
- **Misspellings are corrected only when the word matches nothing.** A typo that is itself a word in the library is searched as typed.
- **`from:name` picks up to 20 authors containing the text** ("simon" matches several); `@handle` is exact.
- Suggest was already fast: worst cases in-process are `tags:` (about 50 ms) and `is:` (about 45 ms); `is:untagged` and `tags:0` are now an anti-join. `has:code` is offered without a count (a regex over all text, about 40 ms).

## 6. Qualifier additions (`kosha/search/query.py`)

- `has:code`: a link to github.com, gist, gitlab, git.new, Colab, Kaggle or Replit, or text with a code fence or an install command (815 items).
- Date words for every date key: `today`, `yesterday`, `thisweek`, `lastweek`, `thismonth`, `lastmonth`, `thisyear`, `lastyear`, also in ranges (`saved:lastweek..today`) and in suggest.
- `from:name` without `@`: the exact handle; else authors whose handle or name contains it; else the closest spelling (`from:karpthy`, `from:hamel`, `from:"Sebastian Raschka"`). This is what makes `from:` usable for LinkedIn, whose handles are internal ids; suggest inserts the name for those.
- `domain:youtube.com` also matches youtu.be; `x.com` and `twitter.com` match each other; `github.com` matches git.new.
- `-from:a,b` already worked; it now has a test.

## 7. For the Ask agent: recall first, then precision

1. **Do not start from the search bar's default.** Use the pipeline's `hybrid "..."` (it calls `engine.rank`, which includes weak matches): recall@30 0.59 against 0.47.
2. **Put filters in `where` first.** A narrow set no longer comes back empty: `where from:simonw | hybrid "..."` returns all 8.
3. **Write `hybrid` text as the need in plain words, 4 to 10 of them, rare terms included.** Partial keyword matching rewards the rare ones. Leave out "which bookmarks", "everything useful for".
4. **Then judge the head with Jev**: `... | hybrid "..." top 50 | jev "<full question that says what counts>" >= 0.2`. On this test set that is nDCG 0.86, P@10 0.94 at about $0.0005 and 0.2 s. Keep 0.2 for breadth, 0.5 when only sure answers are wanted.
5. **For wide recall** ("everything useful for project X"): `semantic "..." top 200`, `union` a `grep` of one or two rare stems, then Jev. The 200 nearest hold more than the fused top 50.
6. **An empty or tiny result is an answer.** With `no_exact_matches` and nothing above Jev 0.2, say the library has nothing on it.
7. **Known item with an author**: `where from:<name> | hybrid "<two or three content words>"`; `from:` tolerates partial and misspelled names.
8. **Misspelled or unfinished words** are handled on the keyword side; prefer Voyage rerank over Jev when the query itself is misspelled.

## 8. For the pipeline owner

The `rerank` stage is still missing; nothing in `kosha/pipeline/` was edited. The function to call is `kosha.search.rerank.rerank(session, user_id, text, ids, method)` with `method` `"voyage"` or `"jev"`: it reorders the first 50 ids, returns `(ids, {id: score})`, writes its own ledger row and raises `RerankError` when the service cannot be used. Suggested stage: `rerank "text" [voyage|jev] [>= score]`. Also: `engine.rank()` now returns up to about 400 ids with no similarity floor, so `hybrid` inside a narrow `where` no longer returns 0.

Edits outside `kosha/search/`: `kosha/api/items.py` (two query parameters passed to `search`, `weak_hidden` in the reply) and `kosha/schemas.py` (`ItemPage.weak_hidden`, the notice comment).

## 9. Money (list prices, all in `spend_ledger`)

| Provider | What | Spent | Budget |
|---|---|---|---|
| OpenAI | 3,657 judgments with gpt-5.4 (2,837 through the Batch API at half price) | $1.587 | $2 |
| Voyage | rerank-3 $0.035, rerank-3-lite $0.014, embedding variants $0.043, re-embedding 140 items $0.004, query vectors under $0.001 | $0.097 | $1 |
| Jev | 3,624 yes/no answers | $0.038 | $0.40 |
