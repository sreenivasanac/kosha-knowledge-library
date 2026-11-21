# Pipeline smoke run on the real library, 2026-11-21

What the pipeline engine (`backend/kosha/pipeline/`) did on the real data, through the running server: `POST /api/query` for pipelines and `POST /mcp` for the `runs` and `tags` tools. No bookmark text is reproduced here beyond stems and author handles.

- Library: 7,159 saved items (X bookmarks), 7,124 with an embedding (`voyage-4-large`, 1,024 dimensions).
- Jev: model `jev-1.14.0`, one layout for every call (the question as the state, up to 100 items per request), reading the first 2,000 characters of each item.
- Tokens are the size of the reply the agent reads, estimated as characters / 4. Latency is wall time of the HTTP call on the same machine. Money is Jev's billed input tokens × $0.042 per million.
- **Jev spent in this run and all testing: $0.0059** (139,527 input tokens, 9 paid or cached calls in `spend_ledger`), of a $0.50 budget.

## 1. The owner's RAG example

Authors came from `all | grep ~retriev | count by author top 10` (76 tokens, 88 ms) and `all | semantic "retrieval augmented generation RAG" top 300 | count by author top 12` (83 tokens, 232 ms). The same four lead both lists.

```
where from:llama_index,jerryjliu0,LangChain,omarsar0 | grep ~retriev | semantic "retrieval augmented generation"
  | head 200 | jev "<question>" >= <p> | tag "AI/RAG"
```

| Call | Stages (in→out) | Reply tokens | Latency | Money |
|---|---|---|---|---|
| The example as written: `jev "about RAG / retrieval-augmented generation?" >= 0.9` | where 428, grep 106, semantic 106, head 106, jev **0**, tag 0 | 48 | 544 ms | $0.0012 |
| The same again (answers cached) | same | 51 | 34 ms | $0 |
| With a full question, `>= 0.8`, in a label run (trial tree): preview | where 428, grep 106, semantic 106, head 106, jev **78**, tag: preview | 236 | 591 ms | $0.0013 |
| The same with the confirm token: applied | tag: new=78, tag created as proposed, one undo batch | 77 | 48 ms | $0 |
| The same in the real tree: preview only, not confirmed | jev 78 (cached), tag: preview | 229 | 27 ms | $0 |

The full question was: "Is this post about retrieval-augmented generation (RAG): retrieving documents to ground a language model's answer, including chunking, rerankers and vector search for LLM apps?"

Histograms of p over the same 106 items:

| Question | <0.2 | 0.2–0.5 | 0.5–0.8 | 0.8–0.9 | ≥0.9 |
|---|---|---|---|---|---|
| "about RAG / retrieval-augmented generation?" | 0 | 9 | 91 | 6 | 0 |
| The full question | 1 | 8 | 19 | 26 | 52 |

What it shows:

- **The example as the owner wrote it tags nothing.** Jev reads literally; a fragment that is not a question gets a middling probability for every item. The grammar in the tool description now says so. The cut-off is not the cause: at 0.8 the fragment still keeps only 6.
- With a spelled-out question, 78 of 106 pass at 0.8 and 52 at 0.9. Whether those 78 are right was not judged by hand.
- The write followed the rules: 78 items is over 25, so the first call returned a preview with five samples and a token; the second applied it; the tag `AI/RAG` and its parent `AI` were created as proposed. `runs undo` removed the 78 rows again (status went from `tag_rows=78` back to 0). The real tree was never written to.
- The whole chain is one call of about 240 tokens for the agent. Reading 106 summaries instead would be about 5,000 tokens (estimated at 45 tokens each).

## 2. Groups, pick one, groups again

| Call | Reply tokens | Latency | Money |
|---|---|---|---|
| `all \| groups 12 as g` | 648 | 1,158 ms | $0 |
| `@g3 \| groups 6 as g3.` | 365 | 130 ms | $0 |

