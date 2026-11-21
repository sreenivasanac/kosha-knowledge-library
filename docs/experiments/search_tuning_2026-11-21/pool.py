"""Pool: top 30 of every base method and several fusions, per query."""
from base import Q20, Q40, dump, load, load_vectors, open_session, save_vectors
from evalib import BEFORE, run_config

load_vectors()
session, uid = open_session()
ALL = Q40 + Q20
WIDE = dict(SEMANTIC_MIN_SIMILARITY=0.0, SEMANTIC_MARGIN=1.0)
runs = {
    "kw_and": run_config(session, uid, ALL, mode="keyword"),
    "kw_any": run_config(session, uid, ALL, KEYWORD_MIN_COVERAGE=0.3, WEIGHTS_DEFAULT=(1, 0), WEIGHTS_IDENTIFIER=(1, 0), WEIGHTS_LONG=(1, 0), **{}),
    "sem_wide": run_config(session, uid, ALL, mode="semantic", **WIDE),
    "hyb_and_wide": run_config(session, uid, ALL, **WIDE),
    "hyb_any_wide": run_config(session, uid, ALL, KEYWORD_MIN_COVERAGE=0.5, **WIDE),
    "hyb_any_sem2": run_config(session, uid, ALL, KEYWORD_MIN_COVERAGE=0.5, WEIGHTS_DEFAULT=(1, 2), WEIGHTS_IDENTIFIER=(1, 2), WEIGHTS_LONG=(1, 2), **WIDE),
    "hyb_any_kw2": run_config(session, uid, ALL, KEYWORD_MIN_COVERAGE=0.5, WEIGHTS_DEFAULT=(2, 1), WEIGHTS_IDENTIFIER=(2, 1), WEIGHTS_LONG=(2, 1), **WIDE),
}
save_vectors()
pool = {}
for qid, *_ in ALL:
    ids = set()
    for mode in ("keyword", "semantic", "hybrid"):
        ids |= set(BEFORE[qid][mode]["ids"][:30])
    for name, r in runs.items():
        ids |= set(r[qid]["ids"][:40 if name == "sem_wide" else 30])
    pool[qid] = sorted(ids)
dump("pool.json", pool)
sizes = [len(v) for v in pool.values()]
print("pool sizes", sizes, "total", sum(sizes))
for qid in ("k1", "y2", "y3", "y5", "n1", "p1"):
    print(qid, {k: (r[qid]["n"], r[qid]["notice"]) for k, r in runs.items()})
