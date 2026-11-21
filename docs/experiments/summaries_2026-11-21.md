# Summaries experiment on real items, 2026-11-21

Which summary recipe should be the default? Five recipes compared on 150 real X bookmarks. No bookmark text is reproduced here. The method below is enough to repeat it.

## Recommendation

**Keep rule-built summaries as the default. Do not write LLM summaries.**

| Text | Recipe | `summary_method` |
|---|---|---|
| 400 characters or less | The text itself | `own_text` |
| Longer | Title, first three sentences, one metadata line (author, source, length, content types, link domains) | `rule` |
| Under 200 characters of its own, and a media extract exists | The text, then the first 300 characters of each extract (at most two): `Transcript: …`, `Image: …` | `rule` |

- On Jev agreement the LLM summary was the worst text-bearing recipe: it lost 9 of 46 true yeses and added 4 false ones. The first 400 characters lost 5 and added none.
- On search, 13 of 15 probe queries ranked their item the same with any summary or none.
- It is cheap ($0.047 per 1,000 items, about $0.38 for the 7,992 items held now), so price is not the reason. It bought nothing that was measured.
- The third row is new and is not measured here (no item had a media extract when the sample was drawn). It follows from "title or first lines only" being the weakest recipe for videos, and it is what makes a transcript show up in a compact row. It is implemented in `kosha/summaries.py`.
- `llm_summaries()` stays in `kosha/summaries.py` (batched, small model, tested) but nothing calls it. So no LLM run was made on the real library.

Jev keeps reading the first 2,000 characters of the item's text, never the summary: that variant missed 1 of 46.

## Sample

150 saved X items, 30 per kind, chosen by a seeded hash of the id.

| Kind | Rule | In the library |
|---|---|---|
| Short post | no article, no video, 80 to 400 characters, at least 60 without URLs | 2,439 |
| Long post | no article, over 400 characters | 1,944 |
| Article | content type `article` | 119 |
| Video with little text | has a video, under 100 characters without URLs | 437 |
| Link only | has a link, no video, under 60 characters without URLs | 325 |

The reference ("full text") is title, body, quoted post, link titles and alt text, up to 12,000 characters. Its mean length in the sample is 368 characters, so most items are short: an article here is usually a post that links to one, not the article's text.

## Recipes

| Recipe | What Jev was given | Mean length |
|---|---|---|
| First 2,000 characters | What Jev reads today (control) | 348 |
| Own text | First 400 characters of title and body | 198 |
| Rule | The existing rule-built summary | 171 |
| Rule + metadata | Rule, plus a line with author, content types, link domains, up to two link titles and the start of a quoted post | 237 |
| LLM | One or two sentences, 40 words at most, from `gpt-6-luna`, 10 items a call, input cut at 6,000 characters; the own text when the model returned null (21 of 148) | 128 |
| Video title only | First two lines of the text, 200 characters at most | 108 |

## a. Jev agreement with the full text

Three yes/no questions from Constella's `measurements_2026-11-21_jev.md` (coding agents; robotics and hardware; shares an open-source project or free resource). Yes means p ≥ 0.8. There is no hand-labelled key: the full text is the reference. 450 answers per recipe; 46 are yes on the full text (10, 3 and 33).

| Recipe | Yeses lost | Yeses added | Agreement |
|---|---|---|---|
| First 2,000 characters | 1 | 0 | 99.8% |
| Own text (400) | 5 | 0 | 98.9% |
| Rule | 7 | 0 | 98.4% |
| Rule + metadata | 8 | 0 | 98.2% |
| LLM | 9 | 4 | 97.1% |
| Video title only | 12 | 0 | 97.3% |

Yeses lost out of the reference yeses, by kind:

