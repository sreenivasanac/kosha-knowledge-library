"""Search (qualifier language, keyword, semantic, hybrid) is built here later.

The schema is ready for it: `items.tsv` (generated, GIN), trigram GIN indexes on
`items.body` and `items.title`, `tags.path` (ltree, GiST), `item_embeddings`.
The list endpoint takes SQL conditions from `kosha.items_query`, which is where a
compiled filter AST plugs in.
"""