- First level, 7,124 items: sizes 900, 704, 683, 653, 601, 582, 582, 537, 514, 505, 441, 422; cohesion 0.57 to 0.73.
- `@g3` (683 items, cohesion 0.73) again: 187, 152, 103, 100, 89, 52; cohesion 0.74 to 0.79.
- Each group is a saved set (`@g1` … `@g12`, `@g3.1` … `@g3.6`) and comes with three 60-character samples, about 54 tokens a group.

## 3. Terms

`@g3 | terms 12`: 93 tokens, 82 ms, $0.

| stem | z | in set | in library |
|---|---|---|---|
| rag | 33.7 | 404 | 468 |
| retriev | 24.6 | 217 | 261 |
| llm | 22.4 | 237 | 612 |
| index | 20.6 | 140 | 221 |
| document | 20.1 | 149 | 291 |
| queri | 19.3 | 124 | 197 |
| data | 18.7 | 219 | 704 |
| pipelin | 17.6 | 117 | 233 |
| llama | 17.0 | 117 | 252 |
| embed | 16.8 | 99 | 174 |
| vector | 16.6 | 93 | 151 |
| complex | 16.5 | 120 | 285 |

So the third embedding group is the retrieval group: it holds 404 of the library's 468 items containing the stem `rag`. The 64 outside it are what `all | grep rag* | except @g3` would hand to a recall check.

## 4. Jev over 300 items

```
all | semantic "AI coding agents such as Claude Code, Codex or Cursor" top 300
  | jev "Is this post about building software with AI coding agents (for example Claude Code, Codex or Cursor)?" as agents | as @agents300
```

| Call | Reply tokens | Latency | Money |
|---|---|---|---|
| First time: 300 questions in 3 requests | 58 | 565 ms | $0.0021 (estimate before the call: $0.0022) |
| The same pipeline again | 50 | 45 ms | $0 (300 of 300 from the cache) |
| `@agents300 \| where agents:>=0.8 \| count by author top 8` | 62 | 5 ms | $0 |
| `@agents300 \| stats agents` | 42 | 4 ms | $0 |

- Histogram: 0 under 0.2, 20 in 0.2–0.5, 94 in 0.5–0.8, 92 in 0.8–0.9, 94 at 0.9 or above. 186 of the 300 nearest items pass at 0.8; median p 0.84.
- 170 billed tokens per item ($0.0071 per 1,000 items), in line with Constella's measurement of 167.
- The estimate was 3% over the bill.

The search bar's route, `POST /api/jev/check` with `q: "from:llama_index"` (132 items) and a yes/no question about RAG: dry run `{estimate_usd: 0.001253, n: 132}` in 10 ms; the real call kept 84 at 0.8 for $0.0012 in 337 ms and returned a snapshot id to page through with `GET /api/items`.

## 5. Other calls in the run

| Call | Reply tokens | Latency |
|---|---|---|
| `all \| count by author top 12` | 80 | 53 ms |
| `runs start` / `status` / `close` (MCP) | 17 / 55 / 57 | 8 to 35 ms |
| `tags` on the trial tree (MCP) | 25 | 4 ms |
| Uncertain band: `... \| jev "..." \| where p:0.5..0.9 \| sample 6 \| show id p text:24` | 106 | 26 ms |

The six tool descriptions cost an agent about 1,500 tokens per session (`query` about 800 of them, the grammar with eight examples).

## Against the weaknesses in `search_eval_2026-11-21.md`

- *No relevance judgment after retrieval.* `semantic ... top 300 | jev ...` adds one: 300 retrieved, 186 judged relevant at 0.8, for $0.002 and half a second.
- *No aggregates.* `count by`, `stats`, `terms` and `groups` answer "who posts about this", "how many", "what words mark this set" in 40 to 650 tokens without any item text leaving the server.

## Not measured

- Correctness of Jev's answers against hand labels.
- `score_labels` on the real library (it needs a tree with definitions; it is covered by tests with a fake Jev).
- `hybrid` inside a pipeline beyond a five-item check, and any set above 7,159 items.
- An MCP client other than raw JSON-RPC calls to `/mcp` (in tests and against the running server).