| Recipe | Short (8) | Long (13) | Article (6) | Video, little text (4) | Link only (15) |
|---|---|---|---|---|---|
| First 2,000 characters | 0 | 1 | 0 | 0 | 0 |
| Own text (400) | 0 | 4 | 0 | 1 | 0 |
| Rule | 0 | 6 | 0 | 1 | 0 |
| Rule + metadata | 1 | 5 | 1 | 1 | 0 |
| LLM | 1 | 3 | 2 | 1 | 2 |
| Video title only | 1 | 7 | 3 | 1 | 0 |

- Most of the loss is in long posts and on the "shares an open-source resource" question, where the offer often sits late in the text. This repeats Constella's finding (its one-line summary lost 21 of 33).
- The LLM summary is the best of the summaries on long posts (3 lost of 13) and the only recipe that invents yeses (4, one in each of four kinds) and the only one that loses them on link-only items (2 of 15).
- The metadata line did not help Jev: 8 lost against 7.
- With 46 reference yeses, a difference of one or two items between recipes is noise. The gap between "first 2,000 characters" and every summary is not.

## b. Search rank for 15 probe queries

- Probes: 3 items per kind. For each, `gpt-6.1-terra` wrote the 4-to-8-word search its owner might type later, told not to copy a distinctive phrase.
- For each item the embedded text (title, body, quoted post, link titles) was embedded with `voyage-4-large` four times: as it is, and with `Summary: …` appended for the rule, rule + metadata and LLM recipes.
- Rank: cosine similarity to the `voyage-4-lite` query vector against the 7,159 stored vectors, with the item's own vector swapped for the variant; fused with the keyword ranking from `GET /api/items?mode=keyword` by reciprocal rank fusion (k = 60).

| Embedded text | Rank 1 | Better / worse than no summary | Mean similarity | Ranks of the 15 items |
|---|---|---|---|---|
| No summary | 12 | – | 0.6218 | 1, 3, 1, 1, 1, 2, 30, 1, 1, 1, 1, 1, 1, 1, 1 |
| + rule | 12 | 1 / 2 | 0.6219 | 1, 1, 1, 1, 1, 2, 59, 1, 1, 1, 1, 1, 1, 1, 2 |
| + rule and metadata | 13 | 1 / 1 | 0.6273 | 1, 1, 1, 1, 1, 2, 72, 1, 1, 1, 1, 1, 1, 1, 1 |
| + LLM | 12 | 2 / 1 | 0.6305 | 1, 1, 1, 3, 1, 2, 24, 1, 1, 1, 1, 1, 1, 1, 1 |

- No recipe changes the picture. The LLM summary raises mean similarity by 0.009 and moved two items up and one down.
- The probes are too easy to separate the recipes: 12 of 15 items are already first. Only one probe had a keyword hit at all (the keyword ranker wants the query's words in the item), so the hybrid rank here is the semantic rank in 14 of 15 cases.
- Not measured: the keyword side. A summary is indexed at the lowest weight (D), and the rule recipes add no new words to it.

## c. Cost

| Recipe | Per 1,000 items | Whole library (7,992 items) |
|---|---|---|
| Own text, rule, rule + metadata, video title | $0 | $0 |
| LLM, `gpt-6-luna`, 10 items a call | **$0.047** (measured: 148 items, 18,860 input and 9,531 output tokens, $0.0070) | about $0.38 |

- Output is 64 tokens an item, about half the bill. Through the Batch API the price halves.
- The sample is weighted towards short kinds; long posts and articles alone would cost more per 1,000.

## What this experiment spent

| Provider | What | Dollars |
|---|---|---|
| OpenAI | 15 summary calls ($0.0070), 15 probe queries ($0.0090) | $0.016 |
| Jev | 7 calls, 3 questions × 150 items × 7 texts | $0.0082 |
| Voyage | 60 documents and 15 queries | $0.0011 |

## If this is revisited

- LLM summaries would be worth another look for long posts only (1,944 items), judged on what they are for now: the compact rows that Ask and a person read. That needs a reading test, not Jev agreement.
- A harder probe set (queries written without seeing the item, or taken from real searches) is needed before a search effect can be ruled in or out.
