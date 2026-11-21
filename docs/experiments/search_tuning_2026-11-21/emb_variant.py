"""Embedded-text variants: re-embed (in memory only) the items whose text has a
quoted post or link titles, without those parts. Vectors cached in emb_variants.npz."""
import sys
from types import SimpleNamespace

import numpy as np
from sqlalchemy import select

from base import HERE, Q40, engine, load, load_vectors, open_session
from evalib import line, run_config, summarize
from kosha.models import Item, ItemEmbedding
from kosha.search import embeddings as emb

load_vectors(); session, uid = open_session(); J = load("judgments.json")
rows = session.execute(emb.DOCS_SQL, {"user_id": uid, "model": emb.DOC_MODEL, "body_max": emb.BODY_MAX_CHARS, "media_max": emb.MEDIA_MAX_CHARS}).all()
VARIANTS = {"no_quoted": {"quoted"}, "no_links": {"links"}, "body_only": {"quoted", "links"}}
cache = HERE / "emb_variants.npz"
store = dict(np.load(cache)) if cache.exists() else {}
for name, drop in VARIANTS.items():
    if name + "_ids" in store:
        continue
    todo = []
    for row in rows:
        full = emb.document_text(row)
        cut = emb.document_text(SimpleNamespace(title=row.title, body=row.body, summary=row.summary, summary_method=row.summary_method,
                                                media=row.media, quoted=None if "quoted" in drop else row.quoted, links=None if "links" in drop else row.links))
        if cut and cut != full:
            todo.append((row.id, cut))
    vectors, tokens = [], 0
    for start in range(0, len(todo), 100):
        got, n = emb.embed_texts([t for _, t in todo[start:start + 100]], emb.DOC_MODEL, "document")
        vectors += got; tokens += n
    cost = emb.record_spend(session, uid, emb.DOC_MODEL, "embed_documents_experiment", tokens, items=len(todo), variant=name)
    session.commit()
    store[name + "_ids"], store[name + "_vec"] = np.array([i for i, _ in todo]), np.array(vectors, dtype=np.float32)
    np.savez(cache, **store)
    print(name, len(todo), "items", tokens, "tokens", f"${float(cost):.4f}")

pairs = session.execute(select(ItemEmbedding.item_id, ItemEmbedding.vector).where(ItemEmbedding.model == emb.DOC_MODEL)).all()
ids = np.array([i for i, _ in pairs])
base = np.array([v for _, v in pairs], dtype=np.float32)
pos = {int(i): n for n, i in enumerate(ids)}


def make_ranker(matrix):
    matrix = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

    def semantic_rank(session, where, vector, exclude_id=None):
        allowed = [pos[i] for i in session.scalars(select(Item.id).where(*where)) if i in pos]
        if not allowed:
            return []
        q = np.asarray(vector, dtype=np.float32); q /= np.linalg.norm(q)
        sims = matrix[allowed] @ q
        order = np.argsort(-sims)[:engine.RANKER_TOP_N]
        best = sims[order[0]]
        return [(int(ids[allowed[n]]), float(sims[n])) for n in order
                if sims[n] >= engine.SEMANTIC_MIN_SIMILARITY and sims[n] >= best - engine.SEMANTIC_MARGIN]
    return semantic_rank


sql_ranker = engine.semantic_rank
configs = {"control (numpy, stored vectors)": base}
for name in VARIANTS:
    m = base.copy()
    for i, v in zip(store[name + "_ids"], store[name + "_vec"]):
        if int(i) in pos:
            m[pos[int(i)]] = v
    configs[name] = m
extra = dict(KEYWORD_MIN_COVERAGE=float(sys.argv[1]) if len(sys.argv) > 1 else 1.0)
for mode in ("semantic", "hybrid"):
    for floor, margin in ((0.30, 0.15), (0.0, 1.0)):
        print(f"== {mode}, floor {floor} margin {margin}", extra if mode == "hybrid" else "")
        engine.semantic_rank = sql_ranker
        print(line("sql", summarize(run_config(session, uid, Q40, mode=mode, SEMANTIC_MIN_SIMILARITY=floor, SEMANTIC_MARGIN=margin, **extra), J, Q40)))
        for name, m in configs.items():
            engine.semantic_rank = make_ranker(m)
            print(line(name, summarize(run_config(session, uid, Q40, mode=mode, SEMANTIC_MIN_SIMILARITY=floor, SEMANTIC_MARGIN=margin, **extra), J, Q40)))
